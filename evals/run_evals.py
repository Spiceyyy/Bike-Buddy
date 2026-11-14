"""Intent-parsing eval: compares the LLM's RouteSpec with hand-labelled specs.

Calls the live LLM once per request and does not touch the routing engine.
Results are saved after every request. If a daily quota stops the run, the same
command continues from where it left off.

    docker compose exec backend python /evals/run_evals.py --version v1
"""

import argparse
import asyncio
import hashlib
import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError

from app.agent.gemini import GEMINI_BASE_URL, GeminiClient
from app.agent.llm import LLMClient, LLMRateLimitError
from app.agent.prompts import load_prompt
from app.config import Settings
from app.evaluation.intent_scoring import CaseScore, score_case, summarize
from app.models.llm import LLMResponse, ToolDefinition, UserMessage
from app.models.spec import RouteSpec
from app.tools.plan_route import DESCRIPTION, NAME
from app.tools.search_places import DESCRIPTION as SEARCH_DESCRIPTION
from app.tools.search_places import NAME as SEARCH_NAME
from app.tools.search_places import SearchPlacesArgs

EVALS_DIR = Path(__file__).parent
MAX_RATE_LIMIT_WAITS = 5
DEFAULT_RATE_LIMIT_WAIT_SECONDS = 30.0
# A longer suggested wait means a daily quota, so stop instead of sleeping for hours.
MAX_WAIT_SECONDS = 120.0


class QuotaExhausted(Exception):
    def __init__(self, wait_seconds: float) -> None:
        super().__init__("LLM quota exhausted")
        self.wait_seconds = wait_seconds


def load_cases(limit: int | None) -> list[dict[str, Any]]:
    lines = (EVALS_DIR / "requests.jsonl").read_text(encoding="utf-8").splitlines()
    cases = [json.loads(line) for line in lines if line.strip()]
    return cases[:limit]


def load_previous(output: Path, model: str, prompt_sha: str) -> dict[str, dict[str, Any]]:
    """Return finished cases from an earlier run, if it used the same model and prompt."""
    if not output.exists():
        return {}
    previous = json.loads(output.read_text(encoding="utf-8"))
    if (previous["model"], previous["prompt_sha256"]) != (model, prompt_sha):
        raise SystemExit(
            f"{output.name} was made with a different model or prompt. "
            "Use a new --version so the results stay comparable."
        )
    return {case["id"]: case for case in previous["cases"]}


async def generate_with_waits(
    llm: LLMClient, system: str, message: str, tools: list[ToolDefinition]
) -> LLMResponse:
    """Call the LLM, waiting out short rate limits rather than losing the case."""
    for _ in range(MAX_RATE_LIMIT_WAITS):
        try:
            return await llm.generate(system, [UserMessage(text=message)], tools)
        except LLMRateLimitError as exc:
            wait = exc.retry_after_seconds or DEFAULT_RATE_LIMIT_WAIT_SECONDS
            if wait > MAX_WAIT_SECONDS:
                raise QuotaExhausted(wait) from exc
            print(f"  rate limited, waiting {wait:.0f}s", flush=True)
            await asyncio.sleep(wait)
    return await llm.generate(system, [UserMessage(text=message)], tools)


async def run_case(
    llm: LLMClient, system: str, tools: list[ToolDefinition], case: dict[str, Any]
) -> dict[str, Any]:
    started = time.perf_counter()
    response = await generate_with_waits(llm, system, case["message"], tools)
    latency_ms = round((time.perf_counter() - started) * 1000)

    predicted, invalid = None, False
    calls = [call for call in response.message.tool_calls if call.name == NAME]
    if calls:
        try:
            predicted = RouteSpec.model_validate(calls[0].arguments)
        except ValidationError:
            invalid = True

    searches = [call for call in response.message.tool_calls if call.name == SEARCH_NAME]
    expected = RouteSpec.model_validate(case["expected"]) if case["expected"] else None
    score = score_case(
        case["id"],
        expected,
        predicted,
        expect_search=case.get("search", False),
        searched=bool(searches),
    )
    return {
        "message": case["message"],
        "expected": case["expected"],
        "predicted": predicted.model_dump(mode="json") if predicted else None,
        "invalid_arguments": invalid,
        "search_queries": [call.arguments.get("query") for call in searches],
        "reply_text": response.message.text,
        "latency_ms": latency_ms,
        "input_tokens": response.input_tokens,
        "output_tokens": response.output_tokens,
        **score.model_dump(),
    }


def build_report(
    version: str, model: str, prompt_sha: str, results: list[dict[str, Any]], total: int
) -> dict[str, Any]:
    latencies = [result["latency_ms"] for result in results]
    return {
        "version": version,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": model,
        "prompt_sha256": prompt_sha,
        "complete": len(results) == total,
        "summary": summarize([CaseScore.model_validate(r) for r in results]).model_dump(),
        "latency_ms": {"median": round(statistics.median(latencies)), "max": max(latencies)},
        "tokens": {
            "input": sum(result["input_tokens"] for result in results),
            "output": sum(result["output_tokens"] for result in results),
        },
        "cases": results,
    }


def save_report(output: Path, report: dict[str, Any]) -> None:
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def print_report(report: dict[str, Any], output: Path) -> None:
    summary = report["summary"]
    state = "complete" if report["complete"] else "PARTIAL"
    print(f"\nModel: {report['model']} ({state}: {summary['total']} requests)")
    print(f"Exact match:       {summary['exact_match']:.1%}")
    print(f"Tool-use decision: {summary['decision_accuracy']:.1%}")
    for name, accuracy in summary["field_accuracy"].items():
        print(f"  {name:<20}{accuracy:.1%}")
    print(f"Median latency:    {report['latency_ms']['median']} ms")
    print(f"Tokens:            {report['tokens']['input']} in, {report['tokens']['output']} out")
    print(f"Saved to {output}")


async def run(version: str, limit: int | None, delay_seconds: float) -> None:
    settings = Settings()
    system = load_prompt("planner")
    prompt_sha = hashlib.sha256(system.encode()).hexdigest()[:12]
    # The same two tools the app offers, so the eval measures the real setup.
    tools = [
        ToolDefinition(name=NAME, description=DESCRIPTION, parameters=RouteSpec.model_json_schema()),
        ToolDefinition(
            name=SEARCH_NAME,
            description=SEARCH_DESCRIPTION,
            parameters=SearchPlacesArgs.model_json_schema(),
        ),
    ]
    cases = load_cases(limit)
    output = EVALS_DIR / "results" / f"{version}.json"
    finished = load_previous(output, settings.gemini_model, prompt_sha)
    if finished:
        print(f"Resuming: {len(finished)} of {len(cases)} already done", flush=True)

    quota_wait = None
    async with httpx.AsyncClient(
        base_url=GEMINI_BASE_URL, timeout=settings.llm_timeout_seconds
    ) as http:
        llm = GeminiClient(
            http,
            api_key=settings.gemini_api_key.get_secret_value(),
            model=settings.gemini_model,
        )
        for index, case in enumerate(cases, 1):
            if case["id"] in finished:
                continue
            try:
                finished[case["id"]] = await run_case(llm, system, tools, case)
            except QuotaExhausted as exc:
                quota_wait = exc.wait_seconds
                break
            status = "pass" if finished[case["id"]]["exact"] else "FAIL"
            print(f"[{index}/{len(cases)}] {status}  {case['id']}", flush=True)
            results = [finished[c["id"]] for c in cases if c["id"] in finished]
            save_report(
                output,
                build_report(version, settings.gemini_model, prompt_sha, results, len(cases)),
            )
            await asyncio.sleep(delay_seconds)

    if quota_wait is not None:
        print(
            f"\nDaily quota reached after {len(finished)} of {len(cases)} requests. "
            f"It resets in about {quota_wait / 3600:.1f} hours. "
            "Run the same command then to continue.",
            flush=True,
        )
    results = [finished[c["id"]] for c in cases if c["id"] in finished]
    if results:
        print_report(
            build_report(version, settings.gemini_model, prompt_sha, results, len(cases)), output
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="Name of the results file.")
    parser.add_argument("--limit", type=int, help="Only run the first N requests.")
    parser.add_argument("--delay", type=float, default=5.0, help="Seconds between requests.")
    args = parser.parse_args()
    asyncio.run(run(args.version, args.limit, args.delay))


if __name__ == "__main__":
    main()

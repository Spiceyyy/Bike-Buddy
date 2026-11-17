"""Faithfulness eval: do the planner's replies match the routes it planned?

Runs every request in requests.jsonl end to end, exactly as /api/plan does:
the live LLM, the real geocoder, routing engine, scenery, café index and forecast. For
each request that produces a route, the reply is checked against the
plan_route result (app/evaluation/faithfulness.py):

- wrong_number:   a distance, riding time, climb, percentage, wind speed or
                  temperature not in the result
- unknown_name:   a name that is not on the route or in the request
- missing_fact:   no distance or riding time, or weather notes left out
- missing_caveat: an unmet stop, climbing limit or loop distance left unsaid
- unsupported_scenery: water, green space or quiet streets with no such
                  highlight, or any claim about the surface
- wrong_caveat:   saying the loop missed its distance when it did not

Each reply is scored twice: the model's first draft, and the final reply after
the one rewrite the app allows. The gap shows what the live check buys.

The checker's name and caveat rules are heuristics, so every flagged reply is
reviewed by hand in faithfulness_review.json ("real" or "false_alarm"), and
errors found by reading the unflagged replies are recorded as "missed". After
adding verdicts, refresh the summary with --resummarize.

Results are saved after every request; after a daily quota stop, the same
command continues where it left off.

    docker compose exec backend python /evals/run_faithfulness.py --version v1
"""

import argparse
import asyncio
import hashlib
import json
import os
import statistics
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import asyncpg
import httpx

from app.agent.gemini import GEMINI_BASE_URL, GeminiClient
from app.agent.llm import LLMClient, LLMRateLimitError
from app.agent.orchestrator import run_agent
from app.agent.prompts import load_prompt
from app.config import Settings
from app.db.places import PostgisPlaceFinder
from app.enrichment.weather import OpenMeteoClient
from app.evaluation.faithfulness import check_reply, last_plan_result, reply_check
from app.main import GEOCODER_USER_AGENT
from app.models.llm import LLMResponse, Message, ToolDefinition
from app.retrieval.embedding_cache import CachingEmbedder
from app.retrieval.embeddings import GeminiEmbedder
from app.retrieval.place_search import PostgisPlaceSearch
from app.routing.geocoder import NominatimGeocoder
from app.routing.graphhopper import GraphHopperClient
from app.scoring.route_scenic import PostgisScenicDescriber
from app.tools.plan_route import PlanRouteTool
from app.tools.search_places import PlaceRegistry, SearchPlacesTool
from run_evals import (
    DEFAULT_RATE_LIMIT_WAIT_SECONDS,
    MAX_RATE_LIMIT_WAITS,
    MAX_WAIT_SECONDS,
    QuotaExhausted,
    load_cases,
)

EVALS_DIR = Path(__file__).parent
REVIEW_FILE = EVALS_DIR / "faithfulness_review.json"


class WaitingLLM:
    """Waits out short rate limits so a multi-turn request is not lost halfway."""

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    async def generate(
        self, system: str, messages: Sequence[Message], tools: Sequence[ToolDefinition]
    ) -> LLMResponse:
        for _ in range(MAX_RATE_LIMIT_WAITS):
            try:
                return await self._llm.generate(system, messages, tools)
            except LLMRateLimitError as exc:
                wait = exc.retry_after_seconds or DEFAULT_RATE_LIMIT_WAIT_SECONDS
                if wait > MAX_WAIT_SECONDS:
                    raise QuotaExhausted(wait) from exc
                print(f"  rate limited, waiting {wait:.0f}s", flush=True)
                await asyncio.sleep(wait)
        return await self._llm.generate(system, messages, tools)


class Planner:
    """Builds the same tools for each request as /api/plan."""

    def __init__(self, routing, geocoder, pool: asyncpg.Pool, place_search, weather) -> None:
        self._routing = routing
        self._weather = weather
        self._geocoder = geocoder
        self._scenery = PostgisScenicDescriber(pool)
        self._places = PostgisPlaceFinder(pool)
        self._place_search = place_search

    def tools(self) -> list:
        registry = PlaceRegistry()
        return [
            PlanRouteTool(
                self._routing,
                self._geocoder,
                scenery=self._scenery,
                places=self._places,
                known_places=registry,
                weather=self._weather,
            ),
            SearchPlacesTool(self._place_search, self._routing, self._geocoder, registry),
        ]


async def run_case(llm: LLMClient, system: str, planner: Planner, case: dict[str, Any]) -> dict[str, Any]:
    message = case["message"]
    started = time.perf_counter()
    run = await run_agent(llm, system, message, planner.tools(), check=reply_check(message))
    latency_ms = round((time.perf_counter() - started) * 1000)

    plan = last_plan_result(run.tool_results)
    draft = run.draft_reply or run.reply
    planned = plan is not None and plan.get("status") == "ok"
    return {
        "message": message,
        "planned": planned,
        "plan_status": plan.get("status") if plan else None,
        "rewritten": run.draft_reply is not None,
        "draft_reply": draft,
        "reply": run.reply,
        "draft_problems": [p.model_dump(mode="json") for p in check_reply(draft, message, run.tool_results)],
        "final_problems": [p.model_dump(mode="json") for p in check_reply(run.reply, message, run.tool_results)],
        "plan_result": plan if planned else None,
        "llm_calls": run.usage.llm_calls,
        "input_tokens": run.usage.input_tokens,
        "output_tokens": run.usage.output_tokens,
        "latency_ms": latency_ms,
    }


def load_review(version: str) -> dict[str, dict[str, str]]:
    """Hand verdicts on this run's drafts: "real", "false_alarm" or "missed"."""
    if not REVIEW_FILE.exists():
        return {}
    return json.loads(REVIEW_FILE.read_text(encoding="utf-8")).get(version, {})


def summarize(results: dict[str, dict[str, Any]], review: dict[str, dict[str, str]]) -> dict[str, Any]:
    planned = {case_id: r for case_id, r in results.items() if r["planned"]}
    count = len(planned)

    def faithful_share(key: str) -> float | None:
        return sum(1 for r in planned.values() if not r[key]) / count if count else None

    def kinds(key: str) -> dict[str, int]:
        return dict(Counter(p["kind"] for r in planned.values() for p in r[key]))

    # Each flagged draft is reviewed by hand: was the problem real? Errors found by
    # reading the unflagged replies are recorded as "missed".
    flagged = [case_id for case_id, r in planned.items() if r["draft_problems"]]
    reviewed = [case_id for case_id in flagged if case_id in review]
    real = [case_id for case_id in reviewed if review[case_id]["verdict"] == "real"]
    missed = [case_id for case_id, entry in review.items() if entry["verdict"] == "missed"]
    return {
        "requests": len(results),
        "planned": count,
        "draft_faithful": faithful_share("draft_problems"),
        "final_faithful": faithful_share("final_problems"),
        "rewritten": sum(1 for r in planned.values() if r["rewritten"]),
        "draft_problems_by_kind": kinds("draft_problems"),
        "final_problems_by_kind": kinds("final_problems"),
        "flagged_drafts": len(flagged),
        "flags_reviewed": len(reviewed),
        "flags_real": len(real),
        "flags_false_alarm": len(reviewed) - len(real),
        "missed": len(missed),
    }


def build_report(
    version: str, model: str, prompt_sha: str, results: dict[str, dict[str, Any]], total: int
) -> dict[str, Any]:
    latencies = [r["latency_ms"] for r in results.values()]
    return {
        "version": version,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": model,
        "prompt_sha256": prompt_sha,
        "complete": len(results) == total,
        "summary": summarize(results, load_review(version)),
        "latency_ms": {"median": round(statistics.median(latencies)), "max": max(latencies)},
        "tokens": {
            "input": sum(r["input_tokens"] for r in results.values()),
            "output": sum(r["output_tokens"] for r in results.values()),
        },
        "cases": results,
    }


def save_report(output: Path, report: dict[str, Any]) -> None:
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def print_report(report: dict[str, Any], output: Path) -> None:
    s = report["summary"]
    state = "complete" if report["complete"] else "PARTIAL"
    print(f"\nModel: {report['model']} ({state}: {s['requests']} requests, {s['planned']} routes)")
    if s["planned"]:
        print(f"Faithful first drafts: {s['draft_faithful']:.1%}  {s['draft_problems_by_kind']}")
        print(f"Faithful final replies: {s['final_faithful']:.1%}  {s['final_problems_by_kind']}")
        print(f"Rewritten:              {s['rewritten']}")
        print(
            f"Flagged drafts reviewed: {s['flags_reviewed']} of {s['flagged_drafts']}, "
            f"{s['flags_real']} real, {s['flags_false_alarm']} false alarms, "
            f"{s['missed']} missed"
        )
    print(f"Median latency: {report['latency_ms']['median']} ms")
    print(f"Saved to {output}")


async def run(version: str, limit: int | None, delay_seconds: float) -> None:
    settings = Settings()
    system = load_prompt("planner")
    prompt_sha = hashlib.sha256(
        (system + load_prompt("reply_feedback")).encode()
    ).hexdigest()[:12]
    cases = load_cases(limit)
    output = EVALS_DIR / "results" / f"faithfulness-{version}.json"

    results: dict[str, dict[str, Any]] = {}
    if output.exists():
        previous = json.loads(output.read_text(encoding="utf-8"))
        if (previous["model"], previous["prompt_sha256"]) != (settings.gemini_model, prompt_sha):
            raise SystemExit(f"{output.name} used another model or prompt. Use a new --version.")
        results = previous["cases"]
        print(f"Resuming: {len(results)} of {len(cases)} already done", flush=True)

    api_key = settings.gemini_api_key.get_secret_value()
    quota_wait = None
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=5)
    async with (
        httpx.AsyncClient(base_url=GEMINI_BASE_URL, timeout=settings.llm_timeout_seconds) as llm_http,
        httpx.AsyncClient(
            base_url=settings.graphhopper_url, timeout=settings.graphhopper_timeout_seconds
        ) as routing_http,
        httpx.AsyncClient(
            base_url=settings.geocoder_url,
            timeout=settings.geocoder_timeout_seconds,
            headers={"User-Agent": GEOCODER_USER_AGENT},
        ) as geocoder_http,
        httpx.AsyncClient(
            base_url=settings.weather_url, timeout=settings.weather_timeout_seconds
        ) as weather_http,
    ):
        llm = WaitingLLM(GeminiClient(llm_http, api_key=api_key, model=settings.gemini_model))
        routing = GraphHopperClient(routing_http, profile=settings.graphhopper_profile)
        search = PostgisPlaceSearch(pool, CachingEmbedder(GeminiEmbedder(llm_http, api_key), pool))
        planner = Planner(
            routing, NominatimGeocoder(geocoder_http), pool, search, OpenMeteoClient(weather_http)
        )

        for index, case in enumerate(cases, 1):
            if case["id"] in results:
                continue
            try:
                result = await run_case(llm, system, planner, case)
            except QuotaExhausted as exc:
                quota_wait = exc.wait_seconds
                break
            results[case["id"]] = result
            if not result["planned"]:
                status = f"no route ({result['plan_status']})"
            elif result["final_problems"]:
                status = "UNFAITHFUL"
            else:
                status = "rewritten, ok" if result["rewritten"] else "ok"
            print(f"[{index}/{len(cases)}] {status:<18} {case['id']}", flush=True)
            for problem in result["draft_problems"]:
                print(f"    draft: {problem['kind']}: {problem['detail']}", flush=True)
            ordered = {c["id"]: results[c["id"]] for c in cases if c["id"] in results}
            save_report(output, build_report(version, settings.gemini_model, prompt_sha, ordered, len(cases)))
            await asyncio.sleep(delay_seconds)
    await pool.close()

    if quota_wait is not None:
        print(
            f"\nDaily quota reached after {len(results)} of {len(cases)} requests. "
            f"It resets in about {quota_wait / 3600:.1f} hours. Run the same command then.",
            flush=True,
        )
    if results:
        ordered = {c["id"]: results[c["id"]] for c in cases if c["id"] in results}
        report = build_report(version, settings.gemini_model, prompt_sha, ordered, len(cases))
        save_report(output, report)
        print_report(report, output)


def resummarize(version: str) -> None:
    """Refresh a finished run's summary after adding hand verdicts. Calls nothing."""
    output = EVALS_DIR / "results" / f"faithfulness-{version}.json"
    report = json.loads(output.read_text(encoding="utf-8"))
    report["summary"] = summarize(report["cases"], load_review(version))
    save_report(output, report)
    print_report(report, output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version", required=True, help="Name of the results file.")
    parser.add_argument("--limit", type=int, help="Only run the first N requests.")
    parser.add_argument("--delay", type=float, default=5.0, help="Seconds between requests.")
    parser.add_argument(
        "--resummarize", action="store_true", help="Only refresh the summary with hand verdicts."
    )
    args = parser.parse_args()
    if args.resummarize:
        resummarize(args.version)
    else:
        asyncio.run(run(args.version, args.limit, args.delay))


if __name__ == "__main__":
    main()

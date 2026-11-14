"""Retrieval eval: does search_places find the places a rider means?

Each labelled query in retrieval_queries.jsonl names the places that count
as a correct answer. The same queries run through three search modes:

- hybrid:   0.85 * embedding similarity + 0.15 * name similarity (what the app uses)
- semantic: embeddings only
- name:     trigram name similarity only

and each is scored by recall@1, recall@5 and mean reciprocal rank (MRR).
Needs the pipeline to have run and GEMINI_API_KEY for query embeddings. No LLM.

    docker compose exec backend python /evals/run_retrieval.py --version v1
"""

import argparse
import asyncio
import json
import os
import statistics
from datetime import datetime, timezone
from pathlib import Path

import asyncpg
import httpx

from app.agent.gemini import GEMINI_BASE_URL
from app.models.route import Coordinate
from app.retrieval.embedding_cache import CachingEmbedder
from app.retrieval.embeddings import EmbeddingError, GeminiEmbedder
from app.retrieval.place_search import PostgisPlaceSearch, SearchMode

EVALS_DIR = Path(__file__).parent
TOP_K = 5


def load_queries() -> list[dict]:
    with open(EVALS_DIR / "retrieval_queries.jsonl", encoding="utf-8") as file:
        return [json.loads(line) for line in file if line.strip()]


def rank_of_first_relevant(found: list[str], relevant: list[str]) -> int | None:
    wanted = {name.casefold() for name in relevant}
    for rank, name in enumerate(found, start=1):
        if name.casefold() in wanted:
            return rank
    return None


def summarize(ranks: list[int | None]) -> dict[str, float]:
    return {
        "recall_at_1": round(sum(rank == 1 for rank in ranks) / len(ranks), 3),
        f"recall_at_{TOP_K}": round(sum(rank is not None for rank in ranks) / len(ranks), 3),
        "mrr": round(statistics.mean(1 / rank if rank else 0.0 for rank in ranks), 3),
    }


async def run(version: str) -> None:
    queries = load_queries()
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=2)
    async with httpx.AsyncClient(base_url=GEMINI_BASE_URL, timeout=60) as http:
        # Query embeddings are cached in Postgres, so re-runs cost no API calls.
        embedder = CachingEmbedder(GeminiEmbedder(http, os.environ["GEMINI_API_KEY"]), pool)
        report: dict = {
            "version": version,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "embedding_model": embedder.model,
            "queries": len(queries),
            "modes": {},
            "cases": [],
        }
        ranks: dict[str, list[int | None]] = {mode.value: [] for mode in SearchMode}
        for query in queries:
            case = {"id": query["id"], "query": query["query"], "relevant": query["relevant"]}
            centre = Coordinate(**query["near"])
            for mode in SearchMode:
                search = PostgisPlaceSearch(pool, embedder, mode=mode, fallback_to_names=False)
                found = await search.search(
                    query["query"],
                    centre,
                    within_m=query.get("within_km", 10) * 1000,
                    category=query.get("kind"),
                    limit=TOP_K,
                )
                names = [place.name for place in found]
                rank = rank_of_first_relevant(names, query["relevant"])
                ranks[mode.value].append(rank)
                case[mode.value] = {"rank": rank, "found": names}
            report["cases"].append(case)
            print(
                f"{query['id']:<14} "
                + "  ".join(f"{mode.value}={case[mode.value]['rank'] or '-'}" for mode in SearchMode)
            )
    await pool.close()

    report["modes"] = {mode: summarize(mode_ranks) for mode, mode_ranks in ranks.items()}
    output = EVALS_DIR / "results" / f"retrieval-{version}.json"
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\n{len(queries)} queries, top {TOP_K}")
    print(f"{'mode':<10} {'recall@1':>9} {'recall@5':>9} {'MRR':>6}")
    for mode, scores in report["modes"].items():
        print(
            f"{mode:<10} {scores['recall_at_1']:>9.1%} {scores[f'recall_at_{TOP_K}']:>9.1%} "
            f"{scores['mrr']:>6.3f}"
        )
    print(f"Saved to {output}")


async def sweep(weights: list[float]) -> None:
    """recall and MRR for several hybrid weightings. Uses cached query embeddings only."""
    queries = load_queries()
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=2)
    async with httpx.AsyncClient(base_url=GEMINI_BASE_URL, timeout=60) as http:
        embedder = CachingEmbedder(GeminiEmbedder(http, os.environ["GEMINI_API_KEY"]), pool)
        print(f"{'semantic weight':<16} {'recall@1':>9} {'recall@5':>9} {'MRR':>6}")
        for weight in weights:
            search = PostgisPlaceSearch(
                pool, embedder, fallback_to_names=False, semantic_weight=weight
            )
            ranks = []
            for query in queries:
                found = await search.search(
                    query["query"],
                    Coordinate(**query["near"]),
                    within_m=query.get("within_km", 10) * 1000,
                    category=query.get("kind"),
                    limit=TOP_K,
                )
                ranks.append(rank_of_first_relevant([p.name for p in found], query["relevant"]))
            scores = summarize(ranks)
            print(
                f"{weight:<16.2f} {scores['recall_at_1']:>9.1%} "
                f"{scores[f'recall_at_{TOP_K}']:>9.1%} {scores['mrr']:>6.3f}"
            )
    await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version", help="name for the results file")
    parser.add_argument(
        "--sweep",
        type=float,
        nargs="+",
        metavar="WEIGHT",
        help="instead of a full run, compare hybrid semantic weights, e.g. --sweep 1 0.9 0.75",
    )
    args = parser.parse_args()
    if not args.sweep and not args.version:
        parser.error("give --version for a full run, or --sweep")
    try:
        asyncio.run(sweep(args.sweep) if args.sweep else run(args.version))
    except EmbeddingError as exc:
        # Never save a report that silently fell back to name matching.
        raise SystemExit(
            f"Stopped: could not embed a query ({exc}). Nothing was saved. Queries embedded "
            "so far are cached, so re-running later only needs the rest."
        ) from exc


if __name__ == "__main__":
    main()

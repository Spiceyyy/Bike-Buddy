"""Route-quality eval: the scenic profile against the plain bike profile.

Routes the same trips with both GraphHopper profiles and scores each route
with the scored road segments in PostGIS. Needs no LLM and no API key, but
does need GraphHopper running and the scoring pipeline to have run.

    docker compose exec backend python /evals/run_route_quality.py --version v1
"""

import argparse
import asyncio
import json
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import asyncpg
import httpx

from app.config import Settings
from app.db.segments import route_scenic_score
from app.models.route import Coordinate, RouteFeature
from app.routing.graphhopper import GraphHopperClient, RoutingError

EVALS_DIR = Path(__file__).parent
BASELINE = "bike"
CANDIDATE = "scenic"


@dataclass(frozen=True)
class Trip:
    name: str
    start: tuple[float, float]  # lat, lon
    end: tuple[float, float] | None = None
    loop_km: float | None = None
    seed: int = 0


# Spread across Montreal: downtown, the Plateau, the canal, the river, the
# north shore, and across the bridges.
TRIPS = [
    Trip("Old Port to Lachine", (45.5040, -73.5530), end=(45.4350, -73.6750)),
    Trip("McGill to Parc Jean-Drapeau", (45.5048, -73.5772), end=(45.5150, -73.5330)),
    Trip("Parc La Fontaine to Parc Jarry", (45.5270, -73.5690), end=(45.5340, -73.6280)),
    Trip("Verdun to Old Port", (45.4590, -73.5700), end=(45.5040, -73.5530)),
    Trip("Beaver Lake to Outremont", (45.4980, -73.5960), end=(45.5170, -73.6080)),
    Trip("Rosemont to Olympic Stadium", (45.5440, -73.5770), end=(45.5580, -73.5520)),
    Trip("NDG to downtown", (45.4700, -73.6150), end=(45.5000, -73.5700)),
    Trip("Villeray to Ile-de-la-Visitation", (45.5450, -73.6200), end=(45.5700, -73.6470)),
    Trip("Cote-des-Neiges to Saint-Laurent", (45.4950, -73.6230), end=(45.5100, -73.6800)),
    Trip("Longueuil to Old Port", (45.5310, -73.5180), end=(45.5040, -73.5530)),
    *(
        Trip(f"{name} {km:g} km loop (seed {seed})", start, loop_km=km, seed=seed)
        for name, start, km in (
            ("Old Port", (45.5040, -73.5530), 20),
            ("Parc La Fontaine", (45.5270, -73.5690), 15),
            ("Lachine", (45.4350, -73.6750), 25),
            ("Verdun", (45.4590, -73.5700), 20),
            ("Ahuntsic", (45.5550, -73.6600), 30),
        )
        for seed in (0, 1)
    ),
]


@dataclass
class ProfileResult:
    distance_km: float
    duration_min: float
    scenic_score: float | None
    matched_share: float


async def route_trip(client: GraphHopperClient, trip: Trip) -> RouteFeature:
    start = Coordinate(lat=trip.start[0], lon=trip.start[1])
    if trip.loop_km is not None:
        return await client.round_trip(start, trip.loop_km * 1000, seed=trip.seed)
    return await client.route(start, Coordinate(lat=trip.end[0], lon=trip.end[1]))


async def evaluate(trip: Trip, client: GraphHopperClient, db: asyncpg.Connection) -> ProfileResult:
    route = await route_trip(client, trip)
    scored = await route_scenic_score(db, route)
    return ProfileResult(
        distance_km=round(route.properties.distance_m / 1000, 2),
        duration_min=round(route.properties.duration_s / 60, 1),
        scenic_score=round(scored.score, 3) if scored.score is not None else None,
        matched_share=round(scored.matched_share, 3),
    )


def summarise(cases: list[dict]) -> dict:
    def paired(kind: str) -> list[dict]:
        return [
            case
            for case in cases
            if case["kind"] == kind
            and all(case[p] and case[p]["scenic_score"] is not None for p in (BASELINE, CANDIDATE))
        ]

    summary: dict = {}
    for kind in ("point_to_point", "loop"):
        rows = paired(kind)
        if not rows:
            continue
        base = [row[BASELINE] for row in rows]
        cand = [row[CANDIDATE] for row in rows]
        gains = [c["scenic_score"] - b["scenic_score"] for b, c in zip(base, cand)]
        detours = [c["distance_km"] / b["distance_km"] - 1 for b, c in zip(base, cand)]
        summary[kind] = {
            "trips": len(rows),
            f"median_score_{BASELINE}": round(statistics.median(r["scenic_score"] for r in base), 3),
            f"median_score_{CANDIDATE}": round(statistics.median(r["scenic_score"] for r in cand), 3),
            "median_score_gain": round(statistics.median(gains), 3),
            "trips_more_scenic": sum(gain > 0.005 for gain in gains),
            "trips_less_scenic": sum(gain < -0.005 for gain in gains),
            "median_extra_distance_pct": round(100 * statistics.median(detours), 1),
            "max_extra_distance_pct": round(100 * max(detours), 1),
            "median_matched_share": round(
                statistics.median(r["matched_share"] for r in base + cand), 3
            ),
        }
    return summary


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version", required=True, help="label for the results file")
    args = parser.parse_args()

    settings = Settings()
    cases: list[dict] = []
    async with httpx.AsyncClient(
        base_url=settings.graphhopper_url, timeout=settings.graphhopper_timeout_seconds
    ) as http:
        clients = {
            profile: GraphHopperClient(http, profile=profile) for profile in (BASELINE, CANDIDATE)
        }
        db = await asyncpg.connect(settings.database_url)
        try:
            for trip in TRIPS:
                case = {
                    "name": trip.name,
                    "kind": "loop" if trip.loop_km is not None else "point_to_point",
                }
                for profile, client in clients.items():
                    try:
                        case[profile] = asdict(await evaluate(trip, client, db))
                    except RoutingError as exc:
                        case[profile] = None
                        case[f"{profile}_error"] = type(exc).__name__
                cases.append(case)
                print(_format_case(case))
        finally:
            await db.close()

    summary = summarise(cases)
    print(json.dumps(summary, indent=2))

    output = EVALS_DIR / "results" / f"route-quality-{args.version}.json"
    output.write_text(
        json.dumps(
            {
                "version": args.version,
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "baseline": BASELINE,
                "candidate": CANDIDATE,
                "summary": summary,
                "cases": cases,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Saved {output}")


def _format_case(case: dict) -> str:
    def cell(result: dict | None) -> str:
        if result is None:
            return "failed".ljust(22)
        return f"{result['scenic_score']:.2f} / {result['distance_km']:5.1f} km".ljust(22)

    return f"{case['name'][:42]:42} {BASELINE}: {cell(case[BASELINE])} {CANDIDATE}: {cell(case[CANDIDATE])}"


if __name__ == "__main__":
    asyncio.run(main())

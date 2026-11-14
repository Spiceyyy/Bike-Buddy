"""Constraint eval: do planned routes meet what the rider asked for?

Plans each fixed RouteSpec in constraint_cases.json with the real routing
engine, scenery scores and café index, exactly as the app does after the LLM
has parsed a request, and checks the best option against the request:

- distance:       loops within 10% of the requested distance
- climb limit:    climbing at or under max_climb_m
- climbing goal:  "seek" climbs at least as much as the same ride with no goal,
                  "avoid" no more
- stop:           a café was found and routed through
- via:            every requested place lies within 75 m of the route (plus the
                  area's radius for large places, so reaching a park's edge counts)

No LLM and no geocoder: place names resolve to fixed coordinates in the cases
file, so runs are repeatable. Needs GraphHopper and the pipeline's database.

    docker compose exec backend python /evals/run_constraints.py --version v1
"""

import argparse
import asyncio
import json
import os
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

import asyncpg
import httpx

from app.config import Settings
from app.db.places import PostgisPlaceFinder
from app.evaluation.constraints import (
    ConstraintResult,
    climb_limit_ok,
    climbing_goal_ok,
    distance_ok,
    stop_ok,
    summarize,
    via_ok,
)
from app.models.route import BoundingBox, Coordinate, GeocodedPlace, RouteFeature
from app.models.spec import Climbing, RouteSpec
from app.routing.graphhopper import GraphHopperClient
from app.scoring.route_scenic import PostgisScenicDescriber
from app.tools.plan_route import PlanRouteTool

EVALS_DIR = Path(__file__).parent


class FixedGeocoder:
    """Resolves the cases' place names to fixed coordinates, so no network is involved."""

    def __init__(self, places: dict[str, dict[str, float]]) -> None:
        self._places = {
            name.casefold(): Coordinate(lat=c["lat"], lon=c["lon"]) for name, c in places.items()
        }

    async def search(self, query: str, bounds: BoundingBox) -> GeocodedPlace | None:
        coordinate = self._places.get(query.casefold())
        return GeocodedPlace(name=query, coordinate=coordinate) if coordinate else None


async def plan(tool: PlanRouteTool, spec: RouteSpec) -> tuple[dict, float]:
    started = time.perf_counter()
    result = await tool.run(spec)
    return result, (time.perf_counter() - started) * 1000


async def run(version: str) -> None:
    data = json.loads((EVALS_DIR / "constraint_cases.json").read_text(encoding="utf-8"))
    geocoder = FixedGeocoder(data["places"])
    # Large areas: the route counts as passing them when it reaches their edge.
    radii = {name.casefold(): c.get("radius_m", 0.0) for name, c in data["places"].items()}
    settings = Settings()
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=5)
    results: list[ConstraintResult] = []
    cases_out: list[dict] = []
    latencies: list[float] = []

    async with httpx.AsyncClient(
        base_url=settings.graphhopper_url, timeout=settings.graphhopper_timeout_seconds
    ) as http:
        routing = GraphHopperClient(http, profile=settings.graphhopper_profile)

        def new_tool() -> PlanRouteTool:
            return PlanRouteTool(
                routing,
                geocoder,
                scenery=PostgisScenicDescriber(pool),
                places=PostgisPlaceFinder(pool),
            )

        for case in data["cases"]:
            spec = RouteSpec.model_validate(case["spec"])
            tool = new_tool()
            outcome, latency_ms = await plan(tool, spec)
            latencies.append(latency_ms)
            route = tool.route
            if outcome.get("status") != "ok" or route is None:
                result = ConstraintResult(id=case["id"], distance_ok=False)
                cases_out.append({"id": case["id"], "error": outcome})
                results.append(result)
                print(f"{case['id']:<36} ERROR {outcome}")
                continue

            baseline: RouteFeature | None = None
            if spec.climbing is not Climbing.ANY:
                baseline_tool = new_tool()
                await baseline_tool.run(spec.model_copy(update={"climbing": Climbing.ANY}))
                baseline = baseline_tool.route

            result = ConstraintResult(
                id=case["id"],
                distance_ok=distance_ok(spec, route),
                climb_limit_ok=climb_limit_ok(spec, route),
                climbing_goal_ok=climbing_goal_ok(spec, route, baseline),
                stop_ok=stop_ok(spec, tool.stop),
                via_ok=via_ok(
                    spec,
                    route,
                    [via.coordinate for via in tool.via],
                    [radii.get(via.name.casefold(), 0.0) for via in tool.via],
                ),
            )
            results.append(result)
            cases_out.append(
                {
                    "id": case["id"],
                    "spec": case["spec"],
                    "checks": result.checks,
                    "distance_km": round(route.properties.distance_m / 1000, 2),
                    "climb_m": round(route.properties.ascend_m or 0),
                    "baseline_climb_m": round(baseline.properties.ascend_m or 0) if baseline else None,
                    "stop": tool.stop.name if tool.stop else None,
                    "options": len(tool.options),
                    "latency_ms": round(latency_ms),
                }
            )
            marks = " ".join(f"{name}={'ok' if ok else 'MISS'}" for name, ok in result.checks.items())
            print(f"{case['id']:<36} {route.properties.distance_m / 1000:5.1f} km  {marks}")
    await pool.close()

    summary = summarize(results)
    report = {
        "version": version,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "graphhopper_profile": settings.graphhopper_profile,
        "summary": summary,
        "latency_ms": {
            "median": round(statistics.median(latencies)),
            "max": round(max(latencies)),
        },
        "cases": cases_out,
    }
    output = EVALS_DIR / "results" / f"constraints-{version}.json"
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\n{len(results)} requests")
    print(f"{'constraint':<18} {'requests':>8} {'met':>7}")
    for name, scores in summary.items():
        print(f"{name:<18} {scores['cases']:>8} {scores['met']:>7.1%}")
    print(f"Median planning time {report['latency_ms']['median']} ms, max {report['latency_ms']['max']} ms")
    print(f"Saved to {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version", required=True, help="name for the results file")
    asyncio.run(run(parser.parse_args().version))


if __name__ == "__main__":
    main()

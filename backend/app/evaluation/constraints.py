"""Checks whether a planned route meets what the rider asked for. No LLM, no I/O.

Used by evals/run_constraints.py, which plans fixed RouteSpecs with the real
routing engine and database. Each check returns True, False, or None when the
request did not ask for that constraint.
"""

from collections.abc import Sequence

from pydantic import BaseModel

from app.models.route import Coordinate, RouteFeature, RouteStop
from app.models.spec import Climbing, RouteSpec, RouteType
from app.routing.geometry import distance_m

DISTANCE_TOLERANCE = 0.10
# GraphHopper snaps a via point to the nearest road, so the route passes near it, not on it.
VIA_WITHIN_M = 75.0


class ConstraintResult(BaseModel):
    id: str
    distance_ok: bool | None = None
    climb_limit_ok: bool | None = None
    climbing_goal_ok: bool | None = None
    stop_ok: bool | None = None
    via_ok: bool | None = None

    @property
    def checks(self) -> dict[str, bool]:
        """Only the constraints this request asked for."""
        return {
            name: value
            for name, value in self.model_dump(exclude={"id"}).items()
            if value is not None
        }

    @property
    def all_ok(self) -> bool:
        return all(self.checks.values())


def distance_ok(spec: RouteSpec, route: RouteFeature) -> bool | None:
    if spec.route_type is not RouteType.LOOP or spec.target_distance_km is None:
        return None
    target_m = spec.target_distance_km * 1000
    return abs(route.properties.distance_m - target_m) / target_m <= DISTANCE_TOLERANCE


def climb_limit_ok(spec: RouteSpec, route: RouteFeature) -> bool | None:
    if spec.max_climb_m is None or route.properties.ascend_m is None:
        return None
    return route.properties.ascend_m <= spec.max_climb_m


def climbing_goal_ok(
    spec: RouteSpec, route: RouteFeature, baseline: RouteFeature | None
) -> bool | None:
    """Seeking climbs should climb at least as much as the same ride with no goal; avoiding, no more."""
    if spec.climbing is Climbing.ANY or baseline is None:
        return None
    climb = route.properties.ascend_m or 0.0
    baseline_climb = baseline.properties.ascend_m or 0.0
    return climb >= baseline_climb if spec.climbing is Climbing.SEEK else climb <= baseline_climb


def stop_ok(spec: RouteSpec, stop: RouteStop | None) -> bool | None:
    if spec.stop is None:
        return None
    return stop is not None


def via_ok(
    spec: RouteSpec,
    route: RouteFeature,
    vias: Sequence[Coordinate],
    radii_m: Sequence[float] | None = None,
) -> bool | None:
    """Every requested place lies within VIA_WITHIN_M of the route.

    A large area (a 79 ha garden) is located by a point inside it, which can be
    hundreds of metres from any rideable road. For those, `radii_m` gives the
    area's approximate radius, so reaching its edge counts as passing it.
    """
    if not spec.via_places:
        return None
    if len(vias) != len(spec.via_places):
        return False
    radii = radii_m or [0.0] * len(vias)
    return all(
        _nearest_m(route, via) <= VIA_WITHIN_M + radius for via, radius in zip(vias, radii)
    )


def summarize(results: Sequence[ConstraintResult]) -> dict[str, dict[str, float | int]]:
    """For each constraint: how many requests asked for it and the share that met it."""
    summary: dict[str, dict[str, float | int]] = {}
    for name in ConstraintResult.model_fields:
        if name == "id":
            continue
        values = [getattr(result, name) for result in results if getattr(result, name) is not None]
        if values:
            summary[name] = {"cases": len(values), "met": round(sum(values) / len(values), 3)}
    summary["all"] = {
        "cases": len(results),
        "met": round(sum(result.all_ok for result in results) / len(results), 3) if results else 0,
    }
    return summary


def _nearest_m(route: RouteFeature, point: Coordinate) -> float:
    position = (point.lon, point.lat)
    return min(distance_m(position, vertex) for vertex in route.geometry.coordinates)

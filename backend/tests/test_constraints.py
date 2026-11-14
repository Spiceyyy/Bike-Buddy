"""Constraint checks for the constraint eval. Pure functions, no routing engine."""

import pytest

from app.evaluation import constraints
from app.evaluation.constraints import ConstraintResult, summarize
from app.models.route import Coordinate, LineString, RouteFeature, RouteProperties, RouteStop
from app.models.spec import Climbing, RouteSpec, RouteType, Stop

LOOP = RouteSpec(route_type=RouteType.LOOP, start_place="Old Port", target_distance_km=20)


def route(distance_m: float = 20_000, ascend_m: float | None = 150) -> RouteFeature:
    return RouteFeature(
        geometry=LineString(coordinates=[(-73.60, 45.50), (-73.58, 45.50), (-73.58, 45.52)]),
        properties=RouteProperties(
            distance_m=distance_m, duration_s=3_600, profile="scenic", ascend_m=ascend_m
        ),
    )


@pytest.mark.parametrize(("distance", "ok"), [(20_000, True), (21_900, True), (22_500, False)])
def test_loop_distance_within_ten_percent(distance: float, ok: bool) -> None:
    assert constraints.distance_ok(LOOP, route(distance)) is ok


def test_point_to_point_distance_is_not_checked() -> None:
    spec = RouteSpec(route_type=RouteType.POINT_TO_POINT, start_place="A", end_place="B")

    assert constraints.distance_ok(spec, route()) is None


def test_climbing_limit_implies_avoiding_climbs() -> None:
    spec = RouteSpec(
        route_type=RouteType.LOOP, start_place="Verdun", target_distance_km=25, max_climb_m=150
    )
    seeking = RouteSpec(
        route_type=RouteType.LOOP,
        start_place="Verdun",
        target_distance_km=25,
        max_climb_m=150,
        climbing=Climbing.SEEK,
    )

    assert spec.climbing is Climbing.AVOID
    assert seeking.climbing is Climbing.SEEK  # an explicit goal is left alone


def test_climb_limit() -> None:
    spec = LOOP.model_copy(update={"max_climb_m": 200})

    assert constraints.climb_limit_ok(spec, route(ascend_m=180)) is True
    assert constraints.climb_limit_ok(spec, route(ascend_m=220)) is False
    assert constraints.climb_limit_ok(LOOP, route()) is None


def test_climbing_goal_compares_with_the_same_ride_without_a_goal() -> None:
    seek = LOOP.model_copy(update={"climbing": Climbing.SEEK})
    avoid = LOOP.model_copy(update={"climbing": Climbing.AVOID})
    baseline = route(ascend_m=200)

    assert constraints.climbing_goal_ok(seek, route(ascend_m=300), baseline) is True
    assert constraints.climbing_goal_ok(seek, route(ascend_m=150), baseline) is False
    assert constraints.climbing_goal_ok(avoid, route(ascend_m=150), baseline) is True
    assert constraints.climbing_goal_ok(LOOP, route(), baseline) is None


def test_stop_found() -> None:
    spec = LOOP.model_copy(update={"stop": Stop.CAFE})
    stop = RouteStop(
        category="cafe", name="X", coordinate=Coordinate(lat=45.5, lon=-73.58), distance_along_m=1
    )

    assert constraints.stop_ok(spec, stop) is True
    assert constraints.stop_ok(spec, None) is False
    assert constraints.stop_ok(LOOP, None) is None


def test_via_place_must_lie_near_the_route() -> None:
    spec = LOOP.model_copy(update={"via_places": ["Somewhere"]})
    on_route = Coordinate(lat=45.5003, lon=-73.58)  # about 33 m from a vertex
    off_route = Coordinate(lat=45.51, lon=-73.62)

    assert constraints.via_ok(spec, route(), [on_route]) is True
    assert constraints.via_ok(spec, route(), [off_route]) is False
    assert constraints.via_ok(spec, route(), []) is False
    assert constraints.via_ok(LOOP, route(), []) is None


def test_large_area_counts_as_passed_when_the_route_reaches_its_edge() -> None:
    spec = LOOP.model_copy(update={"via_places": ["Big garden"]})
    centre = Coordinate(lat=45.5040, lon=-73.58)  # about 440 m from the route

    assert constraints.via_ok(spec, route(), [centre]) is False
    assert constraints.via_ok(spec, route(), [centre], radii_m=[500]) is True


def test_summary_counts_only_requests_that_asked_for_each_constraint() -> None:
    results = [
        ConstraintResult(id="a", distance_ok=True, stop_ok=False),
        ConstraintResult(id="b", distance_ok=False),
        ConstraintResult(id="c", distance_ok=True),
    ]

    summary = summarize(results)

    assert summary["distance_ok"] == {"cases": 3, "met": 0.667}
    assert summary["stop_ok"] == {"cases": 1, "met": 0.0}
    assert "via_ok" not in summary
    assert summary["all"] == {"cases": 3, "met": 0.333}

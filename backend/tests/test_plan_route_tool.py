import pytest

from app.models.route import Coordinate
from app.models.spec import RouteSpec, RouteType, Surface, Vibe
from app.routing.geocoder import GeocodingError
from app.routing.graphhopper import (
    NoRouteFoundError,
    PointNotRoutableError,
    RoutingEngineError,
)
from app.tools.plan_route import MAP_POINT_NAME, PlanRouteTool
from tests.fakes import FakeGeocoder, FakeRouting

MAP_PIN = Coordinate(lat=45.6, lon=-73.6)


def loop_spec(**overrides) -> RouteSpec:
    fields = {"route_type": RouteType.LOOP, "start_place": "Old Port", "target_distance_km": 50}
    return RouteSpec(**{**fields, **overrides})


async def test_loop_keeps_the_candidate_closest_to_the_target() -> None:
    routing = FakeRouting([40_000, 49_000, 70_000])
    tool = PlanRouteTool(routing, FakeGeocoder())

    result = await tool.run(loop_spec())

    assert result["status"] == "ok"
    assert result["distance_km"] == 49.0
    assert result["distance_on_target"] is True
    assert sorted(routing.round_trip_requests) == [(50_000, seed) for seed in range(8)]
    assert tool.route is not None


async def test_loop_reports_when_no_candidate_is_near_the_target() -> None:
    tool = PlanRouteTool(FakeRouting([30_000, 38_000]), FakeGeocoder())

    result = await tool.run(loop_spec())

    assert result["distance_km"] == 38.0
    assert result["requested_distance_km"] == 50
    assert result["distance_on_target"] is False


async def test_loop_survives_some_candidates_failing() -> None:
    routing = FakeRouting([50_000], failing_seeds=[0, 1, 2, 3, 4, 5, 6])

    result = await PlanRouteTool(routing, FakeGeocoder()).run(loop_spec())

    assert result["status"] == "ok"


async def test_loop_reports_error_when_every_candidate_fails() -> None:
    routing = FakeRouting([50_000], failing_seeds=range(8))
    tool = PlanRouteTool(routing, FakeGeocoder())

    result = await tool.run(loop_spec())

    assert result["status"] == "error"
    assert tool.route is None


async def test_point_to_point_geocodes_both_ends() -> None:
    routing = FakeRouting()
    geocoder = FakeGeocoder()
    spec = RouteSpec(
        route_type=RouteType.POINT_TO_POINT, start_place="Old Port", end_place="Lachine"
    )

    result = await PlanRouteTool(routing, geocoder).run(spec)

    assert result["status"] == "ok"
    assert (result["start"], result["end"]) == ("Old Port", "Lachine")
    assert routing.route_requests == [(geocoder.known["Old Port"], geocoder.known["Lachine"])]


async def test_map_point_is_used_when_no_start_was_named() -> None:
    routing = FakeRouting([50_000])
    tool = PlanRouteTool(routing, FakeGeocoder(), map_start=MAP_PIN)

    result = await tool.run(loop_spec(start_place=None))

    assert result["status"] == "ok"
    assert result["start"] == MAP_POINT_NAME


async def test_named_start_takes_priority_over_map_point() -> None:
    tool = PlanRouteTool(FakeRouting([50_000]), FakeGeocoder(), map_start=MAP_PIN)

    result = await tool.run(loop_spec())

    assert result["start"] == "Old Port"


@pytest.mark.parametrize(
    ("spec", "missing"),
    [
        (RouteSpec(route_type=RouteType.LOOP), ["start", "distance"]),
        (RouteSpec(route_type=RouteType.LOOP, start_place="Old Port"), ["distance"]),
        (
            RouteSpec(route_type=RouteType.POINT_TO_POINT, start_place="Old Port"),
            ["destination"],
        ),
    ],
)
async def test_reports_missing_details_without_routing(spec: RouteSpec, missing: list[str]) -> None:
    routing = FakeRouting()
    tool = PlanRouteTool(routing, FakeGeocoder())

    result = await tool.run(spec)

    assert result == {"status": "needs_input", "missing": missing}
    assert routing.round_trip_requests == []
    assert tool.route is None
    assert tool.spec == spec


async def test_unknown_place_is_reported_with_its_name() -> None:
    tool = PlanRouteTool(FakeRouting(), FakeGeocoder())

    result = await tool.run(loop_spec(start_place="Atlantis"))

    assert result["status"] == "error"
    assert "Atlantis" in result["reason"]


@pytest.mark.parametrize(
    "error",
    [PointNotRoutableError(0), NoRouteFoundError("none"), RoutingEngineError("down")],
)
async def test_routing_failures_become_error_results(error: Exception) -> None:
    tool = PlanRouteTool(FakeRouting(error=error), FakeGeocoder())

    result = await tool.run(loop_spec())

    assert result["status"] == "error"
    assert tool.route is None


async def test_geocoder_outage_becomes_error_result() -> None:
    class BrokenGeocoder(FakeGeocoder):
        async def search(self, query, bounds):
            raise GeocodingError("down")

    result = await PlanRouteTool(FakeRouting(), BrokenGeocoder()).run(loop_spec())

    assert result["reason"] == "place search is unavailable"


async def test_lists_preferences_that_are_not_applied_yet() -> None:
    spec = loop_spec(surface=Surface.PAVED, max_climb_m=300, vibes=[Vibe.WATER])

    result = await PlanRouteTool(FakeRouting([50_000]), FakeGeocoder()).run(spec)

    assert result["not_applied"] == [
        "surface preference",
        "climbing limit",
        "vibe preferences",
    ]


async def test_failed_replan_clears_previous_route() -> None:
    tool = PlanRouteTool(FakeRouting([50_000]), FakeGeocoder())
    await tool.run(loop_spec())

    await tool.run(loop_spec(start_place="Atlantis"))

    assert tool.route is None

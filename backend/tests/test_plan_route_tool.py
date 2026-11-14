import pytest

from app.models.route import Coordinate, NearbyPlace
from app.models.scenic import RouteScenic, ScenicBand, ScenicReason, ScenicStretch
from app.models.spec import Climbing, RouteSpec, RouteType, Stop, Surface, Vibe
from app.routing.geocoder import GeocodingError
from app.routing.graphhopper import (
    NoRouteFoundError,
    PointNotRoutableError,
    RoutingEngineError,
)
from app.routing.climbing import AVOID_CLIMBS, SEEK_CLIMBS
from app.tools.plan_route import MAP_POINT_NAME, PlanRouteTool, stop_points
from tests.fakes import FakeGeocoder, FakePlaces, FakeRouting, FakeScenery, make_feature

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

    # The climbing limit is applied now (loops are ranked by it), so it is not listed.
    assert result["not_applied"] == ["surface preference", "vibe preferences"]


async def test_failed_replan_clears_previous_route() -> None:
    tool = PlanRouteTool(FakeRouting([50_000]), FakeGeocoder())
    await tool.run(loop_spec())

    await tool.run(loop_spec(start_place="Atlantis"))

    assert tool.route is None


async def test_scenery_is_attached_to_the_route_and_summarized_for_the_llm() -> None:
    stretch = ScenicStretch(
        start_m=0, end_m=4000, band=ScenicBand.SCENIC, score=0.8,
        reason=ScenicReason.WATER, name="Canal path", coordinates=[(-73.55, 45.5), (-73.6, 45.45)],
    )
    scenic = RouteScenic(
        score=0.8, matched_share=1.0,
        band_shares={band: 1.0 if band is ScenicBand.SCENIC else 0.0 for band in ScenicBand},
        stretches=[stretch],
    )
    tool = PlanRouteTool(FakeRouting([50_000]), FakeGeocoder(), scenery=FakeScenery(scenic))

    result = await tool.run(loop_spec())

    assert tool.route.properties.scenic == scenic
    assert result["scenery"]["highlights"][0]["name"] == "Canal path"


async def test_missing_scenery_leaves_it_out_of_the_result() -> None:
    tool = PlanRouteTool(FakeRouting([50_000]), FakeGeocoder(), scenery=FakeScenery(None))

    result = await tool.run(loop_spec())

    assert result["status"] == "ok"
    assert "scenery" not in result


CAFE = NearbyPlace(
    name="Café Olimpico", coordinate=Coordinate(lat=45.52, lon=-73.60), off_route_m=20
)


async def test_seeking_climbs_picks_the_hilliest_loop_near_the_distance() -> None:
    # Seed 2 climbs most but is 30% short, so it must not win.
    routing = FakeRouting([50_000, 51_000, 35_000, 49_000], loop_climbs_m=[100, 300, 900, 200])
    tool = PlanRouteTool(routing, FakeGeocoder())

    result = await tool.run(loop_spec(climbing=Climbing.SEEK))

    assert result["climb_m"] == 300
    assert routing.custom_models[0] == SEEK_CLIMBS


async def test_avoiding_climbs_picks_the_flattest_loop() -> None:
    routing = FakeRouting([50_000, 51_000, 49_000], loop_climbs_m=[300, 80, 200])
    tool = PlanRouteTool(routing, FakeGeocoder())

    result = await tool.run(loop_spec(climbing=Climbing.AVOID))

    assert result["climb_m"] == 80
    assert routing.custom_models[0] == AVOID_CLIMBS


async def test_climbing_limit_also_tries_loops_without_the_avoid_model() -> None:
    routing = FakeRouting([50_000])

    await PlanRouteTool(routing, FakeGeocoder()).run(loop_spec(max_climb_m=200))

    assert routing.custom_models.count(AVOID_CLIMBS) == 8
    assert routing.custom_models.count(None) == 8


async def test_climbing_limit_prefers_loops_under_it() -> None:
    routing = FakeRouting([50_000, 52_000], loop_climbs_m=[400, 150])

    result = await PlanRouteTool(routing, FakeGeocoder()).run(loop_spec(max_climb_m=200))

    assert result["climb_m"] == 150
    assert result["climb_on_target"] is True


async def test_no_climbing_goal_sends_no_custom_model() -> None:
    routing = FakeRouting([50_000])

    await PlanRouteTool(routing, FakeGeocoder()).run(loop_spec())

    assert set(routing.custom_models) == {None}


async def test_loop_with_cafe_is_routed_through_it_keeping_the_loop_shape() -> None:
    routing = FakeRouting([50_000])
    places = FakePlaces(CAFE)
    tool = PlanRouteTool(routing, FakeGeocoder(), places=places)

    result = await tool.run(loop_spec(stop=Stop.CAFE))

    start = Coordinate(lat=45.50, lon=-73.55)
    points = routing.through_requests[0]
    # Start, six shape anchors with the cafe slotted in, back to the start.
    assert (points[0], points[-1]) == (start, start)
    assert len(points) == 9
    assert CAFE.coordinate in points
    assert places.searches[0] == ("cafe", 0.3, 0.7, 1)
    assert result["stop"]["name"] == "Café Olimpico"
    assert tool.stop.name == "Café Olimpico"
    assert tool.stop.distance_along_m > 0


async def test_point_to_point_with_cafe_stops_on_the_way() -> None:
    routing = FakeRouting()
    spec = RouteSpec(
        route_type=RouteType.POINT_TO_POINT, start_place="Old Port", end_place="Lachine",
        stop=Stop.CAFE,
    )
    tool = PlanRouteTool(routing, FakeGeocoder(), places=FakePlaces(CAFE))

    result = await tool.run(spec)

    assert CAFE.coordinate in routing.through_requests[0]
    assert result["stop"]["name"] == "Café Olimpico"


async def test_missing_cafe_is_reported_and_the_plain_route_kept() -> None:
    routing = FakeRouting([50_000])
    places = FakePlaces()
    tool = PlanRouteTool(routing, FakeGeocoder(), places=places)

    result = await tool.run(loop_spec(stop=Stop.CAFE))

    assert result["status"] == "ok"
    assert result["stop_found"] is False
    assert routing.through_requests == []
    assert tool.stop is None
    # The six best loops were tried, then each option looked for cafe choices.
    assert [limit for *_, limit in places.searches].count(1) == 6


async def test_cafe_stop_is_not_applied_without_a_place_finder() -> None:
    result = await PlanRouteTool(FakeRouting([50_000]), FakeGeocoder()).run(
        loop_spec(stop=Stop.CAFE)
    )

    assert result["not_applied"] == ["cafe stop"]


@pytest.mark.parametrize("place", ["a cafe", "A café", "un café", "the nearest coffee shop"])
def test_generic_place_is_rejected_with_a_correction(place: str) -> None:
    with pytest.raises(ValueError, match="set stop to cafe"):
        RouteSpec(route_type=RouteType.POINT_TO_POINT, start_place="Outremont", end_place=place)


def test_named_cafe_is_a_valid_place() -> None:
    spec = RouteSpec(
        route_type=RouteType.POINT_TO_POINT, start_place="Outremont", end_place="Café Olimpico"
    )

    assert spec.end_place == "Café Olimpico"


async def test_rerouted_loop_closest_to_the_distance_wins() -> None:
    # Going through the cafe shortens the first loops; the third stays near 50 km.
    routing = FakeRouting([50_000], through_distances_m=[38_000, 41_000, 49_500, 44_000])
    tool = PlanRouteTool(routing, FakeGeocoder(), places=FakePlaces(CAFE))

    result = await tool.run(loop_spec(stop=Stop.CAFE))

    assert len(routing.through_requests) == 6
    assert result["distance_km"] == 49.5
    assert result["distance_on_target"] is True


async def test_loop_returns_distinct_options_each_with_cafe_choices() -> None:
    routing = FakeRouting([50_000, 51_000, 49_000], loop_climbs_m=[100, 400, 20])
    other = NearbyPlace(name="Dreamy", coordinate=Coordinate(lat=45.47, lon=-73.58), off_route_m=90)
    places = FakePlaces(CAFE, other)
    tool = PlanRouteTool(routing, FakeGeocoder(), places=places)

    result = await tool.run(loop_spec())

    # The fake's loops all share one geometry, so they count as the same route.
    assert [option.label for option in tool.options] == ["Best match"]
    assert [c.name for c in tool.options[0].stop_choices] == ["Café Olimpico", "Dreamy"]
    assert tool.options[0].stop is None  # no stop asked for, but cafes are offered
    assert len(tool.options[0].anchors) == 6
    assert result["other_options"] == []


async def test_point_to_point_offers_graphhopper_alternatives() -> None:
    def line(lat: float, distance: float):
        feature = make_feature(distance, coordinates=[(-73.60, lat), (-73.5746, lat)])
        return feature

    routing = FakeRouting(alternatives=[line(45.50, 7_000), line(45.52, 7_400)])
    spec = RouteSpec(route_type=RouteType.POINT_TO_POINT, start_place="Old Port", end_place="Lachine")
    tool = PlanRouteTool(routing, FakeGeocoder())

    result = await tool.run(spec)

    assert [o.label for o in tool.options] == ["Best match", "Alternative"]
    assert result["other_options"] == [{"label": "Alternative", "distance_km": 7.4}]
    assert tool.end == Coordinate(lat=45.43, lon=-73.68)


def test_stop_goes_where_it_adds_the_least_detour() -> None:
    start = Coordinate(lat=45.50, lon=-73.60)
    north = Coordinate(lat=45.53, lon=-73.60)
    east = Coordinate(lat=45.53, lon=-73.55)
    south = Coordinate(lat=45.50, lon=-73.55)
    cafe = Coordinate(lat=45.531, lon=-73.575)  # between the north and east anchors

    points = stop_points(start, None, [north, east, south], cafe)

    assert points == [start, north, cafe, east, south, start]


def test_point_to_point_stop_keeps_the_route_s_anchors() -> None:
    start, end = Coordinate(lat=45.50, lon=-73.60), Coordinate(lat=45.50, lon=-73.50)
    middle = Coordinate(lat=45.52, lon=-73.55)
    cafe = Coordinate(lat=45.51, lon=-73.58)

    assert stop_points(start, end, [middle], cafe) == [start, cafe, middle, end]


def test_ride_back_to_the_start_becomes_a_loop() -> None:
    spec = RouteSpec(
        route_type=RouteType.POINT_TO_POINT,
        start_place="Outremont",
        end_place="outremont ",
        target_distance_km=40,
    )

    assert (spec.route_type, spec.end_place) == (RouteType.LOOP, None)

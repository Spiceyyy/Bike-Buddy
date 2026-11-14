"""search_places and routing through what it found. Search and routing are faked."""

from app.models.route import Coordinate
from app.models.spec import RouteSpec, RouteType
from app.retrieval.place_search import FoundPlace
from app.tools.plan_route import PlanRouteTool
from app.tools.search_places import PlaceKind, PlaceRegistry, SearchPlacesArgs, SearchPlacesTool
from tests.fakes import FakeGeocoder, FakeRouting

LOOKOUT = FoundPlace(
    name="Belvédère Kondiaronk",
    category="viewpoint",
    description="Belvédère Kondiaronk. Viewpoint, a lookout with a view, in Parc du Mont-Royal.",
    coordinate=Coordinate(lat=45.5038, lon=-73.5871),
    distance_m=2_400,
    score=0.61,
)


class FakeSearch:
    def __init__(self, *found: FoundPlace) -> None:
        self.found = list(found)
        self.calls: list[tuple[str, Coordinate, float, str | None]] = []

    async def search(self, query, centre, within_m, category=None, limit=5):
        self.calls.append((query, centre, within_m, category))
        return self.found[:limit]


async def test_search_returns_names_and_descriptions_but_no_coordinates() -> None:
    search = FakeSearch(LOOKOUT)
    registry = PlaceRegistry()
    tool = SearchPlacesTool(search, FakeRouting(), FakeGeocoder(), registry)
    args = SearchPlacesArgs(
        query="lookout", near_place="Old Port", within_km=6, kind=PlaceKind.VIEWPOINT
    )

    result = await tool.run(args)

    assert result["places"] == [
        {
            "name": "Belvédère Kondiaronk",
            "kind": "viewpoint",
            "distance_km": 2.4,
            "about": LOOKOUT.description,
        }
    ]
    assert "45.5038" not in str(result)
    _, centre, within_m, category = search.calls[0]
    assert (centre, within_m, category) == (Coordinate(lat=45.50, lon=-73.55), 6000, "viewpoint")
    assert registry.get("belvédère kondiaronk ").coordinate == LOOKOUT.coordinate


async def test_search_without_a_start_asks_for_one() -> None:
    tool = SearchPlacesTool(FakeSearch(LOOKOUT), FakeRouting(), FakeGeocoder(), PlaceRegistry())

    result = await tool.run(SearchPlacesArgs(query="lookout"))

    assert result == {"status": "needs_input", "missing": ["start"]}


async def test_search_uses_the_map_point_when_no_start_is_named() -> None:
    pin = Coordinate(lat=45.6, lon=-73.6)
    search = FakeSearch()
    tool = SearchPlacesTool(search, FakeRouting(), FakeGeocoder(), PlaceRegistry(), map_start=pin)

    await tool.run(SearchPlacesArgs(query="beach"))

    assert search.calls[0][1] == pin


async def test_loop_goes_through_found_places_without_geocoding_them() -> None:
    registry = PlaceRegistry()
    registry.add([LOOKOUT])
    routing = FakeRouting([20_000])
    tool = PlanRouteTool(routing, FakeGeocoder(), known_places=registry)
    spec = RouteSpec(
        route_type=RouteType.LOOP,
        start_place="Old Port",
        target_distance_km=20,
        via_places=["Belvédère Kondiaronk"],
    )

    result = await tool.run(spec)

    start = Coordinate(lat=45.50, lon=-73.55)
    # Loops of the requested length come first; the lookout is slotted into each.
    assert routing.round_trip_requests
    path = routing.through_requests[0]
    assert (path[0], path[-1]) == (start, start)
    assert LOOKOUT.coordinate in path
    assert len(path) == 9  # start, 6 shape anchors, the lookout, start
    assert result["via"] == ["Belvédère Kondiaronk"]
    assert [via.name for via in tool.via] == ["Belvédère Kondiaronk"]
    assert LOOKOUT.coordinate in tool.options[0].anchors  # a café pick still passes it


async def test_loop_through_a_place_keeps_the_requested_distance() -> None:
    registry = PlaceRegistry()
    registry.add([LOOKOUT])
    # Going through the lookout changes each loop's length; the one nearest 20 km wins.
    routing = FakeRouting([20_000], through_distances_m=[9_000, 26_000, 19_500, 31_000])
    tool = PlanRouteTool(routing, FakeGeocoder(), known_places=registry)
    spec = RouteSpec(
        route_type=RouteType.LOOP,
        start_place="Old Port",
        target_distance_km=20,
        via_places=["Belvédère Kondiaronk"],
    )

    result = await tool.run(spec)

    assert result["distance_km"] == 19.5
    assert result["distance_on_target"] is True


async def test_named_via_place_is_geocoded() -> None:
    routing = FakeRouting()
    spec = RouteSpec(
        route_type=RouteType.POINT_TO_POINT,
        start_place="Old Port",
        end_place="Lachine",
        via_places=["Lachine"],
    )

    result = await PlanRouteTool(routing, FakeGeocoder()).run(spec)

    assert result["via"] == ["Lachine"]
    assert len(routing.through_requests[0]) == 3


async def test_unknown_via_place_is_reported() -> None:
    spec = RouteSpec(
        route_type=RouteType.LOOP,
        start_place="Old Port",
        target_distance_km=20,
        via_places=["Atlantis"],
    )

    result = await PlanRouteTool(FakeRouting([20_000]), FakeGeocoder()).run(spec)

    assert result == {"status": "error", "reason": "could not find 'Atlantis' in the map region"}


def test_generic_via_place_is_rejected() -> None:
    try:
        RouteSpec(
            route_type=RouteType.LOOP,
            start_place="Old Port",
            target_distance_km=20,
            via_places=["a park"],
        )
    except ValueError as exc:
        assert "not a named place" in str(exc)
    else:
        raise AssertionError("expected a validation error")

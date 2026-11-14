"""Test doubles. No test calls a real LLM, geocoder or routing engine."""

from typing import Any, Sequence

from app.models.llm import (
    AssistantMessage,
    LLMResponse,
    Message,
    ToolCall,
    ToolDefinition,
)
from app.models.route import (
    BoundingBox,
    Coordinate,
    GeocodedPlace,
    LineString,
    NearbyPlace,
    RouteFeature,
    RouteProperties,
)
from app.routing.graphhopper import RoutingEngineError

BOUNDS = BoundingBox(min_lon=-74.0, min_lat=45.3, max_lon=-73.3, max_lat=45.7)


def make_feature(
    distance_m: float = 10_000,
    ascend_m: float | None = None,
    coordinates: list[tuple[float, float]] | None = None,
) -> RouteFeature:
    return RouteFeature(
        geometry=LineString(coordinates=coordinates or [(-73.55, 45.50), (-73.60, 45.45)]),
        properties=RouteProperties(
            distance_m=distance_m, duration_s=1800, profile="bike", ascend_m=ascend_m
        ),
    )


def tool_call_response(arguments: dict[str, Any], name: str = "plan_route") -> LLMResponse:
    call = ToolCall(id="call_1", name=name, arguments=arguments)
    return LLMResponse(
        message=AssistantMessage(tool_calls=[call]), input_tokens=100, output_tokens=20
    )


def text_response(text: str) -> LLMResponse:
    return LLMResponse(message=AssistantMessage(text=text), input_tokens=120, output_tokens=30)


class FakeLLM:
    """Returns scripted responses and records every conversation it receives."""

    def __init__(self, *responses: LLMResponse) -> None:
        self._responses = list(responses)
        self.conversations: list[list[Message]] = []

    async def generate(
        self, system: str, messages: Sequence[Message], tools: Sequence[ToolDefinition]
    ) -> LLMResponse:
        self.conversations.append(list(messages))
        return self._responses.pop(0)


class FakeGeocoder:
    def __init__(self, known: dict[str, Coordinate] | None = None) -> None:
        self.known = known if known is not None else {
            "Old Port": Coordinate(lat=45.50, lon=-73.55),
            "Lachine": Coordinate(lat=45.43, lon=-73.68),
        }

    async def search(self, query: str, bounds: BoundingBox) -> GeocodedPlace | None:
        coordinate = self.known.get(query)
        return GeocodedPlace(name=query, coordinate=coordinate) if coordinate else None


class FakeRouting:
    """Returns loops whose distance (and climb) depends on the seed, repeating the scripted lists."""

    def __init__(
        self,
        loop_distances_m: Sequence[float] = (),
        error: Exception | None = None,
        failing_seeds: Sequence[int] = (),
        loop_climbs_m: Sequence[float] = (),
        through_distances_m: Sequence[float] = (13_000,),
        alternatives: Sequence[RouteFeature] | None = None,
    ):
        self._through_distances = list(through_distances_m)
        self._alternatives = alternatives
        self._loop_distances = list(loop_distances_m)
        self._loop_climbs = list(loop_climbs_m)
        self._error = error
        self._failing_seeds = set(failing_seeds)
        self.round_trip_requests: list[tuple[float, int]] = []
        self.route_requests: list[tuple[Coordinate, Coordinate]] = []
        self.through_requests: list[list[Coordinate]] = []
        self.custom_models: list[dict | None] = []

    async def route(
        self, start: Coordinate, end: Coordinate, custom_model: dict | None = None
    ) -> RouteFeature:
        if self._error:
            raise self._error
        self.route_requests.append((start, end))
        self.custom_models.append(custom_model)
        return make_feature(12_000)

    async def alternatives(
        self, start: Coordinate, end: Coordinate, custom_model: dict | None = None
    ) -> list[RouteFeature]:
        if self._error:
            raise self._error
        self.route_requests.append((start, end))
        self.custom_models.append(custom_model)
        return list(self._alternatives) if self._alternatives else [make_feature(12_000)]

    async def route_through(
        self, points: list[Coordinate], custom_model: dict | None = None
    ) -> RouteFeature:
        if self._error:
            raise self._error
        distance = self._through_distances[len(self.through_requests) % len(self._through_distances)]
        self.through_requests.append(points)
        self.custom_models.append(custom_model)
        return make_feature(distance, coordinates=[(p.lon, p.lat) for p in points])

    async def round_trip(
        self,
        start: Coordinate,
        distance_m: float,
        seed: int = 0,
        custom_model: dict | None = None,
    ) -> RouteFeature:
        if self._error:
            raise self._error
        if seed in self._failing_seeds:
            raise RoutingEngineError("seed failed")
        self.round_trip_requests.append((distance_m, seed))
        self.custom_models.append(custom_model)
        climb = self._loop_climbs[seed % len(self._loop_climbs)] if self._loop_climbs else None
        return make_feature(
            self._loop_distances[seed % len(self._loop_distances)], ascend_m=climb
        )

    async def bounds(self) -> BoundingBox:
        return BOUNDS


class FakePlaces:
    """Finds the given places near any route, recording the search windows."""

    def __init__(self, *places: NearbyPlace) -> None:
        self.places = list(places)
        self.searches: list[tuple[str, float, float, int]] = []

    async def near_route(
        self,
        category: str,
        route: RouteFeature,
        from_fraction: float,
        to_fraction: float,
        limit: int = 1,
    ) -> list[NearbyPlace]:
        self.searches.append((category, from_fraction, to_fraction, limit))
        return self.places[:limit]


class FakeScenery:
    """Describes every route with the given scenery, recording what it was asked about."""

    def __init__(self, scenic=None) -> None:
        self.scenic = scenic
        self.described: list[RouteFeature] = []

    async def describe(self, route: RouteFeature):
        self.described.append(route)
        return self.scenic

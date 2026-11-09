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
    RouteFeature,
    RouteProperties,
)
from app.routing.graphhopper import RoutingEngineError

BOUNDS = BoundingBox(min_lon=-74.0, min_lat=45.3, max_lon=-73.3, max_lat=45.7)


def make_feature(distance_m: float = 10_000) -> RouteFeature:
    return RouteFeature(
        geometry=LineString(coordinates=[(-73.55, 45.50), (-73.60, 45.45)]),
        properties=RouteProperties(distance_m=distance_m, duration_s=1800, profile="bike"),
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
    """Returns loops whose distance depends on the seed, repeating the scripted list."""

    def __init__(
        self,
        loop_distances_m: Sequence[float] = (),
        error: Exception | None = None,
        failing_seeds: Sequence[int] = (),
    ):
        self._loop_distances = list(loop_distances_m)
        self._error = error
        self._failing_seeds = set(failing_seeds)
        self.round_trip_requests: list[tuple[float, int]] = []
        self.route_requests: list[tuple[Coordinate, Coordinate]] = []

    async def route(self, start: Coordinate, end: Coordinate) -> RouteFeature:
        if self._error:
            raise self._error
        self.route_requests.append((start, end))
        return make_feature(12_000)

    async def round_trip(
        self, start: Coordinate, distance_m: float, seed: int = 0
    ) -> RouteFeature:
        if self._error:
            raise self._error
        if seed in self._failing_seeds:
            raise RoutingEngineError("seed failed")
        self.round_trip_requests.append((distance_m, seed))
        return make_feature(self._loop_distances[seed % len(self._loop_distances)])

    async def bounds(self) -> BoundingBox:
        return BOUNDS

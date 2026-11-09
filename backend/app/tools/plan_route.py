"""The plan_route tool: turns a RouteSpec into a real route from the routing engine."""

import asyncio
from typing import Any, Protocol

from pydantic import BaseModel

from app.models.route import BoundingBox, Coordinate, GeocodedPlace, RouteFeature
from app.models.spec import RouteSpec, RouteType, Surface
from app.routing.geocoder import GeocodingError
from app.routing.graphhopper import (
    NoRouteFoundError,
    PointNotRoutableError,
    RoutingEngineError,
    RoutingError,
)

NAME = "plan_route"
DESCRIPTION = (
    "Plan a real cycling route from the rider's request. Give place names as the "
    "rider wrote them. The tool finds coordinates and gets the route from the routing engine."
)
MAP_POINT_NAME = "the point chosen on the map"

# GraphHopper's round-trip distance swings widely near rivers and bridges, and
# rescaling the request does not converge. Measured across Montreal, the closest
# of 8 seeds landed within 10% of the target in 22 of 24 start/distance pairs.
LOOP_CANDIDATES = 8
LOOP_DISTANCE_TOLERANCE = 0.10


class Routing(Protocol):
    async def route(self, start: Coordinate, end: Coordinate) -> RouteFeature: ...

    async def round_trip(
        self, start: Coordinate, distance_m: float, seed: int = 0
    ) -> RouteFeature: ...

    async def bounds(self) -> BoundingBox: ...


class Geocoder(Protocol):
    async def search(self, query: str, bounds: BoundingBox) -> GeocodedPlace | None: ...


class _ToolFailure(Exception):
    """Ends the tool early with a result the LLM can explain to the rider."""

    def __init__(self, **result: Any) -> None:
        super().__init__(result.get("reason", "tool failure"))
        self.result = result


class PlanRouteTool:
    """Created per request. Holds the route so it never passes through the LLM."""

    name = NAME
    description = DESCRIPTION
    args_model: type[BaseModel] = RouteSpec

    def __init__(
        self, routing: Routing, geocoder: Geocoder, map_start: Coordinate | None = None
    ) -> None:
        self._routing = routing
        self._geocoder = geocoder
        self._map_start = map_start
        self.spec: RouteSpec | None = None
        self.route: RouteFeature | None = None
        self.start_name: str | None = None
        self.end_name: str | None = None

    @property
    def not_applied(self) -> list[str]:
        return find_not_applied(self.spec) if self.spec else []

    async def run(self, args: RouteSpec) -> dict[str, Any]:
        self.spec = args
        self.route = None
        self.start_name = None
        self.end_name = None
        try:
            return await self._plan(args)
        except _ToolFailure as failure:
            return failure.result
        except PointNotRoutableError as exc:
            label = "start" if exc.point_index == 0 else "destination"
            return {"status": "error", "reason": f"the {label} is too far from any rideable path"}
        except NoRouteFoundError:
            return {"status": "error", "reason": "no cycling route connects these places"}
        except RoutingEngineError:
            return {"status": "error", "reason": "the routing engine is unavailable"}
        except GeocodingError:
            return {"status": "error", "reason": "place search is unavailable"}

    async def _plan(self, spec: RouteSpec) -> dict[str, Any]:
        missing = self._find_missing(spec)
        if missing:
            return {"status": "needs_input", "missing": missing}

        bounds = await self._routing.bounds()
        start = await self._resolve_start(spec, bounds)

        if spec.route_type is RouteType.LOOP:
            end_name = start.name
            self.route = await self._find_loop(start.coordinate, spec.target_distance_km)
        else:
            end = await self._geocode(spec.end_place, bounds)
            end_name = end.name
            self.route = await self._routing.route(start.coordinate, end.coordinate)

        self.start_name = start.name
        self.end_name = end_name
        result = {
            "status": "ok",
            "route_type": spec.route_type.value,
            "start": start.name,
            "end": end_name,
            "distance_km": round(self.route.properties.distance_m / 1000, 1),
            "riding_time_min": round(self.route.properties.duration_s / 60),
            "not_applied": self.not_applied,
        }
        if spec.route_type is RouteType.LOOP:
            target_m = spec.target_distance_km * 1000
            result["requested_distance_km"] = spec.target_distance_km
            result["distance_on_target"] = (
                _distance_error(self.route, target_m) <= LOOP_DISTANCE_TOLERANCE
            )
        return result

    def _find_missing(self, spec: RouteSpec) -> list[str]:
        missing = []
        if spec.start_place is None and self._map_start is None:
            missing.append("start")
        if spec.route_type is RouteType.LOOP and spec.target_distance_km is None:
            missing.append("distance")
        if spec.route_type is RouteType.POINT_TO_POINT and spec.end_place is None:
            missing.append("destination")
        return missing

    async def _resolve_start(self, spec: RouteSpec, bounds: BoundingBox) -> GeocodedPlace:
        if spec.start_place is not None:
            return await self._geocode(spec.start_place, bounds)
        return GeocodedPlace(name=MAP_POINT_NAME, coordinate=self._map_start)

    async def _geocode(self, query: str, bounds: BoundingBox) -> GeocodedPlace:
        place = await self._geocoder.search(query, bounds)
        if place is None:
            raise _ToolFailure(
                status="error", reason=f"could not find '{query}' in the map region"
            )
        return place

    async def _find_loop(self, start: Coordinate, target_km: float) -> RouteFeature:
        """Generate several different loops and keep the one closest to the target."""
        target_m = target_km * 1000
        outcomes = await asyncio.gather(
            *(self._try_loop(start, target_m, seed) for seed in range(LOOP_CANDIDATES))
        )
        loops = [outcome for outcome in outcomes if isinstance(outcome, RouteFeature)]
        if not loops:
            raise outcomes[0]
        return min(loops, key=lambda loop: _distance_error(loop, target_m))

    async def _try_loop(
        self, start: Coordinate, target_m: float, seed: int
    ) -> RouteFeature | RoutingError:
        # Some seeds fail near water. That should not cost us the other candidates.
        try:
            return await self._routing.round_trip(start, target_m, seed=seed)
        except RoutingError as exc:
            return exc


def find_not_applied(spec: RouteSpec) -> list[str]:
    """List the preferences the planner parsed but cannot act on yet."""
    not_applied = []
    if spec.route_type is RouteType.POINT_TO_POINT and spec.target_distance_km is not None:
        not_applied.append("target distance")
    if spec.surface is not Surface.ANY:
        not_applied.append("surface preference")
    if spec.max_climb_m is not None:
        not_applied.append("climbing limit")
    if spec.vibes:
        not_applied.append("vibe preferences")
    return not_applied


def _distance_error(route: RouteFeature, target_m: float) -> float:
    return abs(route.properties.distance_m - target_m) / target_m

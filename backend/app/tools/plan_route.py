"""The plan_route tool: turns a RouteSpec into a real route from the routing engine."""

import asyncio
from typing import Any, Protocol

from pydantic import BaseModel

from app.models.route import BoundingBox, Coordinate, GeocodedPlace, RouteFeature, RouteStop
from app.models.scenic import RouteScenic, ScenicBand
from app.models.spec import Climbing, RouteSpec, RouteType, Surface
from app.routing.climbing import climbing_model
from app.routing.geocoder import GeocodingError
from app.routing.geometry import distance_along, point_at_fraction
from app.routing.graphhopper import (
    CustomModel,
    NoRouteFoundError,
    PointNotRoutableError,
    RoutingEngineError,
    RoutingError,
)
from app.scoring.route_scenic import ScenicDescriber

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
# Named scenic stretches passed to the LLM, longest first.
MAX_HIGHLIGHTS = 3

# Where along the route a stop is looked for: around halfway on a loop, and
# anywhere in the middle half of a point-to-point ride.
LOOP_STOP_WINDOW = (0.3, 0.7)
POINT_TO_POINT_STOP_WINDOW = (0.25, 0.75)
# A loop is re-routed through its own quarter points and the stop, so it keeps its shape.
LOOP_SHAPE_FRACTIONS = (0.25, 0.75)
# How many of the best loops are re-routed through a stop, to choose among.
LOOPS_TRIED_FOR_STOP = 4


class Routing(Protocol):
    async def route(
        self, start: Coordinate, end: Coordinate, custom_model: CustomModel | None = None
    ) -> RouteFeature: ...

    async def route_through(
        self, points: list[Coordinate], custom_model: CustomModel | None = None
    ) -> RouteFeature: ...

    async def round_trip(
        self,
        start: Coordinate,
        distance_m: float,
        seed: int = 0,
        custom_model: CustomModel | None = None,
    ) -> RouteFeature: ...

    async def bounds(self) -> BoundingBox: ...


class Geocoder(Protocol):
    async def search(self, query: str, bounds: BoundingBox) -> GeocodedPlace | None: ...


class PlaceFinder(Protocol):
    async def near_route(
        self, category: str, route: RouteFeature, from_fraction: float, to_fraction: float
    ) -> GeocodedPlace | None: ...


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
        self,
        routing: Routing,
        geocoder: Geocoder,
        map_start: Coordinate | None = None,
        scenery: ScenicDescriber | None = None,
        places: PlaceFinder | None = None,
    ) -> None:
        self._routing = routing
        self._geocoder = geocoder
        self._map_start = map_start
        self._scenery = scenery
        self._places = places
        self.spec: RouteSpec | None = None
        self.route: RouteFeature | None = None
        self.start_name: str | None = None
        self.end_name: str | None = None
        self.stop: RouteStop | None = None

    @property
    def not_applied(self) -> list[str]:
        if self.spec is None:
            return []
        not_applied = find_not_applied(self.spec)
        if self.spec.stop is not None and self._places is None:
            not_applied.append(f"{self.spec.stop.value} stop")
        return not_applied

    async def run(self, args: RouteSpec) -> dict[str, Any]:
        self.spec = args
        self.route = None
        self.start_name = None
        self.end_name = None
        self.stop = None
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
        model = climbing_model(spec.climbing)

        if spec.route_type is RouteType.LOOP:
            end_name = start.name
            loops = await self._find_loops(start.coordinate, spec.target_distance_km, model)
            ranked = rank_loops(loops, spec)
            self.route = ranked[0]
            if spec.stop is not None and self._places is not None:
                await self._add_stop_to_loop(ranked, start.coordinate, spec, model)
        else:
            end = await self._geocode(spec.end_place, bounds)
            end_name = end.name
            self.route = await self._routing.route(start.coordinate, end.coordinate, model)
            if spec.stop is not None and self._places is not None:
                await self._add_stop_on_the_way(start.coordinate, end.coordinate, spec, model)

        self.start_name = start.name
        self.end_name = end_name
        return await self._result(spec, start.name, end_name)

    async def _result(self, spec: RouteSpec, start_name: str, end_name: str) -> dict[str, Any]:
        properties = self.route.properties
        result: dict[str, Any] = {
            "status": "ok",
            "route_type": spec.route_type.value,
            "start": start_name,
            "end": end_name,
            "distance_km": round(properties.distance_m / 1000, 1),
            "riding_time_min": round(properties.duration_s / 60),
            "not_applied": self.not_applied,
        }
        if spec.route_type is RouteType.LOOP:
            target_m = spec.target_distance_km * 1000
            result["requested_distance_km"] = spec.target_distance_km
            result["distance_on_target"] = (
                _distance_error(self.route, target_m) <= LOOP_DISTANCE_TOLERANCE
            )
        if properties.ascend_m is not None:
            result["climb_m"] = round(properties.ascend_m)
            if spec.max_climb_m is not None:
                result["climb_on_target"] = properties.ascend_m <= spec.max_climb_m
        if spec.stop is not None and self._places is not None:
            if self.stop is not None:
                result["stop"] = {
                    "name": self.stop.name,
                    "at_km": round(self.stop.distance_along_m / 1000, 1),
                }
            else:
                result["stop_found"] = False

        if self._scenery is not None:
            properties.scenic = await self._scenery.describe(self.route)
        if properties.scenic is not None:
            result["scenery"] = summarize_scenery(properties.scenic)
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

    async def _find_loops(
        self, start: Coordinate, target_km: float, model: CustomModel | None
    ) -> list[RouteFeature]:
        """Generate several different loops. Ranking them is up to rank_loops."""
        target_m = target_km * 1000
        outcomes = await asyncio.gather(
            *(self._try_loop(start, target_m, seed, model) for seed in range(LOOP_CANDIDATES))
        )
        loops = [outcome for outcome in outcomes if isinstance(outcome, RouteFeature)]
        if not loops:
            raise outcomes[0]
        return loops

    async def _try_loop(
        self, start: Coordinate, target_m: float, seed: int, model: CustomModel | None
    ) -> RouteFeature | RoutingError:
        # Some seeds fail near water. That should not cost us the other candidates.
        try:
            return await self._routing.round_trip(start, target_m, seed=seed, custom_model=model)
        except RoutingError as exc:
            return exc

    async def _add_stop_to_loop(
        self,
        ranked: list[RouteFeature],
        start: Coordinate,
        spec: RouteSpec,
        model: CustomModel | None,
    ) -> None:
        """Send the best loops through a nearby stop, then rank the results like any loops.

        Going through the stop changes a loop's length (the path between its
        quarter points is often more direct), so each re-routed loop is ranked
        again rather than trusting the original ranking.
        """
        rerouted: dict[int, tuple[RouteFeature, GeocodedPlace]] = {}
        for loop in ranked[:LOOPS_TRIED_FOR_STOP]:
            place = await self._places.near_route(spec.stop.value, loop, *LOOP_STOP_WINDOW)
            if place is None:
                continue
            coordinates = loop.geometry.coordinates
            first, last = (
                _coordinate(point_at_fraction(coordinates, fraction))
                for fraction in LOOP_SHAPE_FRACTIONS
            )
            points = [start, first, place.coordinate, last, start]
            try:
                route = await self._routing.route_through(points, model)
            except RoutingError:
                continue
            rerouted[id(route)] = (route, place)
        if rerouted:
            best = rank_loops([route for route, _ in rerouted.values()], spec)[0]
            self._use_stop_route(best, spec, rerouted[id(best)][1])

    async def _add_stop_on_the_way(
        self, start: Coordinate, end: Coordinate, spec: RouteSpec, model: CustomModel | None
    ) -> None:
        place = await self._places.near_route(
            spec.stop.value, self.route, *POINT_TO_POINT_STOP_WINDOW
        )
        if place is not None:
            route = await self._routing.route_through([start, place.coordinate, end], model)
            self._use_stop_route(route, spec, place)

    def _use_stop_route(self, route: RouteFeature, spec: RouteSpec, place: GeocodedPlace) -> None:
        self.route = route
        position = (place.coordinate.lon, place.coordinate.lat)
        self.stop = RouteStop(
            category=spec.stop.value,
            name=place.name,
            coordinate=place.coordinate,
            distance_along_m=round(distance_along(route.geometry.coordinates, position), 1),
        )


def rank_loops(loops: list[RouteFeature], spec: RouteSpec) -> list[RouteFeature]:
    """Best loop first.

    Loops within the distance tolerance come first (or the closest one, if none
    is). Among those, a climbing limit is respected where possible, then the
    rider's climbing goal decides; with no goal, the closest distance wins.
    """
    target_m = spec.target_distance_km * 1000

    def error(loop: RouteFeature) -> float:
        return _distance_error(loop, target_m)

    def climb(loop: RouteFeature) -> float:
        return loop.properties.ascend_m or 0.0

    by_error = sorted(loops, key=error)
    preferred = [loop for loop in by_error if error(loop) <= LOOP_DISTANCE_TOLERANCE]
    preferred = preferred or by_error[:1]

    if spec.max_climb_m is not None:
        under_limit = [loop for loop in preferred if climb(loop) <= spec.max_climb_m]
        preferred = under_limit or preferred
    if spec.climbing is Climbing.SEEK:
        preferred = sorted(preferred, key=lambda loop: (-climb(loop), error(loop)))
    elif spec.climbing is Climbing.AVOID:
        preferred = sorted(preferred, key=lambda loop: (climb(loop), error(loop)))

    return preferred + [loop for loop in by_error if loop not in preferred]


def find_not_applied(spec: RouteSpec) -> list[str]:
    """List the preferences the planner parsed but cannot act on yet."""
    not_applied = []
    if spec.route_type is RouteType.POINT_TO_POINT and spec.target_distance_km is not None:
        not_applied.append("target distance")
    if spec.surface is not Surface.ANY:
        not_applied.append("surface preference")
    if spec.vibes:
        not_applied.append("vibe preferences")
    return not_applied


def summarize_scenery(scenic: RouteScenic) -> dict[str, Any]:
    """What the LLM may say about the scenery. Names come from OSM segments on the route."""
    named = [
        stretch
        for stretch in scenic.stretches
        if stretch.band in (ScenicBand.SCENIC, ScenicBand.PLEASANT) and stretch.name
    ]
    longest = sorted(named, key=lambda s: s.end_m - s.start_m, reverse=True)[:MAX_HIGHLIGHTS]
    return {
        "scenic_share": round(scenic.band_shares[ScenicBand.SCENIC], 2),
        "highlights": [
            {
                "name": stretch.name,
                "why": stretch.reason.value if stretch.reason else None,
                "from_km": round(stretch.start_m / 1000, 1),
                "to_km": round(stretch.end_m / 1000, 1),
            }
            for stretch in sorted(longest, key=lambda s: s.start_m)
        ],
    }


def _coordinate(position: tuple[float, float]) -> Coordinate:
    lon, lat = position
    return Coordinate(lat=lat, lon=lon)


def _distance_error(route: RouteFeature, target_m: float) -> float:
    return abs(route.properties.distance_m - target_m) / target_m

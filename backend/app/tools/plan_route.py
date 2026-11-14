"""The plan_route tool: turns a RouteSpec into real routes from the routing engine.

It returns up to three labelled options (best match, most scenic, hilliest,
...), each with the cafes near its middle, so the rider can choose without
another LLM call.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel

from app.models.route import (
    BoundingBox,
    Coordinate,
    GeocodedPlace,
    NearbyPlace,
    RouteFeature,
    RouteOption,
    RouteStop,
)
from app.models.scenic import RouteScenic, ScenicBand
from app.models.spec import Climbing, RouteSpec, RouteType, Stop, Surface
from app.routing.climbing import climbing_model
from app.routing.geocoder import GeocodingError
from app.routing.geometry import distance_along, distance_m, point_at_fraction
from app.routing.graphhopper import (
    CustomModel,
    NoRouteFoundError,
    PointNotRoutableError,
    RoutingEngineError,
    RoutingError,
)
from app.scoring.candidates import choose_options
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
# Loops further off than this are never offered as options.
OPTION_DISTANCE_TOLERANCE = 0.20
# Named scenic stretches passed to the LLM, longest first.
MAX_HIGHLIGHTS = 3

# Where along the route a stop is looked for: around halfway on a loop, and
# anywhere in the middle half of a point-to-point ride.
LOOP_STOP_WINDOW = (0.3, 0.7)
POINT_TO_POINT_STOP_WINDOW = (0.25, 0.75)
# Points along each option that keep its shape when it is re-routed through a
# stop. The stop is inserted where it adds the least detour, so a cafe at 30%
# of a loop does not cut off the rest of the loop.
LOOP_ANCHOR_FRACTIONS = tuple(i / 7 for i in range(1, 7))
POINT_TO_POINT_ANCHOR_FRACTIONS = (0.25, 0.5, 0.75)
# How many of the best loops are re-routed through a stop, to choose among.
LOOPS_TRIED_FOR_STOP = 6
# Cafes listed per option for the rider to pick from.
STOP_CHOICES = 5
DEFAULT_STOP = Stop.CAFE


class Routing(Protocol):
    async def route(
        self, start: Coordinate, end: Coordinate, custom_model: CustomModel | None = None
    ) -> RouteFeature: ...

    async def alternatives(
        self, start: Coordinate, end: Coordinate, custom_model: CustomModel | None = None
    ) -> list[RouteFeature]: ...

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


class KnownPlaces(Protocol):
    """Places search_places found in this request, so they need no geocoding."""

    def get(self, name: str) -> GeocodedPlace | None: ...


class PlaceFinder(Protocol):
    async def near_route(
        self,
        category: str,
        route: RouteFeature,
        from_fraction: float,
        to_fraction: float,
        limit: int = 1,
    ) -> list[NearbyPlace]: ...


@dataclass
class _Candidate:
    route: RouteFeature
    stop: NearbyPlace | None = None
    anchors: list[Coordinate] = field(default_factory=list)


class _ToolFailure(Exception):
    """Ends the tool early with a result the LLM can explain to the rider."""

    def __init__(self, **result: Any) -> None:
        super().__init__(result.get("reason", "tool failure"))
        self.result = result


class PlanRouteTool:
    """Created per request. Holds the routes so they never pass through the LLM."""

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
        known_places: KnownPlaces | None = None,
    ) -> None:
        self._routing = routing
        self._geocoder = geocoder
        self._map_start = map_start
        self._scenery = scenery
        self._places = places
        self._known_places = known_places
        self._reset(None)

    def _reset(self, spec: RouteSpec | None) -> None:
        self.spec = spec
        self.options: list[RouteOption] = []
        self.start_name: str | None = None
        self.end_name: str | None = None
        self.start: Coordinate | None = None
        self.end: Coordinate | None = None
        self.via: list[GeocodedPlace] = []

    @property
    def route(self) -> RouteFeature | None:
        """The first option's route: the best match."""
        return self.options[0].route if self.options else None

    @property
    def stop(self) -> RouteStop | None:
        return self.options[0].stop if self.options else None

    @property
    def not_applied(self) -> list[str]:
        if self.spec is None:
            return []
        not_applied = find_not_applied(self.spec)
        if self.spec.stop is not None and self._places is None:
            not_applied.append(f"{self.spec.stop.value} stop")
        return not_applied

    async def run(self, args: RouteSpec) -> dict[str, Any]:
        self._reset(args)
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

        vias = [await self._resolve_via(name, bounds) for name in spec.via_places]

        if spec.route_type is RouteType.LOOP:
            end = None
            end_name = start.name
        else:
            end = await self._geocode(spec.end_place, bounds)
            end_name = end.name
        end_coordinate = end.coordinate if end else None

        if vias and spec.route_type is RouteType.LOOP:
            # Start from loops of the requested length and slot the places into
            # each, so the ride keeps its distance instead of becoming an out-and-back.
            loops = await self._find_loops(start.coordinate, spec.target_distance_km, model)
            candidates = await self._loops_through(loops, start.coordinate, vias, spec, model)
        elif vias:
            anchors = [via.coordinate for via in vias]
            points = [start.coordinate, *anchors, end_coordinate]
            candidates = [_Candidate(await self._routing.route_through(points, model), anchors=anchors)]
        elif spec.route_type is RouteType.LOOP:
            loops = await self._find_loops(start.coordinate, spec.target_distance_km, model)
            if spec.max_climb_m is not None and model is not None:
                # On flat ground the avoid model only reshuffles elevation noise and
                # can lose the one loop under the limit, so plain loops compete too.
                loops += await self._find_loops(start.coordinate, spec.target_distance_km, None)
            candidates = [
                _Candidate(loop, anchors=_anchors(loop, LOOP_ANCHOR_FRACTIONS)) for loop in loops
            ]
        else:
            routes = await self._routing.alternatives(start.coordinate, end.coordinate, model)
            candidates = [
                _Candidate(route, anchors=_anchors(route, POINT_TO_POINT_ANCHOR_FRACTIONS))
                for route in routes
            ]

        stop_requested = spec.stop is not None and self._places is not None
        if stop_requested:
            candidates = await self._through_stops(
                candidates, start.coordinate, end_coordinate, spec, model
            ) or candidates

        pool = _option_pool(candidates, spec)
        await self._describe([candidate.route for candidate in pool])
        by_route = {id(candidate.route): candidate for candidate in pool}
        for label, route in choose_options([c.route for c in pool], spec):
            self.options.append(await self._option(label, by_route[id(route)], spec))

        self.start_name, self.end_name = start.name, end_name
        self.start, self.end = start.coordinate, end_coordinate
        self.via = vias
        return self._result(spec, stop_requested)

    def _result(self, spec: RouteSpec, stop_requested: bool) -> dict[str, Any]:
        best = self.options[0]
        properties = best.route.properties
        result: dict[str, Any] = {
            "status": "ok",
            "route_type": spec.route_type.value,
            "start": self.start_name,
            "end": self.end_name,
            "distance_km": round(properties.distance_m / 1000, 1),
            "riding_time_min": round(properties.duration_s / 60),
            "not_applied": self.not_applied,
        }
        if self.via:
            result["via"] = [via.name for via in self.via]
        if spec.route_type is RouteType.LOOP:
            target_m = spec.target_distance_km * 1000
            result["requested_distance_km"] = spec.target_distance_km
            result["distance_on_target"] = (
                _distance_error(best.route, target_m) <= LOOP_DISTANCE_TOLERANCE
            )
        if properties.ascend_m is not None:
            result["climb_m"] = round(properties.ascend_m)
            if spec.max_climb_m is not None:
                result["climb_on_target"] = properties.ascend_m <= spec.max_climb_m
        if stop_requested:
            if best.stop is not None:
                result["stop"] = {
                    "name": best.stop.name,
                    "at_km": round(best.stop.distance_along_m / 1000, 1),
                }
            else:
                result["stop_found"] = False
        if properties.scenic is not None:
            result["scenery"] = summarize_scenery(properties.scenic)
        result["other_options"] = [
            {
                "label": option.label,
                "distance_km": round(option.route.properties.distance_m / 1000, 1),
            }
            for option in self.options[1:]
        ]
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

    async def _loops_through(
        self,
        loops: list[RouteFeature],
        start: Coordinate,
        vias: list[GeocodedPlace],
        spec: RouteSpec,
        model: CustomModel | None,
    ) -> list[_Candidate]:
        """Re-route the best loops through the rider's places, keeping each loop's shape.

        The places become anchors too, so picking a café later still passes them.
        """
        best = rank_routes(loops, spec)[:LOOPS_TRIED_FOR_STOP]
        paths = []
        for loop in best:
            path = [start, *_anchors(loop, LOOP_ANCHOR_FRACTIONS), start]
            for via in vias:
                path = insert_cheapest(path, via.coordinate)
            paths.append(path)
        outcomes = await asyncio.gather(*(self._try_through(path, model) for path in paths))
        candidates = [
            _Candidate(route, anchors=path[1:-1])
            for route, path in zip(outcomes, paths)
            if isinstance(route, RouteFeature)
        ]
        if not candidates:
            raise outcomes[0]
        return candidates

    async def _try_through(
        self, points: list[Coordinate], model: CustomModel | None
    ) -> RouteFeature | RoutingError:
        try:
            return await self._routing.route_through(points, model)
        except RoutingError as exc:
            return exc

    async def _resolve_via(self, name: str, bounds: BoundingBox) -> GeocodedPlace:
        known = self._known_places.get(name) if self._known_places else None
        return known or await self._geocode(name, bounds)

    async def _find_loops(
        self, start: Coordinate, target_km: float, model: CustomModel | None
    ) -> list[RouteFeature]:
        """Generate several different loops. Ranking them is up to rank_routes."""
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

    async def _through_stops(
        self,
        candidates: list[_Candidate],
        start: Coordinate,
        end: Coordinate | None,
        spec: RouteSpec,
        model: CustomModel | None,
    ) -> list[_Candidate]:
        """Re-route the best candidates through a stop near their middle.

        Going through a stop changes a route's length, so the results are
        ranked again afterwards rather than trusting the original ranking.
        Candidates with no stop nearby are dropped; an empty list means none had one.
        """
        best = rank_routes([c.route for c in candidates], spec)[:LOOPS_TRIED_FOR_STOP]
        by_route = {id(c.route): c for c in candidates}
        rerouted = await asyncio.gather(
            *(self._through_stop(by_route[id(route)], start, end, spec, model) for route in best)
        )
        return [candidate for candidate in rerouted if candidate is not None]

    async def _through_stop(
        self,
        candidate: _Candidate,
        start: Coordinate,
        end: Coordinate | None,
        spec: RouteSpec,
        model: CustomModel | None,
    ) -> _Candidate | None:
        window = LOOP_STOP_WINDOW if end is None else POINT_TO_POINT_STOP_WINDOW
        found = await self._places.near_route(spec.stop.value, candidate.route, *window)
        if not found:
            return None
        place = found[0]
        try:
            route = await self._routing.route_through(
                stop_points(start, end, candidate.anchors, place.coordinate), model
            )
        except RoutingError:
            return None
        return _Candidate(route, stop=place, anchors=candidate.anchors)

    async def _describe(self, routes: list[RouteFeature]) -> None:
        if self._scenery is None:
            return
        scenics = await asyncio.gather(*(self._scenery.describe(route) for route in routes))
        for route, scenic in zip(routes, scenics):
            route.properties.scenic = scenic

    async def _option(self, label: str, candidate: _Candidate, spec: RouteSpec) -> RouteOption:
        route = candidate.route
        choices: list[RouteStop] = []
        if self._places is not None:
            category = (spec.stop or DEFAULT_STOP).value
            window = (
                LOOP_STOP_WINDOW if spec.route_type is RouteType.LOOP else POINT_TO_POINT_STOP_WINDOW
            )
            found = await self._places.near_route(category, route, *window, limit=STOP_CHOICES)
            choices = [make_stop(category, place, route) for place in found]
        stop = (
            make_stop(spec.stop.value, candidate.stop, route)
            if candidate.stop is not None
            else None
        )
        return RouteOption(
            label=label, route=route, stop=stop, stop_choices=choices, anchors=candidate.anchors
        )


def stop_points(
    start: Coordinate, end: Coordinate | None, anchors: list[Coordinate], stop: Coordinate
) -> list[Coordinate]:
    """The points a route through a stop visits, in order.

    The route keeps its shape by passing its anchors; the stop goes into the
    gap between consecutive points where it adds the least straight-line detour.
    """
    return insert_cheapest([start, *anchors, end if end is not None else start], stop)


def insert_cheapest(path: list[Coordinate], point: Coordinate) -> list[Coordinate]:
    """Put `point` between the consecutive points of `path` where it adds the least detour."""
    detours = [
        _metres(a, point) + _metres(point, b) - _metres(a, b) for a, b in zip(path, path[1:])
    ]
    gap = detours.index(min(detours)) + 1
    return [*path[:gap], point, *path[gap:]]


def make_stop(category: str, place: GeocodedPlace, route: RouteFeature) -> RouteStop:
    position = (place.coordinate.lon, place.coordinate.lat)
    return RouteStop(
        category=category,
        name=place.name,
        coordinate=place.coordinate,
        distance_along_m=round(distance_along(route.geometry.coordinates, position), 1),
        off_route_m=getattr(place, "off_route_m", None),
    )


def rank_routes(routes: list[RouteFeature], spec: RouteSpec) -> list[RouteFeature]:
    """Best route first.

    For loops, those within the distance tolerance come first (or the closest
    one, if none is). Among the preferred routes, a climbing limit is respected
    where possible, then the rider's climbing goal decides. With no goal, loops
    keep distance order and point-to-point routes keep GraphHopper's order.
    """
    target_m = spec.target_distance_km * 1000 if spec.route_type is RouteType.LOOP else None

    def error(route: RouteFeature) -> float:
        return _distance_error(route, target_m) if target_m else 0.0

    def climb(route: RouteFeature) -> float:
        return route.properties.ascend_m or 0.0

    by_error = sorted(routes, key=error)
    preferred = [route for route in by_error if error(route) <= LOOP_DISTANCE_TOLERANCE]
    preferred = preferred or by_error[:1]

    if spec.max_climb_m is not None:
        under_limit = [route for route in preferred if climb(route) <= spec.max_climb_m]
        preferred = under_limit or preferred
    if spec.climbing is Climbing.SEEK:
        preferred = sorted(preferred, key=lambda route: (-climb(route), error(route)))
    elif spec.climbing is Climbing.AVOID:
        preferred = sorted(preferred, key=lambda route: (climb(route), error(route)))

    return preferred + [route for route in by_error if route not in preferred]


def _option_pool(candidates: list[_Candidate], spec: RouteSpec) -> list[_Candidate]:
    """Ranked candidates that are close enough to the request to offer as options."""
    by_route = {id(c.route): c for c in candidates}
    ranked = [by_route[id(route)] for route in rank_routes([c.route for c in candidates], spec)]
    if spec.route_type is not RouteType.LOOP:
        return ranked
    target_m = spec.target_distance_km * 1000
    close = [c for c in ranked if _distance_error(c.route, target_m) <= OPTION_DISTANCE_TOLERANCE]
    return [ranked[0], *(c for c in close if c is not ranked[0])]


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


def _anchors(route: RouteFeature, fractions: tuple[float, ...]) -> list[Coordinate]:
    coordinates = route.geometry.coordinates
    return [_coordinate(point_at_fraction(coordinates, f)) for f in fractions]


def _metres(a: Coordinate, b: Coordinate) -> float:
    return distance_m((a.lon, a.lat), (b.lon, b.lat))


def _coordinate(position: tuple[float, float]) -> Coordinate:
    lon, lat = position
    return Coordinate(lat=lat, lon=lon)


def _distance_error(route: RouteFeature, target_m: float) -> float:
    return abs(route.properties.distance_m - target_m) / target_m

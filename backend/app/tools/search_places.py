"""The search_places tool: finds real places matching what the rider wants to pass.

The LLM describes the kind of place ("lookout with a view", "lake in a park");
the tool returns named places near the start, found by hybrid search over the
pipeline's place index. Coordinates stay in the per-request registry, so the
LLM only ever sees names, and plan_route can route through what was found.
"""

from enum import StrEnum
from typing import Any

import asyncpg
from pydantic import BaseModel, Field

from app.models.route import Coordinate, GeocodedPlace
from app.retrieval.place_search import FoundPlace, PlaceSearch
from app.routing.geocoder import GeocodingError
from app.tools.plan_route import Geocoder, Routing

NAME = "search_places"
DESCRIPTION = (
    "Find real places near the start that match a kind of place the rider wants to ride "
    "to or past, such as a lookout, a lake, a beach, a big park or a museum. Returns place "
    "names to pass to plan_route as via_places."
)
DEFAULT_RESULTS = 5


class PlaceKind(StrEnum):
    PARK = "park"
    VIEWPOINT = "viewpoint"
    WATER = "water"
    BEACH = "beach"
    LANDMARK = "landmark"
    CAFE = "cafe"


class SearchPlacesArgs(BaseModel):
    query: str = Field(
        min_length=2,
        max_length=200,
        description="The kind of place in a few words, e.g. 'lookout with a view of the city'.",
    )
    near_place: str | None = Field(
        default=None,
        description="Where the ride starts, as the rider wrote it. Null to use the map point.",
    )
    within_km: float = Field(
        default=8,
        gt=0,
        le=30,
        description="How far from the start to look. For a loop, about a third of its length.",
    )
    kind: PlaceKind | None = Field(
        default=None, description="Restrict to one kind of place if the rider was specific."
    )


class PlaceRegistry:
    """Places found during one request, by name, for plan_route to route through."""

    def __init__(self) -> None:
        self._places: dict[str, FoundPlace] = {}

    def add(self, places: list[FoundPlace]) -> None:
        for place in places:
            self._places.setdefault(place.name.casefold(), place)

    def get(self, name: str) -> GeocodedPlace | None:
        place = self._places.get(name.strip().casefold())
        return GeocodedPlace(name=place.name, coordinate=place.coordinate) if place else None


class SearchPlacesTool:
    name = NAME
    description = DESCRIPTION
    args_model: type[BaseModel] = SearchPlacesArgs

    def __init__(
        self,
        search: PlaceSearch,
        routing: Routing,
        geocoder: Geocoder,
        registry: PlaceRegistry,
        map_start: Coordinate | None = None,
    ) -> None:
        self._search = search
        self._routing = routing
        self._geocoder = geocoder
        self._registry = registry
        self._map_start = map_start

    async def run(self, args: SearchPlacesArgs) -> dict[str, Any]:
        try:
            centre = await self._centre(args)
            if centre is None:
                return {"status": "needs_input", "missing": ["start"]}
            found = await self._search.search(
                args.query,
                centre,
                within_m=args.within_km * 1000,
                category=args.kind.value if args.kind else None,
                limit=DEFAULT_RESULTS,
            )
        except _UnknownPlace as exc:
            return {"status": "error", "reason": f"could not find '{exc}' in the map region"}
        except (GeocodingError, OSError, asyncpg.PostgresError):
            return {"status": "error", "reason": "place search is unavailable"}

        self._registry.add(found)
        return {
            "status": "ok",
            "places": [
                {
                    "name": place.name,
                    "kind": place.category,
                    "distance_km": round(place.distance_m / 1000, 1),
                    "about": place.description,
                }
                for place in found
            ],
        }

    async def _centre(self, args: SearchPlacesArgs) -> Coordinate | None:
        if args.near_place is None:
            return self._map_start
        place = await self._geocoder.search(args.near_place, await self._routing.bounds())
        if place is None:
            raise _UnknownPlace(args.near_place)
        return place.coordinate


class _UnknownPlace(Exception):
    pass

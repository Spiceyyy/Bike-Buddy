from pydantic import BaseModel, Field

from app.models.llm import Usage
from app.models.route import Coordinate, GeocodedPlace, RouteFeature, RouteOption, RouteStop
from app.models.spec import RouteSpec


class PlanRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    map_start: Coordinate | None = None


class PlanResponse(BaseModel):
    reply: str
    route: RouteFeature | None = None
    spec: RouteSpec | None = None
    # The places actually matched, which may differ from what the rider typed.
    start_name: str | None = None
    end_name: str | None = None
    # A stop the planner found and routed through, e.g. a cafe.
    stop: RouteStop | None = None
    # Up to three labelled routes to choose from. The first is `route` and `stop`.
    options: list[RouteOption] = []
    # Resolved start and end (null for loops), for re-routing through a chosen stop.
    start: Coordinate | None = None
    end: Coordinate | None = None
    # Places the route was sent through (found by search_places or named by the rider).
    via: list[GeocodedPlace] = []
    not_applied: list[str] = []
    usage: Usage

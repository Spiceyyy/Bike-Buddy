from pydantic import BaseModel, Field

from app.models.llm import Usage
from app.models.route import Coordinate, RouteFeature, RouteStop
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
    not_applied: list[str] = []
    usage: Usage

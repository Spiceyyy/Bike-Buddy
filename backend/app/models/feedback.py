from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

from app.models.route import LineString
from app.models.spec import RouteSpec

MAX_TRACK_POINTS = 50_000


class FeedbackRoute(BaseModel):
    """The route that was rated, as the rider saw it (after any option or café switch)."""

    geometry: LineString
    distance_m: float = Field(gt=0)
    ascend_m: float | None = None
    scenic_score: float | None = None
    # Share of the route in the "scenic" band.
    scenic_share: float | None = Field(default=None, ge=0, le=1)
    stop_name: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def geometry_is_a_route(self) -> Self:
        points = self.geometry.coordinates
        if not 2 <= len(points) <= MAX_TRACK_POINTS:
            raise ValueError(f"the route needs 2 to {MAX_TRACK_POINTS} points")
        if any(not (-180 <= lon <= 180 and -90 <= lat <= 90) for lon, lat in points):
            raise ValueError("route points must be [longitude, latitude]")
        return self


class FeedbackRequest(BaseModel):
    """A thumbs up or down on the route on screen. No account or IP address is stored."""

    rating: Literal["up", "down"]
    comment: str | None = Field(default=None, max_length=1000)
    # The rider's request and how it was parsed; null for a route between two map clicks.
    message: str | None = Field(default=None, max_length=1000)
    spec: RouteSpec | None = None
    option_label: str | None = Field(default=None, max_length=50)
    route: FeedbackRoute


class FeedbackResponse(BaseModel):
    id: int

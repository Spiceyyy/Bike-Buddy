from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

from app.models.route import Coordinate, LineString

MAX_TRACK_POINTS = 50_000
MAX_WAYPOINTS = 12


class GpxWaypoint(BaseModel):
    """A named point shown on the rider's device: the start, a café, a place passed."""

    name: str = Field(min_length=1, max_length=200)
    coordinate: Coordinate
    kind: Literal["start", "end", "stop", "via"]


class GpxRequest(BaseModel):
    """A route the rider is looking at, to download for a bike computer or app."""

    name: str = Field(min_length=1, max_length=200)
    track: LineString
    waypoints: list[GpxWaypoint] = Field(default_factory=list, max_length=MAX_WAYPOINTS)

    @model_validator(mode="after")
    def track_is_a_route(self) -> Self:
        points = self.track.coordinates
        if not 2 <= len(points) <= MAX_TRACK_POINTS:
            raise ValueError(f"the track needs 2 to {MAX_TRACK_POINTS} points")
        if any(not (-180 <= lon <= 180 and -90 <= lat <= 90) for lon, lat in points):
            raise ValueError("track points must be [longitude, latitude]")
        return self

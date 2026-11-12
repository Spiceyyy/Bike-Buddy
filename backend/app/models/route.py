from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

from app.models.scenic import RouteScenic


class Coordinate(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class BoundingBox(BaseModel):
    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float


class GeocodedPlace(BaseModel):
    name: str
    coordinate: Coordinate


class RouteRequest(BaseModel):
    start: Coordinate
    end: Coordinate

    @model_validator(mode="after")
    def start_and_end_differ(self) -> Self:
        if self.start == self.end:
            raise ValueError("start and end must be different points")
        return self


class LineString(BaseModel):
    """GeoJSON LineString. Positions are [longitude, latitude]."""

    type: Literal["LineString"] = "LineString"
    coordinates: list[tuple[float, float]]


class RouteProperties(BaseModel):
    distance_m: float
    duration_s: float
    profile: str
    # Metres climbed and descended, from SRTM elevation. Null if the graph has no
    # elevation. SRTM partly measures buildings and trees, so city figures run high.
    ascend_m: float | None = None
    descend_m: float | None = None
    # Added after routing, from the scored segments. Null if unavailable.
    scenic: RouteScenic | None = None


class RouteFeature(BaseModel):
    """GeoJSON Feature describing one route from the routing engine."""

    type: Literal["Feature"] = "Feature"
    geometry: LineString
    properties: RouteProperties


class RouteStop(BaseModel):
    """A place the route was sent through, found by the tool (e.g. a cafe)."""

    category: str
    name: str
    coordinate: Coordinate
    distance_along_m: float
    """How far into the ride the stop comes."""

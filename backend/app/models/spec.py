"""RouteSpec: the structured form of a ride request, produced by the LLM."""

from enum import StrEnum

from pydantic import BaseModel, Field


class RouteType(StrEnum):
    LOOP = "loop"
    POINT_TO_POINT = "point_to_point"


class Surface(StrEnum):
    ANY = "any"
    PAVED = "paved"
    UNPAVED = "unpaved"


class Vibe(StrEnum):
    WATER = "water"
    FOREST = "forest"
    PARK = "park"
    QUIET = "quiet"
    CAFE = "cafe"
    VIEWPOINT = "viewpoint"
    HISTORIC = "historic"


class RouteSpec(BaseModel):
    """What the rider asked for. Holds place names only, never coordinates."""

    route_type: RouteType = Field(
        description="point_to_point if the rider names a destination, otherwise loop."
    )
    start_place: str | None = Field(
        default=None,
        description="Where the ride starts, as the rider wrote it. Null if not stated.",
    )
    end_place: str | None = Field(
        default=None,
        description="Destination, as the rider wrote it. Null for loops or if not stated.",
    )
    target_distance_km: float | None = Field(
        default=None,
        gt=0,
        le=300,
        description="Requested distance in kilometres. Null if not stated.",
    )
    surface: Surface = Field(
        default=Surface.ANY,
        description="paved or unpaved only if the rider says so, otherwise any.",
    )
    max_climb_m: int | None = Field(
        default=None,
        gt=0,
        description="Maximum climbing in metres. Only if the rider gives a number.",
    )
    vibes: list[Vibe] = Field(
        default_factory=list,
        description="Qualities the rider explicitly asks for. Empty if none.",
    )

"""RouteSpec: the structured form of a ride request, produced by the LLM."""

import re
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator, model_validator

# "a cafe" or "un café" describes a kind of place, not a place the geocoder can find.
# Small models sometimes put these in end_place; rejecting them sends the LLM a
# correction through the orchestrator's invalid_arguments path.
GENERIC_PLACE = re.compile(
    r"(a|an|any|some|the nearest|un|une|le|la|des)\s+"
    r"(cafe|café|coffee|coffee shop|bakery|boulangerie|park|parc)s?",
    re.IGNORECASE,
)


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
    VIEWPOINT = "viewpoint"
    HISTORIC = "historic"


class Stop(StrEnum):
    """A kind of place the route should pass, found by the tool, not named by the rider."""

    CAFE = "cafe"


class Climbing(StrEnum):
    ANY = "any"
    AVOID = "avoid"
    SEEK = "seek"


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
    climbing: Climbing = Field(
        default=Climbing.ANY,
        description="seek for hills or climbing training, avoid for flat rides, otherwise any.",
    )
    stop: Stop | None = Field(
        default=None,
        description="A kind of place to stop at along the way, e.g. cafe. Null if none.",
    )
    via_places: list[str] = Field(
        default_factory=list,
        max_length=3,
        description=(
            "Places the route should pass, in riding order: names exactly as search_places "
            "returned them, or places the rider named. Empty if none."
        ),
    )
    vibes: list[Vibe] = Field(
        default_factory=list,
        description="Qualities the rider explicitly asks for. Empty if none.",
    )

    @field_validator("start_place", "end_place")
    @classmethod
    def must_be_a_named_place(cls, value: str | None) -> str | None:
        if value is not None and GENERIC_PLACE.fullmatch(value.strip()):
            raise ValueError(
                f"'{value}' is not a named place. For an unnamed cafe, set stop to cafe "
                "and leave the place null; with no other destination the ride is a loop."
            )
        return value

    @field_validator("via_places")
    @classmethod
    def vias_must_be_named_places(cls, values: list[str]) -> list[str]:
        for value in values:
            cls.must_be_a_named_place(value)
        return values

    @model_validator(mode="after")
    def same_start_and_end_is_a_loop(self) -> "RouteSpec":
        """The prompt's rule, enforced: a ride back to where it started is a loop."""
        same = (
            self.route_type is RouteType.POINT_TO_POINT
            and self.start_place is not None
            and self.end_place is not None
            and self.start_place.strip().casefold() == self.end_place.strip().casefold()
        )
        if same:
            self.route_type = RouteType.LOOP
            self.end_place = None
        return self

    @model_validator(mode="after")
    def climbing_limit_means_avoid(self) -> "RouteSpec":
        """The prompt's rule, enforced: a climbing limit leans the route away from climbs."""
        if self.max_climb_m is not None and self.climbing is Climbing.ANY:
            self.climbing = Climbing.AVOID
        return self

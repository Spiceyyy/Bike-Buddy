"""How scenic a route is, stretch by stretch, from the pipeline's segment scores."""

from enum import StrEnum

from pydantic import BaseModel


class ScenicBand(StrEnum):
    SCENIC = "scenic"
    PLEASANT = "pleasant"
    PLAIN = "plain"
    # No scored segment near the route, e.g. before the pipeline has run there.
    UNKNOWN = "unknown"


class ScenicReason(StrEnum):
    """The main reason a stretch scored as it did. Values are shown to riders."""

    WATER = "along the water"
    GREEN = "through green space"
    QUIET = "on quiet streets"
    BUSY = "on a busy road"
    LITTLE_SCENERY = "little scenery"


class ScenicStretch(BaseModel):
    """A run of the route with one scenic band."""

    start_m: float
    end_m: float
    band: ScenicBand
    score: float | None
    reason: ScenicReason | None
    # The street or path name covering most of the stretch, from OSM. Null if unnamed.
    name: str | None
    # GeoJSON positions, [longitude, latitude], cut from the route geometry.
    coordinates: list[tuple[float, float]]


class RouteScenic(BaseModel):
    score: float
    """Mean scenic score along the route, 0..1."""
    matched_share: float
    """Share of route sample points matched to a scored segment."""
    band_shares: dict[ScenicBand, float]
    """Share of the route's length in each band."""
    stretches: list[ScenicStretch]

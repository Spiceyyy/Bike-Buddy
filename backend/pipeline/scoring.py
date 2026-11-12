"""Turns segment features into one scenic score between 0 and 1.

The score is a weighted average of six features, each already scaled to
0..1 where 1 is better for a relaxed, scenic ride. The weights are hand
tuned for now; Phase 3 plans to learn them from user ratings later. Keeping
the formula this simple means every score can be explained feature by
feature.
"""

import re
from collections.abc import Mapping
from dataclasses import astuple, dataclass, fields

import numpy as np

# How calm each road class is to ride on, as a proxy for traffic.
HIGHWAY_QUIETNESS: dict[str, float] = {
    "cycleway": 1.0,
    "path": 1.0,
    "footway": 1.0,
    "pedestrian": 1.0,
    "bridleway": 1.0,
    "track": 0.9,
    "living_street": 0.9,
    "residential": 0.8,
    "service": 0.7,
    "unclassified": 0.6,
    "tertiary": 0.5,
    "tertiary_link": 0.5,
    "secondary": 0.3,
    "secondary_link": 0.3,
    "primary": 0.15,
    "primary_link": 0.15,
    "trunk": 0.0,
    "trunk_link": 0.0,
}
DEFAULT_QUIETNESS = 0.5
FAST_ROAD_KMH = 70
SLOW_ROAD_KMH = 30
# A physically separated bike track makes even a busy road pleasant to ride.
SEPARATED_TRACK_QUIETNESS = 0.7
PAINTED_LANE_BONUS = 0.1
SLOW_ROAD_BONUS = 0.1

# How smooth each surface is to ride. Preference for gravel is a per-ride
# choice, applied when routes are re-ranked, not baked into the scenic score.
SURFACE_QUALITY: dict[str, float] = {
    "asphalt": 1.0,
    "paved": 1.0,
    "concrete": 1.0,
    "concrete:plates": 0.9,
    "paving_stones": 0.85,
    "wood": 0.8,
    "compacted": 0.75,
    "fine_gravel": 0.7,
    "sett": 0.5,
    "cobblestone": 0.4,
    "gravel": 0.4,
    "unpaved": 0.4,
    "ground": 0.3,
    "dirt": 0.3,
    "grass": 0.2,
    "sand": 0.1,
}
# Paths and tracks with no surface tag are often unpaved; roads usually are not.
UNKNOWN_SURFACE_QUALITY = {"path": 0.6, "track": 0.5}
DEFAULT_SURFACE_QUALITY = 1.0

# Intersections within the search radius at which a segment counts as fully
# stop-and-go. A downtown grid has about 15 within 200 m.
INTERSECTION_SATURATION = 20

CYCLEWAY_KEYS = ("cycleway", "cycleway:both", "cycleway:left", "cycleway:right")


@dataclass(frozen=True)
class ScoringWeights:
    water: float = 0.30
    green: float = 0.25
    quietness: float = 0.25
    calmness: float = 0.10
    viewpoint: float = 0.05
    surface: float = 0.05

    def __post_init__(self) -> None:
        total = sum(astuple(self))
        if not np.isclose(total, 1.0):
            raise ValueError(f"weights must sum to 1, got {total}")


@dataclass(frozen=True)
class SegmentFeatures:
    """All six scored features, each 0..1. Arrays of one value per segment, or floats."""

    water: np.ndarray | float
    green: np.ndarray | float
    quietness: np.ndarray | float
    calmness: np.ndarray | float
    viewpoint: np.ndarray | float
    surface: np.ndarray | float


def scenic_score(
    features: SegmentFeatures, weights: ScoringWeights = ScoringWeights()
) -> np.ndarray | float:
    return sum(
        getattr(weights, field.name) * getattr(features, field.name)
        for field in fields(SegmentFeatures)
    )


def quietness(tags: Mapping[str, str]) -> float:
    score = HIGHWAY_QUIETNESS.get(tags.get("highway", ""), DEFAULT_QUIETNESS)

    speed = parse_maxspeed_kmh(tags.get("maxspeed"))
    if speed is not None and speed >= FAST_ROAD_KMH:
        score = min(score, 0.1)
    elif speed is not None and speed <= SLOW_ROAD_KMH:
        score += SLOW_ROAD_BONUS

    cycleways = {tags[key] for key in CYCLEWAY_KEYS if key in tags}
    if cycleways & {"track", "separate"}:
        score = max(score, SEPARATED_TRACK_QUIETNESS)
    elif "lane" in cycleways:
        score += PAINTED_LANE_BONUS

    return min(score, 1.0)


def surface_quality(tags: Mapping[str, str]) -> float:
    surface = tags.get("surface")
    if surface in SURFACE_QUALITY:
        return SURFACE_QUALITY[surface]
    return UNKNOWN_SURFACE_QUALITY.get(tags.get("highway", ""), DEFAULT_SURFACE_QUALITY)


def calmness(intersections_nearby: np.ndarray) -> np.ndarray:
    """1 for a segment with no nearby intersections, 0 at saturation or above."""
    return 1.0 - np.minimum(intersections_nearby / INTERSECTION_SATURATION, 1.0)


def parse_maxspeed_kmh(value: str | None) -> float | None:
    """Parse "50" or "30 mph". Returns None for zone codes like "CA:urban"."""
    if value is None:
        return None
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(mph)?\s*", value)
    if match is None:
        return None
    speed = float(match.group(1))
    return speed * 1.609 if match.group(2) else speed

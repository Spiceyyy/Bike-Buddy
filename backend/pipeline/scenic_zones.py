"""Exports segment scores to GraphHopper as scenic zones.

GraphHopper cannot read a score per road segment without custom Java code,
but its custom models can test whether a road lies inside a named area. So
the map is cut into a grid, each cell gets the length-weighted mean score of
the segments in it, and cells are grouped into "scenic" and "pleasant" zones.

GraphHopper only allows a custom model to lower priority, never raise it, so
the model penalises roads outside the zones rather than boosting roads in
them. The effect is the same: routes are drawn towards scenic areas, and the
built-in bike model still picks the quietest roads within each zone.
"""

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import shapely
from shapely.geometry.base import BaseGeometry

from pipeline.projection import METRES_PER_DEGREE_LAT, METRES_PER_DEGREE_LON_AT_EQUATOR

SCENIC = "scenic"
PLEASANT = "pleasant"


@dataclass(frozen=True)
class ZoneSettings:
    cell_size_m: float = 150.0
    # About the top 10% and top 25% of segments in Montreal.
    scenic_threshold: float = 0.55
    pleasant_threshold: float = 0.40
    # Priority multipliers. A road outside every zone costs 1 / 0.5, twice as
    # much to ride as the same road in a scenic zone. Chosen by a sweep on the
    # route-quality eval: 0.85 / 0.7 changed only 3 of 10 point-to-point
    # routes, while 0.55 / 0.3 made one trip 68% longer. This setting kept
    # every detour under 10%.
    pleasant_priority: float = 0.70
    other_priority: float = 0.50
    coordinate_decimals: int = 6


@dataclass(frozen=True)
class ScenicZones:
    scenic: BaseGeometry
    pleasant: BaseGeometry
    cell_count: int


def build_zones(
    midpoints: np.ndarray,
    lengths_m: np.ndarray,
    scores: np.ndarray,
    settings: ZoneSettings = ZoneSettings(),
) -> ScenicZones:
    """Group segments into grid cells and return the scenic and pleasant zones.

    `midpoints` is an (N, 2) array of segment midpoints in lon/lat.
    """
    origin_lon, origin_lat = midpoints.min(axis=0)
    cell_lat = settings.cell_size_m / METRES_PER_DEGREE_LAT
    cell_lon = settings.cell_size_m / (
        METRES_PER_DEGREE_LON_AT_EQUATOR * math.cos(math.radians(midpoints[:, 1].mean()))
    )

    cell_xy = np.column_stack(
        (
            np.floor((midpoints[:, 0] - origin_lon) / cell_lon),
            np.floor((midpoints[:, 1] - origin_lat) / cell_lat),
        )
    ).astype(int)
    cells, cell_of_segment = np.unique(cell_xy, axis=0, return_inverse=True)
    cell_of_segment = cell_of_segment.ravel()
    cell_scores = np.bincount(cell_of_segment, weights=lengths_m * scores) / np.bincount(
        cell_of_segment, weights=lengths_m
    )

    def zone(mask: np.ndarray) -> BaseGeometry:
        x, y = cells[mask, 0], cells[mask, 1]
        boxes = shapely.box(
            origin_lon + x * cell_lon,
            origin_lat + y * cell_lat,
            origin_lon + (x + 1) * cell_lon,
            origin_lat + (y + 1) * cell_lat,
        )
        return shapely.union_all(boxes)

    scenic = cell_scores >= settings.scenic_threshold
    pleasant = (cell_scores >= settings.pleasant_threshold) & ~scenic
    return ScenicZones(scenic=zone(scenic), pleasant=zone(pleasant), cell_count=len(cells))


def custom_model(zones: ScenicZones, settings: ZoneSettings = ZoneSettings()) -> dict[str, Any]:
    """Build the GraphHopper custom model (priority rules plus zone areas)."""
    features: list[dict[str, Any]] = []
    rules: list[dict[str, str]] = []
    for area_id, geometry, priority in (
        (SCENIC, zones.scenic, 1.0),
        (PLEASANT, zones.pleasant, settings.pleasant_priority),
    ):
        if geometry.is_empty:
            continue
        features.append(
            {
                "type": "Feature",
                "id": area_id,
                "properties": {},
                "geometry": _rounded(
                    shapely.geometry.mapping(geometry), settings.coordinate_decimals
                ),
            }
        )
        rules.append({"else_if" if rules else "if": f"in_{area_id}", "multiply_by": str(priority)})

    if not rules:
        return {"priority": []}
    rules.append({"else": "", "multiply_by": str(settings.other_priority)})
    return {
        "priority": rules,
        "areas": {"type": "FeatureCollection", "features": features},
    }


def write_custom_model(path: Path, model: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".part")
    temporary.write_text(json.dumps(model, separators=(",", ":")), encoding="utf-8")
    temporary.replace(path)


def _rounded(value: Any, decimals: int) -> Any:
    if isinstance(value, float):
        return round(value, decimals)
    if isinstance(value, (list, tuple)):
        return [_rounded(item, decimals) for item in value]
    if isinstance(value, dict):
        return {key: _rounded(item, decimals) for key, item in value.items()}
    return value

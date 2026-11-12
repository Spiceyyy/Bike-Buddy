"""Geometric features of each segment, measured against its surroundings.

All geometries passed in here are already in metres (see projection.py).
Each feature is measured by sampling points every few metres along a
segment and asking what lies near each point, which gives a share between
0 and 1 that is easy to explain: "62% of this segment is within 100 m of
water".
"""

from dataclasses import dataclass

import numpy as np
import shapely
from shapely.geometry.base import BaseGeometry


@dataclass(frozen=True)
class FeatureSettings:
    sample_spacing_m: float = 25.0
    water_distance_m: float = 100.0
    green_distance_m: float = 50.0
    viewpoint_distance_m: float = 200.0
    intersection_radius_m: float = 200.0
    # Smaller areas are ignored: OSM maps planters and pocket squares as
    # gardens and parks, and fountains as water, none of which make a street
    # scenic. 0.5 ha is roughly a city block's worth of park.
    min_green_area_m2: float = 5_000.0
    min_water_area_m2: float = 2_000.0
    # Large polygons (a river, a big park) are cut into tiles no wider than
    # this, so distance checks only touch nearby parts of them.
    tile_size_m: float = 1000.0


@dataclass(frozen=True)
class GeometricFeatures:
    """One value per segment, in the order the segments were given."""

    water_share: np.ndarray
    green_share: np.ndarray
    near_viewpoint: np.ndarray
    intersections_nearby: np.ndarray


@dataclass(frozen=True)
class Samples:
    points: np.ndarray
    segment_index: np.ndarray
    segment_count: int


def measure(
    lines: np.ndarray,
    water: list[BaseGeometry],
    green: list[BaseGeometry],
    viewpoints: list[BaseGeometry],
    intersections: list[BaseGeometry],
    settings: FeatureSettings = FeatureSettings(),
) -> GeometricFeatures:
    samples = sample_along(lines, settings.sample_spacing_m)
    midpoints = shapely.line_interpolate_point(lines, 0.5, normalized=True)
    water = drop_small_areas(water, settings.min_water_area_m2)
    green = drop_small_areas(green, settings.min_green_area_m2)
    return GeometricFeatures(
        water_share=share_near(
            samples, subdivide(water, settings.tile_size_m), settings.water_distance_m
        ),
        green_share=share_near(
            samples, subdivide(green, settings.tile_size_m), settings.green_distance_m
        ),
        near_viewpoint=share_near(samples, viewpoints, settings.viewpoint_distance_m) > 0,
        intersections_nearby=count_near(
            midpoints, intersections, settings.intersection_radius_m
        ),
    )


def sample_along(lines: np.ndarray, spacing_m: float) -> Samples:
    """Evenly spaced points along each line, both ends included."""
    lengths = shapely.length(lines)
    per_line = np.maximum(2, np.ceil(lengths / spacing_m).astype(int) + 1)
    segment_index = np.repeat(np.arange(len(lines)), per_line)
    first_sample = np.repeat(np.cumsum(per_line) - per_line, per_line)
    position = np.arange(per_line.sum()) - first_sample
    fraction = position / np.repeat(per_line - 1, per_line)
    points = shapely.line_interpolate_point(lines[segment_index], fraction, normalized=True)
    return Samples(points=points, segment_index=segment_index, segment_count=len(lines))


def share_near(samples: Samples, targets: list[BaseGeometry], distance_m: float) -> np.ndarray:
    """Share of each segment's sample points within `distance_m` of any target."""
    near = np.zeros(len(samples.points), dtype=bool)
    if targets:
        point_index, _ = shapely.STRtree(targets).query(
            samples.points, predicate="dwithin", distance=distance_m
        )
        near[point_index] = True
    hits = np.bincount(samples.segment_index, weights=near, minlength=samples.segment_count)
    totals = np.bincount(samples.segment_index, minlength=samples.segment_count)
    return hits / totals


def count_near(points: np.ndarray, targets: list[BaseGeometry], radius_m: float) -> np.ndarray:
    """Number of targets within `radius_m` of each point."""
    if not targets:
        return np.zeros(len(points), dtype=int)
    point_index, _ = shapely.STRtree(targets).query(
        points, predicate="dwithin", distance=radius_m
    )
    return np.bincount(point_index, minlength=len(points))


def drop_small_areas(geometries: list[BaseGeometry], min_area_m2: float) -> list[BaseGeometry]:
    """Remove polygons smaller than `min_area_m2`. Lines, such as rivers, are kept."""
    return [
        geometry
        for geometry in geometries
        if geometry.geom_type not in ("Polygon", "MultiPolygon") or geometry.area >= min_area_m2
    ]


def subdivide(geometries: list[BaseGeometry], tile_size_m: float) -> list[BaseGeometry]:
    """Cut geometries wider than `tile_size_m` into tiles, by recursive quartering."""
    pieces: list[BaseGeometry] = []
    pending = list(geometries)
    while pending:
        geometry = pending.pop()
        if geometry.is_empty:
            continue
        min_x, min_y, max_x, max_y = geometry.bounds
        if max_x - min_x <= tile_size_m and max_y - min_y <= tile_size_m:
            pieces.append(geometry)
            continue
        mid_x, mid_y = (min_x + max_x) / 2, (min_y + max_y) / 2
        for box in (
            (min_x, min_y, mid_x, mid_y),
            (mid_x, min_y, max_x, mid_y),
            (min_x, mid_y, mid_x, max_y),
            (mid_x, mid_y, max_x, max_y),
        ):
            pending.append(shapely.clip_by_rect(geometry, *box))
    return pieces

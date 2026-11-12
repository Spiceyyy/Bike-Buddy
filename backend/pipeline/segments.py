"""Splits rideable OSM ways into the segments that get scored.

A segment runs between two intersections, so a router can choose it or not
as one unit. Long stretches with no junction are cut every
`max_length_m`, so a 3 km riverside path that turns into an arterial road
does not get one averaged score.
"""

from collections.abc import Iterable, Set
from dataclasses import dataclass

from pipeline.osm_reader import RoadWay
from pipeline.projection import step_length_m

MAX_SEGMENT_LENGTH_M = 400.0


@dataclass(frozen=True)
class Segment:
    osm_way_id: int
    coords: tuple[tuple[float, float], ...]
    tags: dict[str, str]


def split_road(
    road: RoadWay, intersections: Set[int], max_length_m: float = MAX_SEGMENT_LENGTH_M
) -> list[Segment]:
    segments: list[Segment] = []
    start = 0
    piece_length_m = 0.0
    last = len(road.node_ids) - 1
    for index in range(1, last + 1):
        piece_length_m += step_length_m(road.coords[index - 1], road.coords[index])
        at_junction = road.node_ids[index] in intersections
        if index == last or at_junction or piece_length_m >= max_length_m:
            piece = road.coords[start : index + 1]
            # Ways can repeat a coordinate; a piece needs two distinct points.
            if len(set(piece)) >= 2:
                segments.append(Segment(osm_way_id=road.osm_id, coords=piece, tags=road.tags))
            start = index
            piece_length_m = 0.0
    return segments


def split_roads(roads: Iterable[RoadWay], intersections: Set[int]) -> list[Segment]:
    return [segment for road in roads for segment in split_road(road, intersections)]


def intersection_coords(
    roads: Iterable[RoadWay], intersections: Set[int]
) -> list[tuple[float, float]]:
    """Return one lon/lat per intersection node."""
    found: dict[int, tuple[float, float]] = {}
    for road in roads:
        for node_id, coord in zip(road.node_ids, road.coords, strict=True):
            if node_id in intersections:
                found.setdefault(node_id, coord)
    return list(found.values())

"""Reads the roads and scenic features the pipeline needs from an .osm.pbf file."""

import logging
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import osmium
import shapely
from shapely.geometry.base import BaseGeometry

from pipeline.osm_tags import (
    KEPT_ROAD_TAGS,
    is_green_area,
    is_rideable,
    is_viewpoint,
    is_water_area,
    is_water_line,
    place_category,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RoadWay:
    """One rideable OSM way, with the node ids needed to find intersections."""

    osm_id: int
    node_ids: tuple[int, ...]
    coords: tuple[tuple[float, float], ...]
    tags: dict[str, str]


@dataclass(frozen=True)
class Place:
    """A named stop such as a cafe. Mapped as a node, or as a building we take the centre of."""

    category: str
    name: str
    lon: float
    lat: float


@dataclass
class OsmExtract:
    """Everything the pipeline reads from OSM. Geometries are in lon/lat."""

    roads: list[RoadWay] = field(default_factory=list)
    intersections: set[int] = field(default_factory=set)
    water: list[BaseGeometry] = field(default_factory=list)
    green: list[BaseGeometry] = field(default_factory=list)
    viewpoints: list[tuple[float, float]] = field(default_factory=list)
    places: list[Place] = field(default_factory=list)


def read_extract(path: Path) -> OsmExtract:
    extract = OsmExtract(intersections=find_intersections(path))
    wkb = osmium.geom.WKBFactory()
    skipped = 0

    # Untagged objects (most nodes) are only needed for their locations, which
    # the location handler stores before the filter drops them.
    objects = (
        osmium.FileProcessor(str(path))
        .with_locations()
        .with_areas()
        .with_filter(osmium.filter.EmptyTagFilter())
    )
    for obj in objects:
        tags = obj.tags
        try:
            category = place_category(tags)
            if category and "name" in tags and (obj.is_node() or obj.is_area()):
                extract.places.append(_place(obj, category, wkb))
            if obj.is_node():
                if is_viewpoint(tags):
                    extract.viewpoints.append((obj.location.lon, obj.location.lat))
            elif obj.is_way():
                if "highway" in tags and is_rideable(tags):
                    road = _road_from_way(obj)
                    if road is not None:
                        extract.roads.append(road)
                elif is_water_line(tags):
                    extract.water.append(shapely.from_wkb(wkb.create_linestring(obj)))
            elif obj.is_area():
                if is_water_area(tags):
                    extract.water.append(shapely.from_wkb(wkb.create_multipolygon(obj)))
                elif is_green_area(tags):
                    extract.green.append(shapely.from_wkb(wkb.create_multipolygon(obj)))
        except (RuntimeError, osmium.InvalidLocationError):
            # Broken geometry in the source data, e.g. a way cut off by the extract edge.
            skipped += 1

    logger.info(
        "osm.read roads=%d intersections=%d water=%d green=%d viewpoints=%d places=%d skipped=%d",
        len(extract.roads),
        len(extract.intersections),
        len(extract.water),
        len(extract.green),
        len(extract.viewpoints),
        len(extract.places),
        skipped,
    )
    return extract


def _place(obj, category: str, wkb: osmium.geom.WKBFactory) -> Place:
    if obj.is_node():
        lon, lat = obj.location.lon, obj.location.lat
    else:
        centre = shapely.from_wkb(wkb.create_multipolygon(obj)).centroid
        lon, lat = centre.x, centre.y
    return Place(category=category, name=obj.tags["name"], lon=lon, lat=lat)


def find_intersections(path: Path) -> set[int]:
    """Return ids of nodes shared by two or more rideable ways."""
    ways_per_node: Counter[int] = Counter()
    for way in osmium.FileProcessor(str(path), osmium.osm.WAY):
        if "highway" in way.tags and is_rideable(way.tags):
            ways_per_node.update({node.ref for node in way.nodes})
    return {node for node, count in ways_per_node.items() if count >= 2}


def _road_from_way(way: osmium.osm.Way) -> RoadWay | None:
    nodes = [node for node in way.nodes if node.location.valid()]
    if len(nodes) < 2:
        return None
    return RoadWay(
        osm_id=way.id,
        node_ids=tuple(node.ref for node in nodes),
        coords=tuple((node.lon, node.lat) for node in nodes),
        tags={key: way.tags[key] for key in KEPT_ROAD_TAGS if key in way.tags},
    )

"""Reads the roads and scenic features the pipeline needs from an .osm.pbf file."""

import logging
import math
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import osmium
import shapely
from shapely.geometry.base import BaseGeometry

from pipeline.projection import METRES_PER_DEGREE_LAT, METRES_PER_DEGREE_LON_AT_EQUATOR
from pipeline.osm_tags import (
    KEPT_ROAD_TAGS,
    is_green_area,
    is_rideable,
    is_viewpoint,
    is_water_area,
    is_context_place,
    is_named_peak,
    is_water_line,
    KEPT_PLACE_TAGS,
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
    """A named place: a node, the centre of an area, or the middle of a canal."""

    category: str
    name: str
    lon: float
    lat: float
    tags: dict[str, str]
    area_m2: float | None = None
    # Outline of a park, used to say which park other places are in.
    shape: BaseGeometry | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class NamedPoint:
    """A neighbourhood or hill, used to describe where places are."""

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
    neighbourhoods: list[NamedPoint] = field(default_factory=list)
    peaks: list[NamedPoint] = field(default_factory=list)


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
            if category and "name" in tags:
                place = _place(obj, category, wkb)
                if place is not None:
                    extract.places.append(place)
            if obj.is_node():
                if is_context_place(tags):
                    extract.neighbourhoods.append(_named_point(obj))
                elif is_named_peak(tags):
                    extract.peaks.append(_named_point(obj))
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
        "osm.read roads=%d intersections=%d water=%d green=%d viewpoints=%d places=%d "
        "neighbourhoods=%d skipped=%d",
        len(extract.roads),
        len(extract.intersections),
        len(extract.water),
        len(extract.green),
        len(extract.viewpoints),
        len(extract.places),
        len(extract.neighbourhoods),
        skipped,
    )
    return extract


def _place(obj, category: str, wkb: osmium.geom.WKBFactory) -> Place | None:
    """Locate a place. Ways only count as canals; other places are nodes or areas."""
    tags = {key: obj.tags[key] for key in KEPT_PLACE_TAGS if key in obj.tags}
    area_m2 = None
    outline = None
    if obj.is_node():
        lon, lat = obj.location.lon, obj.location.lat
    elif obj.is_area():
        shape = shapely.from_wkb(wkb.create_multipolygon(obj))
        centre = shape.point_on_surface()
        lon, lat = centre.x, centre.y
        area_m2 = shape.area * _square_metres_per_degree(lat)
        outline = shape if category == "park" else None
    elif obj.is_way() and obj.tags.get("waterway") == "canal":
        line = shapely.from_wkb(wkb.create_linestring(obj))
        middle = line.interpolate(0.5, normalized=True)
        lon, lat = middle.x, middle.y
    else:
        return None
    return Place(category, obj.tags["name"], lon, lat, tags, area_m2, outline)


def _named_point(node: osmium.osm.Node) -> NamedPoint:
    return NamedPoint(node.tags["name"], node.location.lon, node.location.lat)


def _square_metres_per_degree(lat: float) -> float:
    """Rough conversion for areas measured in lon/lat degrees, fine for size filters."""
    return METRES_PER_DEGREE_LAT * METRES_PER_DEGREE_LON_AT_EQUATOR * math.cos(math.radians(lat))


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

"""Decides from OSM tags what counts as a rideable road, water, or green space.

Kept separate from the file reader so the rules can be unit tested with
plain dicts and read in one place.
"""

from collections.abc import Mapping

# Roads and paths a bike can normally use.
RIDEABLE_HIGHWAYS = frozenset(
    {
        "cycleway",
        "path",
        "track",
        "living_street",
        "residential",
        "service",
        "unclassified",
        "tertiary",
        "tertiary_link",
        "secondary",
        "secondary_link",
        "primary",
        "primary_link",
        "trunk",
        "trunk_link",
    }
)
# Paths that are only rideable when bikes are explicitly allowed.
SHARED_PATH_HIGHWAYS = frozenset({"footway", "pedestrian", "bridleway"})
# Service roads that are parking lots and driveways, not streets.
IGNORED_SERVICE = frozenset({"parking_aisle", "driveway", "drive-through"})
BICYCLE_ALLOWED = frozenset({"yes", "designated", "permissive"})

WATER_LINE_WATERWAYS = frozenset({"river", "canal"})
GREEN_LEISURE = frozenset({"park", "nature_reserve", "garden"})
GREEN_LANDUSE = frozenset({"forest", "meadow", "cemetery"})
GREEN_NATURAL = frozenset({"wood", "scrub", "grassland", "heath", "wetland"})

# The only tags later stages read, so road ways stay small in memory.
KEPT_ROAD_TAGS = (
    "highway",
    "name",
    "surface",
    "maxspeed",
    "bicycle",
    "cycleway",
    "cycleway:both",
    "cycleway:left",
    "cycleway:right",
)


def is_rideable(tags: Mapping[str, str]) -> bool:
    highway = tags.get("highway")
    bicycle = tags.get("bicycle")
    if bicycle in ("no", "dismount") or tags.get("area") == "yes":
        return False
    if highway in SHARED_PATH_HIGHWAYS:
        return bicycle in BICYCLE_ALLOWED
    if highway not in RIDEABLE_HIGHWAYS:
        return False
    if highway == "service" and tags.get("service") in IGNORED_SERVICE:
        return False
    if tags.get("access") in ("no", "private") and bicycle not in BICYCLE_ALLOWED:
        return False
    return True


def is_water_area(tags: Mapping[str, str]) -> bool:
    return (
        tags.get("natural") == "water"
        or tags.get("waterway") == "riverbank"
        or tags.get("landuse") == "reservoir"
    )


def is_water_line(tags: Mapping[str, str]) -> bool:
    """Rivers and canals drawn as lines. Streams are skipped: many run in culverts."""
    return tags.get("waterway") in WATER_LINE_WATERWAYS


def is_green_area(tags: Mapping[str, str]) -> bool:
    return (
        tags.get("leisure") in GREEN_LEISURE
        or tags.get("landuse") in GREEN_LANDUSE
        or tags.get("natural") in GREEN_NATURAL
    )


def is_viewpoint(tags: Mapping[str, str]) -> bool:
    return tags.get("tourism") == "viewpoint"


# Places a rider might stop at, keyed by the category RouteSpec uses.
PLACE_CATEGORIES: dict[str, tuple[str, str]] = {
    "cafe": ("amenity", "cafe"),
}


def place_category(tags: Mapping[str, str]) -> str | None:
    for category, (key, value) in PLACE_CATEGORIES.items():
        if tags.get(key) == value:
            return category
    return None

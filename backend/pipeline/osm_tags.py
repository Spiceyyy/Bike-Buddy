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


# Places a rider might ride to, past or stop at. search_places looks them up by
# meaning; cafe stops use the "cafe" category directly.
PLACE_WATER_TYPES = frozenset({"lake", "pond", "reservoir", "basin", "lagoon", "river", "canal"})
LANDMARK_TOURISM = frozenset({"museum", "attraction"})
LANDMARK_HISTORIC = frozenset({"monument", "castle", "fort", "ruins", "manor", "building", "church"})
CONTEXT_PLACE_TYPES = frozenset({"neighbourhood", "suburb", "quarter"})

# Tags read when describing a place, so they stay small in memory.
KEPT_PLACE_TAGS = (
    "amenity", "leisure", "tourism", "historic", "natural", "water", "waterway",
    "man_made", "cuisine", "outdoor_seating", "description",
)


def place_category(tags: Mapping[str, str]) -> str | None:
    """The kind of place, or None if it is not one a rider would look for."""
    if tags.get("amenity") == "cafe":
        return "cafe"
    if tags.get("tourism") == "viewpoint":
        return "viewpoint"
    if tags.get("leisure") in GREEN_LEISURE:
        return "park"
    if tags.get("natural") == "beach":
        return "beach"
    if tags.get("natural") == "water" and tags.get("water", "lake") in PLACE_WATER_TYPES:
        return "water"
    if tags.get("waterway") == "canal":
        return "water"
    if (
        tags.get("tourism") in LANDMARK_TOURISM
        or tags.get("historic") in LANDMARK_HISTORIC
        or tags.get("man_made") == "lighthouse"
    ):
        return "landmark"
    return None


def is_context_place(tags: Mapping[str, str]) -> bool:
    """Neighbourhood names, used to say where a place is."""
    return tags.get("place") in CONTEXT_PLACE_TYPES and "name" in tags


def is_named_peak(tags: Mapping[str, str]) -> bool:
    return tags.get("natural") == "peak" and "name" in tags

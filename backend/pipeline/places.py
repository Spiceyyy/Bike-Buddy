"""Turns OSM places into rows with a short description, for search_places.

The description is what gets embedded, so it says in plain words what a
place is and what is around it: "Parc La Fontaine. Large park (34 ha) in
Le Plateau-Mont-Royal, by the water." The context (neighbourhood, nearby
water, green space, hills) is computed here from the same OSM extract.
"""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import shapely

from pipeline.osm_reader import NamedPoint, OsmExtract, Place
from pipeline.projection import LocalProjection

# Below these sizes OSM areas are mostly planters, lawns and fountains.
MIN_PARK_HA = 0.5
MIN_WATER_HA = 1.0
LARGE_PARK_HA = 20.0
MIN_EMBEDDED_PARK_HA = 2.0

# Names that only say what a place is ("Belvédère" means lookout). A rider
# cannot be told "the route passes Belvédère", and their short descriptions
# match generic queries so closely that they outranked Belvédère Kondiaronk.
GENERIC_NAMES = frozenset(
    {
        "belvédère", "belvedere", "halte", "espace vert", "plage", "parc", "park", "lac",
        "étang", "observatoire", "viewpoint", "lookout", "point de vue", "beach", "jardin",
    }
)

NEAR_WATER_M = 100.0
IN_GREEN_M = 30.0
NEIGHBOURHOOD_WITHIN_M = 2_000.0
PEAK_WITHIN_M = 1_000.0
# The same name and category this close together is one place mapped twice.
DUPLICATE_WITHIN_M = 300.0

PARK_KINDS = {"park": "park", "nature_reserve": "nature reserve", "garden": "garden"}
WATER_KINDS = {
    "canal": "canal",
    "river": "river",
    "lake": "lake",
    "pond": "pond",
    "reservoir": "basin",
    "basin": "basin",
    "lagoon": "lagoon",
}
HISTORIC_KINDS = {
    "monument": "historic monument",
    "castle": "historic castle",
    "fort": "historic fort",
    "ruins": "historic ruins",
    "manor": "historic manor",
    "church": "historic church",
    "building": "historic building",
}


@dataclass(frozen=True)
class PlaceContext:
    neighbourhood: str | None = None
    peak: str | None = None
    near_water: bool = False
    in_green: bool = False
    park: str | None = None
    """The largest named park the place is inside, if any."""


@dataclass(frozen=True)
class PlaceRow:
    category: str
    name: str
    description: str
    lon: float
    lat: float
    area_ha: float | None


def build_place_rows(extract: OsmExtract) -> list[PlaceRow]:
    places = deduplicate(
        [place for place in extract.places if big_enough(place) and has_real_name(place)]
    )
    if not places:
        return []
    contexts = _contexts(places, extract)
    return [
        PlaceRow(
            category=place.category,
            name=place.name,
            description=describe(place, context),
            lon=place.lon,
            lat=place.lat,
            area_ha=round(place.area_m2 / 10_000, 2) if place.area_m2 else None,
        )
        for place, context in zip(places, contexts)
    ]


def worth_embedding(row: PlaceRow) -> bool:
    """Whether search_places should find this place by meaning, not just by name.

    Cafés are found by category for stops, and parks under 2 ha are rarely
    somewhere a rider sets out to reach. Skipping both cuts the places sent
    to the embedding API from about 3,400 to about 1,200.
    """
    if row.category == "cafe":
        return False
    if row.category == "park":
        return (row.area_ha or 0.0) >= MIN_EMBEDDED_PARK_HA
    return True


def has_real_name(place: Place) -> bool:
    return place.name.strip().casefold() not in GENERIC_NAMES


def big_enough(place: Place) -> bool:
    if place.area_m2 is None:
        return True
    hectares = place.area_m2 / 10_000
    if place.category == "park":
        return hectares >= MIN_PARK_HA
    if place.category == "water":
        return hectares >= MIN_WATER_HA
    return True


def deduplicate(places: Sequence[Place]) -> list[Place]:
    """Keep the largest of each same-name, same-category group within DUPLICATE_WITHIN_M."""
    groups: dict[tuple[str, str], list[Place]] = defaultdict(list)
    for place in places:
        groups[(place.category, place.name.casefold())].append(place)

    kept: list[Place] = []
    for group in groups.values():
        group.sort(key=lambda place: place.area_m2 or 0.0, reverse=True)
        chosen: list[Place] = []
        for place in group:
            if all(_metres_apart(place, other) > DUPLICATE_WITHIN_M for other in chosen):
                chosen.append(place)
        kept.extend(chosen)
    return kept


def describe(place: Place, context: PlaceContext) -> str:
    """One or two plain sentences about the place, for embedding and for the LLM."""
    parts = [_kind(place)]
    if context.neighbourhood:
        parts.append(f"in {context.neighbourhood}")
    if context.peak:
        parts.append(f"near {context.peak}")
    if context.near_water and place.category != "water":
        parts.append("by the water")
    if context.park:
        parts.append(f"in {context.park}")
    elif context.in_green and place.category != "park":
        parts.append("in a park")
    sentence = f"{place.name}. {_capitalize(' '.join(parts[:1]))}"
    if len(parts) > 1:
        sentence += " " + ", ".join(parts[1:])
    sentence += "."
    note = place.tags.get("description")
    if note:
        sentence += f" {note.strip().rstrip('.')}."
    return sentence


def _kind(place: Place) -> str:
    tags = place.tags
    hectares = place.area_m2 / 10_000 if place.area_m2 else None
    if place.category == "cafe":
        kind = "café"
        cuisine = tags.get("cuisine", "").replace("_", " ").replace(";", ", ")
        if cuisine and cuisine != "coffee shop":
            kind += f" serving {cuisine}"
        if tags.get("outdoor_seating") == "yes":
            kind += " with outdoor seating"
        return kind
    if place.category == "viewpoint":
        return "viewpoint, a lookout with a view"
    if place.category == "park":
        kind = PARK_KINDS.get(tags.get("leisure", ""), "park")
        if hectares and hectares >= LARGE_PARK_HA:
            kind = f"large {kind}"
        return f"{kind} ({hectares:.0f} ha)" if hectares and hectares >= 1 else kind
    if place.category == "water":
        if tags.get("waterway") == "canal":
            return "canal"
        return WATER_KINDS.get(tags.get("water", "lake"), "lake")
    if place.category == "beach":
        return "beach"
    if tags.get("tourism") == "museum":
        return "museum"
    if tags.get("man_made") == "lighthouse":
        return "lighthouse"
    if tags.get("historic") in HISTORIC_KINDS:
        return HISTORIC_KINDS[tags["historic"]]
    return "tourist attraction"


def _contexts(places: Sequence[Place], extract: OsmExtract) -> list[PlaceContext]:
    lons = [place.lon for place in places]
    lats = [place.lat for place in places]
    projection = LocalProjection(
        origin_lon=(min(lons) + max(lons)) / 2, origin_lat=(min(lats) + max(lats)) / 2
    )
    points = projection.to_metres(np.array([shapely.Point(p.lon, p.lat) for p in places]))

    near_water = _within(points, projection, extract.water, NEAR_WATER_M)
    in_green = _within(points, projection, extract.green, IN_GREEN_M)
    neighbourhoods = _nearest_names(points, projection, extract.neighbourhoods, NEIGHBOURHOOD_WITHIN_M)
    peaks = _nearest_names(points, projection, extract.peaks, PEAK_WITHIN_M)
    parks = _containing_parks(places)
    return [
        PlaceContext(neighbourhoods[i], peaks[i], bool(near_water[i]), bool(in_green[i]), parks[i])
        for i in range(len(places))
    ]


def _containing_parks(places: Sequence[Place]) -> list[str | None]:
    """For each place, the name of the largest other park whose outline contains it."""
    parks = [place for place in places if place.shape is not None]
    names: list[str | None] = [None] * len(places)
    if not parks:
        return names
    tree = shapely.STRtree([park.shape for park in parks])
    points = [shapely.Point(place.lon, place.lat) for place in places]
    point_index, park_index = tree.query(points, predicate="within")
    best_area: dict[int, float] = {}
    for i, j in zip(point_index, park_index):
        park = parks[j]
        if park.name.casefold() == places[i].name.casefold():
            continue
        if (park.area_m2 or 0.0) > best_area.get(i, -1.0):
            best_area[i] = park.area_m2 or 0.0
            names[i] = park.name
    return names


def _within(points: np.ndarray, projection: LocalProjection, geometries: list, metres: float) -> np.ndarray:
    hits = np.zeros(len(points), dtype=bool)
    if not geometries:
        return hits
    tree = shapely.STRtree(projection.to_metres(np.array(geometries, dtype=object)))
    point_index, _ = tree.query(points, predicate="dwithin", distance=metres)
    hits[point_index] = True
    return hits


def _nearest_names(
    points: np.ndarray, projection: LocalProjection, named: Sequence[NamedPoint], metres: float
) -> list[str | None]:
    names: list[str | None] = [None] * len(points)
    if not named:
        return names
    tree = shapely.STRtree(
        projection.to_metres(np.array([shapely.Point(n.lon, n.lat) for n in named]))
    )
    point_index, named_index = tree.query_nearest(points, max_distance=metres, all_matches=False)
    for i, j in zip(point_index, named_index):
        names[i] = named[j].name
    return names


def _metres_apart(a: Place, b: Place) -> float:
    projection = LocalProjection(origin_lon=a.lon, origin_lat=a.lat)
    return projection.to_metres(shapely.Point(a.lon, a.lat)).distance(
        projection.to_metres(shapely.Point(b.lon, b.lat))
    )


def _capitalize(text: str) -> str:
    return text[:1].upper() + text[1:]

"""Which OSM objects become places, and how they are described. No OSM file needed."""

import pytest

from pipeline.osm_reader import Place
from pipeline.osm_tags import place_category
from pipeline.places import (
    PlaceContext,
    PlaceRow,
    big_enough,
    deduplicate,
    describe,
    has_real_name,
    worth_embedding,
)


def place(category: str, name: str = "X", area_m2: float | None = None, **tags: str) -> Place:
    return Place(category, name, -73.6, 45.5, tags, area_m2)


@pytest.mark.parametrize(
    ("tags", "category"),
    [
        ({"amenity": "cafe"}, "cafe"),
        ({"tourism": "viewpoint"}, "viewpoint"),
        ({"leisure": "park"}, "park"),
        ({"leisure": "nature_reserve"}, "park"),
        ({"natural": "beach"}, "beach"),
        ({"natural": "water", "water": "lake"}, "water"),
        ({"natural": "water"}, "water"),
        ({"waterway": "canal"}, "water"),
        ({"tourism": "museum"}, "landmark"),
        ({"historic": "monument"}, "landmark"),
        ({"natural": "water", "water": "wastewater"}, None),
        ({"historic": "memorial"}, None),
        ({"amenity": "restaurant"}, None),
    ],
)
def test_place_categories(tags: dict[str, str], category: str | None) -> None:
    assert place_category(tags) == category


def test_park_description_gives_size_and_surroundings() -> None:
    park = place("park", "Parc La Fontaine", area_m2=360_000, leisure="park")

    text = describe(park, PlaceContext(neighbourhood="Le Plateau", near_water=True))

    assert text == "Parc La Fontaine. Large park (36 ha) in Le Plateau, by the water."


def test_viewpoint_description_names_the_park_it_is_in() -> None:
    lookout = place("viewpoint", "Belvédère Kondiaronk", tourism="viewpoint")

    text = describe(lookout, PlaceContext(park="Parc du Mont-Royal", in_green=True))

    assert text == "Belvédère Kondiaronk. Viewpoint, a lookout with a view in Parc du Mont-Royal."


def test_cafe_description_mentions_terrace_and_osm_description() -> None:
    cafe = place(
        "cafe", "Café Y", amenity="cafe", outdoor_seating="yes", description="Roastery."
    )

    assert describe(cafe, PlaceContext()) == "Café Y. Café with outdoor seating. Roastery."


def test_small_parks_and_ponds_are_dropped() -> None:
    assert not big_enough(place("park", area_m2=2_000))
    assert not big_enough(place("water", area_m2=5_000))
    assert big_enough(place("park", area_m2=6_000))
    assert big_enough(place("cafe"))  # nodes have no area


def test_only_places_worth_searching_by_meaning_are_embedded() -> None:
    def row(category: str, area_ha: float | None = None) -> PlaceRow:
        return PlaceRow(category, "X", "X.", -73.6, 45.5, area_ha)

    assert worth_embedding(row("viewpoint"))
    assert worth_embedding(row("park", 2.5))
    assert not worth_embedding(row("park", 1.0))
    assert not worth_embedding(row("park"))
    assert not worth_embedding(row("cafe"))


def test_places_named_only_by_their_kind_are_dropped() -> None:
    assert not has_real_name(place("viewpoint", "Belvédère"))
    assert not has_real_name(place("viewpoint", " halte "))
    assert has_real_name(place("viewpoint", "Belvédère Kondiaronk"))


def test_duplicates_keep_the_largest_but_distant_namesakes_stay() -> None:
    big = place("park", "Parc A", area_m2=50_000)
    small = place("park", "parc a", area_m2=1_000)  # same place mapped twice
    far = Place("park", "Parc A", -73.5, 45.5, {}, 8_000)  # about 8 km east

    kept = deduplicate([small, big, far])

    assert big in kept and far in kept and small not in kept

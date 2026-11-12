import json

import numpy as np
import pytest
import shapely

from pipeline.scenic_zones import (
    ScenicZones,
    ZoneSettings,
    build_zones,
    custom_model,
    write_custom_model,
)

SETTINGS = ZoneSettings(cell_size_m=100)
# About 100 m east of the origin at Montreal's latitude.
LON_100M = 100 / (111_320 * np.cos(np.radians(45.5)))


def cell_midpoints(*columns: int) -> np.ndarray:
    """One segment midpoint per given grid column, on one row.

    The grid starts at the westernmost midpoint, so column 0 sits on the grid's
    edge and later columns sit mid-cell, away from boundaries.
    """
    return np.array(
        [(-73.6 + (column + (0.5 if column else 0.0)) * LON_100M, 45.5) for column in columns]
    )


def test_cells_take_the_tier_of_their_length_weighted_score() -> None:
    zones = build_zones(
        cell_midpoints(0, 1, 2),
        lengths_m=np.array([100.0, 100.0, 100.0]),
        scores=np.array([0.9, 0.45, 0.1]),
        settings=SETTINGS,
    )
    assert zones.cell_count == 3
    assert zones.scenic.covers(shapely.Point(cell_midpoints(0)[0]))
    assert zones.pleasant.covers(shapely.Point(cell_midpoints(1)[0]))
    assert not zones.scenic.covers(shapely.Point(cell_midpoints(2)[0]))
    assert not zones.pleasant.covers(shapely.Point(cell_midpoints(2)[0]))


def test_long_dull_segment_outweighs_short_scenic_one_in_same_cell() -> None:
    zones = build_zones(
        cell_midpoints(0, 0),
        lengths_m=np.array([10.0, 300.0]),
        scores=np.array([1.0, 0.2]),
        settings=SETTINGS,
    )
    assert zones.scenic.is_empty and zones.pleasant.is_empty


def test_neighbouring_scenic_cells_merge_into_one_polygon() -> None:
    zones = build_zones(
        cell_midpoints(0, 1, 2),
        lengths_m=np.ones(3),
        scores=np.full(3, 0.9),
        settings=SETTINGS,
    )
    assert zones.scenic.geom_type == "Polygon"


def test_custom_model_penalises_roads_outside_the_zones() -> None:
    zones = ScenicZones(
        scenic=shapely.box(0, 0, 1, 1), pleasant=shapely.box(1, 0, 2, 1), cell_count=2
    )
    model = custom_model(zones, ZoneSettings(pleasant_priority=0.85, other_priority=0.7))

    assert model["priority"] == [
        {"if": "in_scenic", "multiply_by": "1.0"},
        {"else_if": "in_pleasant", "multiply_by": "0.85"},
        {"else": "", "multiply_by": "0.7"},
    ]
    assert [feature["id"] for feature in model["areas"]["features"]] == ["scenic", "pleasant"]


def test_every_multiplier_is_at_most_one_as_graphhopper_requires() -> None:
    zones = ScenicZones(
        scenic=shapely.box(0, 0, 1, 1), pleasant=shapely.box(1, 0, 2, 1), cell_count=2
    )
    for rule in custom_model(zones)["priority"]:
        assert float(rule["multiply_by"]) <= 1.0


def test_missing_zone_is_left_out_of_rules_and_areas() -> None:
    zones = ScenicZones(
        scenic=shapely.Polygon(), pleasant=shapely.box(1, 0, 2, 1), cell_count=1
    )
    model = custom_model(zones)
    assert model["priority"][0]["if"] == "in_pleasant"
    assert [feature["id"] for feature in model["areas"]["features"]] == ["pleasant"]


def test_no_zones_gives_a_model_that_changes_nothing() -> None:
    zones = ScenicZones(scenic=shapely.Polygon(), pleasant=shapely.Polygon(), cell_count=0)
    assert custom_model(zones) == {"priority": []}


def test_coordinates_are_rounded_and_file_is_valid_json(tmp_path) -> None:
    zones = ScenicZones(
        scenic=shapely.box(-73.123456789, 45.0, -73.0, 45.1),
        pleasant=shapely.Polygon(),
        cell_count=1,
    )
    path = tmp_path / "scenic" / "Montreal.json"
    write_custom_model(path, custom_model(zones))

    written = json.loads(path.read_text(encoding="utf-8"))
    ring = written["areas"]["features"][0]["geometry"]["coordinates"][0]
    assert all(lon == pytest.approx(round(lon, 6), abs=0) for lon, _ in ring)
    assert min(lon for lon, _ in ring) == -73.123457

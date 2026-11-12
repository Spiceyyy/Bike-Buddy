"""Segmenting, OSM tag rules, and geometric features, on small hand-made data."""

import numpy as np
import pytest
import shapely

from pipeline.features import (
    count_near,
    drop_small_areas,
    measure,
    sample_along,
    share_near,
    subdivide,
)
from pipeline.osm_reader import OsmExtract, RoadWay
from pipeline.osm_tags import is_green_area, is_rideable, is_water_area
from pipeline.projection import LocalProjection, step_length_m
from pipeline.run import score_segments
from pipeline.segments import intersection_coords, split_road, split_roads

# About 0.0013 degrees of longitude is 100 m at Montreal's latitude.
LON_100M = 100 / (111_320 * np.cos(np.radians(45.5)))


def road(node_ids: list[int], tags: dict[str, str] | None = None, osm_id: int = 1) -> RoadWay:
    coords = tuple((-73.6 + i * LON_100M, 45.5) for i in range(len(node_ids)))
    return RoadWay(
        osm_id=osm_id,
        node_ids=tuple(node_ids),
        coords=coords,
        tags=tags or {"highway": "residential"},
    )


# Segments


def test_splits_road_at_intersections_only() -> None:
    segments = split_road(road([1, 2, 3, 4, 5]), intersections={3})
    assert [len(segment.coords) for segment in segments] == [3, 3]


def test_road_with_no_junctions_stays_whole_when_short() -> None:
    assert len(split_road(road([1, 2, 3]), intersections=set())) == 1


def test_long_road_is_cut_at_max_length() -> None:
    # 10 nodes 100 m apart: 900 m, cut once a piece reaches 390 m (every 4 steps).
    segments = split_road(road(list(range(10))), intersections=set(), max_length_m=390)
    assert [len(segment.coords) for segment in segments] == [5, 5, 2]


def test_skips_pieces_with_one_distinct_point() -> None:
    duplicated = RoadWay(
        osm_id=1,
        node_ids=(1, 2, 3),
        coords=((-73.6, 45.5), (-73.6, 45.5), (-73.599, 45.5)),
        tags={"highway": "residential"},
    )
    segments = split_road(duplicated, intersections={2})
    assert len(segments) == 1


def test_intersection_coords_returns_each_node_once() -> None:
    roads = [road([1, 2, 3], osm_id=1), road([3, 4], osm_id=2)]
    assert len(intersection_coords(roads, {3})) == 1


def test_step_length_matches_known_distance() -> None:
    assert step_length_m((-73.6, 45.5), (-73.6 + LON_100M, 45.5)) == pytest.approx(100, rel=1e-3)


# OSM tag rules


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        ({"highway": "cycleway"}, True),
        ({"highway": "residential"}, True),
        ({"highway": "motorway"}, False),
        ({"highway": "footway"}, False),
        ({"highway": "footway", "bicycle": "yes"}, True),
        ({"highway": "residential", "bicycle": "no"}, False),
        ({"highway": "service", "service": "parking_aisle"}, False),
        ({"highway": "service", "access": "private"}, False),
        ({"highway": "service", "access": "private", "bicycle": "yes"}, True),
        ({"highway": "pedestrian", "area": "yes", "bicycle": "yes"}, False),
    ],
)
def test_rideable_rules(tags: dict[str, str], expected: bool) -> None:
    assert is_rideable(tags) is expected


def test_water_and_green_rules() -> None:
    assert is_water_area({"natural": "water"})
    assert not is_water_area({"natural": "wood"})
    assert is_green_area({"leisure": "park"})
    assert is_green_area({"landuse": "cemetery"})
    assert not is_green_area({"landuse": "grass"})


# Geometric features (coordinates in metres)


def test_samples_include_both_ends_and_respect_spacing() -> None:
    lines = np.array([shapely.LineString([(0, 0), (100, 0)])])
    samples = sample_along(lines, spacing_m=25)
    xs = shapely.get_x(samples.points)
    np.testing.assert_allclose(xs, [0, 25, 50, 75, 100])


def test_share_near_measures_fraction_of_segment_close_to_target() -> None:
    # Water lies alongside the first 50 m of a 100 m segment.
    lines = np.array([shapely.LineString([(0, 0), (100, 0)])])
    water = [shapely.box(0, 10, 50, 20)]
    # Samples at x = 0, 10, ..., 100. Those at x <= 50 are 10 m away; x = 60 is 14 m.
    share = share_near(sample_along(lines, 10), water, distance_m=12)
    assert share[0] == pytest.approx(6 / 11)


def test_share_near_is_zero_without_targets() -> None:
    lines = np.array([shapely.LineString([(0, 0), (100, 0)])])
    assert share_near(sample_along(lines, 25), [], distance_m=100)[0] == 0


def test_count_near_counts_targets_in_radius() -> None:
    points = np.array([shapely.Point(0, 0), shapely.Point(1000, 0)])
    targets = [shapely.Point(10, 0), shapely.Point(0, 50), shapely.Point(500, 0)]
    np.testing.assert_array_equal(count_near(points, targets, radius_m=100), [2, 0])


def test_drops_planter_sized_areas_but_keeps_parks_and_rivers() -> None:
    planter = shapely.box(0, 0, 10, 10)
    park = shapely.box(0, 0, 100, 100)
    river = shapely.LineString([(0, 0), (1000, 0)])
    assert drop_small_areas([planter, park, river], min_area_m2=5000) == [park, river]


def test_small_garden_beside_street_does_not_count_as_green() -> None:
    lines = np.array([shapely.LineString([(0, 0), (100, 0)])])
    planter = shapely.box(40, 5, 50, 10)
    features = measure(lines, water=[], green=[planter], viewpoints=[], intersections=[])
    assert features.green_share[0] == 0.0


def test_subdivide_keeps_area_and_limits_tile_size() -> None:
    big = shapely.box(0, 0, 3500, 1200)
    tiles = subdivide([big], tile_size_m=1000)
    assert sum(tile.area for tile in tiles) == pytest.approx(big.area)
    for tile in tiles:
        min_x, min_y, max_x, max_y = tile.bounds
        assert max_x - min_x <= 1000 and max_y - min_y <= 1000


def test_measure_combines_all_features() -> None:
    lines = np.array([shapely.LineString([(0, 0), (100, 0)])])
    features = measure(
        lines,
        water=[shapely.box(-50, 20, 150, 60)],
        green=[],
        viewpoints=[shapely.Point(50, 150)],
        intersections=[shapely.Point(0, 0), shapely.Point(100, 0)],
    )
    assert features.water_share[0] == 1.0
    assert features.green_share[0] == 0.0
    assert bool(features.near_viewpoint[0])
    assert features.intersections_nearby[0] == 2


def test_projection_maps_origin_to_zero_and_scales_latitude() -> None:
    projection = LocalProjection(origin_lon=-73.6, origin_lat=45.5)
    metres = projection.coords_to_metres(np.array([[-73.6, 45.5], [-73.6, 45.51]]))
    np.testing.assert_allclose(metres[0], [0, 0])
    assert metres[1, 1] == pytest.approx(1105.74)


# End to end, without a PBF file or database


def test_waterside_path_outscores_inland_arterial() -> None:
    path = road([1, 2, 3], {"highway": "cycleway", "name": "Riverside"}, osm_id=1)
    arterial = RoadWay(
        osm_id=2,
        node_ids=(4, 5, 6),
        coords=tuple((lon, 45.52) for lon, _ in path.coords),
        tags={"highway": "primary", "name": "Big Road"},
    )
    river = shapely.box(-73.61, 45.5005, -73.59, 45.502)
    extract = OsmExtract(roads=[path, arterial], water=[river])

    rows = score_segments(split_roads(extract.roads, set()), extract)

    by_name = {row.name: row for row in rows}
    assert by_name["Riverside"].water_share == 1.0
    assert by_name["Big Road"].water_share == 0.0
    assert by_name["Riverside"].scenic_score > by_name["Big Road"].scenic_score
    assert shapely.from_wkb(by_name["Riverside"].geom_wkb).coords[0] == path.coords[0]

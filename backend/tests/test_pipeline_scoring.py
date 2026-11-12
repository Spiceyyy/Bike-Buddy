import numpy as np
import pytest

from pipeline.scoring import (
    ScoringWeights,
    SegmentFeatures,
    calmness,
    parse_maxspeed_kmh,
    quietness,
    scenic_score,
    surface_quality,
)

PERFECT = SegmentFeatures(water=1, green=1, quietness=1, calmness=1, viewpoint=1, surface=1)


def test_perfect_segment_scores_one_and_empty_scores_zero() -> None:
    empty = SegmentFeatures(water=0, green=0, quietness=0, calmness=0, viewpoint=0, surface=0)
    assert scenic_score(PERFECT) == pytest.approx(1.0)
    assert scenic_score(empty) == pytest.approx(0.0)


def test_score_is_the_weighted_sum_of_features() -> None:
    features = SegmentFeatures(
        water=1, green=0.5, quietness=0.8, calmness=0, viewpoint=0, surface=1
    )
    expected = 0.30 * 1 + 0.25 * 0.5 + 0.25 * 0.8 + 0.05 * 1
    assert scenic_score(features) == pytest.approx(expected)


def test_scores_arrays_elementwise() -> None:
    features = SegmentFeatures(
        water=np.array([1.0, 0.0]),
        green=np.zeros(2),
        quietness=np.zeros(2),
        calmness=np.zeros(2),
        viewpoint=np.zeros(2),
        surface=np.zeros(2),
    )
    np.testing.assert_allclose(scenic_score(features), [0.30, 0.0])


def test_weights_must_sum_to_one() -> None:
    with pytest.raises(ValueError):
        ScoringWeights(water=0.9)


def test_riverside_cycleway_beats_arterial_road() -> None:
    assert quietness({"highway": "cycleway"}) > quietness({"highway": "primary"})


def test_fast_road_is_capped_whatever_its_class() -> None:
    assert quietness({"highway": "tertiary", "maxspeed": "80"}) == pytest.approx(0.1)


def test_separated_track_makes_busy_road_pleasant() -> None:
    assert quietness({"highway": "primary", "cycleway:right": "track"}) == pytest.approx(0.7)


def test_painted_lane_helps_a_little() -> None:
    plain = quietness({"highway": "secondary"})
    assert quietness({"highway": "secondary", "cycleway": "lane"}) == pytest.approx(plain + 0.1)


def test_quietness_never_exceeds_one() -> None:
    assert quietness({"highway": "cycleway", "maxspeed": "20", "cycleway": "lane"}) == 1.0


def test_surface_uses_tag_then_falls_back_by_road_type() -> None:
    assert surface_quality({"highway": "path", "surface": "asphalt"}) == 1.0
    assert surface_quality({"highway": "path"}) == pytest.approx(0.6)
    assert surface_quality({"highway": "residential"}) == 1.0
    assert surface_quality({"highway": "track", "surface": "sand"}) == pytest.approx(0.1)


def test_calmness_falls_with_intersections_and_bottoms_out() -> None:
    np.testing.assert_allclose(calmness(np.array([0, 10, 20, 40])), [1.0, 0.5, 0.0, 0.0])


@pytest.mark.parametrize(
    ("value", "expected"),
    [("50", 50.0), ("30 mph", 48.27), (" 40 ", 40.0), ("CA:urban", None), (None, None)],
)
def test_parses_maxspeed(value: str | None, expected: float | None) -> None:
    result = parse_maxspeed_kmh(value)
    assert result == (pytest.approx(expected) if expected is not None else None)

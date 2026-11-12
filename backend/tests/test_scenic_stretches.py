"""Scenic stretches are built from sample scores in pure Python, so no database is needed."""

import pytest

from app.models.scenic import ScenicBand, ScenicReason
from app.routing.geometry import cumulative_distances
from app.scoring.stretches import SegmentScore, build_route_scenic, sample_route
from app.tools.plan_route import summarize_scenery

# About 2 km due east along Montreal's latitude; 0.0127 degrees is close to 1 km.
LINE = [(-73.60, 45.50), (-73.5873, 45.50), (-73.5746, 45.50)]


def canal(score: float = 0.8) -> SegmentScore:
    return SegmentScore(score, water_share=1.0, green_share=0.3, quietness=1.0, name="Canal path")


def park(score: float = 0.6) -> SegmentScore:
    return SegmentScore(score, water_share=0.0, green_share=1.0, quietness=0.6, name=None)


def arterial(score: float = 0.2) -> SegmentScore:
    return SegmentScore(score, water_share=0.0, green_share=0.0, quietness=0.15, name="Rue X")


def scores_for(samples, pattern):
    """Assign each sample the score of the first pattern entry whose end distance covers it."""
    result = []
    for sample in samples:
        for end_m, score in pattern:
            if sample.distance_m < end_m:
                result.append(score)
                break
        else:
            result.append(pattern[-1][1])
    return result


def test_sample_route_spaces_points_evenly_and_ends_at_the_last_point() -> None:
    samples = sample_route(LINE, spacing_m=25)
    total = cumulative_distances(LINE)[-1]

    assert samples[0].distance_m == 0
    assert samples[1].distance_m == 25
    assert samples[-1].distance_m == pytest.approx(total)
    assert samples[-1].position == LINE[-1]


def test_route_splits_into_bands_with_reasons() -> None:
    samples = sample_route(LINE)
    scores = scores_for(samples, [(1000, canal()), (10_000, arterial())])

    scenic = build_route_scenic(LINE, samples, scores)

    first, second = scenic.stretches
    assert (first.band, first.reason, first.name) == (
        ScenicBand.SCENIC,
        ScenicReason.WATER,
        "Canal path",
    )
    assert (second.band, second.reason) == (ScenicBand.PLAIN, ScenicReason.BUSY)
    assert first.end_m == second.start_m == 1000
    assert scenic.band_shares[ScenicBand.SCENIC] == pytest.approx(0.5, abs=0.01)


def test_short_runs_are_folded_into_their_neighbours() -> None:
    samples = sample_route(LINE)
    # A 100 m bridge in the middle of a canal ride should not split it.
    scores = scores_for(samples, [(900, canal()), (1000, arterial()), (10_000, canal())])

    scenic = build_route_scenic(LINE, samples, scores)

    assert [s.band for s in scenic.stretches] == [ScenicBand.SCENIC]


def test_stretch_geometry_is_cut_at_the_boundary() -> None:
    samples = sample_route(LINE)
    scores = scores_for(samples, [(500, park()), (10_000, arterial())])

    first, second = build_route_scenic(LINE, samples, scores).stretches

    assert first.coordinates[0] == LINE[0]
    assert first.coordinates[-1] == second.coordinates[0]
    assert -73.594 < first.coordinates[-1][0] < -73.593  # about 500 m along
    assert second.coordinates[-1] == LINE[-1]


def test_park_stretch_is_explained_by_green_space() -> None:
    samples = sample_route(LINE)

    scenic = build_route_scenic(LINE, samples, [park()] * len(samples))

    assert scenic.stretches[0].reason is ScenicReason.GREEN
    assert scenic.stretches[0].name is None


def test_unmatched_samples_become_unknown_and_lower_the_matched_share() -> None:
    samples = sample_route(LINE)
    scores = scores_for(samples, [(1000, None), (10_000, canal())])

    scenic = build_route_scenic(LINE, samples, scores)

    assert scenic.stretches[0].band is ScenicBand.UNKNOWN
    assert scenic.stretches[0].score is None
    assert scenic.matched_share == pytest.approx(0.5, abs=0.02)


def test_no_matches_means_no_scenery() -> None:
    samples = sample_route(LINE)

    assert build_route_scenic(LINE, samples, [None] * len(samples)) is None


def test_summary_for_the_llm_names_only_scenic_stretches_from_the_data() -> None:
    samples = sample_route(LINE)
    scores = scores_for(samples, [(1000, canal()), (10_000, arterial())])

    summary = summarize_scenery(build_route_scenic(LINE, samples, scores))

    assert summary["highlights"] == [
        {"name": "Canal path", "why": "along the water", "from_km": 0.0, "to_km": 1.0}
    ]

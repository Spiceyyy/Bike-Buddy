"""Splits a route into scenic, pleasant and plain stretches.

The route is sampled every 25 m, each sample takes the score of the nearest
scored road segment, and runs of samples in the same band become stretches.
Runs shorter than MIN_STRETCH_M are folded into a neighbour, so one bridge or
intersection does not chop a canal ride into pieces.

Everything here is pure Python, so it is unit tested without a database.
"""

import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from app.models.scenic import RouteScenic, ScenicBand, ScenicReason, ScenicStretch
from app.routing.geometry import LonLat, cumulative_distances, point_at_distance

SAMPLE_SPACING_M = 25.0
MIN_STRETCH_M = 300.0

# The same thresholds the pipeline uses for its scenic and pleasant zones
# (backend/pipeline/scenic_zones.py), so the map agrees with how routes are steered.
SCENIC_THRESHOLD = 0.55
PLEASANT_THRESHOLD = 0.40

# Feature weights from backend/pipeline/scoring.py. Used only to pick the
# feature that contributed most to a stretch's score.
WATER_WEIGHT = 0.30
GREEN_WEIGHT = 0.25
QUIETNESS_WEIGHT = 0.25
BUSY_QUIETNESS = 0.4
# A name must cover this share of a stretch's samples to label it.
NAME_MIN_SHARE = 0.4



@dataclass(frozen=True)
class SegmentScore:
    """The scored road segment nearest to one sample point."""

    scenic_score: float
    water_share: float
    green_share: float
    quietness: float
    name: str | None


@dataclass(frozen=True)
class Sample:
    distance_m: float
    position: LonLat


@dataclass
class _Run:
    band: ScenicBand
    first: int
    """Index of the run's first sample."""
    last: int
    """Index of the run's last sample, inclusive."""
    start_m: float
    end_m: float

    @property
    def length_m(self) -> float:
        return self.end_m - self.start_m


def sample_route(
    coordinates: Sequence[LonLat], spacing_m: float = SAMPLE_SPACING_M
) -> list[Sample]:
    """Points every `spacing_m` along the line, plus its last point."""
    cumulative = cumulative_distances(coordinates)
    total = cumulative[-1]
    count = max(1, math.ceil(total / spacing_m))
    distances = [min(i * spacing_m, total) for i in range(count)] + [total]
    return [Sample(d, point_at_distance(coordinates, cumulative, d)) for d in distances]


def band_for(score: SegmentScore | None) -> ScenicBand:
    if score is None:
        return ScenicBand.UNKNOWN
    if score.scenic_score >= SCENIC_THRESHOLD:
        return ScenicBand.SCENIC
    if score.scenic_score >= PLEASANT_THRESHOLD:
        return ScenicBand.PLEASANT
    return ScenicBand.PLAIN


def build_route_scenic(
    coordinates: Sequence[LonLat],
    samples: Sequence[Sample],
    scores: Sequence[SegmentScore | None],
    min_stretch_m: float = MIN_STRETCH_M,
) -> RouteScenic | None:
    """Describe the route's scenery. None if no sample matched a scored segment."""
    if len(samples) != len(scores):
        raise ValueError("need one score per sample")
    matched = [score for score in scores if score is not None]
    if not matched:
        return None

    total_m = samples[-1].distance_m
    runs = _merge_short_runs(_initial_runs(samples, scores, total_m), min_stretch_m)
    cumulative = cumulative_distances(coordinates)
    stretches = [
        _stretch(run, scores[run.first : run.last + 1], coordinates, cumulative)
        for run in runs
    ]

    band_shares = {band: 0.0 for band in ScenicBand}
    for stretch in stretches:
        band_shares[stretch.band] += (stretch.end_m - stretch.start_m) / total_m if total_m else 0
    return RouteScenic(
        score=_mean(score.scenic_score for score in matched),
        matched_share=len(matched) / len(scores),
        band_shares={band: round(share, 3) for band, share in band_shares.items()},
        stretches=stretches,
    )


def _initial_runs(
    samples: Sequence[Sample], scores: Sequence[SegmentScore | None], total_m: float
) -> list[_Run]:
    """Group consecutive samples with the same band. Each sample covers up to the next one."""
    runs: list[_Run] = []
    for index, score in enumerate(scores):
        band = band_for(score)
        if runs and runs[-1].band is band:
            runs[-1].last = index
        else:
            if runs:
                runs[-1].end_m = samples[index].distance_m
            runs.append(_Run(band, index, index, samples[index].distance_m, total_m))
    runs[-1].end_m = total_m
    return runs


def _merge_short_runs(runs: list[_Run], min_stretch_m: float) -> list[_Run]:
    """Fold the shortest run into its longer neighbour until every run is long enough."""
    runs = list(runs)
    while len(runs) > 1:
        index = min(range(len(runs)), key=lambda i: runs[i].length_m)
        if runs[index].length_m >= min_stretch_m:
            break
        neighbours = [i for i in (index - 1, index + 1) if 0 <= i < len(runs)]
        target = max(neighbours, key=lambda i: runs[i].length_m)
        low, high = sorted((index, target))
        merged = _Run(
            band=runs[target].band,
            first=runs[low].first,
            last=runs[high].last,
            start_m=runs[low].start_m,
            end_m=runs[high].end_m,
        )
        runs[low : high + 1] = [merged]
        runs = _join_same_band(runs)
    return runs


def _join_same_band(runs: list[_Run]) -> list[_Run]:
    joined = [runs[0]]
    for run in runs[1:]:
        if run.band is joined[-1].band:
            joined[-1] = _Run(run.band, joined[-1].first, run.last, joined[-1].start_m, run.end_m)
        else:
            joined.append(run)
    return joined


def _stretch(
    run: _Run,
    scores: Sequence[SegmentScore | None],
    coordinates: Sequence[LonLat],
    cumulative: Sequence[float],
) -> ScenicStretch:
    matched = [score for score in scores if score is not None]
    return ScenicStretch(
        start_m=round(run.start_m, 1),
        end_m=round(run.end_m, 1),
        band=run.band,
        score=round(_mean(s.scenic_score for s in matched), 3) if matched else None,
        reason=_reason(run.band, matched),
        name=_main_name(scores),
        coordinates=_slice_line(coordinates, cumulative, run.start_m, run.end_m),
    )


def _reason(band: ScenicBand, matched: Sequence[SegmentScore]) -> ScenicReason | None:
    if not matched or band is ScenicBand.UNKNOWN:
        return None
    quietness = _mean(s.quietness for s in matched)
    if band is ScenicBand.PLAIN:
        return ScenicReason.BUSY if quietness < BUSY_QUIETNESS else ScenicReason.LITTLE_SCENERY
    contributions = {
        ScenicReason.WATER: WATER_WEIGHT * _mean(s.water_share for s in matched),
        ScenicReason.GREEN: GREEN_WEIGHT * _mean(s.green_share for s in matched),
        ScenicReason.QUIET: QUIETNESS_WEIGHT * quietness,
    }
    return max(contributions, key=contributions.__getitem__)


def _main_name(scores: Sequence[SegmentScore | None]) -> str | None:
    names = Counter(score.name for score in scores if score is not None and score.name)
    if not names:
        return None
    name, count = names.most_common(1)[0]
    return name if count / len(scores) >= NAME_MIN_SHARE else None


def _slice_line(
    coordinates: Sequence[LonLat], cumulative: Sequence[float], start_m: float, end_m: float
) -> list[LonLat]:
    """The part of the line between two distances along it, with interpolated ends."""
    inner = [
        position
        for position, d in zip(coordinates, cumulative)
        if start_m < d < end_m
    ]
    return [
        point_at_distance(coordinates, cumulative, start_m),
        *inner,
        point_at_distance(coordinates, cumulative, end_m),
    ]


def _mean(values) -> float:
    values = list(values)
    return sum(values) / len(values)

"""Chooses a few distinct, labelled route options from the planner's candidates.

The planner generates several routes (8 loops, or up to 3 GraphHopper
alternatives). The rider sees up to three, each labelled by what it is best
at. Two routes that mostly share the same roads count as one, so the options
are real choices rather than near-copies.

Pure Python, unit tested without a routing engine or database.
"""

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from app.models.route import RouteFeature
from app.models.scenic import ScenicBand
from app.models.spec import Climbing, RouteSpec
from app.routing.geometry import LonLat
from app.scoring.stretches import sample_route

MAX_OPTIONS = 3
# Routes sharing more than this share of their length are treated as the same route.
SIMILARITY_LIMIT = 0.7
# Grid cell size. A route point counts as shared if the other route has a point
# in the same or a neighbouring cell, i.e. within roughly 40 to 80 m.
SHARED_WITHIN_M = 40.0
OVERLAP_SAMPLE_SPACING_M = 25.0

METRES_PER_DEGREE = 111_320.0

BEST_MATCH = "Best match"
MOST_SCENIC = "Most scenic"
HILLIEST = "Hilliest"
FLATTEST = "Flattest"
ALTERNATIVE = "Alternative"


@dataclass(frozen=True)
class Criterion:
    label: str
    key: Callable[[RouteFeature], float]
    """Higher is better."""


def scenic_share(route: RouteFeature) -> float:
    """Share of the route in scenic stretches: the figure the rider sees on each option."""
    scenic = route.properties.scenic
    return scenic.band_shares.get(ScenicBand.SCENIC, 0.0) if scenic else 0.0


def climb(route: RouteFeature) -> float:
    return route.properties.ascend_m or 0.0


def criteria_for(spec: RouteSpec) -> list[Criterion]:
    """What the other options are best at. A rider seeking climbs is not offered the flattest."""
    criteria = [Criterion(MOST_SCENIC, scenic_share)]
    if spec.climbing is not Climbing.AVOID:
        criteria.append(Criterion(HILLIEST, climb))
    if spec.climbing is not Climbing.SEEK:
        criteria.append(Criterion(FLATTEST, lambda route: -climb(route)))
    return criteria


def choose_options(
    ranked: Sequence[RouteFeature], spec: RouteSpec, max_options: int = MAX_OPTIONS
) -> list[tuple[str, RouteFeature]]:
    """Up to `max_options` distinct routes with labels. `ranked` is best first.

    The best-ranked route always comes first. Each criterion then adds the
    route that is best at it, unless that route is already shown or nearly
    the same as one that is. Remaining slots go to distinct routes in rank order.
    """
    if not ranked:
        return []
    chosen: list[tuple[str, RouteFeature]] = [(BEST_MATCH, ranked[0])]
    shapes = {id(route): _Shape(route) for route in ranked}

    def is_new(route: RouteFeature) -> bool:
        return all(
            route is not other and shapes[id(route)].similarity(shapes[id(other)]) <= SIMILARITY_LIMIT
            for _, other in chosen
        )

    for criterion in criteria_for(spec):
        if len(chosen) >= max_options:
            break
        top = max(ranked, key=criterion.key)
        if is_new(top) and criterion.key(top) > criterion.key(ranked[0]):
            chosen.append((criterion.label, top))

    for route in ranked:
        if len(chosen) >= max_options:
            break
        if is_new(route):
            chosen.append((ALTERNATIVE, route))
    return chosen


class _Shape:
    """A route's sample points, with a grid index for fast "is there a point near here" checks."""

    def __init__(self, route: RouteFeature) -> None:
        coordinates = route.geometry.coordinates
        self._scale = math.cos(math.radians(coordinates[0][1])) if coordinates else 1.0
        self.points = [
            self._project(sample.position)
            for sample in sample_route(coordinates, OVERLAP_SAMPLE_SPACING_M)
        ]
        self._cells: set[tuple[int, int]] = {self._cell(point) for point in self.points}

    def similarity(self, other: "_Shape") -> float:
        return max(self.shared_with(other), other.shared_with(self))

    def shared_with(self, other: "_Shape") -> float:
        """Share of this route's points that lie near the other route."""
        if not self.points:
            return 0.0
        shared = sum(1 for point in self.points if other._has_point_near(point))
        return shared / len(self.points)

    def _has_point_near(self, point: tuple[float, float]) -> bool:
        cx, cy = self._cell(point)
        return any((cx + dx, cy + dy) in self._cells for dx in (-1, 0, 1) for dy in (-1, 0, 1))

    def _project(self, position: LonLat) -> tuple[float, float]:
        lon, lat = position
        return (lon * METRES_PER_DEGREE * self._scale, lat * METRES_PER_DEGREE)

    @staticmethod
    def _cell(point: tuple[float, float]) -> tuple[int, int]:
        return (int(point[0] // SHARED_WITHIN_M), int(point[1] // SHARED_WITHIN_M))

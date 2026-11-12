"""Small geometry helpers for GeoJSON lines of [lon, lat] positions."""

import math
from collections.abc import Sequence

EARTH_RADIUS_M = 6_371_000.0

LonLat = tuple[float, float]


def distance_m(a: LonLat, b: LonLat) -> float:
    """Great-circle distance between two [lon, lat] positions."""
    lon1, lat1, lon2, lat2 = map(math.radians, (*a, *b))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def cumulative_distances(coordinates: Sequence[LonLat]) -> list[float]:
    totals = [0.0]
    for previous, current in zip(coordinates, coordinates[1:]):
        totals.append(totals[-1] + distance_m(previous, current))
    return totals


def point_at_distance(
    coordinates: Sequence[LonLat], cumulative: Sequence[float], d: float
) -> LonLat:
    """The position `d` metres along the line. Linear in lon/lat, which is fine over one edge."""
    if d <= 0:
        return tuple(coordinates[0])
    for i in range(1, len(cumulative)):
        if cumulative[i] >= d:
            edge = cumulative[i] - cumulative[i - 1]
            t = (d - cumulative[i - 1]) / edge if edge else 0.0
            (lon1, lat1), (lon2, lat2) = coordinates[i - 1], coordinates[i]
            return (round(lon1 + t * (lon2 - lon1), 6), round(lat1 + t * (lat2 - lat1), 6))
    return tuple(coordinates[-1])


def point_at_fraction(coordinates: Sequence[LonLat], fraction: float) -> LonLat:
    cumulative = cumulative_distances(coordinates)
    return point_at_distance(coordinates, cumulative, fraction * cumulative[-1])


def distance_along(coordinates: Sequence[LonLat], position: LonLat) -> float:
    """How far along the line its closest vertex to `position` lies."""
    cumulative = cumulative_distances(coordinates)
    nearest = min(range(len(coordinates)), key=lambda i: distance_m(coordinates[i], position))
    return cumulative[nearest]

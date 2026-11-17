"""How much of a route rides into the wind.

Each piece of the route is classed by the angle between the direction of
travel and the direction the wind comes from: within 45° is a headwind,
within 45° of the opposite is a tailwind, anything else is a crosswind.
Shares are by distance.
"""

import math
from typing import Sequence

from app.models.weather import WindExposure
from app.routing.geometry import LonLat, distance_m

HEADWIND_MAX_ANGLE = 45.0
TAILWIND_MIN_ANGLE = 135.0


def bearing_deg(a: LonLat, b: LonLat) -> float:
    """Compass direction of travel from a to b (0 = north, 90 = east).

    A flat approximation, accurate to well under a degree over a few hundred metres.
    """
    mean_lat = math.radians((a[1] + b[1]) / 2)
    east = (b[0] - a[0]) * math.cos(mean_lat)
    north = b[1] - a[1]
    return math.degrees(math.atan2(east, north)) % 360


def angle_between(a_deg: float, b_deg: float) -> float:
    """The smaller angle between two compass directions, 0 to 180."""
    return abs((a_deg - b_deg + 180) % 360 - 180)


def wind_exposure(coordinates: Sequence[LonLat], wind_from_deg: float) -> WindExposure:
    head = tail = cross = 0.0
    for a, b in zip(coordinates, coordinates[1:]):
        length = distance_m(a, b)
        if length == 0:
            continue
        # Riding toward where the wind comes from is riding into it.
        angle = angle_between(bearing_deg(a, b), wind_from_deg)
        if angle <= HEADWIND_MAX_ANGLE:
            head += length
        elif angle >= TAILWIND_MIN_ANGLE:
            tail += length
        else:
            cross += length

    total = head + tail + cross
    if total == 0:
        return WindExposure(headwind_pct=0, tailwind_pct=0, crosswind_pct=0)
    headwind_pct = round(100 * head / total)
    tailwind_pct = round(100 * tail / total)
    # The three always add up to 100, whatever the rounding.
    return WindExposure(
        headwind_pct=headwind_pct,
        tailwind_pct=tailwind_pct,
        crosswind_pct=100 - headwind_pct - tailwind_pct,
    )

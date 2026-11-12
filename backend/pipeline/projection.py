"""A flat metric projection for one city-sized region.

The pipeline measures distances in metres ("within 100 m of water"). Over a
region the size of one city, an equirectangular projection centred on the
region is accurate to well under 1%, which is plenty for scoring, and avoids
a dependency on pyproj.
"""

import math
from dataclasses import dataclass

import numpy as np
import shapely
from shapely.geometry.base import BaseGeometry

METRES_PER_DEGREE_LAT = 110_574.0
METRES_PER_DEGREE_LON_AT_EQUATOR = 111_320.0


@dataclass(frozen=True)
class LocalProjection:
    origin_lon: float
    origin_lat: float

    @property
    def _metres_per_degree_lon(self) -> float:
        return METRES_PER_DEGREE_LON_AT_EQUATOR * math.cos(math.radians(self.origin_lat))

    def coords_to_metres(self, lonlat: np.ndarray) -> np.ndarray:
        """Convert an (N, 2) array of lon/lat to x/y metres from the origin."""
        metres = np.empty(lonlat.shape, dtype=float)
        metres[:, 0] = (lonlat[:, 0] - self.origin_lon) * self._metres_per_degree_lon
        metres[:, 1] = (lonlat[:, 1] - self.origin_lat) * METRES_PER_DEGREE_LAT
        return metres

    def to_metres(self, geometry: BaseGeometry | np.ndarray) -> BaseGeometry | np.ndarray:
        """Project one geometry, or an array of them in a single call."""
        return shapely.transform(geometry, self.coords_to_metres)


def step_length_m(start: tuple[float, float], end: tuple[float, float]) -> float:
    """Distance in metres between two nearby lon/lat points."""
    mid_lat = math.radians((start[1] + end[1]) / 2)
    dx = (end[0] - start[0]) * METRES_PER_DEGREE_LON_AT_EQUATOR * math.cos(mid_lat)
    dy = (end[1] - start[1]) * METRES_PER_DEGREE_LAT
    return math.hypot(dx, dy)

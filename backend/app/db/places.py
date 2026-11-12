"""Finds places such as cafes near a route, from the places table the pipeline writes."""

import asyncio
import json
import logging

import asyncpg

from app.models.route import Coordinate, GeocodedPlace, RouteFeature

logger = logging.getLogger(__name__)

# First look for places practically on the route, then allow a short detour.
SEARCH_RADII_M = (150.0, 600.0)

NEAR_ROUTE_SQL = """
WITH route AS (
    SELECT ST_SetSRID(ST_GeomFromGeoJSON($1), 4326) AS geom
),
middle AS (
    SELECT ST_LineSubstring(geom, $3, $4)::geography AS part,
           ST_LineInterpolatePoint(geom, ($3 + $4) / 2)::geography AS centre
    FROM route
)
SELECT place.name, ST_X(place.geom) AS lon, ST_Y(place.geom) AS lat
FROM places AS place, middle
WHERE place.category = $2
  AND ST_DWithin(place.geom::geography, middle.part, $5)
ORDER BY ST_Distance(place.geom::geography, middle.centre)
LIMIT 1
"""


class PostgisPlaceFinder:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def near_route(
        self, category: str, route: RouteFeature, from_fraction: float, to_fraction: float
    ) -> GeocodedPlace | None:
        """The place of this category closest to the middle of the given part of the route."""
        geometry = json.dumps(route.geometry.model_dump())
        try:
            for radius_m in SEARCH_RADII_M:
                row = await self._pool.fetchrow(
                    NEAR_ROUTE_SQL, geometry, category, from_fraction, to_fraction, radius_m
                )
                if row is not None:
                    logger.info("places.near_route found=%s radius_m=%.0f", row["name"], radius_m)
                    return GeocodedPlace(
                        name=row["name"], coordinate=Coordinate(lat=row["lat"], lon=row["lon"])
                    )
        except (OSError, asyncio.TimeoutError, asyncpg.PostgresError) as exc:
            logger.warning("places.near_route failed: %s", exc)
        return None

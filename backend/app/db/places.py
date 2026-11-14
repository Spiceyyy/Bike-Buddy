"""Finds places such as cafes near a route, from the places table the pipeline writes."""

import asyncio
import json
import logging

import asyncpg

from app.models.route import Coordinate, NearbyPlace, RouteFeature

logger = logging.getLogger(__name__)

# Places practically on the route come first; others up to the detour limit follow.
ON_ROUTE_M = 150.0
MAX_DETOUR_M = 600.0

NEAR_ROUTE_SQL = """
WITH route AS (
    SELECT ST_SetSRID(ST_GeomFromGeoJSON($1), 4326) AS geom
),
middle AS (
    SELECT ST_LineSubstring(geom, $3, $4)::geography AS part,
           ST_LineInterpolatePoint(geom, ($3 + $4) / 2)::geography AS centre
    FROM route
),
nearby AS (
    SELECT place.name, place.geom,
           ST_Distance(place.geom::geography, middle.part) AS off_route_m,
           ST_Distance(place.geom::geography, middle.centre) AS from_centre_m
    FROM places AS place, middle
    WHERE place.category = $2
      AND ST_DWithin(place.geom::geography, middle.part, $5)
)
SELECT name, ST_X(geom) AS lon, ST_Y(geom) AS lat, off_route_m
FROM nearby
ORDER BY off_route_m > $6, from_centre_m
LIMIT $7
"""


class PostgisPlaceFinder:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def near_route(
        self,
        category: str,
        route: RouteFeature,
        from_fraction: float,
        to_fraction: float,
        limit: int = 1,
    ) -> list[NearbyPlace]:
        """Places of this category near the given part of the route.

        Places within ON_ROUTE_M of the route come first, then the rest; each
        group is ordered by distance from the middle of that part.
        """
        try:
            rows = await self._pool.fetch(
                NEAR_ROUTE_SQL,
                json.dumps(route.geometry.model_dump()),
                category,
                from_fraction,
                to_fraction,
                MAX_DETOUR_M,
                ON_ROUTE_M,
                limit,
            )
        except (OSError, asyncio.TimeoutError, asyncpg.PostgresError) as exc:
            logger.warning("places.near_route failed: %s", exc)
            return []
        logger.info("places.near_route category=%s found=%d", category, len(rows))
        return [
            NearbyPlace(
                name=row["name"],
                coordinate=Coordinate(lat=row["lat"], lon=row["lon"]),
                off_route_m=round(row["off_route_m"], 1),
            )
            for row in rows
        ]

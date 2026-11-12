"""Adds a stretch-by-stretch scenic description to a route.

Scenery is extra information, never a reason to fail: if the database is
down or the pipeline has not run, the route is returned without it.
"""

import asyncio
import logging
import time
from typing import Protocol

import asyncpg

from app.db.segments import nearest_segment_scores
from app.models.route import RouteFeature
from app.models.scenic import RouteScenic
from app.scoring.stretches import build_route_scenic, sample_route

logger = logging.getLogger(__name__)


class ScenicDescriber(Protocol):
    async def describe(self, route: RouteFeature) -> RouteScenic | None: ...


class PostgisScenicDescriber:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def describe(self, route: RouteFeature) -> RouteScenic | None:
        coordinates = route.geometry.coordinates
        if len(coordinates) < 2:
            return None
        samples = sample_route(coordinates)
        started = time.perf_counter()
        try:
            scores = await nearest_segment_scores(
                self._pool, [sample.position for sample in samples]
            )
        except (OSError, asyncio.TimeoutError, asyncpg.PostgresError) as exc:
            logger.warning("scenic.describe failed: %s", exc)
            return None
        scenic = build_route_scenic(coordinates, samples, scores)
        logger.info(
            "scenic.describe samples=%d latency_ms=%.0f score=%s stretches=%d",
            len(samples),
            (time.perf_counter() - started) * 1000,
            f"{scenic.score:.3f}" if scenic else None,
            len(scenic.stretches) if scenic else 0,
        )
        return scenic

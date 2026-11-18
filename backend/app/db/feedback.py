"""Route ratings from riders, kept for later analysis.

The table belongs to the app, not the offline pipeline, so rebuilding the
scenery never deletes ratings. Each rating stores the route's line, so it can
later be joined to the scored road segments it passed (see
evals/feedback_report.py): the first step towards learning the scenic weights
from riders instead of setting them by hand.
"""

from typing import Protocol

import asyncpg

from app.models.feedback import FeedbackRequest

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS route_feedback (
    id            bigserial PRIMARY KEY,
    created_at    timestamptz NOT NULL DEFAULT now(),
    rating        smallint NOT NULL CHECK (rating IN (-1, 1)),
    comment       text,
    message       text,
    spec          jsonb,
    option_label  text,
    distance_m    real NOT NULL,
    ascend_m      real,
    scenic_score  real,
    scenic_share  real,
    stop_name     text,
    geom          geometry(LineString, 4326) NOT NULL
);
CREATE INDEX IF NOT EXISTS route_feedback_geom_idx ON route_feedback USING gist (geom);
"""

INSERT_SQL = """
INSERT INTO route_feedback (
    rating, comment, message, spec, option_label,
    distance_m, ascend_m, scenic_score, scenic_share, stop_name, geom
)
VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7, $8, $9, $10, ST_SetSRID(ST_GeomFromGeoJSON($11), 4326))
RETURNING id
"""


class FeedbackError(Exception):
    """The rating could not be stored."""


class FeedbackStore(Protocol):
    async def save(self, feedback: FeedbackRequest) -> int: ...


class PostgisFeedbackStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        self._schema_ready = False

    async def save(self, feedback: FeedbackRequest) -> int:
        route = feedback.route
        comment = (feedback.comment or "").strip() or None
        try:
            async with self._pool.acquire() as connection:
                # Created on first use, so the API still starts while Postgres is down.
                if not self._schema_ready:
                    await connection.execute(SCHEMA_SQL)
                    self._schema_ready = True
                return await connection.fetchval(
                    INSERT_SQL,
                    1 if feedback.rating == "up" else -1,
                    comment,
                    feedback.message,
                    feedback.spec.model_dump_json() if feedback.spec else None,
                    feedback.option_label,
                    route.distance_m,
                    route.ascend_m,
                    route.scenic_score,
                    route.scenic_share,
                    route.stop_name,
                    route.geometry.model_dump_json(),
                )
        except (OSError, asyncpg.PostgresError) as exc:
            raise FeedbackError(str(exc)) from exc

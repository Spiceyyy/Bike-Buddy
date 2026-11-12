"""Reads scored road segments (written by the offline pipeline) from PostGIS."""

import json
from collections.abc import Sequence
from dataclasses import dataclass

import asyncpg

from app.models.route import RouteFeature
from app.scoring.stretches import SegmentScore

SAMPLE_SPACING_M = 25.0
# How far a route point may be from a segment and still be matched to it.
# GraphHopper's geometry follows OSM, so most points match within a metre or two.
MATCH_DISTANCE_M = 15.0
# Degrees are used for the distance filter so the spatial index applies.
# At Montreal's latitude this is 15 m north-south and about 11 m east-west.
MATCH_DISTANCE_DEG = MATCH_DISTANCE_M / 111_000

ROUTE_SCORE_SQL = """
WITH route AS (
    SELECT ST_SetSRID(ST_GeomFromGeoJSON($1), 4326) AS geom
),
samples AS (
    SELECT (ST_Dump(ST_LineInterpolatePoints(
        geom, LEAST(1.0, $2 / GREATEST(ST_Length(geom::geography), $2))
    ))).geom AS geom
    FROM route
)
SELECT avg(nearest.scenic_score) AS score,
       count(*) AS samples,
       count(nearest.scenic_score) AS matched
FROM samples
LEFT JOIN LATERAL (
    SELECT segment.scenic_score
    FROM road_segments AS segment
    WHERE ST_DWithin(segment.geom, samples.geom, $3)
    ORDER BY segment.geom <-> samples.geom
    LIMIT 1
) AS nearest ON true
"""


NEAREST_SEGMENTS_SQL = """
SELECT nearest.scenic_score, nearest.water_share, nearest.green_share,
       nearest.quietness, nearest.name
FROM unnest($1::float8[], $2::float8[]) WITH ORDINALITY AS sample(lon, lat, index)
LEFT JOIN LATERAL (
    SELECT segment.scenic_score, segment.water_share, segment.green_share,
           segment.quietness, segment.name
    FROM road_segments AS segment
    WHERE ST_DWithin(segment.geom, ST_SetSRID(ST_MakePoint(sample.lon, sample.lat), 4326), $3)
    ORDER BY segment.geom <-> ST_SetSRID(ST_MakePoint(sample.lon, sample.lat), 4326)
    LIMIT 1
) AS nearest ON true
ORDER BY sample.index
"""


async def nearest_segment_scores(
    pool: asyncpg.Pool, positions: Sequence[tuple[float, float]]
) -> list[SegmentScore | None]:
    """The nearest scored segment to each [lon, lat] position, or None if none is close."""
    rows = await pool.fetch(
        NEAREST_SEGMENTS_SQL,
        [lon for lon, _ in positions],
        [lat for _, lat in positions],
        MATCH_DISTANCE_DEG,
    )
    return [
        SegmentScore(
            scenic_score=row["scenic_score"],
            water_share=row["water_share"],
            green_share=row["green_share"],
            quietness=row["quietness"],
            name=row["name"],
        )
        if row["scenic_score"] is not None
        else None
        for row in rows
    ]


@dataclass(frozen=True)
class RouteScenicScore:
    score: float | None
    """Mean scenic score along the route, 0..1. None if nothing matched."""
    matched_share: float
    """Share of route points matched to a scored segment."""


async def route_scenic_score(
    connection: asyncpg.Connection, route: RouteFeature
) -> RouteScenicScore:
    """Score a route by sampling it every 25 m and averaging the nearest segments' scores."""
    row = await connection.fetchrow(
        ROUTE_SCORE_SQL,
        json.dumps(route.geometry.model_dump()),
        SAMPLE_SPACING_M,
        MATCH_DISTANCE_DEG,
    )
    samples = row["samples"] or 0
    return RouteScenicScore(
        score=float(row["score"]) if row["score"] is not None else None,
        matched_share=row["matched"] / samples if samples else 0.0,
    )

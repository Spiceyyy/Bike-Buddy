"""Feedback report: what riders liked and disliked.

Summarises the thumbs up and down stored by POST /api/feedback:

- totals, and the share of thumbs up by route option and by ride type
- liked against disliked routes: distance, climbing, scenic share
- scored segment features along liked against disliked routes: each rated
  route is joined to the road segments it passes (segment midpoint within
  15 m), and their features are averaged, weighted by length

The last part is the input for learning the scenic weights from riders
instead of setting them by hand (CLAUDE.md, pipeline step 3). It needs many
more ratings than a demo collects before the differences mean anything, so
the report prints counts next to every figure.

    docker compose exec backend python /evals/feedback_report.py
    docker compose exec backend python /evals/feedback_report.py --json
"""

import argparse
import asyncio
import json
import os
from typing import Any

import asyncpg

SEGMENT_FEATURES = ("water_share", "green_share", "quietness", "calmness", "surface_quality", "scenic_score")
MATCH_DISTANCE_M = 15

BY_LABEL_SQL = """
SELECT coalesce(option_label, 'map click') AS label,
       count(*) AS ratings,
       avg((rating = 1)::int) AS up_share
FROM route_feedback GROUP BY 1 ORDER BY ratings DESC, label
"""

BY_TYPE_SQL = """
SELECT coalesce(spec->>'route_type', 'map click') AS route_type,
       coalesce(spec->>'climbing', 'any') AS climbing,
       count(*) AS ratings,
       avg((rating = 1)::int) AS up_share
FROM route_feedback GROUP BY 1, 2 ORDER BY ratings DESC, route_type, climbing
"""

BY_RATING_SQL = """
SELECT rating, count(*) AS ratings,
       avg(distance_m) / 1000 AS distance_km,
       avg(ascend_m) AS ascend_m,
       avg(scenic_share) AS scenic_share
FROM route_feedback GROUP BY rating ORDER BY rating DESC
"""

SEGMENTS_SQL = f"""
WITH matched AS (
    SELECT DISTINCT f.id AS feedback_id, f.rating, s.id AS segment_id
    FROM route_feedback f
    JOIN road_segments s
      ON s.geom && ST_Expand(f.geom, 0.0005)
     AND ST_DWithin(
            ST_LineInterpolatePoint(s.geom, 0.5)::geography, f.geom::geography, {MATCH_DISTANCE_M}
         )
)
SELECT m.rating,
       count(DISTINCT m.feedback_id) AS routes,
       sum(s.length_m) / 1000 AS km,
       {", ".join(f"sum(s.{name} * s.length_m) / sum(s.length_m) AS {name}" for name in SEGMENT_FEATURES)}
FROM matched m JOIN road_segments s ON s.id = m.segment_id
GROUP BY m.rating ORDER BY m.rating DESC
"""

COMMENTS_SQL = """
SELECT created_at, rating, coalesce(option_label, 'map click') AS label, message, comment
FROM route_feedback WHERE comment IS NOT NULL ORDER BY created_at DESC LIMIT 10
"""


async def build_report(pool: asyncpg.Pool) -> dict[str, Any]:
    async with pool.acquire() as connection:
        exists = await connection.fetchval("SELECT to_regclass('route_feedback') IS NOT NULL")
        if not exists:
            return {"ratings": 0}
        total = await connection.fetchrow(
            "SELECT count(*) AS ratings, coalesce(avg((rating = 1)::int), 0) AS up_share FROM route_feedback"
        )

        def rows(records) -> list[dict[str, Any]]:
            return [{key: _plain(value) for key, value in record.items()} for record in records]

        return {
            "ratings": total["ratings"],
            "up_share": _plain(total["up_share"]),
            "by_option": rows(await connection.fetch(BY_LABEL_SQL)),
            "by_ride_type": rows(await connection.fetch(BY_TYPE_SQL)),
            "by_rating": rows(await connection.fetch(BY_RATING_SQL)),
            "segment_features": rows(await connection.fetch(SEGMENTS_SQL)),
            "recent_comments": rows(await connection.fetch(COMMENTS_SQL)),
        }


def _plain(value: Any) -> Any:
    """JSON-friendly values: Decimal and floats rounded, timestamps as text."""
    if value is None or isinstance(value, (int, str)):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat(timespec="minutes")
    return round(float(value), 3)


def print_report(report: dict[str, Any]) -> None:
    if not report["ratings"]:
        print("No ratings yet.")
        return
    rating_name = {1: "liked", -1: "disliked"}
    print(f"{report['ratings']} ratings, {report['up_share']:.0%} thumbs up\n")

    print("By route option")
    for row in report["by_option"]:
        print(f"  {row['label']:<14}{row['ratings']:>5} ratings  {row['up_share']:.0%} up")

    print("\nBy ride type")
    for row in report["by_ride_type"]:
        kind = f"{row['route_type']}, climbing {row['climbing']}"
        print(f"  {kind:<34}{row['ratings']:>5} ratings  {row['up_share']:.0%} up")

    print("\nLiked and disliked routes")
    for row in report["by_rating"]:
        climb = f"{row['ascend_m']:.0f} m" if row["ascend_m"] is not None else "n/a"
        share = f"{row['scenic_share']:.0%}" if row["scenic_share"] is not None else "n/a"
        print(
            f"  {rating_name[row['rating']]:<9}{row['ratings']:>5} routes  "
            f"{row['distance_km']:.1f} km  climb {climb}  scenic {share}"
        )

    print(f"\nSegment features along the routes (length-weighted, segments within {MATCH_DISTANCE_M} m)")
    header = "".join(f"{name:>16}" for name in SEGMENT_FEATURES)
    print(f"  {'':<9}{'routes':>7}{'km':>8}{header}")
    for row in report["segment_features"]:
        values = "".join(f"{row[name]:>16.2f}" for name in SEGMENT_FEATURES)
        print(f"  {rating_name[row['rating']]:<9}{row['routes']:>7}{row['km']:>8.1f}{values}")

    if report["recent_comments"]:
        print("\nRecent comments")
        for row in report["recent_comments"]:
            thumb = "up" if row["rating"] == 1 else "down"
            print(f"  [{row['created_at']}] {thumb}, {row['label']}: {row['comment']}")
            if row["message"]:
                print(f"      for \"{row['message']}\"")


async def main(as_json: bool) -> None:
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=2)
    try:
        report = await build_report(pool)
    finally:
        await pool.close()
    if as_json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print_report(report)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="Print the report as JSON.")
    asyncio.run(main(parser.parse_args().json))

"""Writes scored segments to PostGIS, replacing the previous run's rows."""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import asyncpg

from pipeline.osm_reader import Place

SCHEMA_SQL = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")

COLUMNS = (
    "osm_way_id",
    "name",
    "highway",
    "surface",
    "length_m",
    "water_share",
    "green_share",
    "quietness",
    "calmness",
    "near_viewpoint",
    "surface_quality",
    "scenic_score",
)


@dataclass(frozen=True)
class ScoredSegmentRow:
    osm_way_id: int
    name: str | None
    highway: str
    surface: str | None
    length_m: float
    water_share: float
    green_share: float
    quietness: float
    calmness: float
    near_viewpoint: bool
    surface_quality: float
    scenic_score: float
    geom_wkb: bytes

    def as_record(self) -> tuple:
        return (*(getattr(self, column) for column in COLUMNS), self.geom_wkb)


async def replace_segments(
    database_url: str, rows: Sequence[ScoredSegmentRow], places: Sequence[Place] = ()
) -> None:
    connection = await asyncpg.connect(database_url)
    try:
        async with connection.transaction():
            await connection.execute(SCHEMA_SQL)
            # Rows are bulk-copied into a staging table as raw WKB, then
            # converted to PostGIS geometry in one INSERT.
            await connection.execute(
                "CREATE TEMP TABLE staging (LIKE road_segments INCLUDING DEFAULTS) ON COMMIT DROP;"
                "ALTER TABLE staging DROP COLUMN id, DROP COLUMN geom, ADD COLUMN geom_wkb bytea;"
            )
            await connection.copy_records_to_table(
                "staging",
                records=[row.as_record() for row in rows],
                columns=[*COLUMNS, "geom_wkb"],
            )
            column_list = ", ".join(COLUMNS)
            await connection.execute(
                f"TRUNCATE road_segments RESTART IDENTITY;"
                f"INSERT INTO road_segments ({column_list}, geom) "
                f"SELECT {column_list}, ST_SetSRID(ST_GeomFromWKB(geom_wkb), 4326) FROM staging;"
            )
            await connection.execute("TRUNCATE places RESTART IDENTITY")
            await connection.executemany(
                "INSERT INTO places (category, name, geom) "
                "VALUES ($1, $2, ST_SetSRID(ST_MakePoint($3, $4), 4326))",
                [(place.category, place.name, place.lon, place.lat) for place in places],
            )
        await connection.execute("ANALYZE road_segments; ANALYZE places")
    finally:
        await connection.close()

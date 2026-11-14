"""Writes scored segments to PostGIS, replacing the previous run's rows."""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import asyncpg

from pipeline.places import PlaceRow

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


async def replace_segments(database_url: str, rows: Sequence[ScoredSegmentRow]) -> None:
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
        await connection.execute("ANALYZE road_segments")
    finally:
        await connection.close()


async def replace_places(
    database_url: str, rows: Sequence[PlaceRow], embeddings: Sequence[list[float] | None]
) -> None:
    """Rewrite the places table. A missing embedding leaves that place to name search only."""
    connection = await asyncpg.connect(database_url)
    try:
        async with connection.transaction():
            await connection.execute(SCHEMA_SQL)
            await connection.executemany(
                "INSERT INTO places (category, name, description, area_ha, geom, embedding) "
                "VALUES ($1, $2, $3, $4, ST_SetSRID(ST_MakePoint($5, $6), 4326), $7::vector)",
                [
                    (
                        row.category,
                        row.name,
                        row.description,
                        row.area_ha,
                        row.lon,
                        row.lat,
                        vector_literal(vector) if vector is not None else None,
                    )
                    for row, vector in zip(rows, embeddings)
                ],
            )
        await connection.execute("ANALYZE places")
    finally:
        await connection.close()


def vector_literal(vector: Sequence[float]) -> str:
    """pgvector's text format, so no extra driver codec is needed."""
    return "[" + ",".join(f"{x:.7g}" for x in vector) + "]"

"""The rider's recorded rides and profile, for trainer mode.

One rider per installation: the app has no accounts, so this is meant for
running locally (production turns trainer mode off by default). Each ride
keeps the inputs of its training load, so every load is recomputed when the
profile (FTP, heart rates) changes.
"""

from typing import Protocol

import asyncpg

from app.models.route import LineString
from app.models.training import LoadMethod, Ride, RideDetail, RideMetrics, RiderProfile

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS rides (
    id                bigserial PRIMARY KEY,
    uploaded_at       timestamptz NOT NULL DEFAULT now(),
    filename          text NOT NULL,
    name              text,
    file_sha256       text NOT NULL UNIQUE,
    started_at        timestamptz NOT NULL,
    elapsed_s         double precision NOT NULL,
    moving_s          double precision NOT NULL,
    distance_m        double precision NOT NULL,
    ascent_m          double precision,
    avg_speed_kmh     double precision NOT NULL,
    max_speed_kmh     double precision NOT NULL,
    avg_hr            smallint,
    max_hr            smallint,
    avg_power         smallint,
    normalized_power  smallint,
    intensity         double precision,
    load              double precision NOT NULL,
    load_method       text NOT NULL,
    geom              geometry(LineString, 4326) NOT NULL
);
CREATE INDEX IF NOT EXISTS rides_started_at_idx ON rides (started_at);

CREATE TABLE IF NOT EXISTS rider_profile (
    id          smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    ftp_w       smallint,
    max_hr      smallint,
    resting_hr  smallint,
    updated_at  timestamptz NOT NULL DEFAULT now()
);
"""

RIDE_COLUMNS = (
    "id, filename, name, started_at, elapsed_s, moving_s, distance_m, ascent_m, avg_speed_kmh, "
    "max_speed_kmh, avg_hr, max_hr, avg_power, normalized_power, intensity, load, load_method"
)


class RideStoreError(Exception):
    """Rides could not be read or written."""


class RideStore(Protocol):
    async def add(
        self, filename: str, sha256: str, name: str | None, metrics: RideMetrics, geometry: LineString
    ) -> Ride | None: ...

    async def list_rides(self) -> list[Ride]: ...

    async def get(self, ride_id: int) -> RideDetail | None: ...

    async def delete(self, ride_id: int) -> bool: ...

    async def profile(self) -> RiderProfile: ...

    async def save_profile(self, profile: RiderProfile) -> None: ...

    async def update_loads(self, loads: list[tuple[int, float, LoadMethod, float | None]]) -> None: ...


class PostgisRideStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        self._schema_ready = False

    async def _connection(self):
        connection = await self._pool.acquire()
        if not self._schema_ready:
            await connection.execute(SCHEMA_SQL)
            self._schema_ready = True
        return connection

    async def _run(self, work):
        try:
            connection = await self._connection()
        except (OSError, asyncpg.PostgresError) as exc:
            raise RideStoreError(str(exc)) from exc
        try:
            return await work(connection)
        except (OSError, asyncpg.PostgresError) as exc:
            raise RideStoreError(str(exc)) from exc
        finally:
            await self._pool.release(connection)

    async def add(
        self, filename: str, sha256: str, name: str | None, metrics: RideMetrics, geometry: LineString
    ) -> Ride | None:
        """Store a ride, or return None if the same file was uploaded before."""
        m = metrics

        async def work(connection):
            record = await connection.fetchrow(
                f"""
                INSERT INTO rides (
                    filename, name, file_sha256, started_at, elapsed_s, moving_s, distance_m,
                    ascent_m, avg_speed_kmh, max_speed_kmh, avg_hr, max_hr, avg_power,
                    normalized_power, intensity, load, load_method, geom
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17,
                        ST_SetSRID(ST_GeomFromGeoJSON($18), 4326))
                ON CONFLICT (file_sha256) DO NOTHING
                RETURNING {RIDE_COLUMNS}
                """,
                filename, name, sha256, m.started_at, m.elapsed_s, m.moving_s, m.distance_m,
                m.ascent_m, m.avg_speed_kmh, m.max_speed_kmh, m.avg_hr, m.max_hr, m.avg_power,
                m.normalized_power, m.intensity, m.load, m.load_method, geometry.model_dump_json(),
            )
            return Ride(**dict(record)) if record else None

        return await self._run(work)

    async def list_rides(self) -> list[Ride]:
        async def work(connection):
            records = await connection.fetch(f"SELECT {RIDE_COLUMNS} FROM rides ORDER BY started_at DESC")
            return [Ride(**dict(r)) for r in records]

        return await self._run(work)

    async def get(self, ride_id: int) -> RideDetail | None:
        async def work(connection):
            record = await connection.fetchrow(
                f"SELECT {RIDE_COLUMNS}, ST_AsGeoJSON(geom) AS geometry FROM rides WHERE id = $1", ride_id
            )
            if record is None:
                return None
            values = dict(record)
            values["geometry"] = LineString.model_validate_json(values["geometry"])
            return RideDetail(**values)

        return await self._run(work)

    async def delete(self, ride_id: int) -> bool:
        async def work(connection):
            return await connection.execute("DELETE FROM rides WHERE id = $1", ride_id) == "DELETE 1"

        return await self._run(work)

    async def profile(self) -> RiderProfile:
        async def work(connection):
            record = await connection.fetchrow("SELECT ftp_w, max_hr, resting_hr FROM rider_profile WHERE id = 1")
            return RiderProfile(**dict(record)) if record else RiderProfile()

        return await self._run(work)

    async def save_profile(self, profile: RiderProfile) -> None:
        async def work(connection):
            await connection.execute(
                """
                INSERT INTO rider_profile (id, ftp_w, max_hr, resting_hr) VALUES (1, $1, $2, $3)
                ON CONFLICT (id) DO UPDATE SET ftp_w = $1, max_hr = $2, resting_hr = $3, updated_at = now()
                """,
                profile.ftp_w, profile.max_hr, profile.resting_hr,
            )

        await self._run(work)

    async def update_loads(self, loads: list[tuple[int, float, LoadMethod, float | None]]) -> None:
        async def work(connection):
            await connection.executemany(
                "UPDATE rides SET load = $2, load_method = $3, intensity = $4 WHERE id = $1", loads
            )

        await self._run(work)

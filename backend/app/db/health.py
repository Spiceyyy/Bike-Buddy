import asyncio

import asyncpg


async def postgis_is_available(database_url: str, timeout_seconds: float = 3.0) -> bool:
    """Return True if Postgres is reachable and the PostGIS extension responds."""
    try:
        connection = await asyncpg.connect(database_url, timeout=timeout_seconds)
    except (OSError, asyncio.TimeoutError, asyncpg.PostgresError):
        return False

    try:
        await connection.fetchval("SELECT postgis_version()", timeout=timeout_seconds)
    except (asyncio.TimeoutError, asyncpg.PostgresError):
        return False
    finally:
        await connection.close()
    return True

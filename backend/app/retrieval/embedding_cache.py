"""Caches embeddings in Postgres, so the same text is never sent to the API twice.

The pipeline caches place descriptions; search_places and the retrieval eval
cache queries. Repeated searches ("lookout", "beach") then cost no API calls,
which matters on a free tier of about 1,000 embeddings a day shared by
indexing and searching.
"""

import asyncio
import hashlib
import json
import logging
from collections.abc import Sequence

import asyncpg

from app.retrieval.embeddings import Embedder, EmbeddingTask

logger = logging.getLogger(__name__)


def cache_key(model: str, task: EmbeddingTask, text: str) -> str:
    # Documents keep the key format the pipeline has always used, so its cache stays valid.
    payload = f"{model}\n{text}" if task is EmbeddingTask.DOCUMENT else f"{model}\n{task.value}\n{text}"
    return hashlib.sha256(payload.encode()).hexdigest()


def vector_literal(vector: Sequence[float]) -> str:
    """pgvector's text format, so no extra driver codec is needed."""
    return "[" + ",".join(f"{x:.7g}" for x in vector) + "]"


class CachingEmbedder:
    """Wraps an Embedder. If the cache is unreachable, it simply calls the API."""

    def __init__(self, inner: Embedder, pool: asyncpg.Pool) -> None:
        self._inner = inner
        self._pool = pool
        self.model = inner.model

    async def embed(self, texts: Sequence[str], task: EmbeddingTask) -> list[list[float]]:
        keys = [cache_key(self.model, task, text) for text in texts]
        cached = await self._read(keys)
        missing = {key: text for key, text in zip(keys, texts) if key not in cached}
        if missing:
            vectors = await self._inner.embed(list(missing.values()), task)
            fresh = dict(zip(missing.keys(), vectors))
            await self._write(fresh)
            cached.update(fresh)
        return [cached[key] for key in keys]

    async def _read(self, keys: list[str]) -> dict[str, list[float]]:
        try:
            rows = await self._pool.fetch(
                "SELECT key, embedding::text AS embedding FROM embedding_cache "
                "WHERE key = ANY($1)",
                keys,
            )
        except (OSError, asyncio.TimeoutError, asyncpg.PostgresError) as exc:
            logger.warning("embedding_cache read failed: %s", exc)
            return {}
        return {row["key"]: json.loads(row["embedding"]) for row in rows}

    async def _write(self, vectors: dict[str, list[float]]) -> None:
        try:
            await self._pool.executemany(
                "INSERT INTO embedding_cache (key, embedding) VALUES ($1, $2::vector) "
                "ON CONFLICT (key) DO NOTHING",
                [(key, vector_literal(vector)) for key, vector in vectors.items()],
            )
        except (OSError, asyncio.TimeoutError, asyncpg.PostgresError) as exc:
            logger.warning("embedding_cache write failed: %s", exc)

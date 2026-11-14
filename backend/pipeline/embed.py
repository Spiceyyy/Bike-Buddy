"""Embeds place descriptions, reusing earlier results from the embedding cache.

Each batch is saved to the cache as soon as it arrives. The Gemini free tier
allows about 100 embeddings a minute and about 1,000 a day, so the ~1,200
places worth embedding take two days the first time; if a run is stopped or
the daily quota runs out, the next run continues where it left off.
"""

import json
import logging
from collections.abc import Sequence

import asyncpg

from app.retrieval.embedding_cache import cache_key as shared_cache_key
from app.retrieval.embeddings import BATCH_SIZE, Embedder, EmbeddingError, EmbeddingTask
from pipeline.store import vector_literal

logger = logging.getLogger(__name__)


def cache_key(model: str, text: str) -> str:
    return shared_cache_key(model, EmbeddingTask.DOCUMENT, text)


async def embed_with_cache(
    database_url: str, embedder: Embedder, texts: Sequence[str]
) -> list[list[float] | None]:
    """One vector per text, or None for texts the API could not embed this run."""
    keys = [cache_key(embedder.model, text) for text in texts]
    connection = await asyncpg.connect(database_url)
    try:
        await connection.execute(
            "CREATE EXTENSION IF NOT EXISTS vector;"
            "CREATE TABLE IF NOT EXISTS embedding_cache "
            "(key text PRIMARY KEY, embedding vector(768) NOT NULL)"
        )
        rows = await connection.fetch(
            "SELECT key, embedding::text AS embedding FROM embedding_cache WHERE key = ANY($1)",
            list(set(keys)),
        )
        cached = {row["key"]: json.loads(row["embedding"]) for row in rows}

        missing = sorted({key: text for key, text in zip(keys, texts) if key not in cached}.items())
        logger.info("embed cached=%d to_embed=%d", len(set(keys)) - len(missing), len(missing))
        for start in range(0, len(missing), BATCH_SIZE):
            batch = missing[start : start + BATCH_SIZE]
            try:
                vectors = await embedder.embed([text for _, text in batch], EmbeddingTask.DOCUMENT)
            except EmbeddingError as exc:
                logger.warning(
                    "embed stopped after %d of %d: %s. Re-run the pipeline to embed the rest.",
                    start,
                    len(missing),
                    exc,
                )
                break
            await connection.executemany(
                "INSERT INTO embedding_cache (key, embedding) VALUES ($1, $2::vector) "
                "ON CONFLICT (key) DO NOTHING",
                [(key, vector_literal(vector)) for (key, _), vector in zip(batch, vectors)],
            )
            cached.update({key: vector for (key, _), vector in zip(batch, vectors)})
            logger.info("embed progress=%d/%d", start + len(batch), len(missing))
    finally:
        await connection.close()
    return [cached.get(key) for key in keys]

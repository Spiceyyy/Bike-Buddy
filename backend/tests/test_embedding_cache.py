"""The query-embedding cache and strict search mode, with a fake database."""

import hashlib

import asyncpg
import pytest

from app.models.route import Coordinate
from app.retrieval.embedding_cache import CachingEmbedder, cache_key
from app.retrieval.embeddings import EmbeddingError, EmbeddingTask
from app.retrieval.place_search import PostgisPlaceSearch


class FakeEmbedder:
    model = "test-model"

    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[list[str]] = []
        self._error = error

    async def embed(self, texts, task):
        if self._error:
            raise self._error
        self.calls.append(list(texts))
        return [[float(len(text)), 0.0] for text in texts]


class FakePool:
    """Stores cache rows in a dict; can be told to fail like an unreachable database."""

    def __init__(self, broken: bool = False) -> None:
        self.rows: dict[str, str] = {}
        self.broken = broken
        self.queries: list[str] = []

    async def fetch(self, sql, *args):
        self.queries.append(sql)
        if self.broken:
            raise asyncpg.PostgresError("down")
        if "embedding_cache" in sql:
            return [{"key": key, "embedding": self.rows[key]} for key in args[0] if key in self.rows]
        return []

    async def executemany(self, sql, records):
        if self.broken:
            raise asyncpg.PostgresError("down")
        for key, vector in records:
            self.rows[key] = vector


def test_document_keys_match_the_pipeline_cache() -> None:
    expected = hashlib.sha256(b"m\nsome text").hexdigest()

    assert cache_key("m", EmbeddingTask.DOCUMENT, "some text") == expected
    assert cache_key("m", EmbeddingTask.QUERY, "some text") != expected


async def test_repeated_query_is_embedded_once() -> None:
    inner, pool = FakeEmbedder(), FakePool()
    embedder = CachingEmbedder(inner, pool)

    first = await embedder.embed(["lookout"], EmbeddingTask.QUERY)
    second = await embedder.embed(["lookout", "beach"], EmbeddingTask.QUERY)

    assert inner.calls == [["lookout"], ["beach"]]
    assert first[0] == second[0] == [7.0, 0.0]


async def test_unreachable_cache_still_embeds() -> None:
    inner = FakeEmbedder()

    vectors = await CachingEmbedder(inner, FakePool(broken=True)).embed(["a"], EmbeddingTask.QUERY)

    assert vectors == [[1.0, 0.0]]


async def test_app_search_falls_back_to_names_when_embedding_fails() -> None:
    pool = FakePool()
    search = PostgisPlaceSearch(pool, FakeEmbedder(error=EmbeddingError("quota")))

    assert await search.search("lookout", Coordinate(lat=45.5, lon=-73.6), within_m=5000) == []
    assert pool.queries  # the name-only search still ran


async def test_strict_search_raises_instead_of_falling_back() -> None:
    search = PostgisPlaceSearch(
        FakePool(), FakeEmbedder(error=EmbeddingError("quota")), fallback_to_names=False
    )

    with pytest.raises(EmbeddingError):
        await search.search("lookout", Coordinate(lat=45.5, lon=-73.6), within_m=5000)

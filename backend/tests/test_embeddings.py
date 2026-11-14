"""The Gemini embedding client, against a fake HTTP server."""

import json
import math

import httpx
import pytest

from app.retrieval.embeddings import EmbeddingError, EmbeddingTask, GeminiEmbedder


def make_embedder(handler, dimensions: int = 3) -> GeminiEmbedder:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://gemini")
    return GeminiEmbedder(http, api_key="test", dimensions=dimensions, retry_delay_seconds=0)


def vectors_for(request: httpx.Request) -> httpx.Response:
    count = len(json.loads(request.content)["requests"])
    return httpx.Response(200, json={"embeddings": [{"values": [3.0, 0.0, 4.0]}] * count})


async def test_texts_are_sent_in_batches_and_vectors_normalized() -> None:
    sizes: list[int] = []
    tasks: set[str] = set()

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sizes.append(len(body["requests"]))
        tasks.update(item["taskType"] for item in body["requests"])
        return vectors_for(request)

    vectors = await make_embedder(handler).embed(["t"] * 250, EmbeddingTask.DOCUMENT)

    assert sizes == [100, 100, 50]
    assert tasks == {"RETRIEVAL_DOCUMENT"}
    assert vectors[0] == [0.6, 0.0, 0.8]
    assert math.isclose(sum(x * x for x in vectors[-1]), 1.0)


async def test_rate_limit_is_retried() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(429) if len(calls) == 1 else vectors_for(request)

    vectors = await make_embedder(handler).embed(["a"], EmbeddingTask.QUERY)

    assert len(calls) == 2
    assert len(vectors) == 1


def rate_limited(retry_delay: str) -> httpx.Response:
    detail = {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry_delay}
    return httpx.Response(429, json={"error": {"code": 429, "details": [detail]}})


async def test_suggested_short_wait_is_followed() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return rate_limited("0s") if len(calls) == 1 else vectors_for(request)

    assert len(await make_embedder(handler).embed(["a"], EmbeddingTask.QUERY)) == 1
    assert len(calls) == 2


async def test_daily_quota_stops_instead_of_waiting() -> None:
    with pytest.raises(EmbeddingError, match="quota"):
        await make_embedder(lambda request: rate_limited("3600s")).embed(
            ["a"], EmbeddingTask.QUERY
        )


async def test_bad_request_fails_without_retrying() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(400)

    with pytest.raises(EmbeddingError):
        await make_embedder(handler).embed(["a"], EmbeddingTask.QUERY)
    assert len(calls) == 1


async def test_wrong_dimensions_are_rejected() -> None:
    with pytest.raises(EmbeddingError, match="shape"):
        await make_embedder(vectors_for, dimensions=768).embed(["a"], EmbeddingTask.QUERY)

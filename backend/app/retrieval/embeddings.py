"""Text embeddings behind a small interface, so the provider can be swapped.

Used offline by the pipeline (place descriptions) and online by search_places
(the rider's query). Documents and queries are embedded with different task
types, which the Gemini embedding models are trained for.
"""

import asyncio
import logging
import math
from collections.abc import Sequence
from enum import StrEnum
from typing import Protocol

import httpx

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = "gemini-embedding-2"
EMBEDDING_DIMENSIONS = 768
# The most texts Gemini accepts in one batchEmbedContents request.
BATCH_SIZE = 100
MAX_ATTEMPTS = 6
# Waits longer than this mean a daily quota, not a per-minute one: give up.
MAX_SUGGESTED_WAIT_SECONDS = 90.0


class EmbeddingTask(StrEnum):
    DOCUMENT = "RETRIEVAL_DOCUMENT"
    QUERY = "RETRIEVAL_QUERY"


class EmbeddingError(Exception):
    """The embedding provider failed or returned something unusable."""


class Embedder(Protocol):
    model: str

    async def embed(self, texts: Sequence[str], task: EmbeddingTask) -> list[list[float]]: ...


class GeminiEmbedder:
    def __init__(
        self,
        http: httpx.AsyncClient,
        api_key: str,
        model: str = EMBEDDING_MODEL,
        dimensions: int = EMBEDDING_DIMENSIONS,
        retry_delay_seconds: float = 2.0,
    ) -> None:
        self._http = http
        self._api_key = api_key
        self.model = model
        self._dimensions = dimensions
        self._retry_delay = retry_delay_seconds

    async def embed(self, texts: Sequence[str], task: EmbeddingTask) -> list[list[float]]:
        """One unit-length vector per text, in order."""
        vectors: list[list[float]] = []
        for start in range(0, len(texts), BATCH_SIZE):
            vectors.extend(await self._embed_batch(texts[start : start + BATCH_SIZE], task))
        return vectors

    async def _embed_batch(self, texts: Sequence[str], task: EmbeddingTask) -> list[list[float]]:
        body = {
            "requests": [
                {
                    "model": f"models/{self.model}",
                    "content": {"parts": [{"text": text}]},
                    "taskType": task.value,
                    "outputDimensionality": self._dimensions,
                }
                for text in texts
            ]
        }
        url = f"/v1beta/models/{self.model}:batchEmbedContents"
        headers = {"x-goog-api-key": self._api_key}

        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = await self._http.post(url, json=body, headers=headers)
            except httpx.HTTPError as exc:
                if attempt == MAX_ATTEMPTS:
                    raise EmbeddingError("could not reach the embedding service") from exc
            else:
                if response.status_code == httpx.codes.OK:
                    return _parse(response, len(texts), self._dimensions)
                retryable = response.status_code == 429 or response.status_code >= 500
                if not retryable or attempt == MAX_ATTEMPTS:
                    raise EmbeddingError(f"embedding service returned {response.status_code}")
                logger.warning("embeddings status=%s attempt=%d", response.status_code, attempt)
                # Free-tier limits are per minute; Gemini says how long to wait.
                suggested = _retry_after_seconds(response)
                if suggested is not None and suggested > MAX_SUGGESTED_WAIT_SECONDS:
                    raise EmbeddingError(f"embedding quota used up; retry in {suggested:.0f} s")
                if suggested is not None:
                    await asyncio.sleep(suggested + 1)
                    continue
            await asyncio.sleep(self._retry_delay * 2 ** (attempt - 1))
        raise EmbeddingError("embedding request failed")  # Unreachable; satisfies type checkers.


def _retry_after_seconds(response: httpx.Response) -> float | None:
    """The wait Gemini suggests in a RetryInfo detail such as "37s"."""
    try:
        details = response.json()["error"]["details"]
    except (KeyError, TypeError, ValueError):
        return None
    for detail in details if isinstance(details, list) else []:
        delay = str(detail.get("retryDelay", ""))
        if delay.endswith("s"):
            try:
                return float(delay[:-1])
            except ValueError:
                return None
    return None


def _parse(response: httpx.Response, count: int, dimensions: int) -> list[list[float]]:
    try:
        vectors = [item["values"] for item in response.json()["embeddings"]]
    except (KeyError, TypeError, ValueError) as exc:
        raise EmbeddingError("unexpected embedding response") from exc
    if len(vectors) != count or any(len(vector) != dimensions for vector in vectors):
        raise EmbeddingError("embedding response has the wrong shape")
    return [normalize(vector) for vector in vectors]


def normalize(vector: Sequence[float]) -> list[float]:
    """Scale to unit length. Reduced-size Gemini embeddings are not normalized."""
    norm = math.sqrt(sum(x * x for x in vector))
    return [x / norm for x in vector] if norm else list(vector)

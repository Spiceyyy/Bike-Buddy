"""Hybrid place search: meaning (embeddings) plus name match, near a point.

"a lookout with a view" has no words in common with "Belvédère Kondiaronk",
so names alone miss it; "Lachine canal" is a name, which embeddings of short
descriptions can rank below similar canals. The hybrid score combines both:

    score = 0.85 * cosine similarity + 0.15 * trigram name similarity

The weighting was chosen with the retrieval eval (evals/run_retrieval.py
--sweep): 0.80 to 0.90 tied for best, 0.75 dropped one query from the top 5,
and 0.65 or less got steadily worse. That is one query of 23, measured on
the queries used to choose it, so treat it as a sensible default, not a
tuned optimum.

Places are filtered to a radius around the centre first. At about 3,400
places an exact scan takes a few milliseconds; the HNSW index on the
embedding column is there for larger regions.
"""

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

import asyncpg

from app.models.route import Coordinate
from app.retrieval.embeddings import Embedder, EmbeddingError, EmbeddingTask

logger = logging.getLogger(__name__)

SEMANTIC_WEIGHT = 0.85
NAME_WEIGHT = 0.15
# Rows fetched before removing repeated names (a canal mapped in several pieces).
OVERFETCH = 3


class SearchMode(StrEnum):
    HYBRID = "hybrid"
    SEMANTIC = "semantic"
    NAME = "name"


@dataclass(frozen=True)
class FoundPlace:
    name: str
    category: str
    description: str
    coordinate: Coordinate
    distance_m: float
    score: float


class PlaceSearch(Protocol):
    async def search(
        self,
        query: str,
        centre: Coordinate,
        within_m: float,
        category: str | None = None,
        limit: int = 5,
    ) -> list[FoundPlace]: ...


SEARCH_SQL = """
WITH centre AS (
    SELECT ST_SetSRID(ST_MakePoint($2, $3), 4326)::geography AS point
),
scored AS (
    SELECT place.name, place.category, place.description, place.geom,
           ST_Distance(place.geom::geography, centre.point) AS distance_m,
           $6 * COALESCE(1 - (place.embedding <=> $1::vector), 0)
             + $7 * similarity(place.name, $4) AS score
    FROM places AS place, centre
    WHERE ST_DWithin(place.geom::geography, centre.point, $5)
      AND ($8::text IS NULL OR place.category = $8)
)
SELECT name, category, description, ST_X(geom) AS lon, ST_Y(geom) AS lat, distance_m, score
FROM scored
ORDER BY score DESC, distance_m
LIMIT $9
"""


class PostgisPlaceSearch:
    def __init__(
        self,
        pool: asyncpg.Pool,
        embedder: Embedder | None,
        mode: SearchMode = SearchMode.HYBRID,
        fallback_to_names: bool = True,
        semantic_weight: float | None = None,
    ) -> None:
        self._pool = pool
        self._embedder = embedder
        self.mode = mode
        # The app keeps working on names alone; an eval must fail instead of
        # quietly measuring a different search.
        self._fallback_to_names = fallback_to_names
        # Overrides the hybrid weighting; used by the eval's weight sweep.
        self._semantic_weight = semantic_weight

    async def search(
        self,
        query: str,
        centre: Coordinate,
        within_m: float,
        category: str | None = None,
        limit: int = 5,
    ) -> list[FoundPlace]:
        """Best matches first, at most one per name."""
        semantic_weight, name_weight = _weights(self.mode)
        if self._semantic_weight is not None:
            semantic_weight, name_weight = self._semantic_weight, 1.0 - self._semantic_weight
        vector = await self._query_vector(query) if semantic_weight else None
        if vector is None:
            # No embedding (no API key, or the service failed): names only.
            semantic_weight, name_weight = 0.0, 1.0

        rows = await self._pool.fetch(
            SEARCH_SQL,
            _vector_literal(vector) if vector else None,
            centre.lon,
            centre.lat,
            query,
            within_m,
            semantic_weight,
            name_weight,
            category,
            limit * OVERFETCH,
        )
        found: list[FoundPlace] = []
        seen: set[str] = set()
        for row in rows:
            key = row["name"].casefold()
            if key in seen:
                continue
            seen.add(key)
            found.append(
                FoundPlace(
                    name=row["name"],
                    category=row["category"],
                    description=row["description"],
                    coordinate=Coordinate(lat=row["lat"], lon=row["lon"]),
                    distance_m=round(row["distance_m"], 1),
                    score=round(row["score"], 4),
                )
            )
            if len(found) == limit:
                break
        logger.info(
            "places.search mode=%s query=%r found=%s",
            self.mode.value,
            query,
            [place.name for place in found],
        )
        return found

    async def _query_vector(self, query: str) -> list[float] | None:
        if self._embedder is None:
            return None
        try:
            return (await self._embedder.embed([query], EmbeddingTask.QUERY))[0]
        except EmbeddingError as exc:
            if not self._fallback_to_names:
                raise
            logger.warning("places.search embedding failed, matching names only: %s", exc)
            return None


def _weights(mode: SearchMode) -> tuple[float, float]:
    return {
        SearchMode.HYBRID: (SEMANTIC_WEIGHT, NAME_WEIGHT),
        SearchMode.SEMANTIC: (1.0, 0.0),
        SearchMode.NAME: (0.0, 1.0),
    }[mode]


def _vector_literal(vector: list[float]) -> str:
    """pgvector's text format."""
    return "[" + ",".join(f"{x:.7g}" for x in vector) + "]"


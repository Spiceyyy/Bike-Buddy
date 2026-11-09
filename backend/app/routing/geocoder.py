"""Turns place names into coordinates, so the LLM never has to produce any."""

import asyncio
import logging
import re
import time

import httpx

from app.models.route import BoundingBox, Coordinate, GeocodedPlace

logger = logging.getLogger(__name__)

# Nominatim's public service allows at most one request per second.
MIN_SECONDS_BETWEEN_REQUESTS = 1.0
LEADING_THE = re.compile(r"^the\s+", re.IGNORECASE)


class GeocodingError(Exception):
    """The geocoding service was unreachable or returned something unexpected."""


class NominatimGeocoder:
    def __init__(
        self,
        http: httpx.AsyncClient,
        min_seconds_between_requests: float = MIN_SECONDS_BETWEEN_REQUESTS,
    ) -> None:
        self._http = http
        self._min_interval = min_seconds_between_requests
        self._cache: dict[str, GeocodedPlace | None] = {}
        self._lock = asyncio.Lock()
        self._last_request = float("-inf")

    async def search(self, query: str, bounds: BoundingBox) -> GeocodedPlace | None:
        """Return the best match inside `bounds`, or None if nothing matches."""
        # "the Old Port" and "Old Port" can otherwise match different places.
        cleaned = LEADING_THE.sub("", query.strip())
        key = cleaned.casefold()
        if key in self._cache:
            return self._cache[key]

        async with self._lock:
            await self._wait_for_rate_limit()
            place = await self._fetch(cleaned, bounds)

        self._cache[key] = place
        logger.info("geocoder.search query=%r result=%s", query, place)
        return place

    async def _wait_for_rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self._min_interval:
            await asyncio.sleep(self._min_interval - elapsed)
        self._last_request = time.monotonic()

    async def _fetch(self, query: str, bounds: BoundingBox) -> GeocodedPlace | None:
        params = {
            "q": query,
            "format": "jsonv2",
            "limit": 1,
            "viewbox": f"{bounds.min_lon},{bounds.max_lat},{bounds.max_lon},{bounds.min_lat}",
            "bounded": 1,
        }
        try:
            response = await self._http.get("/search", params=params)
            results = response.raise_for_status().json()
            if not results:
                return None
            best = results[0]
            return GeocodedPlace(
                name=best.get("name") or best["display_name"].split(",")[0],
                coordinate=Coordinate(lat=float(best["lat"]), lon=float(best["lon"])),
            )
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise GeocodingError("could not geocode place") from exc

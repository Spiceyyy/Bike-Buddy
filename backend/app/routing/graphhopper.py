"""Client for the GraphHopper routing engine.

Every route the application returns comes through this module.
"""

import logging
import time
from typing import Any

import httpx

from app.models.route import (
    BoundingBox,
    Coordinate,
    LineString,
    RouteFeature,
    RouteProperties,
)

logger = logging.getLogger(__name__)


class RoutingError(Exception):
    """Base class for failures while requesting a route."""


class PointNotRoutableError(RoutingError):
    """A requested point is outside the loaded region or far from any road."""

    def __init__(self, point_index: int) -> None:
        super().__init__(f"point {point_index} is not routable")
        self.point_index = point_index


class NoRouteFoundError(RoutingError):
    """Both points are routable, but no connection exists between them."""


class RoutingEngineError(RoutingError):
    """GraphHopper was unreachable or returned something unexpected."""


class GraphHopperClient:
    def __init__(self, http: httpx.AsyncClient, profile: str) -> None:
        self._http = http
        self._profile = profile
        self._bounds: BoundingBox | None = None

    async def route(self, start: Coordinate, end: Coordinate) -> RouteFeature:
        return await self._request(points=[start, end])

    async def round_trip(
        self, start: Coordinate, distance_m: float, seed: int = 0
    ) -> RouteFeature:
        """Return a loop from `start`. GraphHopper treats the distance as approximate."""
        return await self._request(
            points=[start],
            algorithm="round_trip",
            **{"round_trip.distance": round(distance_m), "round_trip.seed": seed},
        )

    async def bounds(self) -> BoundingBox:
        """Return the bounding box of the loaded map extract."""
        if self._bounds is None:
            try:
                response = await self._http.get("/info")
                min_lon, min_lat, max_lon, max_lat = response.raise_for_status().json()["bbox"]
            except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
                raise RoutingEngineError("could not read GraphHopper bounds") from exc
            self._bounds = BoundingBox(
                min_lon=min_lon, min_lat=min_lat, max_lon=max_lon, max_lat=max_lat
            )
        return self._bounds

    async def is_healthy(self) -> bool:
        try:
            response = await self._http.get("/health")
        except httpx.HTTPError:
            return False
        return response.status_code == httpx.codes.OK

    async def _request(self, points: list[Coordinate], **options: Any) -> RouteFeature:
        payload = {
            "profile": self._profile,
            # GraphHopper's JSON body takes [lon, lat], the same order as GeoJSON.
            "points": [[point.lon, point.lat] for point in points],
            "points_encoded": False,
            "instructions": False,
            **options,
        }

        started = time.perf_counter()
        try:
            response = await self._http.post("/route", json=payload)
        except httpx.HTTPError as exc:
            raise RoutingEngineError("could not reach GraphHopper") from exc
        latency_ms = (time.perf_counter() - started) * 1000

        logger.info(
            "graphhopper.route status=%s latency_ms=%.0f input=%s options=%s",
            response.status_code,
            latency_ms,
            payload["points"],
            options,
        )

        if response.status_code != httpx.codes.OK:
            raise _error_from_response(response)
        return self._feature_from_response(response)

    def _feature_from_response(self, response: httpx.Response) -> RouteFeature:
        try:
            path = response.json()["paths"][0]
            return RouteFeature(
                geometry=LineString(coordinates=path["points"]["coordinates"]),
                properties=RouteProperties(
                    distance_m=path["distance"],
                    duration_s=path["time"] / 1000,
                    profile=self._profile,
                ),
            )
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise RoutingEngineError("unexpected GraphHopper response") from exc


def _error_from_response(response: httpx.Response) -> RoutingError:
    """Translate a GraphHopper error body into a typed exception."""
    hint = _first_hint(response)
    exception_name = str(hint.get("details", "")).rsplit(".", 1)[-1]

    if exception_name in ("PointNotFoundException", "PointOutOfBoundsException"):
        return PointNotRoutableError(int(hint.get("point_index", 0)))
    if exception_name == "ConnectionNotFoundException":
        return NoRouteFoundError("no connection between the points")
    return RoutingEngineError(f"GraphHopper returned {response.status_code}")


def _first_hint(response: httpx.Response) -> dict[str, Any]:
    try:
        hints = response.json()["hints"]
        return hints[0] if isinstance(hints[0], dict) else {}
    except (KeyError, IndexError, TypeError, ValueError):
        return {}

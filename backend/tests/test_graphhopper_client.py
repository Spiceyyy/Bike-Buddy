import json

import httpx
import pytest

from app.models.route import Coordinate
from app.routing.graphhopper import GraphHopperClient, RoutingEngineError

START = Coordinate(lat=45.5, lon=-73.55)
PATH_BODY = {
    "paths": [
        {
            "distance": 20_000,
            "time": 3_600_000,
            "points": {"type": "LineString", "coordinates": [[-73.55, 45.5], [-73.55, 45.5]]},
        }
    ]
}


def make_client(handler) -> GraphHopperClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://gh")
    return GraphHopperClient(http, profile="bike")


async def test_round_trip_sends_one_point_and_distance() -> None:
    bodies: list[dict] = []

    def capture(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=PATH_BODY)

    route = await make_client(capture).round_trip(START, 20_000.4, seed=3)

    assert bodies[0]["points"] == [[-73.55, 45.5]]
    assert bodies[0]["algorithm"] == "round_trip"
    assert bodies[0]["round_trip.distance"] == 20_000
    assert bodies[0]["round_trip.seed"] == 3
    assert route.properties.distance_m == 20_000


async def test_bounds_are_read_once_and_cached() -> None:
    calls: list[int] = []

    def info(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, json={"bbox": [-74.1, 45.2, -73.2, 45.8]})

    client = make_client(info)
    first = await client.bounds()
    await client.bounds()

    assert (first.min_lon, first.max_lat) == (-74.1, 45.8)
    assert len(calls) == 1


async def test_bad_bounds_response_raises() -> None:
    client = make_client(lambda request: httpx.Response(200, json={}))

    with pytest.raises(RoutingEngineError):
        await client.bounds()

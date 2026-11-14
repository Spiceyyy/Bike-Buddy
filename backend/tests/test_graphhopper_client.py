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


async def test_alternatives_return_every_path_with_climbing_and_2d_geometry() -> None:
    bodies: list[dict] = []
    path = {
        "distance": 7_000,
        "time": 1_500_000,
        "ascend": 69.4,
        "descend": 60.1,
        "points": {"type": "LineString", "coordinates": [[-73.55, 45.5, 30.0], [-73.6, 45.52, 41.5]]},
    }

    def capture(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"paths": [path, {**path, "distance": 7_400}]})

    end = Coordinate(lat=45.52, lon=-73.6)
    seek = {"priority": [{"if": "average_slope < 2", "multiply_by": "0.5"}]}
    routes = await make_client(capture).alternatives(START, end, custom_model=seek)

    assert bodies[0]["algorithm"] == "alternative_route"
    assert bodies[0]["alternative_route.max_paths"] == 3
    assert bodies[0]["custom_model"] == seek
    assert [r.properties.distance_m for r in routes] == [7_000, 7_400]
    assert routes[0].properties.ascend_m == 69.4
    assert routes[0].geometry.coordinates == [(-73.55, 45.5), (-73.6, 45.52)]

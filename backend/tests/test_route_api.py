"""API tests run the real GraphHopperClient against a fake GraphHopper server."""

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api import routes
from app.api.routes import get_routing_client
from app.config import Settings
from app.main import create_app
from app.routing.graphhopper import GraphHopperClient

ROUTE_BODY = {
    "start": {"lat": 41.82, "lon": -71.41},
    "end": {"lat": 41.49, "lon": -71.31},
}
GRAPHHOPPER_SUCCESS: dict[str, Any] = {
    "paths": [
        {
            "distance": 1234.5,
            "time": 300000,
            "points": {
                "type": "LineString",
                "coordinates": [[-71.41, 41.82], [-71.31, 41.49]],
            },
        }
    ]
}
Handler = Callable[[httpx.Request], httpx.Response]


def make_client(handler: Handler) -> TestClient:
    app = create_app(Settings(database_url="postgresql://unused"))
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://graphhopper"
    )
    routing = GraphHopperClient(http, profile="bike")
    app.dependency_overrides[get_routing_client] = lambda: routing
    return TestClient(app)


def graphhopper_error(details: str, point_index: int | None = None) -> Handler:
    hint: dict[str, Any] = {"message": "failed", "details": details}
    if point_index is not None:
        hint["point_index"] = point_index
    body = {"message": "failed", "hints": [hint]}
    return lambda request: httpx.Response(400, json=body)


def test_returns_route_as_geojson_feature() -> None:
    client = make_client(lambda request: httpx.Response(200, json=GRAPHHOPPER_SUCCESS))

    response = client.post("/api/route", json=ROUTE_BODY)

    assert response.status_code == 200
    assert response.json() == {
        "type": "Feature",
        "geometry": {
            "type": "LineString",
            "coordinates": [[-71.41, 41.82], [-71.31, 41.49]],
        },
        "properties": {"distance_m": 1234.5, "duration_s": 300.0, "profile": "bike"},
    }


@pytest.mark.parametrize(
    ("point_index", "label"), [(0, "start"), (1, "end")]
)
def test_unroutable_point_names_the_point(point_index: int, label: str) -> None:
    details = "com.graphhopper.util.exceptions.PointNotFoundException"
    client = make_client(graphhopper_error(details, point_index))

    response = client.post("/api/route", json=ROUTE_BODY)

    assert response.status_code == 422
    assert label in response.json()["detail"]


def test_out_of_bounds_point_is_unroutable() -> None:
    details = "com.graphhopper.util.exceptions.PointOutOfBoundsException"
    client = make_client(graphhopper_error(details, 1))

    assert client.post("/api/route", json=ROUTE_BODY).status_code == 422


def test_no_connection_returns_404() -> None:
    details = "com.graphhopper.util.exceptions.ConnectionNotFoundException"
    client = make_client(graphhopper_error(details))

    assert client.post("/api/route", json=ROUTE_BODY).status_code == 404


def test_unknown_engine_error_returns_502() -> None:
    client = make_client(lambda request: httpx.Response(500, text="boom"))

    assert client.post("/api/route", json=ROUTE_BODY).status_code == 502


def test_unreachable_engine_returns_502() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = make_client(refuse)

    assert client.post("/api/route", json=ROUTE_BODY).status_code == 502


def test_malformed_engine_response_returns_502() -> None:
    client = make_client(lambda request: httpx.Response(200, json={"paths": []}))

    assert client.post("/api/route", json=ROUTE_BODY).status_code == 502


def test_identical_start_and_end_rejected_without_calling_engine() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise AssertionError("GraphHopper should not be called")

    client = make_client(fail)
    point = {"lat": 41.82, "lon": -71.41}

    assert client.post("/api/route", json={"start": point, "end": point}).status_code == 422


def test_out_of_range_latitude_rejected() -> None:
    client = make_client(lambda request: httpx.Response(200, json=GRAPHHOPPER_SUCCESS))
    body = {"start": {"lat": 91, "lon": 0}, "end": {"lat": 41.49, "lon": -71.31}}

    assert client.post("/api/route", json=body).status_code == 422


def test_sends_lon_lat_order_to_graphhopper() -> None:
    seen: list[httpx.Request] = []

    def capture(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=GRAPHHOPPER_SUCCESS)

    make_client(capture).post("/api/route", json=ROUTE_BODY)

    sent = json.loads(seen[0].content)
    assert sent["points"] == [[-71.41, 41.82], [-71.31, 41.49]]
    assert sent["profile"] == "bike"


@pytest.mark.parametrize(
    ("engine_status", "postgis", "expected"),
    [(200, True, 200), (503, True, 503), (200, False, 503)],
)
def test_health_reflects_dependencies(
    monkeypatch: pytest.MonkeyPatch, engine_status: int, postgis: bool, expected: int
) -> None:
    async def fake_postgis(database_url: str) -> bool:
        return postgis

    monkeypatch.setattr(routes, "postgis_is_available", fake_postgis)
    client = make_client(lambda request: httpx.Response(engine_status, text="OK"))

    assert client.get("/health").status_code == expected

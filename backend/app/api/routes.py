from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel

from app.config import Settings
from app.db.health import postgis_is_available
from app.models.route import RouteFeature, RouteRequest, ViaRequest, ViaResponse
from app.routing.climbing import climbing_model
from app.routing.graphhopper import (
    GraphHopperClient,
    NoRouteFoundError,
    PointNotRoutableError,
    RoutingError,
)
from app.scoring.route_scenic import ScenicDescriber
from app.tools.plan_route import make_stop, stop_points

router = APIRouter()


def get_routing_client(request: Request) -> GraphHopperClient:
    return request.app.state.routing_client


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_scenic_describer(request: Request) -> ScenicDescriber | None:
    return getattr(request.app.state, "scenic_describer", None)


RoutingClient = Annotated[GraphHopperClient, Depends(get_routing_client)]
AppSettings = Annotated[Settings, Depends(get_settings)]
Scenery = Annotated[ScenicDescriber | None, Depends(get_scenic_describer)]


class HealthResponse(BaseModel):
    graphhopper: bool
    postgis: bool


@router.post("/api/route", response_model=RouteFeature)
async def create_route(
    body: RouteRequest, routing: RoutingClient, scenery: Scenery
) -> RouteFeature:
    """Return a cycling route between two points as a GeoJSON Feature."""
    try:
        route = await routing.route(body.start, body.end)
    except RoutingError as exc:
        raise _http_error(exc) from exc
    if scenery is not None:
        route.properties.scenic = await scenery.describe(route)
    return route


@router.post("/api/route/via", response_model=ViaResponse)
async def route_via_stop(
    body: ViaRequest, routing: RoutingClient, scenery: Scenery
) -> ViaResponse:
    """Re-route a planned option through the stop the rider picked."""
    points = stop_points(body.start, body.end, body.anchors, body.stop.coordinate)
    try:
        route = await routing.route_through(points, climbing_model(body.climbing))
    except RoutingError as exc:
        raise _http_error(exc) from exc
    if scenery is not None:
        route.properties.scenic = await scenery.describe(route)
    return ViaResponse(route=route, stop=make_stop(body.category, body.stop, route))


def _http_error(exc: RoutingError) -> HTTPException:
    if isinstance(exc, PointNotRoutableError):
        label = "start" if exc.point_index == 0 else "end"
        return HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"The {label} point is outside the map region or too far from a road.",
        )
    if isinstance(exc, NoRouteFoundError):
        return HTTPException(status.HTTP_404_NOT_FOUND, "No cycling route connects these points.")
    return HTTPException(status.HTTP_502_BAD_GATEWAY, "The routing engine is unavailable.")


@router.get("/health", response_model=HealthResponse)
async def health(
    response: Response, routing: RoutingClient, settings: AppSettings
) -> HealthResponse:
    result = HealthResponse(
        graphhopper=await routing.is_healthy(),
        postgis=await postgis_is_available(settings.database_url),
    )
    if not (result.graphhopper and result.postgis):
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return result

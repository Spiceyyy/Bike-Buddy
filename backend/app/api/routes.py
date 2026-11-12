from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel

from app.config import Settings
from app.db.health import postgis_is_available
from app.models.route import RouteFeature, RouteRequest
from app.routing.graphhopper import (
    GraphHopperClient,
    NoRouteFoundError,
    PointNotRoutableError,
    RoutingEngineError,
)
from app.scoring.route_scenic import ScenicDescriber

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
    except PointNotRoutableError as exc:
        label = "start" if exc.point_index == 0 else "end"
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"The {label} point is outside the map region or too far from a road.",
        ) from exc
    except NoRouteFoundError as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "No cycling route connects these two points.",
        ) from exc
    except RoutingEngineError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "The routing engine is unavailable.",
        ) from exc
    if scenery is not None:
        route.properties.scenic = await scenery.describe(route)
    return route


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

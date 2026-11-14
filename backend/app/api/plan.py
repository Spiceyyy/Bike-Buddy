import math
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.agent.llm import LLMClient, LLMError, LLMRateLimitError
from app.agent.orchestrator import run_agent
from app.agent.prompts import load_prompt
from app.api.routes import RoutingClient, Scenery
from app.models.plan import PlanRequest, PlanResponse
from app.routing.geocoder import NominatimGeocoder
from app.retrieval.place_search import PlaceSearch
from app.tools.base import Tool
from app.tools.plan_route import PlaceFinder, PlanRouteTool
from app.tools.search_places import PlaceRegistry, SearchPlacesTool

router = APIRouter()

LONG_WAIT_SECONDS = 120


def get_llm_client(request: Request) -> LLMClient:
    client = request.app.state.llm_client
    if client is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "The AI planner is not configured. Set GEMINI_API_KEY.",
        )
    return client


def get_geocoder(request: Request) -> NominatimGeocoder:
    return request.app.state.geocoder


def get_place_finder(request: Request) -> PlaceFinder | None:
    return getattr(request.app.state, "place_finder", None)


def get_place_search(request: Request) -> PlaceSearch | None:
    return getattr(request.app.state, "place_search", None)


def _rate_limit_message(retry_after_seconds: float | None) -> str:
    if retry_after_seconds is not None and retry_after_seconds > LONG_WAIT_SECONDS:
        hours = math.ceil(retry_after_seconds / 3600)
        return f"The AI planner has used up its daily quota. Try again in about {hours} h."
    return "The AI planner is busy. Please try again in a minute."


@router.post("/api/plan", response_model=PlanResponse)
async def create_plan(
    body: PlanRequest,
    routing: RoutingClient,
    llm: Annotated[LLMClient, Depends(get_llm_client)],
    geocoder: Annotated[NominatimGeocoder, Depends(get_geocoder)],
    scenery: Scenery,
    places: Annotated[PlaceFinder | None, Depends(get_place_finder)],
    place_search: Annotated[PlaceSearch | None, Depends(get_place_search)],
) -> PlanResponse:
    """Plan a ride from a plain-language request."""
    registry = PlaceRegistry()
    tool = PlanRouteTool(
        routing,
        geocoder,
        map_start=body.map_start,
        scenery=scenery,
        places=places,
        known_places=registry,
    )
    tools: list[Tool] = [tool]
    if place_search is not None:
        tools.append(
            SearchPlacesTool(place_search, routing, geocoder, registry, map_start=body.map_start)
        )
    try:
        run = await run_agent(llm, load_prompt("planner"), body.message, tools)
    except LLMRateLimitError as exc:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, _rate_limit_message(exc.retry_after_seconds)
        ) from exc
    except LLMError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "The AI planner is unavailable right now.",
        ) from exc

    return PlanResponse(
        reply=run.reply,
        route=tool.route,
        spec=tool.spec,
        start_name=tool.start_name,
        end_name=tool.end_name,
        stop=tool.stop,
        options=tool.options,
        start=tool.start,
        end=tool.end,
        via=tool.via,
        not_applied=tool.not_applied,
        usage=run.usage,
    )

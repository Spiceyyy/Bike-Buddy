import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agent.gemini import GEMINI_BASE_URL, GeminiClient
from app.api.plan import router as plan_router
from app.api.routes import router as routes_router
from app.config import Settings
from app.routing.geocoder import NominatimGeocoder
from app.routing.graphhopper import GraphHopperClient

GEOCODER_USER_AGENT = "scenic-route-finder/0.1"


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with (
            httpx.AsyncClient(
                base_url=resolved.graphhopper_url,
                timeout=resolved.graphhopper_timeout_seconds,
            ) as routing_http,
            httpx.AsyncClient(
                base_url=GEMINI_BASE_URL, timeout=resolved.llm_timeout_seconds
            ) as llm_http,
            httpx.AsyncClient(
                base_url=resolved.geocoder_url,
                timeout=resolved.geocoder_timeout_seconds,
                headers={"User-Agent": GEOCODER_USER_AGENT},
            ) as geocoder_http,
        ):
            app.state.routing_client = GraphHopperClient(
                routing_http, profile=resolved.graphhopper_profile
            )
            app.state.geocoder = NominatimGeocoder(geocoder_http)
            api_key = resolved.gemini_api_key.get_secret_value()
            app.state.llm_client = (
                GeminiClient(llm_http, api_key=api_key, model=resolved.gemini_model)
                if api_key
                else None
            )
            yield

    logging.basicConfig(level=logging.INFO)

    app = FastAPI(title="Scenic Route Finder", lifespan=lifespan)
    app.state.settings = resolved
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
    app.include_router(routes_router)
    app.include_router(plan_router)
    return app

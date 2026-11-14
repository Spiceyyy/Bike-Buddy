import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg
import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agent.gemini import GEMINI_BASE_URL, GeminiClient
from app.api.plan import router as plan_router
from app.api.routes import router as routes_router
from app.config import Settings
from app.db.places import PostgisPlaceFinder
from app.retrieval.embedding_cache import CachingEmbedder
from app.retrieval.embeddings import GeminiEmbedder
from app.retrieval.place_search import PostgisPlaceSearch
from app.routing.geocoder import NominatimGeocoder
from app.routing.graphhopper import GraphHopperClient
from app.scoring.route_scenic import PostgisScenicDescriber

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
            # min_size=0 connects lazily, so the API still starts if Postgres is down.
            asyncpg.create_pool(resolved.database_url, min_size=0, max_size=5) as db_pool,
        ):
            app.state.scenic_describer = PostgisScenicDescriber(db_pool)
            app.state.place_finder = PostgisPlaceFinder(db_pool)
            api_key = resolved.gemini_api_key.get_secret_value()
            embedder = (
                CachingEmbedder(GeminiEmbedder(llm_http, api_key), db_pool) if api_key else None
            )
            # Without an API key, search falls back to matching place names.
            app.state.place_search = PostgisPlaceSearch(db_pool, embedder)
            app.state.routing_client = GraphHopperClient(
                routing_http, profile=resolved.graphhopper_profile
            )
            app.state.geocoder = NominatimGeocoder(geocoder_http)
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

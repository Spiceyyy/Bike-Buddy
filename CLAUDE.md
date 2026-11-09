# Scenic Route Finder

An app that turns a plain-language cycling request (e.g. "a quiet 50 km loop along the water with a café halfway") into a real, rideable route chosen for scenery and riding feel, not speed or training load.

This is a portfolio project for AI/ML internship applications. Code quality, clear architecture, and measurable evaluation matter as much as features.

## Core design rule

**The LLM plans; the routing engine routes.**

- The LLM NEVER generates coordinates, road names, or route geometry itself.
- The LLM parses intent, retrieves scenic context, calls deterministic tools, and explains the result.
- Every route returned to the user must come from the routing engine.
- Every place named in an explanation must actually lie on (or near) the returned route.

## Architecture

```
ONLINE REQUEST PATH
  Web client ──▶ API gateway ──▶ Agent orchestrator (LLM loop with tool calls)
                                        │ tool calls
          ┌──────────────┬──────────────┼──────────────┐
          ▼              ▼              ▼              ▼
   Retrieval service  Routing engine  Scoring service  Enrichment
   (vector + geo)     (real routes)   (re-ranking)     (elevation, weather)
          │              │              │
          ▼              ▼              ▼
  STORAGE: Postgres + PostGIS + pgvector
  (scored road segments, place embeddings, saved routes, ratings)
          ▲              ▲
          │ writes       │
OFFLINE PIPELINE (nightly / on demand)
  Open data ──▶ Segment and score ──▶ Embed descriptions
  (OSM, elevation, wiki text)
```

### Components

| Component | Responsibility |
| --- | --- |
| Web client | Chat box, map view, route cards, GPX download, thumbs up/down feedback |
| API gateway | Auth, rate limits, request and tool-call logging |
| Agent orchestrator | Runs the LLM loop; exposes tools `search_places`, `plan_route`, `get_elevation`, `get_weather` with typed schemas |
| Retrieval service | Embeds queries, searches pgvector, filters by distance from start (hybrid search) |
| Routing engine | GraphHopper with a custom scenic profile; deterministic routes only |
| Scoring service | Scores and re-ranks candidate routes with a transparent, testable function |
| Enrichment adapters | Elevation, weather, surface data behind one interface |
| Storage | Postgres + PostGIS (segments, routes), pgvector (embeddings) |

## Request flow

1. **Parse intent.** LLM converts the request into a structured `RouteSpec` via tool calling: start point, target distance, loop or point-to-point, surface preference, max climbing, vibe keywords (water, forest, quiet, café).
2. **Retrieve scenic context (RAG).** Embed vibe keywords; search place/segment descriptions near the start; return parks, waterfronts, viewpoints, cafés.
3. **Generate candidates.** Pick 2–3 retrieved places as waypoints; request routes from the routing engine with the scenic profile. Produce several candidates.
4. **Enrich.** Add elevation gain, surface mix, traffic exposure, forecast for the ride window.
5. **Re-rank.** Deterministic score = scenic score − distance error penalty − constraint violation penalty. An LLM judge may break ties only.
6. **Explain.** LLM writes a short description citing places actually on the route. Return description + GPX + GeoJSON geometry.

If no candidate meets the constraints, relax one constraint at a time and tell the user what changed.

## Data sources

| Source | Provides |
| --- | --- |
| OpenStreetMap regional extract (Geofabrik) | Roads, bike paths, surfaces, parks, water, landmarks, cafés |
| Elevation model (SRTM or Copernicus DEM) | Climbing per segment |
| Wikipedia / Wikivoyage | Short place descriptions for RAG |
| Open-Meteo | Wind and rain forecast |
| User's own GPX/FIT files | Ground truth for routes the rider enjoyed |

Start with ONE small region to keep data and build times small.

### Offline scenic-scoring pipeline

1. Load the OSM extract; split the road network into segments.
2. Compute per-segment features: share near water or green space, road class / speed limit (traffic proxy), surface type, nearby viewpoints, intersection density.
3. Combine into a scenic score (hand-tuned weights first; learned weights from user ratings later).
4. Generate short text descriptions for places and segment clusters; embed and store in pgvector.
5. Export scores as edge weights for the GraphHopper custom profile.

## Tech stack

| Layer | Choice |
| --- | --- |
| Front end | Next.js (React) + MapLibre GL |
| Backend | Python 3.11+, FastAPI, Pydantic models for all tool schemas |
| LLM | API model with tool calling, behind a provider-agnostic interface |
| Embeddings | API embedding model, swappable for sentence-transformers |
| Database | Postgres + PostGIS + pgvector |
| Routing | GraphHopper (custom models) in Docker |
| Data processing | pyrosm or osmium, GeoPandas, Shapely |
| Dev environment | Docker Compose |
| Tests | pytest |

Write the agent loop by hand. Do NOT use LangChain, LangGraph, or similar frameworks.

## Suggested repo layout

```
backend/
  app/
    api/            # FastAPI routes
    agent/          # orchestrator loop, prompts, tool definitions
    tools/          # search_places, plan_route, get_elevation, get_weather
    retrieval/      # embedding + hybrid search
    scoring/        # route scoring and re-ranking
    models/         # Pydantic schemas (RouteSpec, Candidate, RouteResult)
    db/             # database access
  pipeline/         # offline OSM → segments → scores → embeddings
  tests/
evals/
  requests.jsonl    # test requests with hand-labelled expected specs
  run_evals.py
  results/          # one results file per version
frontend/
docker-compose.yml
README.md
```

## Build phases

Work ONE phase at a time. At the end of each phase: make sure tests pass, update the README, summarise what was built, and stop for review before starting the next phase.

1. **Routing skeleton (no AI).** Docker Compose with Postgres/PostGIS and GraphHopper loaded with one OSM extract. FastAPI endpoint returns a route between two points as GeoJSON. Front end shows it on a map.
2. **LLM planner.** Intent parsing into `RouteSpec` via tool calling; `plan_route` tool. Natural-language requests produce real routes. Create `evals/requests.jsonl` with the first 30 test requests and an intent-parsing eval.
3. **Scenic layer and RAG.** Offline scoring pipeline, vector index, `search_places` tool, multiple candidates, re-ranking. Add retrieval and constraint evals; record results.
4. **Polish and proof.** Explanations with an automated faithfulness check, weather, GPX export, feedback buttons, deployment config, README with architecture diagram and eval results table.
5. **(Later) Trainer mode.** Reuse the agent + tools pattern with the user's ride history; compute all metrics in code, LLM only explains and plans.

## Evaluation

Run `evals/run_evals.py` after every significant change and save results per version.

| Metric | How |
| --- | --- |
| Intent parsing accuracy | LLM `RouteSpec` vs hand-labelled spec |
| Retrieval recall@k | Expected places appear in top-k results |
| Constraint satisfaction | Distance within ±10%, climbing under limit, surface respected |
| Route quality | Scenic score of chosen route vs fastest-route baseline |
| Explanation faithfulness | Every named place lies within a set distance of the route |
| Latency and cost | Seconds and tokens per request |

## Coding conventions

- Type hints everywhere; Pydantic for all request, response and tool schemas.
- Keep LLM prompts in their own files under `agent/prompts/`, not inline strings.
- Log every LLM call and tool call (inputs, outputs, latency, tokens) for debugging and evals.
- Secrets only via environment variables; provide `.env.example`, never commit `.env`.
- Unit-test scoring and retrieval logic without calling the LLM (mock the LLM client).
- Prefer small, readable modules over clever abstractions; this code will be read by interviewers.

# Scenic Route Finder

Turns a plain-language cycling request into a real, rideable route chosen for scenery and riding feel. The design rule throughout: **the LLM plans; the routing engine routes.**

**Status: Phase 2 of 5, the LLM planner.** Type a request such as "a 30 km loop from the Old Port" and get a real cycling route. Routes are not yet chosen for scenery; that arrives in Phase 3.

## Architecture

```
Browser (Next.js + MapLibre GL)
   │  POST /api/plan  (or /api/route for two map clicks)
   ▼
FastAPI backend
   │
   ├─▶ Agent loop ──▶ Gemini (parses the request, calls tools, writes the reply)
   │      │
   │      ▼ plan_route tool (RouteSpec)
   │      ├─▶ Nominatim   (place name → coordinates)
   │      └─▶ GraphHopper (coordinates → real route)
   │
   └─▶ Postgres + PostGIS (running, no tables until Phase 3)
```

The LLM only ever sees place names and route summaries. Coordinates and geometry stay in code, and the route returned to the browser comes straight from the routing engine.

| Path | Purpose |
| --- | --- |
| [backend/app/agent/orchestrator.py](backend/app/agent/orchestrator.py) | The hand-written agent loop, with LLM and tool call logging |
| [backend/app/agent/llm.py](backend/app/agent/llm.py), [gemini.py](backend/app/agent/gemini.py) | Provider-agnostic LLM interface and its Gemini implementation |
| [backend/app/agent/prompts/planner.md](backend/app/agent/prompts/planner.md) | The system prompt |
| [backend/app/tools/plan_route.py](backend/app/tools/plan_route.py) | Turns a `RouteSpec` into a route |
| [backend/app/models/spec.py](backend/app/models/spec.py) | `RouteSpec`, the structured form of a ride request |
| [backend/app/routing/](backend/app/routing/) | GraphHopper and geocoder clients |
| [backend/app/evaluation/intent_scoring.py](backend/app/evaluation/intent_scoring.py) | Eval scoring, unit tested without an LLM |
| [evals/](evals/) | Labelled requests, eval runner, one results file per version |
| [graphhopper/](graphhopper/) | Pinned GraphHopper image, config, and extract download |
| [frontend/components/RouteMap.tsx](frontend/components/RouteMap.tsx) | Map, ride request box, route display |

## Running it

Requires Docker Desktop. Python and Node run inside containers, so host versions don't matter.

```sh
cp .env.example .env     # then set POSTGRES_PASSWORD and GEMINI_API_KEY
docker compose up --build
```

Open <http://localhost:3000>. Describe a ride, or click two points on the map. If you click one point and then describe a ride with no start, the clicked point becomes the start.

The first start downloads the OSM extract and builds the routing graph before the backend comes up. Later starts reuse both from a Docker volume. GraphHopper needs about 2 GB of memory (`GRAPHHOPPER_JAVA_OPTS`).

### Choosing a Gemini model

Set `GEMINI_MODEL` in `.env`. The default is `gemini-3.5-flash-lite`. Free-tier quotas are per model and can be small: `gemini-3.5-flash` allowed only 20 requests per day when tested, and each planned ride uses 2.

### Changing region

The default is the [BBBike Montreal extract](https://download.bbbike.org/osm/bbbike/Montreal/) (longitude -74.09 to -73.27, latitude 45.30 to 45.75). To change it, set `OSM_EXTRACT_URL` to any `.osm.pbf` URL and `MAP_CENTER` to a `lon,lat` inside it, then restart. Each extract gets its own graph cache.

### Changing the GraphHopper config

The routing graph is built once and cached, so changes to [graphhopper/config.yml](graphhopper/config.yml) or its custom models need the cache cleared:

```sh
docker compose stop graphhopper
docker compose run --rm --no-deps --entrypoint sh graphhopper -c "rm -rf /data/graph-cache/*"
docker compose up -d --build
```

### Routing rules

Ferries are excluded because a route must be rideable end to end, and motorways are left out of the graph. Points outside the extract's bounding box return a 422.

## Known limitations

- **Surface, climbing limit and vibes** are parsed but not applied yet. The app says so on each result.
- **Loop distance** is approximate. The planner tries 8 loops and keeps the closest, which landed within 10% for 22 of 24 tested start and distance pairs. When it misses, the reply says so.
- **Place matching** uses the first result from public Nominatim, so a name can match the wrong place. The app always shows the place it actually matched. Nominatim's public service is limited to one request per second and is not suitable for production traffic.

## API

### `POST /api/plan`

```json
{ "message": "A 30 km loop from the Old Port", "map_start": null }
```

`map_start` is an optional `{ "lat", "lon" }`, used when the message names no start.

Returns `reply`, `route` (a GeoJSON Feature, or null), `spec` (the parsed `RouteSpec`), `start_name` and `end_name` (the places actually matched), `not_applied`, and `usage` (LLM calls, tokens, latency).

| Status | Meaning |
| --- | --- |
| 422 | Invalid input |
| 429 | The LLM provider's rate limit or quota was reached |
| 502 | The LLM provider failed |
| 503 | `GEMINI_API_KEY` is not set |

### `POST /api/route`

```json
{ "start": { "lat": 45.504, "lon": -73.553 }, "end": { "lat": 45.435, "lon": -73.675 } }
```

Returns a GeoJSON Feature (coordinates shortened):

```json
{
  "type": "Feature",
  "geometry": { "type": "LineString", "coordinates": [[-73.552942, 45.503985], "..."] },
  "properties": { "distance_m": 14085.476, "duration_s": 2914.339, "profile": "bike" }
}
```

| Status | Meaning |
| --- | --- |
| 422 | Invalid input, or a point is outside the region or too far from a road |
| 404 | Both points are valid, but no cycling connection exists |
| 502 | The routing engine is unavailable or returned something unexpected |

### `GET /health`

Reports `graphhopper` and `postgis` as booleans. Returns 503 if either is down.

## Tests

```sh
docker compose run --rm --no-deps backend pytest
```

The LLM, geocoder and routing engine are all faked, so the tests need no running services and no API key.

## Evaluation

```sh
docker compose exec backend python /evals/run_evals.py --version my-version
```

The eval sends 30 hand-labelled requests from [evals/requests.jsonl](evals/requests.jsonl) to the live LLM and compares each `RouteSpec` with its label. They cover loops, point-to-point rides, French requests, missing details, and messages that are not ride requests. Results are saved after every request, so a run stopped by a quota continues when the same command is re-run.

Current result, `gemini-3.5-flash-lite` ([v3](evals/results/v3-gemini-3.5-flash-lite.json)):

| Metric | Result |
| --- | --- |
| Exact match (every field correct) | 93.3% (28 of 30) |
| Correct decision to call the tool or not | 96.7% (29 of 30) |
| Median latency per parse | 753 ms |
| Tokens across all 30 | 34,140 in, 1,176 out |

The two misses: "Plan me a ride" got a request for details instead of a tool call, and one French point-to-point request lost its destination.

Earlier prompt versions ([v1](evals/results/v1-gemini-3.5-flash-lite.json), [v2](evals/results/v2-gemini-3.5-flash-lite.json)) scored 100%, but v3 is the prompt in use. Treat these numbers with care: there are only 30 requests, one person wrote both the labels and the prompt, and small wording changes moved the score. A held-out set would give a more reliable figure.

## Roadmap

1. **Routing skeleton** (done)
2. **LLM planner** (done): intent parsing into `RouteSpec`, `plan_route` tool, first evals
3. Scenic layer and RAG: scoring pipeline, vector index, re-ranking
4. Polish and proof: faithful explanations, weather, GPX export, feedback, deployment
5. Trainer mode

Map data © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors. Geocoding by [Nominatim](https://nominatim.org/). Basemap by [OpenFreeMap](https://openfreemap.org/).

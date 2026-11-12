# Scenic Route Finder

Turns a plain-language cycling request into a real, rideable route chosen for scenery and riding feel. The design rule throughout: **the LLM plans; the routing engine routes.**

**Status: Phase 3 of 5, in progress.** Type a request such as "a 30 km loop from the Old Port" and get a real cycling route. Routes now lean toward scenic areas (water, green space, quiet roads) using scores from an offline pipeline, show their scenery stretch by stretch, can stop at a café, and can seek or avoid climbs. Place search (RAG) and scenic re-ranking are still to come.

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

The routing graph is built once and cached. [entrypoint.sh](graphhopper/entrypoint.sh) rebuilds it whenever [graphhopper/config.yml](graphhopper/config.yml) or the scenic zones change, so after editing the config run:

```sh
docker compose up -d --build graphhopper   # rebuilds the graph, a few minutes
```

To force a rebuild for any other reason, clear the cache:

```sh
docker compose stop graphhopper
docker compose run --rm --no-deps --entrypoint sh graphhopper -c "rm -rf /data/graph-cache/*"
docker compose up -d --build
```

The graph includes SRTM elevation (downloaded once into the data volume) and `average_slope`, used for climbing requests.

### Scenic scoring pipeline (Phase 3, in progress)

```sh
docker compose exec backend python -m pipeline.run            # score and write to PostGIS
docker compose exec backend python -m pipeline.run --dry-run  # score and print a summary only
```

The pipeline reads the same OSM extract as GraphHopper and writes one row per road segment to the `road_segments` table. Montreal produces about 278,000 segments (21,000 km) in roughly 80 seconds. It also writes scenic zones for GraphHopper (see below), and the 1,250 named cafés in the extract to the `places` table, for café stops. After a run, restart GraphHopper to route with the new zones:

```sh
docker compose restart graphhopper   # rebuilds the routing graph, about 7 minutes
```

How a segment is scored ([backend/pipeline/](backend/pipeline/)):

1. **Segment.** Rideable ways are split at every intersection, and again every 400 m, so one score never averages a riverside path with the arterial road it turns into.
2. **Measure.** Points are sampled every 25 m along each segment. Each feature is a share from 0 to 1, where 1 is better:

   | Feature | Weight | Measured as |
   | --- | --- | --- |
   | Water | 0.30 | Share of the segment within 100 m of a river, canal or lake |
   | Green | 0.25 | Share within 50 m of a park, wood, cemetery or nature reserve |
   | Quietness | 0.25 | Road class as a traffic proxy (cycleway 1.0 to trunk 0.0), capped on roads of 70 km/h or more, raised by separated bike tracks |
   | Calmness | 0.10 | Fewer intersections within 200 m means fewer stops |
   | Viewpoint | 0.05 | A mapped viewpoint within 200 m |
   | Surface | 0.05 | Smoothness from the surface tag. Preferring gravel is a per-ride choice applied later, not part of this score |

3. **Score.** The scenic score is the weighted sum, so every score can be explained feature by feature. Weights are hand-tuned for now, in [scoring.py](backend/pipeline/scoring.py).

Green areas under 0.5 ha and water under 0.2 ha are ignored, because OSM maps planters as gardens and fountains as water. Before this filter, downtown Rue Sainte-Catherine scored 36% green.

Spot checks after a full run:

| Street | Score | Why |
| --- | --- | --- |
| Lachine Canal bike path | 0.89 | Water and green all the way, no traffic |
| Chemin Olmsted (Mount Royal) | 0.60 | Fully green and quiet, no water |
| Rue Rachel Est | 0.33 | Quiet, with a bike path, but little scenery |
| Rue Sainte-Catherine Ouest | 0.21 | Busy, and an intersection every block |
| Rue Sherbrooke Est | 0.14 | Arterial road with little green |

#### How routing uses the scores

GraphHopper cannot read a score per segment without custom Java code, but its custom models can test whether a road lies in a named area. So the pipeline ([scenic_zones.py](backend/pipeline/scenic_zones.py)):

1. Cuts the map into 150 m cells and gives each cell the length-weighted mean score of its segments.
2. Groups cells scoring 0.55 or more into a `scenic` zone, and 0.40 to 0.55 into a `pleasant` zone.
3. Writes a custom model for the `scenic` profile. GraphHopper only lets a custom model lower priority, so roads in pleasant zones get ×0.7 and roads outside both get ×0.5. Routes are drawn toward scenic areas, and the built-in bike model still picks the quietest roads within each zone.

The zones are baked into the routing graph. [entrypoint.sh](graphhopper/entrypoint.sh) installs them at startup and rebuilds the graph whenever they change. Before the pipeline has run, the `scenic` profile routes exactly like `bike`. The app uses `scenic` by default (`GRAPHHOPPER_PROFILE`).

#### Scenery along each route

Every route, planned or clicked, comes back with a stretch-by-stretch scenery breakdown ([stretches.py](backend/app/scoring/stretches.py)). The map colours the route by band, and the panel lists each stretch. Clicking a stretch zooms to it.

1. The route is sampled every 25 m, and each point takes the score of the nearest scored segment within 15 m.
2. Consecutive points in the same band become a stretch, using the zone thresholds: scenic at 0.55 or more, pleasant from 0.40, plain below that. Points with no segment nearby are "not scored".
3. Stretches shorter than 300 m are merged into their longer neighbour, so one bridge does not split a canal ride.
4. Each stretch gets the OSM name covering most of it, and a reason: the feature that contributed most to its score (water, green space or quiet streets), or "busy road" / "little scenery" for plain stretches.

The `plan_route` tool also passes the planner up to three named scenic stretches, so the reply can mention places that are actually on the route. Looking up a 14 km route takes about 100 ms. If the database is down or the pipeline has not run, routes are returned without scenery.

### Café stops and climbing

The planner understands two more things in a request ([spec.py](backend/app/models/spec.py)):

| Field | Set when the rider says | What the planner does |
| --- | --- | --- |
| `stop: cafe` | "a café halfway", "coffee break on the way", "ride to a café" | Finds the café nearest the middle of the route, within 150 m, or else 600 m, and routes through it |
| `climbing: seek` | "hilly", "elevation training", "beaucoup de côtes" | Lowers the priority of flat roads, then keeps the hilliest candidate loop |
| `climbing: avoid` | "flat", "easy on the knees", or any climbing limit | Lowers the priority of steep climbs, then keeps the flattest candidate loop |

How it works ([plan_route.py](backend/app/tools/plan_route.py)):

- **Loops with a café.** The planner looks for a café between 30% and 70% of a candidate loop. If one is found, it re-routes through the loop's own 25% and 75% points and the café, so the ride keeps its shape rather than turning into an out-and-back. This is done for the four best loops, and the re-routed loops are ranked again, because going through a café changes a loop's length. In one test it would otherwise have shrunk a 40 km request to 32 km. If no loop passes a café, the reply says so.
- **Point-to-point with a café.** It looks along the middle half of the direct route, then routes start → café → end.
- **Climbing.** GraphHopper custom models can only lower priorities, so "seek" makes flat roads (slope under 2%) less attractive rather than hills more attractive ([climbing.py](backend/app/routing/climbing.py)). Across two 25 km loop starts, this plus picking the hilliest seed gave 407 and 697 m of climbing, against medians of 257 and 373 m without it.
- **Climbing limits.** Loops under the limit are preferred, and the reply says when no candidate met it.

The panel shows the café as a brown marker, its distance into the ride, and the route's climbing.



Ferries are excluded because a route must be rideable end to end, and motorways are left out of the graph. Points outside the extract's bounding box return a 422.

## Known limitations

- **Surface and vibes** are parsed but not applied yet. The app says so on each result.
- **Climbing figures are approximate.** SRTM elevation partly measures buildings and trees, so city routes show extra climbing: the flat Lachine Canal path reads 113 m over 14 km. Comparisons between routes are still meaningful.
- **Climbing on point-to-point rides** only uses the custom model. There is a single candidate, so a limit can be reported but not enforced.
- **Loop distance** is approximate. The planner tries 8 loops and keeps the closest, which landed within 10% for 22 of 24 tested start and distance pairs. When it misses, the reply says so.
- **Place matching** uses the first result from public Nominatim, so a name can match the wrong place. The app always shows the place it actually matched. Nominatim's public service is limited to one request per second and is not suitable for production traffic.

## API

### `POST /api/plan`

```json
{ "message": "A 30 km loop from the Old Port", "map_start": null }
```

`map_start` is an optional `{ "lat", "lon" }`, used when the message names no start.

Returns `reply`, `route` (a GeoJSON Feature, or null), `spec` (the parsed `RouteSpec`), `start_name` and `end_name` (the places actually matched), `stop` (the café routed through, with its coordinate and `distance_along_m`, or null), `not_applied`, and `usage` (LLM calls, tokens, latency).

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
  "properties": { "distance_m": 14085.476, "duration_s": 2914.339, "profile": "bike", "ascend_m": 113.2, "descend_m": 120.5, "scenic": { "...": "..." } }
}
```

| Status | Meaning |
| --- | --- |
| 422 | Invalid input, or a point is outside the region or too far from a road |
| 404 | Both points are valid, but no cycling connection exists |

`properties.scenic` holds `score`, `matched_share`, `band_shares` and `stretches` (each with `start_m`, `end_m`, `band`, `score`, `reason`, `name` and `coordinates`), or null when scenery is unavailable. Routes from `/api/plan` carry the same field.
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

The eval sends 36 hand-labelled requests from [evals/requests.jsonl](evals/requests.jsonl) to the live LLM and compares each `RouteSpec` with its label. They cover loops, point-to-point rides, café stops, climbing goals, French requests, missing details, and messages that are not ride requests. Results are saved after every request, so a run stopped by a quota continues when the same command is re-run.

Current result, `gemini-3.5-flash-lite` ([v5](evals/results/v5-gemini-3.5-flash-lite.json)), on all 36 requests:

| Metric | Result |
| --- | --- |
| Exact match (every field correct) | 94.4% (34 of 36) |
| Correct decision to call the tool or not | 97.2% (35 of 36) |
| Median latency per parse | 808 ms |
| Tokens across all 36 | 54,313 in, 1,484 out |

The two misses:

- "Plan me a ride" got a request for details instead of a tool call, as in v3.
- "I want to go to a cafe and have a good elevation training, 40 km from Outremont" was parsed as a ride to a destination called "a cafe". A prompt rule did not fix this, so `RouteSpec` now rejects generic names such as "a cafe" as a place, with a message saying to set `stop` instead. In the app, the orchestrator returns that message to the model, which corrects itself on the next call (3 LLM calls instead of 2). The eval scores only the first call, so this request still counts as a miss.

[v4](evals/results/v4-gemini-3.5-flash-lite.json) scored the same before that prompt rule. Between v3 and v4, the `cafe` vibe became the `stop` field, and a climbing limit now also sets `climbing: avoid`: the model did this unprompted, and avoiding climbs makes a route more likely to stay under the limit, so two labels were updated to match.

Earlier prompt versions ([v1](evals/results/v1-gemini-3.5-flash-lite.json), [v2](evals/results/v2-gemini-3.5-flash-lite.json)) scored 100% on 30 requests. Treat these numbers with care: there are only 36 requests, one person wrote both the labels and the prompt, and small wording changes moved the score. A held-out set would give a more reliable figure.

### Route quality: scenic profile against the plain bike profile

```sh
docker compose exec backend python /evals/run_route_quality.py --version my-version
```

Routes 20 fixed trips (10 point-to-point across Montreal, 10 loops) with both profiles and scores each route by sampling it every 25 m and matching each point to the nearest scored segment. Over 99% of route points match a segment. No LLM is involved.

The zone penalty was chosen by a sweep across three strengths:

| Penalty (pleasant / other) | Point-to-point routes improved | Worst point-to-point detour | Median loop score gain |
| --- | --- | --- | --- |
| 0.85 / 0.70 ([v1](evals/results/route-quality-v1.json)) | 3 of 10 | +3.9% | +0.040 |
| **0.70 / 0.50 ([v2](evals/results/route-quality-v2.json), in use)** | 5 of 10 | +8.9% | +0.054 |
| 0.55 / 0.30 (sweep only) | 8 of 10 | +68% | +0.075 |

Current result (v2):

| Metric | Point-to-point | Loops |
| --- | --- | --- |
| Median scenic score, bike → scenic | 0.400 → 0.407 | 0.476 → 0.552 |
| Routes more scenic / less scenic | 5 / 0 | 10 / 0 |
| Median extra distance | +0.3% | +4.6% |
| Largest extra distance | +8.9% | +9.4% |

The scenic profile helps loops most, because a loop has freedom to choose where it goes. Point-to-point trips gain little, because the bike model already prefers bike paths and a stronger push causes long detours. Twenty trips is a small sample, and the same scores are used both to steer and to judge the routes, so this measures whether the steering works, not whether riders find the routes nicer.

## Roadmap

1. **Routing skeleton** (done)
2. **LLM planner** (done): intent parsing into `RouteSpec`, `plan_route` tool, first evals
3. Scenic layer and RAG: scoring pipeline, vector index, re-ranking
4. Polish and proof: faithful explanations, weather, GPX export, feedback, deployment
5. Trainer mode

Map data © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors. Geocoding by [Nominatim](https://nominatim.org/). Basemap by [OpenFreeMap](https://openfreemap.org/).

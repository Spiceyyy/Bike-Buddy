-- Scored road segments, written by the offline pipeline (python -m pipeline.run).
-- Every feature column is 0..1, where 1 is better for a scenic, relaxed ride.
CREATE TABLE IF NOT EXISTS road_segments (
    id              bigserial PRIMARY KEY,
    osm_way_id      bigint NOT NULL,
    name            text,
    highway         text NOT NULL,
    surface         text,
    length_m        real NOT NULL,
    water_share     real NOT NULL,
    green_share     real NOT NULL,
    quietness       real NOT NULL,
    calmness        real NOT NULL,
    near_viewpoint  boolean NOT NULL,
    surface_quality real NOT NULL,
    scenic_score    real NOT NULL,
    geom            geometry(LineString, 4326) NOT NULL
);

CREATE INDEX IF NOT EXISTS road_segments_geom_idx ON road_segments USING gist (geom);
CREATE INDEX IF NOT EXISTS road_segments_score_idx ON road_segments (scenic_score);

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Named places a rider might ride to, past or stop at, rebuilt on every run.
-- description is what was embedded; search_places ranks by embedding and name.
DROP TABLE IF EXISTS places;
CREATE TABLE places (
    id          bigserial PRIMARY KEY,
    category    text NOT NULL,
    name        text NOT NULL,
    description text NOT NULL,
    area_ha     real,
    geom        geometry(Point, 4326) NOT NULL,
    embedding   vector(768)
);

CREATE INDEX places_geom_idx ON places USING gist (geom);
CREATE INDEX places_name_trgm_idx ON places USING gin (name gin_trgm_ops);
CREATE INDEX places_embedding_idx ON places USING hnsw (embedding vector_cosine_ops);

-- Embeddings by model and text, kept across runs so unchanged descriptions are
-- not sent to the embedding API again.
CREATE TABLE IF NOT EXISTS embedding_cache (
    key       text PRIMARY KEY,
    embedding vector(768) NOT NULL
);

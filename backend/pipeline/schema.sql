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

-- Named places a ride can stop at, such as cafes. category matches RouteSpec's stop types.
CREATE TABLE IF NOT EXISTS places (
    id       bigserial PRIMARY KEY,
    category text NOT NULL,
    name     text NOT NULL,
    geom     geometry(Point, 4326) NOT NULL
);

CREATE INDEX IF NOT EXISTS places_geom_idx ON places USING gist (geom);

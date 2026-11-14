"""Runs the scenic-scoring pipeline end to end.

    python -m pipeline.run [--pbf PATH] [--dry-run]

By default it reads the extract GraphHopper downloaded (OSM_EXTRACT_URL
decides the file name), writes segments to DATABASE_URL, and writes scenic
zones to /osm/scenic/<extract>.json, where GraphHopper picks them up on its
next start.
"""

import argparse
import asyncio
import logging
import os
import time
from pathlib import Path

import httpx
import numpy as np
import shapely

from app.agent.gemini import GEMINI_BASE_URL
from app.retrieval.embeddings import GeminiEmbedder
from pipeline.features import measure
from pipeline.osm_reader import OsmExtract, read_extract
from pipeline.projection import LocalProjection
from pipeline.scoring import (
    ScoringWeights,
    SegmentFeatures,
    calmness,
    quietness,
    scenic_score,
    surface_quality,
)
from pipeline.scenic_zones import ScenicZones, build_zones, custom_model, write_custom_model
from pipeline.segments import Segment, intersection_coords, split_roads
from pipeline.embed import embed_with_cache
from pipeline.places import build_place_rows, worth_embedding
from pipeline.store import ScoredSegmentRow, replace_places, replace_segments

logger = logging.getLogger("pipeline")

DEFAULT_OSM_DIR = Path("/osm")


def score_segments(
    segments: list[Segment], extract: OsmExtract, weights: ScoringWeights = ScoringWeights()
) -> list[ScoredSegmentRow]:
    lonlat_lines = np.array([shapely.LineString(segment.coords) for segment in segments])
    projection = _projection_for(lonlat_lines)

    def to_metres(geometries: list) -> list:
        return list(projection.to_metres(np.array(geometries, dtype=object)))

    def points(coords: list[tuple[float, float]]) -> list:
        return [shapely.Point(coord) for coord in coords]

    lines = projection.to_metres(lonlat_lines)
    geometric = measure(
        lines,
        water=to_metres(extract.water),
        green=to_metres(extract.green),
        viewpoints=to_metres(points(extract.viewpoints)),
        intersections=to_metres(
            points(intersection_coords(extract.roads, extract.intersections))
        ),
    )
    features = SegmentFeatures(
        water=geometric.water_share,
        green=geometric.green_share,
        quietness=np.array([quietness(segment.tags) for segment in segments]),
        calmness=calmness(geometric.intersections_nearby),
        viewpoint=geometric.near_viewpoint.astype(float),
        surface=np.array([surface_quality(segment.tags) for segment in segments]),
    )
    scores = scenic_score(features, weights)
    lengths = shapely.length(lines)
    wkb = shapely.to_wkb(lonlat_lines)

    return [
        ScoredSegmentRow(
            osm_way_id=segment.osm_way_id,
            name=segment.tags.get("name"),
            highway=segment.tags["highway"],
            surface=segment.tags.get("surface"),
            length_m=float(lengths[i]),
            water_share=float(features.water[i]),
            green_share=float(features.green[i]),
            quietness=float(features.quietness[i]),
            calmness=float(features.calmness[i]),
            near_viewpoint=bool(geometric.near_viewpoint[i]),
            surface_quality=float(features.surface[i]),
            scenic_score=float(scores[i]),
            geom_wkb=wkb[i],
        )
        for i, segment in enumerate(segments)
    ]


def _projection_for(lines: np.ndarray) -> LocalProjection:
    min_lon, min_lat, max_lon, max_lat = shapely.total_bounds(lines)
    return LocalProjection(origin_lon=(min_lon + max_lon) / 2, origin_lat=(min_lat + max_lat) / 2)


def zones_from_rows(rows: list[ScoredSegmentRow]) -> ScenicZones:
    lines = shapely.from_wkb([row.geom_wkb for row in rows])
    midpoints = shapely.get_coordinates(
        shapely.line_interpolate_point(lines, 0.5, normalized=True)
    )
    return build_zones(
        midpoints,
        lengths_m=np.array([row.length_m for row in rows]),
        scores=np.array([row.scenic_score for row in rows]),
    )


def default_pbf_path() -> Path:
    url = os.environ.get("OSM_EXTRACT_URL")
    if not url:
        raise SystemExit("Set OSM_EXTRACT_URL or pass --pbf.")
    return DEFAULT_OSM_DIR / url.rsplit("/", 1)[-1]


def default_zones_path(pbf: Path) -> Path:
    return DEFAULT_OSM_DIR / "scenic" / (pbf.name.removesuffix(".osm.pbf") + ".json")


def summarise(rows: list[ScoredSegmentRow]) -> None:
    scores = np.array([row.scenic_score for row in rows])
    lengths = np.array([row.length_m for row in rows])
    logger.info(
        "summary segments=%d total_km=%.0f score_p10=%.2f p50=%.2f p90=%.2f",
        len(rows),
        lengths.sum() / 1000,
        *np.percentile(scores, [10, 50, 90]),
    )
    named = [row for row in rows if row.name and row.length_m >= 100]
    for label, chosen in (
        ("most scenic", sorted(named, key=lambda row: -row.scenic_score)[:8]),
        ("least scenic", sorted(named, key=lambda row: row.scenic_score)[:5]),
    ):
        logger.info("%s named segments:", label)
        for row in chosen:
            logger.info(
                "  %.2f  %-40s %-12s water=%.2f green=%.2f quiet=%.2f calm=%.2f",
                row.scenic_score,
                row.name[:40],
                row.highway,
                row.water_share,
                row.green_share,
                row.quietness,
                row.calmness,
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pbf", type=Path, help="OSM extract (default: GraphHopper's)")
    parser.add_argument("--zones", type=Path, help="scenic zones output for GraphHopper")
    parser.add_argument("--dry-run", action="store_true", help="score but do not write")
    parser.add_argument(
        "--no-embeddings",
        action="store_true",
        help="store places without embeddings (search_places then matches names only)",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    pbf = args.pbf or default_pbf_path()
    started = time.perf_counter()

    extract = read_extract(pbf)
    logger.info("stage=read seconds=%.0f", time.perf_counter() - started)

    segments = split_roads(extract.roads, extract.intersections)
    logger.info("stage=segment segments=%d seconds=%.0f", len(segments), time.perf_counter() - started)

    rows = score_segments(segments, extract)
    logger.info("stage=score seconds=%.0f", time.perf_counter() - started)
    summarise(rows)

    zones = zones_from_rows(rows)
    model = custom_model(zones)
    logger.info(
        "stage=zones cells=%d scenic_km2=%.1f pleasant_km2=%.1f seconds=%.0f",
        zones.cell_count,
        _area_km2(zones.scenic),
        _area_km2(zones.pleasant),
        time.perf_counter() - started,
    )

    if args.dry_run:
        return
    asyncio.run(replace_segments(os.environ["DATABASE_URL"], rows))
    asyncio.run(
        store_places(os.environ["DATABASE_URL"], extract, embed=not args.no_embeddings)
    )
    zones_path = args.zones or default_zones_path(pbf)
    write_custom_model(zones_path, model)
    logger.info(
        "stage=store zones=%s zones_kb=%.0f seconds=%.0f",
        zones_path,
        zones_path.stat().st_size / 1024,
        time.perf_counter() - started,
    )
    logger.info("Restart GraphHopper to route with the new zones: docker compose restart graphhopper")


async def store_places(database_url: str, extract: OsmExtract, embed: bool) -> None:
    started = time.perf_counter()
    rows = build_place_rows(extract)
    embeddings: list[list[float] | None] = [None] * len(rows)
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if embed and not api_key:
        logger.warning("GEMINI_API_KEY is not set; storing places without embeddings")
    elif embed:
        chosen = [index for index, row in enumerate(rows) if worth_embedding(row)]
        async with httpx.AsyncClient(base_url=GEMINI_BASE_URL, timeout=60) as http:
            embedder = GeminiEmbedder(http, api_key)
            vectors = await embed_with_cache(
                database_url, embedder, [rows[index].description for index in chosen]
            )
        for index, vector in zip(chosen, vectors):
            embeddings[index] = vector
    await replace_places(database_url, rows, embeddings)
    logger.info(
        "stage=places places=%d embedded=%d seconds=%.0f",
        len(rows),
        sum(vector is not None for vector in embeddings),
        time.perf_counter() - started,
    )


def _area_km2(geometry: shapely.Geometry) -> float:
    """Approximate area of a lon/lat geometry, for the log only."""
    if geometry.is_empty:
        return 0.0
    min_lon, min_lat, max_lon, max_lat = geometry.bounds
    projection = LocalProjection((min_lon + max_lon) / 2, (min_lat + max_lat) / 2)
    return projection.to_metres(geometry).area / 1e6


if __name__ == "__main__":
    main()

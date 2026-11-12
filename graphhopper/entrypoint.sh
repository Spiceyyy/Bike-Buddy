#!/bin/sh
# Downloads the configured OSM extract once, then starts GraphHopper.
# GraphHopper builds the routing graph on first start and reuses it afterwards.
set -eu

: "${OSM_EXTRACT_URL:?OSM_EXTRACT_URL must be set}"

pbf="/data/$(basename "$OSM_EXTRACT_URL")"
graph="/data/graph-cache/$(basename "$pbf" .osm.pbf)"

if [ ! -f "$pbf" ]; then
  echo "Downloading $OSM_EXTRACT_URL"
  curl -fL --retry 3 -o "$pbf.part" "$OSM_EXTRACT_URL"
  mv "$pbf.part" "$pbf"
fi

# Scenic zones are written by the scoring pipeline (python -m pipeline.run).
# Until it has run, the scenic profile routes exactly like the bike profile.
zones="/data/scenic/$(basename "$pbf" .osm.pbf).json"
model=/opt/graphhopper/custom_models/scenic.json
if [ -f "$zones" ]; then
  cp "$zones" "$model"
else
  echo "No scenic zones at $zones yet; the scenic profile will match the bike profile."
  echo '{"priority": []}' > "$model"
fi

# The zones and config (profiles, elevation) are baked into the graph, so
# rebuild it when either changes.
build_sha=$(cat "$model" /opt/graphhopper/config.yml | sha256sum | cut -d' ' -f1)
if [ -d "$graph" ] && [ "$(cat "$graph.build-sha" 2>/dev/null)" != "$build_sha" ]; then
  echo "Scenic zones or config changed; rebuilding the routing graph."
  rm -rf "$graph"
fi
rm -f "$graph.scenic-sha"
echo "$build_sha" > "$graph.build-sha"

# Dropwizard reads dots in -Ddw.* as nesting. GraphHopper's keys contain literal
# dots ("datareader.file"), so those must be escaped.
# shellcheck disable=SC2086 # JAVA_OPTS is intentionally word-split.
exec java ${JAVA_OPTS:-} \
  "-Ddw.graphhopper.datareader\.file=$pbf" \
  "-Ddw.graphhopper.graph\.location=$graph" \
  -jar graphhopper.jar server config.yml

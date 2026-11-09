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

# Dropwizard reads dots in -Ddw.* as nesting. GraphHopper's keys contain literal
# dots ("datareader.file"), so those must be escaped.
# shellcheck disable=SC2086 # JAVA_OPTS is intentionally word-split.
exec java ${JAVA_OPTS:-} \
  "-Ddw.graphhopper.datareader\.file=$pbf" \
  "-Ddw.graphhopper.graph\.location=$graph" \
  -jar graphhopper.jar server config.yml

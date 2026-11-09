export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/** OpenFreeMap serves this basemap without an API key. */
export const MAP_STYLE_URL =
  process.env.NEXT_PUBLIC_MAP_STYLE_URL ?? "https://tiles.openfreemap.org/styles/liberty";

function parseCenter(value: string): [number, number] {
  const [lon, lat] = value.split(",").map(Number);
  return [lon, lat];
}

/** Initial map centre as [lon, lat]. Should sit inside the loaded OSM extract. */
export const MAP_CENTER = parseCenter(process.env.NEXT_PUBLIC_MAP_CENTER ?? "-73.6,45.52");

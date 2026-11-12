import type { ExpressionSpecification } from "maplibre-gl";

import type { RouteScenic, ScenicBand } from "./api";

export const BAND_COLORS: Record<ScenicBand, string> = {
  scenic: "#1f8a5b",
  pleasant: "#d99a1e",
  plain: "#8b9590",
  unknown: "#c3c9c5",
};

export const BAND_LABELS: Record<ScenicBand, string> = {
  scenic: "Scenic",
  pleasant: "Pleasant",
  plain: "Plain",
  unknown: "Not scored",
};

export const BAND_ORDER: ScenicBand[] = ["scenic", "pleasant", "plain", "unknown"];

/** One GeoJSON line per stretch, with its band and index for styling. */
export function stretchFeatures(scenic: RouteScenic | null): GeoJSON.FeatureCollection {
  return {
    type: "FeatureCollection",
    features: (scenic?.stretches ?? []).map((stretch, index) => ({
      type: "Feature",
      geometry: { type: "LineString", coordinates: stretch.coordinates },
      properties: { band: stretch.band, index },
    })),
  };
}

/** MapLibre expression that colours a stretch by its band. */
export const BAND_COLOR_EXPRESSION: ExpressionSpecification = [
  "match",
  ["get", "band"],
  "scenic",
  BAND_COLORS.scenic,
  "pleasant",
  BAND_COLORS.pleasant,
  "plain",
  BAND_COLORS.plain,
  BAND_COLORS.unknown,
];

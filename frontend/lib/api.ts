import type { Feature, LineString } from "geojson";

import { API_URL } from "./config";

export type LonLat = [number, number];

export type ScenicBand = "scenic" | "pleasant" | "plain" | "unknown";

export type ScenicStretch = {
  start_m: number;
  end_m: number;
  band: ScenicBand;
  score: number | null;
  reason: string | null;
  name: string | null;
  coordinates: LonLat[];
};

export type RouteScenic = {
  score: number;
  matched_share: number;
  band_shares: Record<ScenicBand, number>;
  stretches: ScenicStretch[];
};

export type RouteFeature = Feature<
  LineString,
  {
    distance_m: number;
    duration_s: number;
    profile: string;
    ascend_m: number | null;
    descend_m: number | null;
    scenic: RouteScenic | null;
  }
>;

export type Coordinate = { lat: number; lon: number };

export type RouteStop = {
  category: string;
  name: string;
  coordinate: Coordinate;
  distance_along_m: number;
  off_route_m: number | null;
};

export type RouteOption = {
  label: string;
  route: RouteFeature;
  stop: RouteStop | null;
  stop_choices: RouteStop[];
  anchors: Coordinate[];
};

export type Climbing = "any" | "avoid" | "seek";

export type PlanResponse = {
  reply: string;
  route: RouteFeature | null;
  spec: { route_type: "loop" | "point_to_point"; climbing: Climbing } | null;
  start_name: string | null;
  end_name: string | null;
  stop: RouteStop | null;
  options: RouteOption[];
  start: Coordinate | null;
  end: Coordinate | null;
  /** Places the route passes, found by search_places or named by the rider. */
  via: { name: string; coordinate: Coordinate }[];
  not_applied: string[];
};

export type ViaResponse = { route: RouteFeature; stop: RouteStop };

export function fetchRoute(
  start: LonLat,
  end: LonLat,
  signal: AbortSignal,
): Promise<RouteFeature> {
  return postJson("/api/route", { start: toCoordinate(start), end: toCoordinate(end) }, signal);
}

export function planRide(
  message: string,
  mapStart: LonLat | null,
  signal: AbortSignal,
): Promise<PlanResponse> {
  const map_start = mapStart ? toCoordinate(mapStart) : null;
  return postJson("/api/plan", { message, map_start }, signal);
}

/** Re-route a planned option through a stop the rider picked. No LLM call. */
export function routeVia(
  body: {
    start: Coordinate;
    end: Coordinate | null;
    anchors: Coordinate[];
    stop: { name: string; coordinate: Coordinate };
    category: string;
    climbing: Climbing;
  },
  signal: AbortSignal,
): Promise<ViaResponse> {
  return postJson("/api/route/via", body, signal);
}

function toCoordinate([lon, lat]: LonLat) {
  return { lon, lat };
}

async function postJson<T>(path: string, body: unknown, signal: AbortSignal): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });

  if (!response.ok) {
    throw new Error(await readErrorMessage(response));
  }
  return response.json();
}

async function readErrorMessage(response: Response): Promise<string> {
  const fallback = "Something went wrong. Please try again.";
  try {
    const body = await response.json();
    // FastAPI validation errors use a list here; our own errors use a string.
    return typeof body.detail === "string" ? body.detail : fallback;
  } catch {
    return fallback;
  }
}

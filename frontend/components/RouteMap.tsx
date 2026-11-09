"use client";

import {
  LngLatBounds,
  Map as MapLibreMap,
  Marker,
  NavigationControl,
  setWorkerUrl,
  type GeoJSONSource,
} from "maplibre-gl";
import { useEffect, useRef, useState, type FormEvent } from "react";

import {
  fetchRoute,
  planRide,
  type LonLat,
  type PlanResponse,
  type RouteFeature,
} from "@/lib/api";
import { MAP_CENTER, MAP_STYLE_URL } from "@/lib/config";

const ROUTE_SOURCE = "route";
const EMPTY_ROUTE: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };
const MARKER_COLORS = ["#1f6f54", "#a93226"];

function formatDistance(metres: number): string {
  return `${(metres / 1000).toFixed(1)} km`;
}

function formatDuration(seconds: number): string {
  const totalMinutes = Math.round(seconds / 60);
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return hours > 0 ? `${hours} h ${minutes} min` : `${minutes} min`;
}

function errorMessage(reason: unknown, fallback: string): string {
  return reason instanceof Error ? reason.message : fallback;
}

/** Marker positions for a planned route: a loop only needs one. */
function plannedMarkers(plan: PlanResponse): LonLat[] {
  const coordinates = plan.route?.geometry.coordinates;
  if (!coordinates?.length) return [];
  const first = coordinates[0] as LonLat;
  const last = coordinates[coordinates.length - 1] as LonLat;
  return plan.spec?.route_type === "loop" ? [first] : [first, last];
}

export default function RouteMap() {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const markersRef = useRef<Marker[]>([]);
  const planRequestRef = useRef<AbortController | null>(null);

  const [points, setPoints] = useState<LonLat[]>([]);
  const [clickedRoute, setClickedRoute] = useState<RouteFeature | null>(null);
  const [isRouting, setIsRouting] = useState(false);
  const [routeError, setRouteError] = useState<string | null>(null);

  const [message, setMessage] = useState("");
  const [plan, setPlan] = useState<PlanResponse | null>(null);
  const [isPlanning, setIsPlanning] = useState(false);
  const [planError, setPlanError] = useState<string | null>(null);

  const route = plan?.route ?? clickedRoute;
  const error = planError ?? routeError;

  // Create the map once. Clicks are enabled only after the route layer exists.
  useEffect(() => {
    // MapLibre 6 can't locate its worker from inside a bundle, so point at it here.
    setWorkerUrl(
      new URL("../node_modules/maplibre-gl/dist/maplibre-gl-worker.mjs", import.meta.url).href,
    );
    const map = new MapLibreMap({
      container: containerRef.current!,
      style: MAP_STYLE_URL,
      center: MAP_CENTER,
      zoom: 10,
      // Clicks place route points, so a double-click must not also zoom.
      doubleClickZoom: false,
    });
    map.addControl(new NavigationControl(), "top-right");
    mapRef.current = map;

    map.on("load", () => {
      map.addSource(ROUTE_SOURCE, { type: "geojson", data: EMPTY_ROUTE });
      map.addLayer({
        id: "route-line",
        type: "line",
        source: ROUTE_SOURCE,
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": "#e4572e", "line-width": 5 },
      });
      map.on("click", (event) => {
        const clicked: LonLat = [event.lngLat.lng, event.lngLat.lat];
        setPlan(null);
        setPlanError(null);
        setPoints((current) => {
          // A third click starts a new route from the clicked point.
          if (current.length >= 2) return [clicked];
          // The second half of a double-click lands on the same coordinates.
          const previous = current.at(-1);
          if (previous?.[0] === clicked[0] && previous?.[1] === clicked[1]) return current;
          return [...current, clicked];
        });
      });
    });

    return () => {
      planRequestRef.current?.abort();
      map.remove();
      mapRef.current = null;
    };
  }, []);

  // Keep the markers in step with what is on the map.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    const positions = plan?.route ? plannedMarkers(plan) : points;
    markersRef.current.forEach((marker) => marker.remove());
    markersRef.current = positions.map((position, index) =>
      new Marker({ color: MARKER_COLORS[index] }).setLngLat(position).addTo(map),
    );
  }, [points, plan]);

  // Request a direct route whenever two points are clicked.
  useEffect(() => {
    setClickedRoute(null);
    setRouteError(null);
    if (points.length !== 2) return;

    const controller = new AbortController();
    setIsRouting(true);
    fetchRoute(points[0], points[1], controller.signal)
      .then(setClickedRoute)
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setRouteError(errorMessage(reason, "Could not find a route."));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setIsRouting(false);
      });

    return () => {
      controller.abort();
      setIsRouting(false);
    };
  }, [points]);

  // Draw the current route and bring it into view.
  useEffect(() => {
    const map = mapRef.current;
    const source = map?.getSource<GeoJSONSource>(ROUTE_SOURCE);
    if (!map || !source) return;

    source.setData(route ?? EMPTY_ROUTE);
    if (route) {
      const bounds = new LngLatBounds();
      route.geometry.coordinates.forEach(([lon, lat]) => bounds.extend([lon, lat]));
      map.fitBounds(bounds, { padding: 80, maxZoom: 15 });
    }
  }, [route]);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const text = message.trim();
    if (!text) return;

    planRequestRef.current?.abort();
    const controller = new AbortController();
    planRequestRef.current = controller;
    setIsPlanning(true);
    setPlanError(null);
    setPlan(null);

    try {
      const result = await planRide(text, points[0] ?? null, controller.signal);
      setPlan(result);
      if (result.route) setPoints([]);
    } catch (reason) {
      if (!controller.signal.aborted) {
        setPlanError(errorMessage(reason, "Could not plan that ride."));
      }
    } finally {
      if (!controller.signal.aborted) setIsPlanning(false);
    }
  }

  function handleClear() {
    planRequestRef.current?.abort();
    setIsPlanning(false);
    setPlan(null);
    setPlanError(null);
    setPoints([]);
  }

  let hint: string | null = null;
  if (isPlanning) hint = "Planning your ride…";
  else if (isRouting) hint = "Finding a route…";
  else if (!plan && !error && points.length === 0) {
    hint = "Describe a ride, or click two points on the map.";
  } else if (!plan && !error && points.length === 1) {
    hint = "Start set. Click a finish, or describe your ride.";
  }

  const canClear = points.length > 0 || plan !== null || planError !== null;

  return (
    <main className="map-page">
      <div ref={containerRef} className="map" />
      <section className="panel" aria-live="polite">
        <h1>Scenic Route Finder</h1>
        <form className="plan-form" onSubmit={handleSubmit}>
          <input
            type="text"
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            maxLength={1000}
            placeholder="A 30 km loop from the Old Port"
            aria-label="Describe your ride"
          />
          <button type="submit" disabled={isPlanning || !message.trim()}>
            Plan
          </button>
        </form>
        {hint && <p className="panel-hint">{hint}</p>}
        {plan && <p className="panel-reply">{plan.reply}</p>}
        {plan?.route && (
          <p className="panel-note">
            {plan.spec?.route_type === "loop"
              ? `Loop from ${plan.start_name}`
              : `${plan.start_name} → ${plan.end_name}`}
          </p>
        )}
        {error && (
          <p className="panel-error" role="alert">
            {error}
          </p>
        )}
        {route && (
          <dl className="route-stats">
            <div>
              <dt>Distance</dt>
              <dd>{formatDistance(route.properties.distance_m)}</dd>
            </div>
            <div>
              <dt>Riding time</dt>
              <dd>{formatDuration(route.properties.duration_s)}</dd>
            </div>
          </dl>
        )}
        {plan && plan.not_applied.length > 0 && (
          <p className="panel-note">Not supported yet: {plan.not_applied.join(", ")}.</p>
        )}
        {canClear && (
          <button type="button" className="panel-clear" onClick={handleClear}>
            Clear
          </button>
        )}
      </section>
    </main>
  );
}

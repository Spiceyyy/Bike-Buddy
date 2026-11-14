"use client";

import {
  LngLatBounds,
  Map as MapLibreMap,
  Marker,
  NavigationControl,
  Popup,
  setWorkerUrl,
  type GeoJSONSource,
  type MapMouseEvent,
} from "maplibre-gl";
import { useEffect, useRef, useState, type FormEvent } from "react";

import {
  fetchRoute,
  planRide,
  routeVia,
  type LonLat,
  type PlanResponse,
  type RouteFeature,
  type RouteOption,
  type RouteStop,
} from "@/lib/api";
import { MAP_CENTER, MAP_STYLE_URL } from "@/lib/config";
import { BAND_COLOR_EXPRESSION, stretchFeatures } from "@/lib/scenery";
import CafeChoices, { sameStop } from "./CafeChoices";
import RouteOptions from "./RouteOptions";
import ScenerySummary from "./ScenerySummary";

const ROUTE_SOURCE = "route";
const STRETCH_SOURCE = "scenic-stretches";
const OTHER_ROUTES_SOURCE = "other-routes";
const CAFE_SOURCE = "cafe-choices";
const NO_SELECTION = -1;
const EMPTY: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };
const MARKER_COLORS = ["#1f6f54", "#a93226"];
const STOP_COLOR = "#7a4b2a";
const VIA_COLOR = "#5b4b8a";
// Clicks this close (in pixels) to a line or café dot count as clicking it.
const CLICK_TOLERANCE_PX = 10;

function formatDistance(metres: number): string {
  return `${(metres / 1000).toFixed(1)} km`;
}

function formatKm(metres: number): string {
  return (metres / 1000).toFixed(1);
}

function formatDuration(seconds: number): string {
  const totalMinutes = Math.round(seconds / 60);
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return hours > 0 ? `${hours} h ${minutes} min` : `${minutes} min`;
}

/** Keeps fitted routes clear of the side panel on wide screens. */
function fitPadding(base: number) {
  const panelWidth = window.innerWidth >= 720 ? 372 : 0;
  return { top: base, bottom: base, right: base, left: base + panelWidth };
}

function errorMessage(reason: unknown, fallback: string): string {
  return reason instanceof Error ? reason.message : fallback;
}

/** Marker positions for a planned route: a loop only needs one. */
function plannedMarkers(route: RouteFeature, isLoop: boolean): LonLat[] {
  const coordinates = route.geometry.coordinates;
  if (!coordinates.length) return [];
  const first = coordinates[0] as LonLat;
  const last = coordinates[coordinates.length - 1] as LonLat;
  return isLoop ? [first] : [first, last];
}

function otherRouteFeatures(options: RouteOption[], selected: number): GeoJSON.FeatureCollection {
  return {
    type: "FeatureCollection",
    features: options.flatMap((option, index) =>
      index === selected ? [] : [{ ...option.route, properties: { index } }],
    ),
  };
}

function cafeFeatures(option: RouteOption | null): GeoJSON.FeatureCollection {
  return {
    type: "FeatureCollection",
    features: (option?.stop_choices ?? []).map((choice, index) => ({
      type: "Feature",
      geometry: { type: "Point", coordinates: [choice.coordinate.lon, choice.coordinate.lat] },
      properties: { index, name: choice.name },
    })),
  };
}

function hitIndex(map: MapLibreMap, event: MapMouseEvent, layer: string): number | null {
  const { x, y } = event.point;
  const box: [[number, number], [number, number]] = [
    [x - CLICK_TOLERANCE_PX, y - CLICK_TOLERANCE_PX],
    [x + CLICK_TOLERANCE_PX, y + CLICK_TOLERANCE_PX],
  ];
  const feature = map.queryRenderedFeatures(box, { layers: [layer] })[0];
  return feature ? Number(feature.properties.index) : null;
}

export default function RouteMap() {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const markersRef = useRef<Marker[]>([]);
  const planRequestRef = useRef<AbortController | null>(null);
  const viaRequestRef = useRef<AbortController | null>(null);
  // The map's click handler is registered once, so it calls the latest handlers through refs.
  const selectOptionRef = useRef<(index: number) => void>(() => {});
  const pickCafeRef = useRef<(index: number) => void>(() => {});
  const hasOptionsRef = useRef(false);

  const [points, setPoints] = useState<LonLat[]>([]);
  const [clickedRoute, setClickedRoute] = useState<RouteFeature | null>(null);
  const [isRouting, setIsRouting] = useState(false);
  const [routeError, setRouteError] = useState<string | null>(null);

  const [message, setMessage] = useState("");
  const [plan, setPlan] = useState<PlanResponse | null>(null);
  const [isPlanning, setIsPlanning] = useState(false);
  const [planError, setPlanError] = useState<string | null>(null);

  // The planned options, updated in place when the rider picks a different café.
  const [options, setOptions] = useState<RouteOption[]>([]);
  const [selectedOption, setSelectedOption] = useState(0);
  const [isRerouting, setIsRerouting] = useState(false);
  const [viaError, setViaError] = useState<string | null>(null);

  const [selectedStretch, setSelectedStretch] = useState<number | null>(null);

  const option = options[selectedOption] ?? null;
  const route = option?.route ?? clickedRoute;
  const stop = option?.stop ?? null;
  const scenic = route?.properties.scenic ?? null;
  const isLoop = plan?.spec?.route_type === "loop";
  const error = planError ?? routeError ?? viaError;

  // Create the map once. Clicks are enabled only after the route layers exist.
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
      // Other route options: dashed and blue-grey, so they never look like the
      // selected route's grey "plain" stretches.
      map.addSource(OTHER_ROUTES_SOURCE, { type: "geojson", data: EMPTY });
      map.addLayer({
        id: "other-routes",
        type: "line",
        source: OTHER_ROUTES_SOURCE,
        layout: { "line-join": "round" },
        paint: {
          "line-color": "#4f6d8f",
          "line-width": 3.5,
          "line-opacity": 0.75,
          "line-dasharray": [2, 1.5],
        },
      });
      map.addSource(ROUTE_SOURCE, { type: "geojson", data: EMPTY });
      map.addLayer({
        id: "route-line",
        type: "line",
        source: ROUTE_SOURCE,
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": "#e4572e", "line-width": 5 },
      });
      // Scenic stretches cover the plain route line when scores are available.
      map.addSource(STRETCH_SOURCE, { type: "geojson", data: EMPTY });
      map.addLayer({
        id: "scenic-casing",
        type: "line",
        source: STRETCH_SOURCE,
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": "#ffffff", "line-width": 8 },
      });
      map.addLayer({
        id: "scenic-line",
        type: "line",
        source: STRETCH_SOURCE,
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": BAND_COLOR_EXPRESSION, "line-width": 5 },
      });
      map.addLayer({
        id: "scenic-selected",
        type: "line",
        source: STRETCH_SOURCE,
        filter: ["==", ["get", "index"], NO_SELECTION],
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": BAND_COLOR_EXPRESSION, "line-width": 10 },
      });
      // Cafés the rider can switch to.
      map.addSource(CAFE_SOURCE, { type: "geojson", data: EMPTY });
      map.addLayer({
        id: "cafe-choices",
        type: "circle",
        source: CAFE_SOURCE,
        paint: {
          "circle-radius": 6,
          "circle-color": STOP_COLOR,
          "circle-stroke-color": "#ffffff",
          "circle-stroke-width": 2,
        },
      });

      map.on("click", (event) => {
        const cafe = hitIndex(map, event, "cafe-choices");
        if (cafe !== null) return pickCafeRef.current(cafe);
        const other = hitIndex(map, event, "other-routes");
        if (other !== null) return selectOptionRef.current(other);
        // While planned routes are shown, a stray click must not throw them away.
        if (hasOptionsRef.current) return;

        const clicked: LonLat = [event.lngLat.lng, event.lngLat.lat];
        setPlan(null);
        setOptions([]);
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
      for (const layer of ["cafe-choices", "other-routes"]) {
        map.on("mouseenter", layer, () => (map.getCanvas().style.cursor = "pointer"));
        map.on("mouseleave", layer, () => (map.getCanvas().style.cursor = ""));
      }
    });

    return () => {
      planRequestRef.current?.abort();
      viaRequestRef.current?.abort();
      map.remove();
      mapRef.current = null;
    };
  }, []);

  // Keep the markers in step with what is on the map.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    const positions = option ? plannedMarkers(option.route, isLoop) : points;
    markersRef.current.forEach((marker) => marker.remove());
    markersRef.current = positions.map((position, index) =>
      new Marker({ color: MARKER_COLORS[index] }).setLngLat(position).addTo(map),
    );
    if (stop) {
      markersRef.current.push(
        new Marker({ color: STOP_COLOR, scale: 0.85 })
          .setLngLat([stop.coordinate.lon, stop.coordinate.lat])
          .setPopup(new Popup({ offset: 24, closeButton: false }).setText(stop.name))
          .addTo(map),
      );
    }
    for (const place of option ? (plan?.via ?? []) : []) {
      markersRef.current.push(
        new Marker({ color: VIA_COLOR, scale: 0.85 })
          .setLngLat([place.coordinate.lon, place.coordinate.lat])
          .setPopup(new Popup({ offset: 24, closeButton: false }).setText(place.name))
          .addTo(map),
      );
    }
  }, [points, option, stop, isLoop, plan]);

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

  // Draw the current route, the other options and the café choices, and bring them into view.
  useEffect(() => {
    const map = mapRef.current;
    const source = map?.getSource<GeoJSONSource>(ROUTE_SOURCE);
    if (!map || !source) return;

    source.setData(route ?? EMPTY);
    map.getSource<GeoJSONSource>(STRETCH_SOURCE)?.setData(stretchFeatures(scenic));
    map.getSource<GeoJSONSource>(OTHER_ROUTES_SOURCE)?.setData(
      otherRouteFeatures(options, selectedOption),
    );
    map.getSource<GeoJSONSource>(CAFE_SOURCE)?.setData(cafeFeatures(option));
    map.setFilter("scenic-selected", ["==", ["get", "index"], NO_SELECTION]);
    setSelectedStretch(null);
    if (route) {
      const bounds = new LngLatBounds();
      route.geometry.coordinates.forEach(([lon, lat]) => bounds.extend([lon, lat]));
      map.fitBounds(bounds, { padding: fitPadding(60), maxZoom: 15 });
    }
  }, [route, scenic, options, selectedOption, option]);

  // Highlight the stretch picked in the panel and bring it into view.
  function handleSelectStretch(index: number | null) {
    setSelectedStretch(index);
    const map = mapRef.current;
    if (!map) return;
    map.setFilter("scenic-selected", ["==", ["get", "index"], index ?? NO_SELECTION]);
    const stretch = index === null ? null : scenic?.stretches[index];
    if (stretch) {
      const bounds = new LngLatBounds();
      stretch.coordinates.forEach(([lon, lat]) => bounds.extend([lon, lat]));
      map.fitBounds(bounds, { padding: fitPadding(80), maxZoom: 16 });
    }
  }

  function handleSelectOption(index: number) {
    viaRequestRef.current?.abort();
    setIsRerouting(false);
    setViaError(null);
    setSelectedOption(index);
  }

  /** Re-route the selected option through a café, or back to its planned route. No LLM call. */
  async function handlePickCafe(choice: RouteStop | null) {
    const planned = plan?.options[selectedOption];
    if (!plan || !option || !planned || !plan.start) return;
    if (sameStop(choice, option.stop)) return;
    const index = selectedOption;

    if (choice === null) {
      setOptions((current) => current.map((o, i) => (i === index ? planned : o)));
      return;
    }

    viaRequestRef.current?.abort();
    const controller = new AbortController();
    viaRequestRef.current = controller;
    setIsRerouting(true);
    setViaError(null);
    try {
      const result = await routeVia(
        {
          start: plan.start,
          end: plan.end,
          anchors: option.anchors,
          stop: { name: choice.name, coordinate: choice.coordinate },
          category: choice.category,
          climbing: plan.spec?.climbing ?? "any",
        },
        controller.signal,
      );
      setOptions((current) =>
        current.map((o, i) => (i === index ? { ...o, route: result.route, stop: result.stop } : o)),
      );
    } catch (reason) {
      if (!controller.signal.aborted) {
        setViaError(errorMessage(reason, "Could not route through that café."));
      }
    } finally {
      if (!controller.signal.aborted) setIsRerouting(false);
    }
  }

  selectOptionRef.current = handleSelectOption;
  hasOptionsRef.current = options.length > 0;
  pickCafeRef.current = (index: number) => {
    const choice = option?.stop_choices[index];
    if (choice && !isRerouting) void handlePickCafe(choice);
  };

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const text = message.trim();
    if (!text) return;

    planRequestRef.current?.abort();
    viaRequestRef.current?.abort();
    const controller = new AbortController();
    planRequestRef.current = controller;
    setIsPlanning(true);
    setPlanError(null);
    setViaError(null);
    setPlan(null);
    setOptions([]);

    try {
      const result = await planRide(text, points[0] ?? null, controller.signal);
      setPlan(result);
      setOptions(result.options);
      setSelectedOption(0);
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
    viaRequestRef.current?.abort();
    setIsPlanning(false);
    setIsRerouting(false);
    setPlan(null);
    setOptions([]);
    setPlanError(null);
    setViaError(null);
    setPoints([]);
  }

  let hint: string | null = null;
  if (isPlanning) hint = "Planning your ride…";
  else if (isRouting) hint = "Finding a route…";
  else if (isRerouting) hint = "Routing through that café…";
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
        {option && (
          <p className="panel-note">
            {isLoop ? `Loop from ${plan?.start_name}` : `${plan?.start_name} → ${plan?.end_name}`}
          </p>
        )}
        {options.length > 1 && (
          <RouteOptions options={options} selected={selectedOption} onSelect={handleSelectOption} />
        )}
        {option && (
          <p className="panel-note">
            Click a dashed route or a café dot on the map to switch. Clear to start over.
          </p>
        )}
        {option && plan && plan.via.length > 0 && (
          <p className="panel-stop">
            <span className="swatch" style={{ background: VIA_COLOR }} />
            Passes {plan.via.map((place) => place.name).join(", ")}
          </p>
        )}
        {stop && (
          <p className="panel-stop">
            <span className="swatch" style={{ background: STOP_COLOR }} />
            Café stop: {stop.name}, at km {formatKm(stop.distance_along_m)}
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
            {route.properties.ascend_m != null && (
              <div>
                <dt>Climbing</dt>
                <dd title="Approximate: the elevation data partly measures buildings and trees">
                  ~{Math.round(route.properties.ascend_m)} m
                </dd>
              </div>
            )}
          </dl>
        )}
        {option && (
          <CafeChoices
            choices={option.stop_choices}
            current={stop}
            canRemove={plan?.options[selectedOption]?.stop === null}
            busy={isRerouting}
            onPick={handlePickCafe}
          />
        )}
        {scenic && (
          <ScenerySummary
            scenic={scenic}
            selected={selectedStretch}
            onSelect={handleSelectStretch}
          />
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

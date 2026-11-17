import type { GpxWaypoint, PlanResponse, RouteFeature, RouteStop } from "./api";

/**
 * What to export for the route on screen: its line, a name, and waypoints for the
 * start, end, café stop and places passed. Works for planned rides and for a
 * route between two map clicks (no plan).
 */
export function gpxRequest(
  route: RouteFeature,
  plan: PlanResponse | null,
  stop: RouteStop | null,
  optionLabel: string | null,
) {
  const coordinates = route.geometry.coordinates;
  const [firstLon, firstLat] = coordinates[0];
  const [lastLon, lastLat] = coordinates[coordinates.length - 1];
  const isLoop = plan?.spec?.route_type === "loop";
  const startName = plan?.start_name ?? "Start";
  const endName = plan?.end_name ?? "Finish";

  let name = plan ? (isLoop ? `Loop from ${startName}` : `${startName} to ${endName}`) : "Route";
  if (optionLabel) name += ` (${optionLabel})`;

  const waypoints: GpxWaypoint[] = [
    { name: startName, coordinate: { lon: firstLon, lat: firstLat }, kind: "start" },
  ];
  if (!isLoop) {
    waypoints.push({ name: endName, coordinate: { lon: lastLon, lat: lastLat }, kind: "end" });
  }
  for (const place of plan?.via ?? []) {
    waypoints.push({ name: place.name, coordinate: place.coordinate, kind: "via" });
  }
  if (stop) {
    waypoints.push({ name: stop.name, coordinate: stop.coordinate, kind: "stop" });
  }
  return { name, track: route.geometry, waypoints };
}

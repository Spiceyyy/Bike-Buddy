import type { RouteStop } from "@/lib/api";

type Props = {
  choices: RouteStop[];
  current: RouteStop | null;
  /** Whether the route can go back to having no stop. */
  canRemove: boolean;
  busy: boolean;
  onPick: (choice: RouteStop | null) => void;
};

export function sameStop(a: RouteStop | null, b: RouteStop | null): boolean {
  return (
    a !== null &&
    b !== null &&
    a.name === b.name &&
    Math.abs(a.coordinate.lat - b.coordinate.lat) < 1e-6 &&
    Math.abs(a.coordinate.lon - b.coordinate.lon) < 1e-6
  );
}

/** Cafés near the middle of the route. Picking one re-routes through it. */
export default function CafeChoices({ choices, current, canRemove, busy, onPick }: Props) {
  if (choices.length === 0) return null;
  return (
    <section className="cafes" aria-label="Cafés on the way">
      <h2>Cafés on the way</h2>
      <ul>
        {choices.map((choice, index) => (
          <li key={index}>
            <button
              type="button"
              aria-pressed={sameStop(choice, current)}
              disabled={busy}
              onClick={() => onPick(choice)}
            >
              <span className="cafe-name">{choice.name}</span>
              <span className="cafe-meta">
                km {(choice.distance_along_m / 1000).toFixed(1)}
                {choice.off_route_m != null && ` · ${Math.round(choice.off_route_m)} m off route`}
              </span>
            </button>
          </li>
        ))}
        {canRemove && current && (
          <li>
            <button type="button" disabled={busy} onClick={() => onPick(null)}>
              <span className="cafe-name">No stop</span>
            </button>
          </li>
        )}
      </ul>
    </section>
  );
}

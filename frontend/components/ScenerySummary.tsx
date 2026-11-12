import type { RouteScenic } from "@/lib/api";
import { BAND_COLORS, BAND_LABELS, BAND_ORDER } from "@/lib/scenery";

type Props = {
  scenic: RouteScenic;
  selected: number | null;
  onSelect: (index: number | null) => void;
};

function km(metres: number): string {
  return (metres / 1000).toFixed(1);
}

/** Share of the route in each band, and the stretches in riding order. */
export default function ScenerySummary({ scenic, selected, onSelect }: Props) {
  const bands = BAND_ORDER.filter((band) => scenic.band_shares[band] > 0);

  return (
    <section className="scenery" aria-label="Scenery along the route">
      <h2>Scenery</h2>
      <div className="scenery-bar" role="img" aria-label={bands
        .map((band) => `${BAND_LABELS[band]} ${Math.round(scenic.band_shares[band] * 100)}%`)
        .join(", ")}>
        {bands.map((band) => (
          <span
            key={band}
            style={{ flexGrow: scenic.band_shares[band], background: BAND_COLORS[band] }}
          />
        ))}
      </div>
      <ul className="scenery-legend">
        {bands.map((band) => (
          <li key={band}>
            <span className="swatch" style={{ background: BAND_COLORS[band] }} />
            {BAND_LABELS[band]} {Math.round(scenic.band_shares[band] * 100)}%
          </li>
        ))}
      </ul>
      <ol className="scenery-stretches">
        {scenic.stretches.map((stretch, index) => (
          <li key={index}>
            <button
              type="button"
              aria-pressed={selected === index}
              onClick={() => onSelect(selected === index ? null : index)}
            >
              <span className="swatch" style={{ background: BAND_COLORS[stretch.band] }} />
              <span className="stretch-km">
                {km(stretch.start_m)}–{km(stretch.end_m)} km
              </span>
              <span className="stretch-text">
                {stretch.name ?? BAND_LABELS[stretch.band]}
                {stretch.reason && <span className="stretch-reason"> · {stretch.reason}</span>}
              </span>
            </button>
          </li>
        ))}
      </ol>
    </section>
  );
}

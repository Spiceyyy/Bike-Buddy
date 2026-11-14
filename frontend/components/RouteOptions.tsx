import type { RouteOption } from "@/lib/api";

type Props = {
  options: RouteOption[];
  selected: number;
  onSelect: (index: number) => void;
};

function km(metres: number): string {
  return `${(metres / 1000).toFixed(1)} km`;
}

/** Cards for the planned routes, so the rider can switch between them. */
export default function RouteOptions({ options, selected, onSelect }: Props) {
  return (
    <section className="options" aria-label="Route options">
      <h2>Routes</h2>
      <div className="option-cards">
        {options.map((option, index) => {
          const { distance_m, ascend_m, scenic } = option.route.properties;
          return (
            <button
              key={index}
              type="button"
              className="option-card"
              aria-pressed={selected === index}
              onClick={() => onSelect(index)}
            >
              <span className="option-label">{option.label}</span>
              <span className="option-stat">{km(distance_m)}</span>
              {ascend_m != null && <span className="option-stat">~{Math.round(ascend_m)} m up</span>}
              {scenic && (
                <span className="option-stat">
                  {Math.round(scenic.band_shares.scenic * 100)}% scenic
                </span>
              )}
            </button>
          );
        })}
      </div>
    </section>
  );
}

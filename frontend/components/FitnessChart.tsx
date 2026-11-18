import { useState, type PointerEvent } from "react";

import type { DayFitness } from "@/lib/training";

// Categorical slots 1 and 2 of the validated chart palette (checked against the panel surface).
const SERIES = [
  { key: "fitness", label: "Fitness", color: "#2a78d6" },
  { key: "fatigue", label: "Fatigue", color: "#eb6834" },
] as const;

const WIDTH = 304;
const HEIGHT = 150;
const PAD = { top: 10, right: 74, bottom: 20, left: 26 };
const LABEL_GAP = 13;

function niceMax(value: number): number {
  const step = value > 60 ? 20 : value > 20 ? 10 : 5;
  return Math.max(step, Math.ceil(value / step) * step);
}

function shortDate(day: string): string {
  return new Date(`${day}T12:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** Fitness and fatigue over time: two lines on one scale (load points), with a crosshair readout. */
export default function FitnessChart({ series }: { series: DayFitness[] }) {
  const [hover, setHover] = useState<number | null>(null);
  if (series.length < 2) return null;

  const max = niceMax(Math.max(...series.map((d) => Math.max(d.fitness, d.fatigue))));
  const plotW = WIDTH - PAD.left - PAD.right;
  const plotH = HEIGHT - PAD.top - PAD.bottom;
  const x = (i: number) => PAD.left + (i / (series.length - 1)) * plotW;
  const y = (v: number) => PAD.top + plotH - (v / max) * plotH;
  const ticks = [0, max / 2, max];
  const months = series
    .map((d, i) => ({ i, d }))
    .filter(({ d, i }) => i > 0 && d.day.endsWith("-01"));

  // Direct labels at the line ends, pushed apart if they would overlap.
  const last = series[series.length - 1];
  const ends = SERIES.map((s) => ({ ...s, value: last[s.key], y: y(last[s.key]) }));
  const [a, b] = ends;
  if (Math.abs(a.y - b.y) < LABEL_GAP) {
    const mid = (a.y + b.y) / 2;
    const upper = a.y <= b.y ? a : b;
    const lower = upper === a ? b : a;
    upper.y = mid - LABEL_GAP / 2;
    lower.y = mid + LABEL_GAP / 2;
  }

  function handleMove(event: PointerEvent<SVGRectElement>) {
    const box = event.currentTarget.getBoundingClientRect();
    const fraction = (event.clientX - box.left) / box.width;
    setHover(Math.max(0, Math.min(series.length - 1, Math.round(fraction * (series.length - 1)))));
  }

  const point = hover !== null ? series[hover] : null;

  return (
    <figure className="fitness-chart">
      <div className="chart-legend" aria-hidden="true">
        {SERIES.map((s) => (
          <span key={s.key}>
            <span className="legend-key" style={{ background: s.color }} />
            {s.label}
          </span>
        ))}
      </div>
      <div className="chart-plot">
        <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label={`Fitness ${last.fitness}, fatigue ${last.fatigue}, over the last ${series.length} days`}>
          {ticks.map((t) => (
            <g key={t}>
              <line x1={PAD.left} x2={PAD.left + plotW} y1={y(t)} y2={y(t)} className="chart-grid" />
              <text x={PAD.left - 5} y={y(t) + 3} textAnchor="end" className="chart-tick">
                {t}
              </text>
            </g>
          ))}
          {months.map(({ i, d }) => (
            <text key={d.day} x={x(i)} y={HEIGHT - 5} textAnchor="middle" className="chart-tick">
              {new Date(`${d.day}T12:00:00`).toLocaleDateString(undefined, { month: "short" })}
            </text>
          ))}
          {SERIES.map((s) => (
            <polyline
              key={s.key}
              points={series.map((d, i) => `${x(i)},${y(d[s.key])}`).join(" ")}
              fill="none"
              stroke={s.color}
              strokeWidth={2}
              strokeLinejoin="round"
              strokeLinecap="round"
            />
          ))}
          {ends.map((e) => (
            <g key={e.key}>
              <circle cx={x(series.length - 1)} cy={y(e.value)} r={4} fill={e.color} stroke="#fbfaf6" strokeWidth={2} />
              <line x1={x(series.length - 1) + 6} x2={x(series.length - 1) + 12} y1={y(e.value)} y2={e.y} className="chart-leader" />
              <text x={x(series.length - 1) + 14} y={e.y + 4} className="chart-label">
                {e.label} {Math.round(e.value)}
              </text>
            </g>
          ))}
          {point && hover !== null && (
            <g pointerEvents="none">
              <line x1={x(hover)} x2={x(hover)} y1={PAD.top} y2={PAD.top + plotH} className="chart-crosshair" />
              {SERIES.map((s) => (
                <circle key={s.key} cx={x(hover)} cy={y(point[s.key])} r={4} fill={s.color} stroke="#fbfaf6" strokeWidth={2} />
              ))}
            </g>
          )}
          <rect
            x={PAD.left}
            y={PAD.top}
            width={plotW}
            height={plotH}
            fill="transparent"
            onPointerMove={handleMove}
            onPointerLeave={() => setHover(null)}
          />
        </svg>
        {point && hover !== null && (
          <div
            className="chart-tooltip"
            style={{ left: `${(x(hover) / WIDTH) * 100}%` }}
            data-flip={x(hover) > WIDTH / 2 ? "left" : "right"}
          >
            <div className="chart-tooltip-date">{shortDate(point.day)}</div>
            {SERIES.map((s) => (
              <div key={s.key}>
                <span className="legend-key" style={{ background: s.color }} />
                <strong>{Math.round(point[s.key])}</strong> {s.label.toLowerCase()}
              </div>
            ))}
            <div>
              <strong>{Math.round(point.form)}</strong> form
            </div>
          </div>
        )}
      </div>
    </figure>
  );
}

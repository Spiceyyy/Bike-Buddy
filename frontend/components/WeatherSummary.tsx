import type { RideWeather, WindExposure } from "@/lib/api";

const COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];

function compass(degrees: number): string {
  return COMPASS[Math.round(degrees / 45) % 8];
}

type Props = {
  weather: RideWeather;
  /** Wind exposure of the route on screen, which changes with the option and café. */
  wind: WindExposure | null;
};

/** The forecast for the ride window, and how much of the route on screen rides into the wind. */
export default function WeatherSummary({ weather, wind }: Props) {
  const f = weather.forecast;
  const temperature =
    Math.round(f.temperature_min_c) === Math.round(f.temperature_max_c)
      ? `${Math.round(f.temperature_max_c)} °C`
      : `${Math.round(f.temperature_min_c)}–${Math.round(f.temperature_max_c)} °C`;
  const hours = f.hours === 1 ? "the next hour" : `the next ${f.hours} hours`;

  return (
    <div className="weather">
      <h2>Weather, {hours}</h2>
      <dl className="weather-stats">
        <div>
          <dt>Temperature</dt>
          <dd>{temperature}</dd>
        </div>
        <div>
          <dt>Rain</dt>
          <dd>{f.rain_chance_pct}%</dd>
        </div>
        <div>
          <dt>Wind</dt>
          <dd title={`Gusts to ${Math.round(f.gusts_kmh)} km/h`}>
            {Math.round(f.wind_kmh)} km/h {compass(f.wind_from_deg)}
          </dd>
        </div>
      </dl>
      {wind && (
        <p className="panel-note">
          This route: {wind.headwind_pct}% into the wind, {wind.tailwind_pct}% with it.
        </p>
      )}
      <p className="weather-credit">
        Forecast at the start, for riding now, from{" "}
        <a href="https://open-meteo.com/" target="_blank" rel="noreferrer">
          Open-Meteo
        </a>
        .
      </p>
    </div>
  );
}

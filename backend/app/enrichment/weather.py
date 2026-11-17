"""Weather for the ride window, from Open-Meteo (free, no API key).

The forecast covers the hours from now until the ride would end, at the
start point. Notes for the rider are written here, in code, from fixed
thresholds, so the LLM only passes them on.
"""

import math
from typing import Any, Protocol, Sequence

import httpx

from app.models.route import Coordinate
from app.models.weather import RideForecast, RideWeather, WindExposure

HOURLY_FIELDS = (
    "temperature_2m",
    "precipitation_probability",
    "precipitation",
    "wind_speed_10m",
    "wind_direction_10m",
    "wind_gusts_10m",
)
MAX_HOURS = 12

# When a note is worth giving.
RAIN_LIKELY_PCT = 50
RAIN_POSSIBLE_PCT = 30
WINDY_KMH = 20
GUSTY_KMH = 45
HEADWIND_NOTE_PCT = 30
COLD_C = 3
HOT_C = 30
COMPASS = ("north", "northeast", "east", "southeast", "south", "southwest", "west", "northwest")


class WeatherError(Exception):
    """The forecast service failed or returned something unusable."""


class Weather(Protocol):
    async def forecast(self, at: Coordinate, hours: int) -> RideForecast: ...


class OpenMeteoClient:
    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def forecast(self, at: Coordinate, hours: int) -> RideForecast:
        hours = max(1, min(hours, MAX_HOURS))
        params = {
            "latitude": round(at.lat, 3),
            "longitude": round(at.lon, 3),
            "hourly": ",".join(HOURLY_FIELDS),
            # From the current hour on.
            "forecast_hours": hours,
            "timezone": "UTC",
        }
        try:
            response = await self._http.get("/v1/forecast", params=params)
            response.raise_for_status()
            return summarize_hours(response.json()["hourly"], hours)
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise WeatherError(str(exc)) from exc


def summarize_hours(hourly: dict[str, Sequence[Any]], hours: int) -> RideForecast:
    """One forecast for the ride from Open-Meteo's hourly arrays."""
    columns = {field: list(hourly[field])[:hours] for field in HOURLY_FIELDS}
    if not columns["temperature_2m"] or any(
        value is None for values in columns.values() for value in values
    ):
        raise ValueError("forecast has missing hours")

    speeds = columns["wind_speed_10m"]
    directions = columns["wind_direction_10m"]
    return RideForecast(
        hours=len(speeds),
        temperature_min_c=round(min(columns["temperature_2m"]), 1),
        temperature_max_c=round(max(columns["temperature_2m"]), 1),
        rain_chance_pct=round(max(columns["precipitation_probability"])),
        rain_mm=round(sum(columns["precipitation"]), 1),
        wind_kmh=round(sum(speeds) / len(speeds)),
        wind_from_deg=round(mean_direction(directions, speeds)),
        gusts_kmh=round(max(columns["wind_gusts_10m"])),
    )


def mean_direction(directions_deg: Sequence[float], weights: Sequence[float]) -> float:
    """Average compass direction, so 350° and 10° average to 0°, not 180°."""
    east = sum(w * math.sin(math.radians(d)) for d, w in zip(directions_deg, weights))
    north = sum(w * math.cos(math.radians(d)) for d, w in zip(directions_deg, weights))
    if east == 0 and north == 0:
        return float(directions_deg[0])
    return math.degrees(math.atan2(east, north)) % 360


def compass(direction_deg: float) -> str:
    return COMPASS[round(direction_deg / 45) % 8]


def ride_weather(forecast: RideForecast, wind: WindExposure | None) -> RideWeather:
    """The forecast with notes for the rider, for the route `wind` describes."""
    notes = []
    if forecast.rain_chance_pct >= RAIN_LIKELY_PCT:
        notes.append(f"rain likely ({forecast.rain_chance_pct}% chance)")
    elif forecast.rain_chance_pct >= RAIN_POSSIBLE_PCT:
        notes.append(f"a chance of rain ({forecast.rain_chance_pct}%)")

    if forecast.wind_kmh >= WINDY_KMH or forecast.gusts_kmh >= GUSTY_KMH:
        note = f"windy: {forecast.wind_kmh:.0f} km/h from the {compass(forecast.wind_from_deg)}"
        if forecast.gusts_kmh >= GUSTY_KMH:
            note += f", gusts to {forecast.gusts_kmh:.0f} km/h"
        if wind is not None and wind.headwind_pct >= HEADWIND_NOTE_PCT:
            note += f", into the wind for {wind.headwind_pct}% of the route"
        notes.append(note)

    if forecast.temperature_max_c <= COLD_C:
        low, high = whole(forecast.temperature_min_c), whole(forecast.temperature_max_c)
        notes.append(f"cold: around {high} °C" if low == high else f"cold: {low} to {high} °C")
    elif forecast.temperature_max_c >= HOT_C:
        notes.append(f"hot: up to {whole(forecast.temperature_max_c)} °C")
    return RideWeather(forecast=forecast, notes=notes)


def whole(value: float) -> int:
    """Rounded for the rider, without the "-0" that formatting -0.3 gives."""
    return round(value) + 0


def ride_hours(duration_s: float) -> int:
    return max(1, math.ceil(duration_s / 3600))

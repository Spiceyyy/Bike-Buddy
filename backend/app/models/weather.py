from pydantic import BaseModel, Field


class RideForecast(BaseModel):
    """The forecast at the start of the ride, over the hours the ride takes."""

    hours: int
    temperature_min_c: float
    temperature_max_c: float
    # The highest hourly chance of rain, and the total expected, over the ride.
    rain_chance_pct: int = Field(ge=0, le=100)
    rain_mm: float
    # Mean wind speed and the direction it blows from (0 = north, 90 = east).
    wind_kmh: float
    wind_from_deg: float
    gusts_kmh: float


class WindExposure(BaseModel):
    """Share of a route's distance ridden into, with, and across the wind."""

    headwind_pct: int
    tailwind_pct: int
    crosswind_pct: int


class RideWeather(BaseModel):
    forecast: RideForecast
    # Short notes worth telling the rider, written in code so the LLM only copies them.
    notes: list[str] = []

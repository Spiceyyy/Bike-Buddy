import httpx
import pytest

from app.enrichment.weather import (
    OpenMeteoClient,
    WeatherError,
    compass,
    mean_direction,
    ride_hours,
    ride_weather,
    summarize_hours,
)
from app.enrichment.wind import angle_between, bearing_deg, wind_exposure
from app.models.route import Coordinate
from app.models.weather import RideForecast, WindExposure

# About 1.1 km legs around Montreal.
ORIGIN = (-73.55, 45.50)
NORTH = (-73.55, 45.51)
EAST = (-73.5357, 45.50)


def hourly(**overrides) -> dict:
    data = {
        "time": ["2026-11-17T14:00", "2026-11-17T15:00"],
        "temperature_2m": [8.2, 10.4],
        "precipitation_probability": [20, 60],
        "precipitation": [0.0, 1.2],
        "wind_speed_10m": [18.0, 22.0],
        "wind_direction_10m": [260, 280],
        "wind_gusts_10m": [35.0, 48.0],
    }
    data.update(overrides)
    return data


def forecast(**overrides) -> RideForecast:
    values = {
        "hours": 2,
        "temperature_min_c": 12.0,
        "temperature_max_c": 16.0,
        "rain_chance_pct": 10,
        "rain_mm": 0.0,
        "wind_kmh": 10.0,
        "wind_from_deg": 270.0,
        "gusts_kmh": 20.0,
    }
    values.update(overrides)
    return RideForecast(**values)


# --- wind exposure ------------------------------------------------------------


@pytest.mark.parametrize("end, expected", [(NORTH, 0), (EAST, 90)])
def test_bearing(end, expected) -> None:
    assert bearing_deg(ORIGIN, end) == pytest.approx(expected, abs=0.5)


@pytest.mark.parametrize("a, b, angle", [(350, 10, 20), (10, 350, 20), (90, 270, 180), (45, 45, 0)])
def test_angle_between(a, b, angle) -> None:
    assert angle_between(a, b) == pytest.approx(angle)


def test_riding_toward_the_wind_is_a_headwind() -> None:
    # Wind from the north; riding north.
    assert wind_exposure([ORIGIN, NORTH], 0) == WindExposure(
        headwind_pct=100, tailwind_pct=0, crosswind_pct=0
    )


def test_riding_away_from_the_wind_is_a_tailwind() -> None:
    assert wind_exposure([NORTH, ORIGIN], 0).tailwind_pct == 100


def test_riding_across_the_wind_is_a_crosswind() -> None:
    assert wind_exposure([ORIGIN, EAST], 0).crosswind_pct == 100


def test_an_out_and_back_is_half_into_the_wind() -> None:
    exposure = wind_exposure([ORIGIN, NORTH, ORIGIN], 10)
    assert (exposure.headwind_pct, exposure.tailwind_pct) == (50, 50)


def test_shares_are_by_distance() -> None:
    # 1.1 km north into the wind, then about 2.2 km east across it.
    far_east = (EAST[0] + (EAST[0] - ORIGIN[0]), NORTH[1])
    exposure = wind_exposure([ORIGIN, NORTH, far_east], 0)
    assert exposure.headwind_pct == pytest.approx(33, abs=1)
    assert exposure.headwind_pct + exposure.tailwind_pct + exposure.crosswind_pct == 100


def test_a_route_with_no_length_has_no_exposure() -> None:
    assert wind_exposure([ORIGIN, ORIGIN], 0).headwind_pct == 0


# --- forecast summary -----------------------------------------------------------


def test_summarizes_the_ride_hours() -> None:
    result = summarize_hours(hourly(), 2)
    assert result == RideForecast(
        hours=2,
        temperature_min_c=8.2,
        temperature_max_c=10.4,
        rain_chance_pct=60,
        rain_mm=1.2,
        wind_kmh=20,
        wind_from_deg=271,
        gusts_kmh=48,
    )


def test_uses_only_the_hours_of_the_ride() -> None:
    assert summarize_hours(hourly(), 1).rain_chance_pct == 20


def test_missing_hours_are_an_error() -> None:
    with pytest.raises(ValueError):
        summarize_hours(hourly(precipitation_probability=[None, 10]), 2)


def test_mean_direction_wraps_around_north() -> None:
    assert angle_between(mean_direction([350, 10], [1, 1]), 0) == pytest.approx(0, abs=1e-6)


def test_mean_direction_weights_stronger_wind() -> None:
    assert mean_direction([0, 90], [3, 1]) == pytest.approx(18.4, abs=0.1)


@pytest.mark.parametrize("deg, name", [(0, "north"), (271, "west"), (225, "southwest"), (350, "north")])
def test_compass(deg, name) -> None:
    assert compass(deg) == name


@pytest.mark.parametrize("seconds, hours", [(600, 1), (3600, 1), (3601, 2), (5 * 3600 + 900, 6)])
def test_ride_hours(seconds, hours) -> None:
    assert ride_hours(seconds) == hours


# --- notes ----------------------------------------------------------------------


def test_fine_weather_has_no_notes() -> None:
    assert ride_weather(forecast(), None).notes == []


def test_notes_rain() -> None:
    assert ride_weather(forecast(rain_chance_pct=70), None).notes == ["rain likely (70% chance)"]
    assert ride_weather(forecast(rain_chance_pct=35), None).notes == ["a chance of rain (35%)"]


def test_notes_wind_with_the_headwind_share() -> None:
    windy = forecast(wind_kmh=25, gusts_kmh=50)
    exposure = WindExposure(headwind_pct=40, tailwind_pct=30, crosswind_pct=30)
    assert ride_weather(windy, exposure).notes == [
        "windy: 25 km/h from the west, gusts to 50 km/h, into the wind for 40% of the route"
    ]


def test_small_headwind_share_is_left_out() -> None:
    exposure = WindExposure(headwind_pct=10, tailwind_pct=10, crosswind_pct=80)
    assert ride_weather(forecast(wind_kmh=25), exposure).notes == ["windy: 25 km/h from the west"]


def test_notes_cold_and_heat() -> None:
    assert ride_weather(forecast(temperature_min_c=-2, temperature_max_c=1), None).notes == [
        "cold: -2 to 1 °C"
    ]
    assert ride_weather(forecast(temperature_max_c=31), None).notes == ["hot: up to 31 °C"]


# --- client ---------------------------------------------------------------------


def client(handler) -> OpenMeteoClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://weather")
    return OpenMeteoClient(http)


async def test_client_asks_for_the_ride_hours_at_the_start() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.url.params)
        return httpx.Response(200, json={"hourly": hourly()})

    result = await client(handler).forecast(Coordinate(lat=45.50412, lon=-73.55319), 2)

    assert result.rain_chance_pct == 60
    assert seen["forecast_hours"] == "2"
    assert (seen["latitude"], seen["longitude"]) == ("45.504", "-73.553")


async def test_client_caps_long_rides() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.url.params)
        return httpx.Response(200, json={"hourly": hourly()})

    await client(handler).forecast(Coordinate(lat=45.5, lon=-73.5), 40)
    assert seen["forecast_hours"] == "12"


@pytest.mark.parametrize(
    "response",
    [httpx.Response(500), httpx.Response(200, json={"nothing": 1}), httpx.Response(200, text="oops")],
)
async def test_client_failures_raise_weather_error(response) -> None:
    with pytest.raises(WeatherError):
        await client(lambda request: response).forecast(Coordinate(lat=45.5, lon=-73.5), 2)


def test_temperatures_just_below_zero_read_as_zero() -> None:
    cold = forecast(temperature_min_c=-0.3, temperature_max_c=0.4)
    assert ride_weather(cold, None).notes == ["cold: around 0 °C"]

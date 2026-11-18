import math
from datetime import datetime, timedelta, timezone

import pytest

from app.models.training import RiderProfile
from app.training.metrics import (
    ascent_m,
    max_speed_kmh,
    power_figures,
    ride_metrics,
    track_geometry,
    training_load,
)
from app.training.parsing import Sample

START = datetime(2026, 6, 1, 13, 0, tzinfo=timezone.utc)
DEGREES_PER_M = 180 / (math.pi * 6_371_000.0)


def ride(
    seconds: int,
    speed_ms: float = 7.0,
    hr: int | None = None,
    power: int | None = None,
    ele=None,
    start_s: int = 0,
    lat0: float = 45.5,
) -> list[Sample]:
    """One sample a second, riding due north at a constant speed."""
    return [
        Sample(
            time=START + timedelta(seconds=start_s + i),
            lat=lat0 + i * speed_ms * DEGREES_PER_M,
            lon=-73.55,
            ele=ele(i) if callable(ele) else ele,
            hr=hr,
            power=power,
        )
        for i in range(seconds + 1)
    ]


NO_PROFILE = RiderProfile()


def test_steady_hour_gives_time_distance_and_speed() -> None:
    m = ride_metrics(ride(3600, speed_ms=7.0), NO_PROFILE)

    assert m.elapsed_s == 3600
    assert m.moving_s == 3600
    assert m.distance_m == pytest.approx(25_200, rel=1e-3)
    assert m.avg_speed_kmh == pytest.approx(25.2, abs=0.1)
    assert m.max_speed_kmh == pytest.approx(25.2, abs=0.1)


def test_stops_and_gaps_are_not_moving_time() -> None:
    riding = ride(600)
    # Standing still for 2 minutes at the last point.
    last = riding[-1]
    standing = [Sample(time=last.time + timedelta(seconds=s), lat=last.lat, lon=last.lon) for s in range(1, 121)]
    # Then a 10-minute auto-pause before riding on.
    after = ride(600, start_s=600 + 120 + 600, lat0=last.lat)

    m = ride_metrics(riding + standing + after, NO_PROFILE)

    assert m.elapsed_s == 600 + 120 + 600 + 600
    assert m.moving_s == pytest.approx(1200, abs=2)
    assert m.avg_speed_kmh == pytest.approx(25.2, abs=0.2)


def test_a_gps_jump_does_not_set_max_speed() -> None:
    samples = ride(300)
    jumped = samples[150]
    samples[150] = Sample(time=jumped.time, lat=jumped.lat + 0.01, lon=jumped.lon)
    assert max_speed_kmh(samples) < 60


@pytest.mark.parametrize(
    "elevations, expected",
    [
        ([10, 11, 12, 13, 14, 15], 5),
        ([10, 11, 10, 11, 10, 11, 10], 0),  # noise on the flat
        ([10, 20, 15, 25], 20),  # two climbs with a real dip between
        ([10, 20, 18, 25], 15),  # a 2 m dip is noise: one climb from 10 to 25
        ([20, 15, 10], 0),
    ],
)
def test_ascent_ignores_noise(elevations, expected) -> None:
    assert ascent_m(elevations) == pytest.approx(expected)


def test_ascent_is_none_without_elevation() -> None:
    assert ascent_m([None, None]) is None


def test_steady_power_has_equal_average_and_normalized_power() -> None:
    assert power_figures(ride(1800, power=200)) == (200, 200)


def test_variable_power_raises_normalized_power() -> None:
    samples = ride(1800, power=0)
    # Alternate 5 minutes at 300 W and 5 minutes at 100 W: average 200 W.
    samples = [
        Sample(time=s.time, lat=s.lat, lon=s.lon, power=300 if (i // 300) % 2 == 0 else 100)
        for i, s in enumerate(samples)
    ]
    average, normalized = power_figures(samples)
    assert average == 200
    assert normalized > 220


def test_short_power_data_gives_no_power_figures() -> None:
    assert power_figures(ride(120, power=200)) == (None, None)


def test_load_from_power_is_one_hundred_for_an_hour_at_ftp() -> None:
    m = ride_metrics(ride(3600, power=250), RiderProfile(ftp_w=250))
    assert m.load_method == "power"
    assert m.intensity == pytest.approx(1.0)
    assert m.load == pytest.approx(100, abs=0.5)


def test_load_from_power_scales_with_intensity_squared() -> None:
    m = ride_metrics(ride(3600, power=175), RiderProfile(ftp_w=250))
    assert m.load == pytest.approx(49, abs=0.5)


def test_load_from_heart_rate_at_threshold_is_one_hundred() -> None:
    # Reserve 190 - 50 = 140; threshold at 90% of it is 50 + 126 = 176 bpm.
    profile = RiderProfile(max_hr=190, resting_hr=50)
    m = ride_metrics(ride(3600, hr=176), profile)
    assert m.load_method == "heart_rate"
    assert m.load == pytest.approx(100, abs=0.5)
    assert (m.avg_hr, m.max_hr) == (176, 176)


def test_power_wins_over_heart_rate() -> None:
    profile = RiderProfile(ftp_w=250, max_hr=190, resting_hr=50)
    assert ride_metrics(ride(3600, power=250, hr=150), profile).load_method == "power"


def test_power_without_ftp_falls_back_to_heart_rate() -> None:
    profile = RiderProfile(max_hr=190, resting_hr=50)
    m = ride_metrics(ride(3600, power=250, hr=150), profile)
    assert m.load_method == "heart_rate"
    assert m.avg_power == 250


def test_without_power_or_heart_rate_load_is_estimated() -> None:
    m = ride_metrics(ride(3600), NO_PROFILE)
    assert m.load_method == "estimate"
    assert m.intensity is None
    assert m.load == pytest.approx(42.25, abs=0.1)


def test_climbing_raises_the_estimate() -> None:
    flat, _, _ = training_load(
        moving_s=3600, distance_m=25_000, ascent=0, normalized_power=None, avg_hr=None, profile=NO_PROFILE
    )
    hilly, _, _ = training_load(
        moving_s=3600, distance_m=25_000, ascent=500, normalized_power=None, avg_hr=None, profile=NO_PROFILE
    )
    assert hilly > flat
    # Capped: intensity at most 0.65 + 0.15.
    assert hilly <= 0.80**2 * 100 + 1e-9


def test_geometry_is_thinned_but_keeps_both_ends() -> None:
    samples = ride(9000)
    points = track_geometry(samples)
    assert len(points) <= 2001
    assert points[0] == (round(samples[0].lon, 6), round(samples[0].lat, 6))
    assert points[-1] == (round(samples[-1].lon, 6), round(samples[-1].lat, 6))

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.models.spec import Climbing
from app.models.training import Ride
from app.training.fitness import (
    fitness_series,
    form_label,
    suggest_ride,
    training_status,
    weekly_summaries,
)

MONTREAL = ZoneInfo("America/Toronto")
# Monday 2026-06-01, noon in Montreal.
MONDAY_NOON = datetime(2026, 6, 1, 16, 0, tzinfo=timezone.utc)


def make_ride(day_offset: float, load: float = 50, km: float = 30, speed: float = 25, ride_id: int = 1) -> Ride:
    started = MONDAY_NOON + timedelta(days=day_offset)
    moving = km / speed * 3600
    return Ride(
        id=ride_id,
        filename="ride.gpx",
        started_at=started,
        elapsed_s=moving,
        moving_s=moving,
        distance_m=km * 1000,
        ascent_m=200,
        avg_speed_kmh=speed,
        max_speed_kmh=45,
        load=load,
        load_method="estimate",
    )


def test_one_ride_raises_fatigue_faster_than_fitness() -> None:
    series = fitness_series([make_ride(0, load=70)], MONDAY_NOON.date(), MONTREAL)
    day = series[-1]
    assert day.fitness == pytest.approx(70 / 42, abs=0.05)
    assert day.fatigue == pytest.approx(70 / 7, abs=0.05)
    # Form is measured at the start of the day, before the ride.
    assert day.form == 0


def test_form_tomorrow_is_fitness_minus_fatigue() -> None:
    series = fitness_series([make_ride(0, load=70)], MONDAY_NOON.date() + timedelta(days=1), MONTREAL)
    assert series[-1].form == pytest.approx(70 / 42 - 70 / 7, abs=0.1)


def test_rest_days_let_fatigue_fall() -> None:
    series = fitness_series([make_ride(0, load=70)], MONDAY_NOON.date() + timedelta(days=14), MONTREAL)
    after_ride = series[0]
    # Two weeks off: fatigue loses about 90%, fitness only about 30%.
    assert series[-1].fatigue == pytest.approx(after_ride.fatigue * (6 / 7) ** 14, abs=0.1)
    assert series[-1].fitness == pytest.approx(after_ride.fitness * (41 / 42) ** 14, abs=0.1)


def test_steady_daily_load_converges_to_that_load() -> None:
    rides = [make_ride(d, load=60, ride_id=d) for d in range(300)]
    series = fitness_series(rides, MONDAY_NOON.date() + timedelta(days=299), MONTREAL)
    assert series[-1].fitness == pytest.approx(60, abs=0.5)
    assert series[-1].fatigue == pytest.approx(60, abs=0.5)


def test_two_rides_on_one_day_add_up() -> None:
    rides = [make_ride(0, load=40), make_ride(0.1, load=30, ride_id=2)]
    assert fitness_series(rides, MONDAY_NOON.date(), MONTREAL)[-1].load == 70


def test_days_are_local_not_utc() -> None:
    # 01:30 UTC on Tuesday is still Monday evening in Montreal.
    late = make_ride(0)
    late = late.model_copy(update={"started_at": datetime(2026, 6, 2, 1, 30, tzinfo=timezone.utc)})
    series = fitness_series([late], date(2026, 6, 1), MONTREAL)
    assert series[-1].day == date(2026, 6, 1)
    assert series[-1].load == 50


def test_weeks_run_monday_to_sunday_and_include_empty_ones() -> None:
    rides = [make_ride(0, km=30), make_ride(6, km=20, ride_id=2), make_ride(7, km=40, ride_id=3)]
    weeks = weekly_summaries(rides, date(2026, 6, 10), MONTREAL, weeks=3)

    assert [w.week_start for w in weeks] == [date(2026, 5, 25), date(2026, 6, 1), date(2026, 6, 8)]
    assert [w.rides for w in weeks] == [0, 2, 1]
    assert [w.distance_km for w in weeks] == [0, 50, 40]


@pytest.mark.parametrize(
    "form, label", [(10, "fresh"), (5, "neutral"), (-10, "neutral"), (-11, "tired"), (-26, "very tired")]
)
def test_form_labels(form, label) -> None:
    assert form_label(form) == label


RECENT = [make_ride(-d, km=km, speed=24, ride_id=d) for d, km in enumerate([30, 40, 50, 35, 45], start=1)]


def test_fresh_suggests_a_longer_hilly_ride() -> None:
    s = suggest_ride(RECENT, "fresh", 2)
    assert s.kind == "long_or_hilly"
    assert s.climbing is Climbing.SEEK
    # Typical 40 km x 1.3 = 52, rounded to 50 (and under 1.1 x the longest, 55).
    assert s.target_distance_km == 50


def test_neutral_suggests_a_typical_ride() -> None:
    s = suggest_ride(RECENT, "neutral", 1)
    assert (s.kind, s.target_distance_km, s.climbing) == ("endurance", 40, Climbing.ANY)


def test_tired_suggests_an_easy_flat_ride_by_time() -> None:
    s = suggest_ride(RECENT, "tired", 1)
    assert s.kind == "recovery"
    assert s.climbing is Climbing.AVOID
    assert s.duration_min == 75
    # 75 min at 90% of 24 km/h = 27 km, rounded to 25.
    assert s.target_distance_km == 25


def test_very_tired_after_riding_today_suggests_rest() -> None:
    s = suggest_ride(RECENT, "very tired", 0)
    assert s.kind == "rest"
    assert s.target_distance_km is None


def test_a_break_suggests_easing_back_in() -> None:
    s = suggest_ride(RECENT, "fresh", 6)
    assert s.kind == "ease_back_in"
    assert s.target_distance_km == 30  # 80% of 40


def test_distances_stay_within_bounds() -> None:
    short = [make_ride(-1, km=5, speed=24)]
    assert suggest_ride(short, "neutral", 1).target_distance_km == 10


def test_status_without_rides() -> None:
    status = training_status([], MONDAY_NOON, MONTREAL)
    assert (status.rides, status.form_label, status.suggestion) == (0, "no data", None)


def test_status_summarises_the_history() -> None:
    rides = [make_ride(-d, load=60, km=30, ride_id=d) for d in range(1, 30, 2)]

    status = training_status(rides, MONDAY_NOON, MONTREAL)

    assert status.rides == 15
    assert status.last_ride is not None and status.last_ride.id == 1
    assert status.days_since_last_ride == 1
    assert status.fitness > 0 and status.fatigue > 0
    assert status.this_week is not None and status.this_week.rides == 0
    assert status.weekly_average is not None and status.weekly_average.rides in (3, 4)
    assert status.suggestion is not None
    assert status.load_methods == {"estimate": 15}

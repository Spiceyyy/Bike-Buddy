"""Fitness, fatigue and form over time, and a suggestion for today's ride.

The standard performance model: daily load feeds two moving averages.

    fitness (CTL) += (load - fitness) / 42      slow: weeks of training
    fatigue (ATL) += (load - fatigue) / 7       fast: the last few days
    form    (TSB)  = fitness - fatigue, at the start of the day

Form decides the kind of ride, recent rides decide its length. Every rule is
a fixed threshold here, so the LLM explains a decision it did not make.
"""

import statistics
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Sequence
from zoneinfo import ZoneInfo

from app.models.spec import Climbing
from app.models.training import DayFitness, Ride, RideSuggestion, TrainingStatus, WeekSummary

FITNESS_DAYS = 42
FATIGUE_DAYS = 7
# Form bands, in load points.
FRESH_FORM = 5
TIRED_FORM = -10
VERY_TIRED_FORM = -25
# Rides used for the rider's typical distance and speed.
RECENT_RIDES = 10
AFTER_BREAK_DAYS = 5
# Distances are rounded to this, and kept within these bounds.
DISTANCE_STEP_KM = 5
MIN_DISTANCE_KM = 10
MAX_DISTANCE_KM = 150
LONG_RIDE_FACTOR = 1.3
EASY_SPEED_FACTOR = 0.9


def local_day(moment: datetime, timezone: ZoneInfo) -> date:
    return moment.astimezone(timezone).date()


def fitness_series(rides: Sequence[Ride], today: date, timezone: ZoneInfo) -> list[DayFitness]:
    """One entry per day from the first ride to today."""
    if not rides:
        return []
    daily: dict[date, float] = {}
    for ride in rides:
        day = local_day(ride.started_at, timezone)
        daily[day] = daily.get(day, 0.0) + ride.load

    series = []
    fitness = fatigue = 0.0
    day = min(daily)
    while day <= today:
        form = fitness - fatigue
        load = daily.get(day, 0.0)
        fitness += (load - fitness) / FITNESS_DAYS
        fatigue += (load - fatigue) / FATIGUE_DAYS
        series.append(
            DayFitness(
                day=day,
                load=round(load, 1),
                fitness=round(fitness, 1),
                fatigue=round(fatigue, 1),
                form=round(form, 1),
            )
        )
        day += timedelta(days=1)
    return series


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def weekly_summaries(rides: Sequence[Ride], today: date, timezone: ZoneInfo, weeks: int) -> list[WeekSummary]:
    """The last `weeks` calendar weeks (Monday to Sunday), oldest first, empty weeks included."""
    current = week_start(today)
    starts = [current - timedelta(weeks=w) for w in range(weeks - 1, -1, -1)]
    by_week: dict[date, list[Ride]] = {start: [] for start in starts}
    for ride in rides:
        start = week_start(local_day(ride.started_at, timezone))
        if start in by_week:
            by_week[start].append(ride)
    return [_summary(start, by_week[start]) for start in starts]


def _summary(start: date, rides: Sequence[Ride]) -> WeekSummary:
    return WeekSummary(
        week_start=start,
        rides=len(rides),
        distance_km=round(sum(r.distance_m for r in rides) / 1000, 1),
        moving_h=round(sum(r.moving_s for r in rides) / 3600, 1),
        ascent_m=round(sum(r.ascent_m or 0 for r in rides)),
        load=round(sum(r.load for r in rides), 1),
    )


def _average(weeks: Sequence[WeekSummary]) -> WeekSummary:
    n = len(weeks)
    return WeekSummary(
        week_start=weeks[0].week_start,
        rides=round(sum(w.rides for w in weeks) / n),
        distance_km=round(sum(w.distance_km for w in weeks) / n, 1),
        moving_h=round(sum(w.moving_h for w in weeks) / n, 1),
        ascent_m=round(sum(w.ascent_m for w in weeks) / n),
        load=round(sum(w.load for w in weeks) / n, 1),
    )


def form_label(form: float) -> str:
    if form > FRESH_FORM:
        return "fresh"
    if form >= TIRED_FORM:
        return "neutral"
    if form >= VERY_TIRED_FORM:
        return "tired"
    return "very tired"


def training_status(rides: Sequence[Ride], now: datetime, timezone: ZoneInfo) -> TrainingStatus:
    today = local_day(now, timezone)
    if not rides:
        return TrainingStatus(today=today, rides=0)

    ordered = sorted(rides, key=lambda r: r.started_at)
    last = ordered[-1]
    series = fitness_series(ordered, today, timezone)
    day = series[-1]
    weeks = weekly_summaries(ordered, today, timezone, weeks=5)
    days_since = (today - local_day(last.started_at, timezone)).days
    label = form_label(day.form)

    return TrainingStatus(
        today=today,
        rides=len(ordered),
        last_ride=last,
        days_since_last_ride=days_since,
        fitness=day.fitness,
        fatigue=day.fatigue,
        form=day.form,
        form_label=label,
        this_week=weeks[-1],
        weekly_average=_average(weeks[:-1]),
        suggestion=suggest_ride(ordered[-RECENT_RIDES:], label, days_since),
        load_methods=dict(Counter(r.load_method for r in ordered)),
    )


def suggest_ride(recent: Sequence[Ride], label: str, days_since_last: int) -> RideSuggestion:
    typical_km = statistics.median(r.distance_m for r in recent) / 1000
    longest_km = max(r.distance_m for r in recent) / 1000
    moving = [r for r in recent if r.moving_s > 0]
    speed_kmh = statistics.median(r.avg_speed_kmh for r in moving) if moving else 20.0
    easy_speed = speed_kmh * EASY_SPEED_FACTOR

    if days_since_last >= AFTER_BREAK_DAYS and label != "very tired":
        return RideSuggestion(
            kind="ease_back_in",
            reason=f"{days_since_last} days since your last ride",
            target_distance_km=_distance(typical_km * 0.8),
            climbing=Climbing.ANY,
            duration_min=None,
        )
    if label == "very tired":
        if days_since_last == 0:
            return RideSuggestion(
                kind="rest",
                reason="fatigue is high and you already rode today",
                target_distance_km=None,
                climbing=Climbing.ANY,
                duration_min=None,
            )
        return RideSuggestion(
            kind="recovery",
            reason="fatigue is high compared with your fitness",
            target_distance_km=_distance(easy_speed * 0.75),
            climbing=Climbing.AVOID,
            duration_min=45,
        )
    if label == "tired":
        return RideSuggestion(
            kind="recovery",
            reason="you are carrying some fatigue",
            target_distance_km=_distance(easy_speed * 1.25),
            climbing=Climbing.AVOID,
            duration_min=75,
        )
    if label == "fresh":
        return RideSuggestion(
            kind="long_or_hilly",
            reason="you are fresh, a good day for a bigger ride",
            target_distance_km=_distance(min(typical_km * LONG_RIDE_FACTOR, longest_km * 1.1)),
            climbing=Climbing.SEEK,
            duration_min=None,
        )
    return RideSuggestion(
        kind="endurance",
        reason="fitness and fatigue are balanced",
        target_distance_km=_distance(typical_km),
        climbing=Climbing.ANY,
        duration_min=None,
    )


def _distance(km: float) -> float:
    rounded = round(km / DISTANCE_STEP_KM) * DISTANCE_STEP_KM
    return float(min(MAX_DISTANCE_KM, max(MIN_DISTANCE_KM, rounded)))

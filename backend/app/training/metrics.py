"""Figures for one recorded ride: time, distance, climbing, speed, heart rate,
power and training load. Everything here is plain arithmetic on the samples.

Training load uses one scale whatever the data, where an hour at threshold
is 100 (the TSS convention): load = moving hours x intensity² x 100.

- power, with the rider's FTP:   intensity = normalized power / FTP
- heart rate, with max and resting HR:
                                 intensity = heart-rate reserve used / reserve at
                                 threshold, taking threshold as 90% of the reserve
- neither: an estimate, assuming endurance pace (0.65) plus a little for climbing

Power is the most reliable and the estimate the least; every ride records
which method was used.
"""

import math
from typing import Sequence

from app.models.training import LoadMethod, RideMetrics, RiderProfile
from app.routing.geometry import distance_m
from app.training.parsing import Sample

# A gap longer than this is a pause (auto-pause or signal loss), not riding.
MAX_GAP_S = 60
# Slower than this is standing still: GPS drift, waiting at lights.
MOVING_MIN_SPEED_MS = 1.0
# Climbing counts only once the road rises this far above the last low point,
# so GPS and barometer noise on flat ground adds nothing.
ASCENT_THRESHOLD_M = 3.0
# Max speed is measured over at least this long, so a single GPS jump does not count.
MAX_SPEED_WINDOW_S = 5
MAX_PLAUSIBLE_SPEED_KMH = 100
NP_WINDOW_S = 30
NP_MIN_SAMPLES_S = 5 * 60
THRESHOLD_HR_RESERVE = 0.90
ESTIMATED_INTENSITY = 0.65
ESTIMATED_INTENSITY_PER_M_PER_KM = 0.01
ESTIMATED_INTENSITY_MAX_EXTRA = 0.15
GEOMETRY_MAX_POINTS = 2000


def ride_metrics(samples: Sequence[Sample], profile: RiderProfile) -> RideMetrics:
    distance = 0.0
    moving_s = 0.0
    hr_time = hr_sum = 0.0
    for a, b in zip(samples, samples[1:]):
        dt = (b.time - a.time).total_seconds()
        step = distance_m((a.lon, a.lat), (b.lon, b.lat))
        distance += step
        if 0 < dt <= MAX_GAP_S and step / dt >= MOVING_MIN_SPEED_MS:
            moving_s += dt
            if a.hr is not None:
                hr_time += dt
                hr_sum += a.hr * dt

    elapsed_s = (samples[-1].time - samples[0].time).total_seconds()
    heart_rates = [s.hr for s in samples if s.hr is not None]
    avg_hr = round(hr_sum / hr_time) if hr_time else None
    power = power_figures(samples)
    ascent = ascent_m([s.ele for s in samples])

    load, method, intensity = training_load(
        moving_s=moving_s,
        distance_m=distance,
        ascent=ascent,
        normalized_power=power[1],
        avg_hr=avg_hr,
        profile=profile,
    )
    return RideMetrics(
        started_at=samples[0].time,
        elapsed_s=elapsed_s,
        moving_s=moving_s,
        distance_m=round(distance, 1),
        ascent_m=round(ascent) if ascent is not None else None,
        avg_speed_kmh=round(distance / moving_s * 3.6, 1) if moving_s else 0.0,
        max_speed_kmh=round(max_speed_kmh(samples), 1),
        avg_hr=avg_hr,
        max_hr=max(heart_rates) if heart_rates else None,
        avg_power=power[0],
        normalized_power=power[1],
        intensity=round(intensity, 2) if intensity is not None else None,
        load=round(load, 1),
        load_method=method,
    )


def ascent_m(elevations: Sequence[float | None]) -> float | None:
    """Metres climbed, counting a rise only once it passes the noise threshold."""
    values = [e for e in elevations if e is not None]
    if len(values) < 2:
        return None
    total = 0.0
    low = high = values[0]
    climbing = False
    for value in values[1:]:
        if climbing:
            if value > high:
                high = value
            elif high - value >= ASCENT_THRESHOLD_M:
                total += high - low
                low = high = value
                climbing = False
        else:
            if value < low:
                low = value
            elif value - low >= ASCENT_THRESHOLD_M:
                climbing = True
                high = value
    if climbing:
        total += high - low
    return total


def max_speed_kmh(samples: Sequence[Sample]) -> float:
    """Fastest speed held over at least MAX_SPEED_WINDOW_S, ignoring implausible jumps."""
    cumulative = [0.0]
    for a, b in zip(samples, samples[1:]):
        cumulative.append(cumulative[-1] + distance_m((a.lon, a.lat), (b.lon, b.lat)))
    best = 0.0
    j = 0
    for i in range(len(samples)):
        j = max(j, i + 1)
        while j < len(samples) and (samples[j].time - samples[i].time).total_seconds() < MAX_SPEED_WINDOW_S:
            j += 1
        if j >= len(samples):
            break
        dt = (samples[j].time - samples[i].time).total_seconds()
        if dt > MAX_GAP_S:
            continue
        speed = (cumulative[j] - cumulative[i]) / dt * 3.6
        if speed <= MAX_PLAUSIBLE_SPEED_KMH:
            best = max(best, speed)
    return best


def power_figures(samples: Sequence[Sample]) -> tuple[int | None, int | None]:
    """Average and normalized power over the riding time, from a 1-second series."""
    series = _power_per_second(samples)
    if len(series) < NP_MIN_SAMPLES_S:
        return None, None
    average = sum(series) / len(series)
    rolling = []
    window_sum = sum(series[:NP_WINDOW_S])
    rolling.append(window_sum / NP_WINDOW_S)
    for k in range(NP_WINDOW_S, len(series)):
        window_sum += series[k] - series[k - NP_WINDOW_S]
        rolling.append(window_sum / NP_WINDOW_S)
    normalized = (sum(p**4 for p in rolling) / len(rolling)) ** 0.25
    return round(average), round(normalized)


def _power_per_second(samples: Sequence[Sample]) -> list[float]:
    """Power for each second of riding, holding each reading until the next. Pauses are left out."""
    series: list[float] = []
    for a, b in zip(samples, samples[1:]):
        if a.power is None:
            continue
        dt = (b.time - a.time).total_seconds()
        if 0 < dt <= MAX_GAP_S:
            series.extend([float(a.power)] * round(dt))
    return series


def training_load(
    *,
    moving_s: float,
    distance_m: float,
    ascent: float | None,
    normalized_power: int | None,
    avg_hr: int | None,
    profile: RiderProfile,
) -> tuple[float, LoadMethod, float | None]:
    hours = moving_s / 3600
    if normalized_power is not None and profile.ftp_w:
        intensity = normalized_power / profile.ftp_w
        return hours * intensity**2 * 100, "power", intensity

    if avg_hr is not None and profile.max_hr and profile.resting_hr and profile.max_hr > profile.resting_hr:
        reserve = profile.max_hr - profile.resting_hr
        threshold = THRESHOLD_HR_RESERVE * reserve
        intensity = max(0.0, avg_hr - profile.resting_hr) / threshold
        return hours * intensity**2 * 100, "heart_rate", intensity

    climb_per_km = (ascent or 0) / (distance_m / 1000) if distance_m > 0 else 0
    intensity = ESTIMATED_INTENSITY + min(
        ESTIMATED_INTENSITY_MAX_EXTRA, climb_per_km * ESTIMATED_INTENSITY_PER_M_PER_KM
    )
    return hours * intensity**2 * 100, "estimate", None


def track_geometry(samples: Sequence[Sample]) -> list[tuple[float, float]]:
    """The ride's line for the map, thinned to at most GEOMETRY_MAX_POINTS."""
    step = max(1, math.ceil(len(samples) / GEOMETRY_MAX_POINTS))
    points = [(round(s.lon, 6), round(s.lat, 6)) for s in samples[::step]]
    last = (round(samples[-1].lon, 6), round(samples[-1].lat, 6))
    if points[-1] != last:
        points.append(last)
    return points

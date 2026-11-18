"""Generate a sample ride history for trainer mode, as GPX files.

Ten weeks of a plausible training pattern (weekday rides, a long weekend
ride, an easier week every fourth week) on real loops from the routing
engine. Each ride is simulated second by second from the route's gradient:

- speed:      a base pace for the kind of ride, slower uphill, faster down
- power:      what that speed costs (rolling resistance, air drag, climbing)
- heart rate: follows power with a lag, as a heart does

The first weeks have heart rate only, like an older device, so the history
mixes load methods. Long rides include a café stop, which is a pause in the
recording. Nothing here is real data.

    docker compose exec backend python -m scripts.make_sample_rides            # writes sample-rides/
    docker compose exec backend python -m scripts.make_sample_rides --upload   # also uploads them
"""

import argparse
import asyncio
import math
import random
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from app.config import Settings
from app.routing.geometry import distance_m

TIMEZONE = ZoneInfo("America/Toronto")
STARTS = {
    "Old Port": (-73.5533, 45.5048),
    "Verdun": (-73.5679, 45.4590),
    "Parc La Fontaine": (-73.5695, 45.5268),
    "Lachine": (-73.6747, 45.4372),
}
# The rider the samples were simulated for; --upload saves this profile.
PROFILE = {"ftp_w": 250, "max_hr": 188, "resting_hr": 52}
MASS_KG = 82
# Riding on the hoods, on city roads.
CDA_M2 = 0.38
CRR = 0.006
DRIVETRAIN_EFFICIENCY = 0.95
AIR_DENSITY = 1.2
G = 9.81
SAMPLE_EVERY_S = 2
WEEKS = 10
POWER_FROM_WEEK = 3


@dataclass(frozen=True)
class Plan:
    weekday: int
    kind: str
    km: float
    pace_kmh: float
    start: time


# A week of riding: (weekday, kind, distance, base pace, start time).
WEEK = [
    Plan(1, "Endurance", 30, 25.5, time(18, 0)),
    Plan(3, "Hill repeats", 25, 23.5, time(18, 0)),
    Plan(5, "Long ride", 65, 25.0, time(8, 30)),
    Plan(6, "Easy spin", 22, 22.5, time(10, 0)),
]


def week_factor(week: int) -> float:
    """Build for three weeks, ease off in the fourth."""
    return 0.75 if week % 4 == 3 else 0.85 + 0.05 * (week % 4) + 0.02 * week


async def closest_loop(http: httpx.AsyncClient, start: tuple[float, float], km: float, rng, profile: str):
    """Like the planner: GraphHopper's loop length varies, so try seeds and keep the closest."""
    best = None
    for seed in rng.sample(range(100), 8):
        try:
            coordinates = await loop(http, start, km, seed, profile)
        except httpx.HTTPStatusError:
            continue
        length = sum(distance_m(a[:2], b[:2]) for a, b in zip(coordinates, coordinates[1:])) / 1000
        if best is None or abs(length - km) < abs(best[0] - km):
            best = (length, coordinates)
        if abs(length - km) / km < 0.1:
            break
    if best is None:
        raise RuntimeError(f"no loop found from {start}")
    return best


async def loop(http: httpx.AsyncClient, start: tuple[float, float], km: float, seed: int, profile: str):
    response = await http.get(
        "/route",
        params={
            "point": f"{start[1]},{start[0]}",
            "profile": profile,
            "algorithm": "round_trip",
            "round_trip.distance": round(km * 1000),
            "round_trip.seed": seed,
            "points_encoded": "false",
            "elevation": "true",
            "instructions": "false",
        },
    )
    response.raise_for_status()
    return response.json()["paths"][0]["points"]["coordinates"]  # [lon, lat, ele]


def simulate(coordinates, started: datetime, plan: Plan, with_power: bool, rng: random.Random):
    """Ride the line: one sample every SAMPLE_EVERY_S seconds."""
    cumulative = [0.0]
    for a, b in zip(coordinates, coordinates[1:]):
        cumulative.append(cumulative[-1] + distance_m(a[:2], b[:2]))
    total = cumulative[-1]
    stop_at = total * 0.5 if plan.kind == "Long ride" else None
    # A steady wind for the ride, felt as extra air speed (a crude average over directions).
    wind_ms = rng.uniform(0.5, 3.0)

    samples = []
    d, t, hr, i = 0.0, started, PROFILE["resting_hr"] + 30.0, 1
    pace_noise = 1.0
    while d < total:
        while i < len(cumulative) - 1 and cumulative[i] < d:
            i += 1
        (lon1, lat1, e1), (lon2, lat2, e2) = coordinates[i - 1], coordinates[i]
        edge = cumulative[i] - cumulative[i - 1] or 1.0
        f = min(1.0, max(0.0, (d - cumulative[i - 1]) / edge))
        grade = max(-0.12, min(0.12, (e2 - e1) / edge)) if edge > 5 else 0.0

        pace_noise += (1.0 - pace_noise) * 0.05 + rng.gauss(0, 0.01)
        speed_kmh = max(8.0, min(55.0, plan.pace_kmh * pace_noise * (1 - 6 * grade)))
        v = speed_kmh / 3.6
        air = v + wind_ms
        power = (CRR * MASS_KG * G + 0.5 * AIR_DENSITY * CDA_M2 * air * air + MASS_KG * G * grade) * v
        power /= DRIVETRAIN_EFFICIENCY
        power = max(0.0, power * rng.uniform(0.92, 1.08))
        if plan.kind == "Hill repeats" and grade > 0.02:
            power *= 1.15

        # Heart-rate reserve used tracks intensity: 90% of the reserve at FTP, which is
        # what the heart-rate load assumes, plus a little drift.
        target_hr = PROFILE["resting_hr"] + (PROFILE["max_hr"] - PROFILE["resting_hr"]) * min(
            0.97, 0.9 * power / PROFILE["ftp_w"] + 0.03
        )
        hr += (target_hr - hr) * SAMPLE_EVERY_S / 30
        samples.append(
            {
                "time": t,
                "lon": lon1 + f * (lon2 - lon1),
                "lat": lat1 + f * (lat2 - lat1),
                "ele": e1 + f * (e2 - e1),
                "hr": round(hr),
                "power": round(power) if with_power else None,
            }
        )
        d += v * SAMPLE_EVERY_S
        t += timedelta(seconds=SAMPLE_EVERY_S)
        if stop_at is not None and d >= stop_at:
            t += timedelta(minutes=rng.randint(12, 20))  # coffee: the recording pauses
            hr = PROFILE["resting_hr"] + 25.0
            stop_at = None
    return samples


def to_gpx(name: str, samples) -> str:
    points = []
    for s in samples:
        stamp = s["time"].astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")
        power = f"<power>{s['power']}</power>" if s["power"] is not None else ""
        points.append(
            f'<trkpt lat="{s["lat"]:.6f}" lon="{s["lon"]:.6f}"><ele>{s["ele"]:.1f}</ele><time>{stamp}</time>'
            f"<extensions>{power}<gpxtpx:TrackPointExtension><gpxtpx:hr>{s['hr']}</gpxtpx:hr>"
            "</gpxtpx:TrackPointExtension></extensions></trkpt>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<gpx version="1.1" creator="Scenic Route Finder sample rides" '
        'xmlns="http://www.topografix.com/GPX/1/1" '
        'xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1">'
        f"<trk><name>{name}</name><type>cycling</type><trkseg>{''.join(points)}</trkseg></trk></gpx>\n"
    )


async def main(out_dir: Path, upload: bool, api_url: str, seed: int) -> None:
    settings = Settings()
    rng = random.Random(seed)
    out_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(TIMEZONE).date()
    first_monday = today - timedelta(days=today.weekday()) - timedelta(weeks=WEEKS - 1)
    written: list[Path] = []

    async with httpx.AsyncClient(base_url=settings.graphhopper_url, timeout=60) as gh:
        for week in range(WEEKS):
            factor = week_factor(week)
            for plan in WEEK:
                day: date = first_monday + timedelta(weeks=week, days=plan.weekday)
                if day >= today or rng.random() < 0.12:  # nothing in the future; the odd missed ride
                    continue
                start_name, start = rng.choice(list(STARTS.items()))
                km = plan.km * factor * rng.uniform(0.9, 1.1)
                profile = "bike" if plan.kind != "Hill repeats" else settings.graphhopper_profile
                km, coordinates = await closest_loop(gh, start, km, rng, profile)
                started = datetime.combine(day, plan.start, TIMEZONE) + timedelta(minutes=rng.randint(-20, 20))
                samples = simulate(coordinates, started, plan, week >= POWER_FROM_WEEK, rng)
                name = f"{plan.kind} from {start_name}"
                path = out_dir / f"{day.isoformat()}-{plan.kind.lower().replace(' ', '-')}.gpx"
                path.write_text(to_gpx(name, samples), encoding="utf-8")
                written.append(path)
                moving_min = len(samples) * SAMPLE_EVERY_S / 60
                print(f"{path.name}: {name}, {km:.1f} km, {moving_min:.0f} min", flush=True)

    print(f"\nWrote {len(written)} rides to {out_dir}")
    if not upload:
        return
    async with httpx.AsyncClient(base_url=api_url, timeout=120) as api:
        (await api.put("/api/profile", json=PROFILE)).raise_for_status()
        for batch in range(0, len(written), 10):
            files = [("files", (p.name, p.read_bytes(), "application/gpx+xml")) for p in written[batch : batch + 10]]
            response = await api.post("/api/rides", files=files)
            response.raise_for_status()
            for result in response.json()["results"]:
                print(f"  {result['filename']}: {result['status']} {result.get('reason') or ''}")
    print(f"Uploaded, with the sample rider's profile {PROFILE}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path("sample-rides"))
    parser.add_argument("--upload", action="store_true", help="Upload to the running app's API.")
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--seed", type=int, default=7, help="Same seed, same rides.")
    args = parser.parse_args()
    asyncio.run(main(args.out, args.upload, args.api, args.seed))

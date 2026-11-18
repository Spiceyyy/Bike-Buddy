"""Trainer mode's operations, shared by the API and the agent's tool."""

import hashlib
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from app.db.rides import RideStore
from app.models.route import LineString
from app.models.training import DayFitness, Ride, RiderProfile, TrainingStatus, WeekSummary
from app.training.fitness import fitness_series, training_status, weekly_summaries
from app.training.metrics import ride_metrics, track_geometry, training_load
from app.training.parsing import RideParseError, parse_ride

CHART_DAYS = 90
CHART_WEEKS = 12


class UploadResult(BaseModel):
    filename: str
    status: str
    """"added", "duplicate" or "error"."""
    ride: Ride | None = None
    reason: str | None = None


class TrainingOverview(BaseModel):
    status: TrainingStatus
    fitness: list[DayFitness]
    weeks: list[WeekSummary]


class Trainer:
    def __init__(self, store: RideStore, timezone_name: str) -> None:
        self.store = store
        self.timezone = ZoneInfo(timezone_name)

    async def upload(self, filename: str, data: bytes) -> UploadResult:
        try:
            parsed = parse_ride(filename, data)
        except RideParseError as exc:
            return UploadResult(filename=filename, status="error", reason=f"This file {exc}.")
        profile = await self.store.profile()
        metrics = ride_metrics(parsed.samples, profile)
        if metrics.moving_s < 60:
            return UploadResult(filename=filename, status="error", reason="This file has under a minute of riding.")
        geometry = LineString(coordinates=track_geometry(parsed.samples))
        ride = await self.store.add(filename, hashlib.sha256(data).hexdigest(), parsed.name, metrics, geometry)
        if ride is None:
            return UploadResult(filename=filename, status="duplicate", reason="This ride was already uploaded.")
        return UploadResult(filename=filename, status="added", ride=ride)

    async def status(self, now: datetime | None = None) -> TrainingStatus:
        return training_status(await self.store.list_rides(), now or datetime.now(timezone.utc), self.timezone)

    async def overview(self, now: datetime | None = None) -> TrainingOverview:
        now = now or datetime.now(timezone.utc)
        rides = await self.store.list_rides()
        status = training_status(rides, now, self.timezone)
        series = fitness_series(rides, status.today, self.timezone)
        return TrainingOverview(
            status=status,
            fitness=series[-CHART_DAYS:],
            weeks=weekly_summaries(rides, status.today, self.timezone, weeks=CHART_WEEKS),
        )

    async def save_profile(self, profile: RiderProfile) -> int:
        """Save the profile and recompute every ride's load with it. Returns how many changed."""
        await self.store.save_profile(profile)
        changed = []
        for ride in await self.store.list_rides():
            load, method, intensity = training_load(
                moving_s=ride.moving_s,
                distance_m=ride.distance_m,
                ascent=ride.ascent_m,
                normalized_power=ride.normalized_power,
                avg_hr=ride.avg_hr,
                profile=profile,
            )
            load, intensity = round(load, 1), (round(intensity, 2) if intensity is not None else None)
            if (load, method, intensity) != (ride.load, ride.load_method, ride.intensity):
                changed.append((ride.id, load, method, intensity))
        if changed:
            await self.store.update_loads(changed)
        return len(changed)

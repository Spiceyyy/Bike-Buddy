from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models.route import LineString
from app.models.spec import Climbing

LoadMethod = Literal["power", "heart_rate", "estimate"]


class RiderProfile(BaseModel):
    """What training load needs to know about the rider. Every field is optional."""

    ftp_w: int | None = Field(default=None, ge=50, le=600)
    """Functional threshold power, in watts."""
    max_hr: int | None = Field(default=None, ge=100, le=230)
    resting_hr: int | None = Field(default=None, ge=30, le=100)


class RideMetrics(BaseModel):
    """Figures for one recorded ride, all computed in code."""

    started_at: datetime
    elapsed_s: float
    moving_s: float
    distance_m: float
    ascent_m: float | None
    avg_speed_kmh: float
    max_speed_kmh: float
    avg_hr: int | None = None
    max_hr: int | None = None
    avg_power: int | None = None
    normalized_power: int | None = None
    intensity: float | None = None
    """Intensity factor: normalized power over FTP, or the heart-rate equivalent."""
    load: float
    load_method: LoadMethod


class Ride(RideMetrics):
    id: int
    name: str | None = None
    filename: str


class RideDetail(Ride):
    geometry: LineString


class DayFitness(BaseModel):
    day: date
    load: float
    fitness: float
    """Chronic training load: 42-day weighted average of daily load (CTL)."""
    fatigue: float
    """Acute training load: 7-day weighted average of daily load (ATL)."""
    form: float
    """Fitness minus fatigue at the start of the day (TSB)."""


class WeekSummary(BaseModel):
    week_start: date
    rides: int
    distance_km: float
    moving_h: float
    ascent_m: float
    load: float


class RideSuggestion(BaseModel):
    """Today's ride, chosen in code from form and recent rides. The LLM explains it."""

    kind: Literal["rest", "recovery", "endurance", "long_or_hilly", "ease_back_in"]
    reason: str
    target_distance_km: float | None
    climbing: Climbing
    duration_min: int | None


class TrainingStatus(BaseModel):
    today: date
    rides: int
    last_ride: Ride | None = None
    days_since_last_ride: int | None = None
    fitness: float = 0
    fatigue: float = 0
    form: float = 0
    form_label: Literal["no data", "fresh", "neutral", "tired", "very tired"] = "no data"
    this_week: WeekSummary | None = None
    weekly_average: WeekSummary | None = None
    """Average of the four weeks before this one."""
    suggestion: RideSuggestion | None = None
    load_methods: dict[str, int] = {}
    """How many rides' load came from power, heart rate or an estimate."""

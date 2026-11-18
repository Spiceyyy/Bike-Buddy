"""The get_training_status tool: the rider's fitness, fatigue, form and a
suggested ride, all computed in code from their uploaded rides.

The LLM explains these figures and can pass the suggestion to plan_route; it
never computes a training number itself.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.db.rides import RideStoreError
from app.models.training import Ride, WeekSummary
from app.tools.plan_route import format_duration
from app.training.service import Trainer

NAME = "get_training_status"
DESCRIPTION = (
    "Get the rider's training status from their recorded rides: fitness, fatigue, form, "
    "recent riding, and a suggested ride for today chosen from those figures. Call it when "
    "the rider asks about their training or form, or wants a ride that suits their training."
)


class TrainingStatusArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")


class TrainingStatusTool:
    name = NAME
    description = DESCRIPTION
    args_model = TrainingStatusArgs

    def __init__(self, trainer: Trainer, now: datetime | None = None) -> None:
        self._trainer = trainer
        self._now = now

    async def run(self, args: TrainingStatusArgs) -> dict[str, Any]:
        try:
            status = await self._trainer.status(self._now)
        except RideStoreError:
            return {"status": "error", "reason": "ride history is unavailable"}
        if status.rides == 0:
            return {
                "status": "no_rides",
                "reason": "no recorded rides yet; the rider can upload GPX or FIT files in the Training panel",
            }

        result: dict[str, Any] = {
            "status": "ok",
            "rides": status.rides,
            "form": {
                "label": status.form_label,
                "fitness": round(status.fitness),
                "fatigue": round(status.fatigue),
                "form": round(status.form),
            },
            "last_ride": _ride(status.last_ride, status.days_since_last_ride),
            "this_week": _week(status.this_week),
            "weekly_average": _week(status.weekly_average),
            "load_from": status.load_methods,
        }
        if status.suggestion is not None:
            s = status.suggestion
            result["suggestion"] = {
                "kind": s.kind,
                "reason": s.reason,
                "target_distance_km": s.target_distance_km,
                "climbing": s.climbing.value,
                "duration": format_duration(s.duration_min * 60) if s.duration_min else None,
            }
        return result


def _ride(ride: Ride | None, days_ago: int | None) -> dict[str, Any] | None:
    if ride is None:
        return None
    return {
        "days_ago": days_ago,
        "distance_km": round(ride.distance_m / 1000, 1),
        "moving_time": format_duration(ride.moving_s),
        "climb_m": round(ride.ascent_m) if ride.ascent_m is not None else None,
        "avg_speed_kmh": ride.avg_speed_kmh,
        "load": round(ride.load),
    }


def _week(week: WeekSummary | None) -> dict[str, Any] | None:
    if week is None:
        return None
    return {
        "rides": week.rides,
        "distance_km": week.distance_km,
        "moving_time": format_duration(week.moving_h * 3600),
        "climb_m": round(week.ascent_m),
        "load": round(week.load),
    }

from datetime import datetime, timedelta, timezone

from app.db.rides import RideStoreError
from app.tools.training_status import TrainingStatusArgs, TrainingStatusTool
from app.training.service import Trainer
from tests.fake_rides import FakeRideStore, gpx_ride

NOW = datetime(2026, 6, 20, 16, 0, tzinfo=timezone.utc)


async def tool_with_rides(*days_ago: int) -> TrainingStatusTool:
    trainer = Trainer(FakeRideStore(), "America/Toronto")
    for days in days_ago:
        await trainer.upload(f"r{days}.gpx", gpx_ride(start=NOW - timedelta(days=days), seconds=3600))
    return TrainingStatusTool(trainer, now=NOW)


async def test_no_rides_says_how_to_add_them() -> None:
    result = await (await tool_with_rides()).run(TrainingStatusArgs())
    assert result["status"] == "no_rides"
    assert "Training panel" in result["reason"]


async def test_reports_form_recent_riding_and_a_suggestion() -> None:
    tool = await tool_with_rides(1, 3, 5, 8, 10)

    result = await tool.run(TrainingStatusArgs())

    assert result["status"] == "ok"
    assert result["rides"] == 5
    assert set(result["form"]) == {"label", "fitness", "fatigue", "form"}
    last = result["last_ride"]
    assert last["days_ago"] == 1
    assert last["distance_km"] == 25.2
    assert last["moving_time"] == "1 h 0 min"
    assert result["this_week"]["rides"] >= 1
    assert result["suggestion"]["kind"] in {"recovery", "endurance", "long_or_hilly"}
    assert result["suggestion"]["climbing"] in {"any", "avoid", "seek"}
    assert result["load_from"] == {"estimate": 5}


async def test_storage_failure_is_reported_to_the_llm() -> None:
    class BrokenStore(FakeRideStore):
        async def list_rides(self):
            raise RideStoreError("down")

    tool = TrainingStatusTool(Trainer(BrokenStore(), "America/Toronto"), now=NOW)
    assert (await tool.run(TrainingStatusArgs()))["status"] == "error"


def test_takes_no_arguments() -> None:
    assert TrainingStatusArgs.model_json_schema()["properties"] == {}
    TrainingStatusArgs.model_validate({"anything": 1})

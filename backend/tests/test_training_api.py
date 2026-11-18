from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.api.training import get_trainer
from app.config import Settings
from app.main import create_app
from app.models.training import RiderProfile
from app.training.service import Trainer
from tests.fake_rides import FakeRideStore, gpx_ride
from tests.fit_writer import write_fit

START = datetime(2026, 6, 1, 13, 0, tzinfo=timezone.utc)


def make_client() -> tuple[TestClient, Trainer]:
    trainer = Trainer(FakeRideStore(), "America/Toronto")
    app = create_app(Settings(database_url="postgresql://unused"))
    app.dependency_overrides[get_trainer] = lambda: trainer
    return TestClient(app), trainer


def upload(client: TestClient, *files: tuple[str, bytes]):
    return client.post("/api/rides", files=[("files", (name, data)) for name, data in files])


def test_uploads_gpx_and_fit_files() -> None:
    client, _ = make_client()
    fit = write_fit(
        [{"time": START + timedelta(seconds=i), "lat": 45.5 + i * 0.00006, "lon": -73.55, "ele": 20, "hr": 140}
         for i in range(600)]
    )

    response = upload(client, ("morning.gpx", gpx_ride()), ("evening.fit", fit))

    assert response.status_code == 200
    results = response.json()["results"]
    assert [r["status"] for r in results] == ["added", "added"]
    gpx = results[0]["ride"]
    assert gpx["name"] == "Morning Ride"
    assert gpx["distance_m"] == pytest.approx(12_600, rel=0.01)
    assert gpx["avg_power"] == 200
    assert gpx["load_method"] == "estimate"  # no profile yet


def test_the_same_file_twice_is_a_duplicate() -> None:
    client, _ = make_client()
    upload(client, ("ride.gpx", gpx_ride()))

    result = upload(client, ("copy.gpx", gpx_ride())).json()["results"][0]

    assert result["status"] == "duplicate"
    assert len(client.get("/api/rides").json()) == 1


def test_bad_files_are_reported_one_by_one() -> None:
    client, _ = make_client()
    route = b'<gpx xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg><trkpt lat="45.5" lon="-73.5"/></trkseg></trk></gpx>'

    results = upload(
        client, ("notes.txt", b"hello"), ("route.gpx", route), ("ok.gpx", gpx_ride())
    ).json()["results"]

    assert [r["status"] for r in results] == ["error", "error", "added"]
    assert results[0]["reason"] == "This file is not a GPX or FIT file."
    assert "planned route" in results[1]["reason"]


def test_a_ride_shorter_than_a_minute_is_refused() -> None:
    client, _ = make_client()
    result = upload(client, ("short.gpx", gpx_ride(seconds=40))).json()["results"][0]
    assert result["status"] == "error"


def test_lists_gets_and_deletes_rides() -> None:
    client, _ = make_client()
    upload(client, ("a.gpx", gpx_ride(start=START)), ("b.gpx", gpx_ride(start=START + timedelta(days=1))))

    rides = client.get("/api/rides").json()
    assert [r["filename"] for r in rides] == ["b.gpx", "a.gpx"]  # newest first

    detail = client.get(f"/api/rides/{rides[0]['id']}").json()
    assert detail["geometry"]["type"] == "LineString"
    assert len(detail["geometry"]["coordinates"]) > 100

    assert client.delete(f"/api/rides/{rides[0]['id']}").status_code == 204
    assert client.get(f"/api/rides/{rides[0]['id']}").status_code == 404
    assert client.delete("/api/rides/999").status_code == 404


def test_saving_the_profile_recomputes_loads() -> None:
    client, _ = make_client()
    upload(client, ("ride.gpx", gpx_ride(seconds=3600, power=250)))

    response = client.put("/api/profile", json={"ftp_w": 250, "max_hr": None, "resting_hr": None})

    assert response.status_code == 200
    assert response.json()["rides_updated"] == 1
    ride = client.get("/api/rides").json()[0]
    assert ride["load_method"] == "power"
    assert ride["load"] == pytest.approx(100, abs=1)
    assert client.get("/api/profile").json()["ftp_w"] == 250


def test_saving_the_same_profile_again_changes_nothing() -> None:
    client, _ = make_client()
    upload(client, ("ride.gpx", gpx_ride(seconds=3600, power=250)))
    client.put("/api/profile", json={"ftp_w": 250})

    assert client.put("/api/profile", json={"ftp_w": 250}).json()["rides_updated"] == 0


def test_profile_rejects_resting_above_max() -> None:
    client, _ = make_client()
    assert client.put("/api/profile", json={"max_hr": 150, "resting_hr": 160}).status_code == 422
    assert client.put("/api/profile", json={"ftp_w": 5000}).status_code == 422


def test_overview_has_status_series_and_weeks() -> None:
    client, _ = make_client()
    now = datetime.now(timezone.utc)
    upload(client, *[(f"r{d}.gpx", gpx_ride(start=now - timedelta(days=d))) for d in (1, 3, 5)])

    overview = client.get("/api/training").json()

    assert overview["status"]["rides"] == 3
    assert overview["status"]["suggestion"] is not None
    assert len(overview["weeks"]) == 12
    assert 5 <= len(overview["fitness"]) <= 7
    assert overview["fitness"][-1]["fitness"] > 0


def test_trainer_mode_can_be_turned_off() -> None:
    app = create_app(Settings(database_url="postgresql://unused", training_enabled=False))
    client = TestClient(app)
    assert client.get("/api/training").status_code == 404


@pytest.mark.parametrize("count, code", [(50, 200), (51, 422)])
def test_upload_count_is_limited(count: int, code: int) -> None:
    client, _ = make_client()
    files = [(f"r{i}.txt", b"x") for i in range(count)]
    assert upload(client, *files).status_code == code


def test_profile_change_moves_a_hr_ride_to_heart_rate_load() -> None:
    client, trainer = make_client()
    upload(client, ("ride.gpx", gpx_ride(seconds=3600, power=None, hr=176)))

    client.put("/api/profile", json={"max_hr": 190, "resting_hr": 50})

    ride = client.get("/api/rides").json()[0]
    assert ride["load_method"] == "heart_rate"
    assert ride["load"] == pytest.approx(100, abs=1)
    assert isinstance(trainer.store.saved_profile, RiderProfile)

from fastapi.testclient import TestClient

from app.api.feedback import get_feedback_store
from app.config import Settings
from app.db.feedback import FeedbackError
from app.main import create_app
from app.models.feedback import FeedbackRequest

ROUTE = {
    "geometry": {"type": "LineString", "coordinates": [[-73.55, 45.50], [-73.56, 45.51]]},
    "distance_m": 20_000,
    "ascend_m": 190.0,
    "scenic_score": 0.52,
    "scenic_share": 0.34,
    "stop_name": "Yaya Café",
}
BODY = {
    "rating": "up",
    "comment": "  Lovely canal stretch  ",
    "message": "A 20 km loop from the Old Port with a coffee stop",
    "spec": {"route_type": "loop", "start_place": "Old Port", "target_distance_km": 20, "stop": "cafe"},
    "option_label": "Most scenic",
    "route": ROUTE,
}


class FakeFeedbackStore:
    def __init__(self, fail: bool = False) -> None:
        self.saved: list[FeedbackRequest] = []
        self.fail = fail

    async def save(self, feedback: FeedbackRequest) -> int:
        if self.fail:
            raise FeedbackError("down")
        self.saved.append(feedback)
        return len(self.saved)


def make_client(store) -> TestClient:
    app = create_app(Settings(database_url="postgresql://unused"))
    app.dependency_overrides[get_feedback_store] = lambda: store
    return TestClient(app)


def test_saves_a_rating() -> None:
    store = FakeFeedbackStore()

    response = make_client(store).post("/api/feedback", json=BODY)

    assert response.status_code == 201
    assert response.json() == {"id": 1}
    saved = store.saved[0]
    assert saved.rating == "up"
    assert saved.option_label == "Most scenic"
    assert saved.spec is not None and saved.spec.stop is not None
    assert saved.route.stop_name == "Yaya Café"


def test_a_map_click_route_needs_no_request_or_spec() -> None:
    store = FakeFeedbackStore()
    body = {"rating": "down", "route": ROUTE}

    assert make_client(store).post("/api/feedback", json=body).status_code == 201
    assert store.saved[0].message is None


def test_rejects_an_unknown_rating() -> None:
    response = make_client(FakeFeedbackStore()).post("/api/feedback", json={**BODY, "rating": "meh"})
    assert response.status_code == 422


def test_rejects_a_long_comment() -> None:
    body = {**BODY, "comment": "x" * 1001}
    assert make_client(FakeFeedbackStore()).post("/api/feedback", json=body).status_code == 422


def test_rejects_a_route_of_one_point() -> None:
    route = {**ROUTE, "geometry": {"type": "LineString", "coordinates": [[-73.55, 45.5]]}}
    body = {**BODY, "route": route}
    assert make_client(FakeFeedbackStore()).post("/api/feedback", json=body).status_code == 422


def test_storage_failure_returns_503() -> None:
    response = make_client(FakeFeedbackStore(fail=True)).post("/api/feedback", json=BODY)

    assert response.status_code == 503
    assert response.json()["detail"] == "Could not save your feedback. Please try again."


def test_no_store_configured_returns_503() -> None:
    app = create_app(Settings(database_url="postgresql://unused"))
    assert TestClient(app).post("/api/feedback", json=BODY).status_code == 503

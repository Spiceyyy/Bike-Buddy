from fastapi.testclient import TestClient

from app.api.feedback import get_feedback_store
from app.api.plan import get_geocoder, get_llm_client
from app.api.routes import get_routing_client
from app.api.rate_limit import RateLimiter
from app.config import Settings
from app.main import create_app
from tests.fakes import FakeGeocoder, FakeLLM, FakeRouting, text_response
from tests.test_feedback_api import BODY, FakeFeedbackStore


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_allows_up_to_the_limit_then_says_how_long_to_wait() -> None:
    clock = Clock()
    limiter = RateLimiter(per_minute=2, clock=clock)

    assert limiter.wait_seconds("a") is None
    clock.now += 10
    assert limiter.wait_seconds("a") is None
    clock.now += 5
    # The first request ages out 60 s after it was made, 45 s from now.
    assert limiter.wait_seconds("a") == 45


def test_requests_free_up_as_they_age_out() -> None:
    clock = Clock()
    limiter = RateLimiter(per_minute=1, clock=clock)
    limiter.wait_seconds("a")

    clock.now += 60
    assert limiter.wait_seconds("a") is None


def test_rejected_requests_do_not_count() -> None:
    clock = Clock()
    limiter = RateLimiter(per_minute=1, clock=clock)
    limiter.wait_seconds("a")
    for _ in range(5):
        limiter.wait_seconds("a")

    clock.now += 60
    assert limiter.wait_seconds("a") is None


def test_clients_are_limited_separately() -> None:
    limiter = RateLimiter(per_minute=1, clock=Clock())
    limiter.wait_seconds("a")

    assert limiter.wait_seconds("b") is None


def test_daily_limit_applies_across_minutes() -> None:
    clock = Clock()
    limiter = RateLimiter(per_minute=5, per_day=2, clock=clock)
    limiter.wait_seconds("a")
    clock.now += 120
    limiter.wait_seconds("a")
    clock.now += 120

    wait = limiter.wait_seconds("a")
    assert wait is not None and wait > 80_000


def test_zero_turns_limits_off() -> None:
    limiter = RateLimiter(per_minute=0, per_day=0)
    assert not limiter.enabled
    assert all(limiter.wait_seconds("a") is None for _ in range(100))


def make_client(**settings) -> TestClient:
    app = create_app(Settings(database_url="postgresql://unused", **settings))
    app.dependency_overrides[get_feedback_store] = lambda: FakeFeedbackStore()
    return TestClient(app)


def test_api_returns_429_with_retry_after() -> None:
    client = make_client(feedback_requests_per_minute=2)

    statuses = [client.post("/api/feedback", json=BODY).status_code for _ in range(3)]
    limited = client.post("/api/feedback", json=BODY)

    assert statuses == [201, 201, 429]
    assert limited.status_code == 429
    assert 1 <= int(limited.headers["Retry-After"]) <= 60
    assert limited.json()["detail"] == "Too many ratings. Please wait a little."


def test_plan_limit_is_checked_before_the_llm() -> None:
    llm = FakeLLM(text_response("I plan bike routes."))
    client = make_client(plan_requests_per_minute=1)
    client.app.dependency_overrides[get_llm_client] = lambda: llm
    client.app.dependency_overrides[get_routing_client] = lambda: FakeRouting([10_000])
    client.app.dependency_overrides[get_geocoder] = lambda: FakeGeocoder()

    first = client.post("/api/plan", json={"message": "Hello"})
    second = client.post("/api/plan", json={"message": "Hello"})

    assert first.status_code == 200
    assert second.status_code == 429
    # The limited request never reached the LLM.
    assert len(llm.conversations) == 1


def test_no_limits_by_default() -> None:
    client = make_client()
    assert all(client.post("/api/feedback", json=BODY).status_code == 201 for _ in range(20))

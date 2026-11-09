from fastapi.testclient import TestClient

from app.agent.llm import LLMError, LLMRateLimitError
from app.api.plan import get_geocoder, get_llm_client
from app.api.routes import get_routing_client
from app.config import Settings
from app.main import create_app
from tests.fakes import FakeGeocoder, FakeLLM, FakeRouting, text_response, tool_call_response

LOOP_ARGS = {"route_type": "loop", "start_place": "Old Port", "target_distance_km": 50}


def make_client(llm, routing: FakeRouting | None = None) -> TestClient:
    app = create_app(Settings(database_url="postgresql://unused"))
    app.dependency_overrides[get_llm_client] = lambda: llm
    app.dependency_overrides[get_routing_client] = lambda: routing or FakeRouting([50_000])
    app.dependency_overrides[get_geocoder] = lambda: FakeGeocoder()
    return TestClient(app)


def test_returns_route_spec_and_reply() -> None:
    llm = FakeLLM(tool_call_response(LOOP_ARGS), text_response("A 50 km loop."))

    response = make_client(llm).post("/api/plan", json={"message": "50 km loop from the Old Port"})

    assert response.status_code == 200
    body = response.json()
    assert body["reply"] == "A 50 km loop."
    assert body["route"]["type"] == "Feature"
    assert body["spec"]["target_distance_km"] == 50
    assert (body["start_name"], body["end_name"]) == ("Old Port", "Old Port")
    assert body["usage"]["llm_calls"] == 2


def test_non_ride_message_returns_reply_without_route() -> None:
    response = make_client(FakeLLM(text_response("I plan bike routes."))).post(
        "/api/plan", json={"message": "Hello!"}
    )

    body = response.json()
    assert body["route"] is None
    assert body["spec"] is None


def test_map_start_reaches_the_tool() -> None:
    args = {"route_type": "loop", "target_distance_km": 50}
    llm = FakeLLM(tool_call_response(args), text_response("Done."))
    body = {"message": "50 km loop", "map_start": {"lat": 45.6, "lon": -73.6}}

    response = make_client(llm).post("/api/plan", json=body)

    assert response.json()["route"] is not None


def test_empty_message_is_rejected() -> None:
    assert make_client(FakeLLM()).post("/api/plan", json={"message": ""}).status_code == 422


def test_rate_limit_returns_429() -> None:
    class Limited:
        async def generate(self, system, messages, tools):
            raise LLMRateLimitError()

    assert make_client(Limited()).post("/api/plan", json={"message": "ride"}).status_code == 429


def test_daily_quota_says_how_long_to_wait() -> None:
    class Exhausted:
        async def generate(self, system, messages, tools):
            raise LLMRateLimitError(retry_after_seconds=16_342)

    response = make_client(Exhausted()).post("/api/plan", json={"message": "ride"})

    assert response.status_code == 429
    assert "daily quota" in response.json()["detail"]
    assert "5 h" in response.json()["detail"]


def test_llm_failure_returns_502() -> None:
    class Broken:
        async def generate(self, system, messages, tools):
            raise LLMError("down")

    assert make_client(Broken()).post("/api/plan", json={"message": "ride"}).status_code == 502


def test_missing_api_key_returns_503() -> None:
    app = create_app(Settings(database_url="postgresql://unused"))
    with TestClient(app) as client:
        app.state.llm_client = None
        response = client.post("/api/plan", json={"message": "ride"})

    assert response.status_code == 503
    assert "GEMINI_API_KEY" in response.json()["detail"]

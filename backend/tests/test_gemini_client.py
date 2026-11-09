import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.agent.gemini import GEMINI_BASE_URL, GeminiClient
from app.agent.llm import LLMError, LLMRateLimitError
from app.models.llm import (
    AssistantMessage,
    ToolCall,
    ToolDefinition,
    ToolResultMessage,
    UserMessage,
)

TOOL = ToolDefinition(name="plan_route", description="Plan", parameters={"type": "object"})
USER = [UserMessage(text="50 km loop")]
TEXT_BODY: dict[str, Any] = {
    "candidates": [{"content": {"role": "model", "parts": [{"text": "Hello"}]}}],
    "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 3, "thoughtsTokenCount": 2},
}
Handler = Callable[[httpx.Request], httpx.Response]


def make_client(handler: Handler) -> GeminiClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=GEMINI_BASE_URL)
    return GeminiClient(http, api_key="secret", model="test-model", retry_delay_seconds=0)


async def test_sends_key_in_header_not_url() -> None:
    seen: list[httpx.Request] = []

    def capture(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=TEXT_BODY)

    await make_client(capture).generate("sys", USER, [TOOL])

    assert seen[0].headers["x-goog-api-key"] == "secret"
    assert "secret" not in str(seen[0].url)
    assert seen[0].url.path == "/v1beta/models/test-model:generateContent"


async def test_sends_system_prompt_and_tool_schema() -> None:
    bodies: list[dict[str, Any]] = []

    def capture(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=TEXT_BODY)

    await make_client(capture).generate("sys", USER, [TOOL])

    assert bodies[0]["systemInstruction"] == {"parts": [{"text": "sys"}]}
    declaration = bodies[0]["tools"][0]["functionDeclarations"][0]
    assert declaration["parametersJsonSchema"] == {"type": "object"}


async def test_parses_text_and_counts_thinking_as_output() -> None:
    client = make_client(lambda request: httpx.Response(200, json=TEXT_BODY))

    response = await client.generate("sys", USER, [TOOL])

    assert response.message.text == "Hello"
    assert (response.input_tokens, response.output_tokens) == (10, 5)


async def test_parses_function_call() -> None:
    part = {"functionCall": {"id": "abc", "name": "plan_route", "args": {"route_type": "loop"}}}
    body = {"candidates": [{"content": {"role": "model", "parts": [part]}}]}
    client = make_client(lambda request: httpx.Response(200, json=body))

    response = await client.generate("sys", USER, [TOOL])

    assert response.message.tool_calls == [
        ToolCall(id="abc", name="plan_route", arguments={"route_type": "loop"})
    ]


async def test_replays_provider_state_and_groups_tool_results() -> None:
    bodies: list[dict[str, Any]] = []

    def capture(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=TEXT_BODY)

    signed = {
        "role": "model",
        "parts": [{"functionCall": {"name": "plan_route", "args": {}}, "thoughtSignature": "sig"}],
    }
    history = [
        *USER,
        AssistantMessage(provider_state=signed),
        ToolResultMessage(call_id="1", name="plan_route", content={"status": "ok"}),
        ToolResultMessage(call_id="2", name="plan_route", content={"status": "ok"}),
    ]

    await make_client(capture).generate("sys", history, [TOOL])

    contents = bodies[0]["contents"]
    assert contents[1] == signed
    assert len(contents) == 3
    assert len(contents[2]["parts"]) == 2


async def test_rate_limit_reports_suggested_wait() -> None:
    body = {"error": {"details": [{"@type": "RetryInfo", "retryDelay": "37s"}]}}
    client = make_client(lambda request: httpx.Response(429, json=body))

    with pytest.raises(LLMRateLimitError) as caught:
        await client.generate("sys", USER, [TOOL])

    assert caught.value.retry_after_seconds == 37


async def test_retries_server_errors_once() -> None:
    responses = [httpx.Response(503), httpx.Response(200, json=TEXT_BODY)]

    response = await make_client(lambda request: responses.pop(0)).generate("sys", USER, [TOOL])

    assert response.message.text == "Hello"


async def test_retries_timeouts_then_raises() -> None:
    attempts: list[int] = []

    def time_out(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(LLMError):
        await make_client(time_out).generate("sys", USER, [TOOL])

    assert len(attempts) == 2


async def test_client_errors_are_not_retried() -> None:
    attempts: list[int] = []

    def reject(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(400, text="bad")

    with pytest.raises(LLMError):
        await make_client(reject).generate("sys", USER, [TOOL])

    assert len(attempts) == 1


@pytest.mark.parametrize(
    "body",
    [
        {"candidates": []},
        {"promptFeedback": {"blockReason": "SAFETY"}},
        {"candidates": [{"finishReason": "MAX_TOKENS", "content": {"role": "model"}}]},
    ],
)
async def test_unusable_responses_raise(body: dict[str, Any]) -> None:
    client = make_client(lambda request: httpx.Response(200, json=body))

    with pytest.raises(LLMError):
        await client.generate("sys", USER, [TOOL])

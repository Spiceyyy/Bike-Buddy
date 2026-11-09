"""LLMClient implementation for the Gemini REST API."""

import asyncio
import logging
import re
from typing import Any, Sequence

import httpx

from app.agent.llm import LLMError, LLMRateLimitError
from app.models.llm import (
    AssistantMessage,
    LLMResponse,
    Message,
    ToolCall,
    ToolDefinition,
    ToolResultMessage,
    UserMessage,
)

logger = logging.getLogger(__name__)

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com"
MAX_ATTEMPTS = 2


class GeminiClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        api_key: str,
        model: str,
        retry_delay_seconds: float = 1.0,
    ) -> None:
        self._http = http
        self._api_key = api_key
        self._model = model
        self._retry_delay = retry_delay_seconds

    async def generate(
        self,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition],
    ) -> LLMResponse:
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": _to_contents(messages),
            "tools": [{"functionDeclarations": [_to_declaration(tool) for tool in tools]}],
            "toolConfig": {"functionCallingConfig": {"mode": "AUTO"}},
            "generationConfig": {"temperature": 0},
        }
        return _parse_response(await self._post(body))

    async def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        url = f"/v1beta/models/{self._model}:generateContent"
        headers = {"x-goog-api-key": self._api_key}

        for attempt in range(1, MAX_ATTEMPTS + 1):
            is_last_attempt = attempt == MAX_ATTEMPTS
            try:
                response = await self._http.post(url, json=body, headers=headers)
            except httpx.HTTPError as exc:
                if is_last_attempt:
                    raise LLMError("could not reach Gemini") from exc
            else:
                if response.status_code == httpx.codes.TOO_MANY_REQUESTS:
                    logger.warning("gemini rate limited: %s", response.text[:800])
                    raise LLMRateLimitError(_retry_after_seconds(response))
                if response.status_code == httpx.codes.OK:
                    return _json_object(response)
                if response.status_code < 500 or is_last_attempt:
                    raise LLMError(f"Gemini returned {response.status_code}")
            await asyncio.sleep(self._retry_delay)

        raise LLMError("Gemini request failed")  # Unreachable; satisfies type checkers.


def _to_declaration(tool: ToolDefinition) -> dict[str, Any]:
    return {
        "name": tool.name,
        "description": tool.description,
        "parametersJsonSchema": tool.parameters,
    }


def _to_contents(messages: Sequence[Message]) -> list[dict[str, Any]]:
    contents: list[dict[str, Any]] = []
    for message in messages:
        if isinstance(message, UserMessage):
            contents.append({"role": "user", "parts": [{"text": message.text}]})
        elif isinstance(message, AssistantMessage):
            contents.append(message.provider_state or _assistant_content(message))
        else:
            _append_tool_result(contents, message)
    return contents


def _assistant_content(message: AssistantMessage) -> dict[str, Any]:
    parts: list[dict[str, Any]] = [{"text": message.text}] if message.text else []
    parts += [
        {"functionCall": {"id": call.id, "name": call.name, "args": call.arguments}}
        for call in message.tool_calls
    ]
    return {"role": "model", "parts": parts}


def _append_tool_result(contents: list[dict[str, Any]], message: ToolResultMessage) -> None:
    """Add a result, grouping results of parallel calls into one user turn."""
    part = {
        "functionResponse": {
            "id": message.call_id,
            "name": message.name,
            "response": message.content,
        }
    }
    previous = contents[-1] if contents else None
    if previous and previous["role"] == "user" and "functionResponse" in previous["parts"][0]:
        previous["parts"].append(part)
    else:
        contents.append({"role": "user", "parts": [part]})


def _parse_response(data: dict[str, Any]) -> LLMResponse:
    try:
        candidate = data["candidates"][0]
        content = candidate["content"]
        parts = content.get("parts", [])
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError("Gemini returned no usable candidate") from exc

    text = "".join(part["text"] for part in parts if "text" in part and not part.get("thought"))
    tool_calls = [
        ToolCall(
            id=part["functionCall"].get("id") or f"call_{index}",
            name=part["functionCall"]["name"],
            arguments=part["functionCall"].get("args") or {},
        )
        for index, part in enumerate(parts)
        if "functionCall" in part
    ]
    if not text and not tool_calls:
        raise LLMError(f"Gemini returned an empty reply ({candidate.get('finishReason')})")

    usage = data.get("usageMetadata", {})
    return LLMResponse(
        # Replaying `content` unchanged preserves Gemini's thought signatures.
        message=AssistantMessage(text=text, tool_calls=tool_calls, provider_state=content),
        input_tokens=usage.get("promptTokenCount", 0),
        output_tokens=usage.get("candidatesTokenCount", 0) + usage.get("thoughtsTokenCount", 0),
    )


def _json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as exc:
        raise LLMError("Gemini returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise LLMError("Gemini returned an unexpected body")
    return data


def _retry_after_seconds(response: httpx.Response) -> float | None:
    """Read the wait Gemini suggests, sent as a RetryInfo detail such as "37s"."""
    try:
        details = response.json()["error"]["details"]
    except (KeyError, TypeError, ValueError):
        return None
    for detail in details if isinstance(details, list) else []:
        match = re.fullmatch(r"(\d+(?:\.\d+)?)s", str(detail.get("retryDelay", "")))
        if match:
            return float(match.group(1))
    return None

"""Provider-neutral message types used by the agent loop."""

from typing import Any

from pydantic import BaseModel


class ToolDefinition(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any]


class UserMessage(BaseModel):
    text: str


class AssistantMessage(BaseModel):
    text: str = ""
    tool_calls: list[ToolCall] = []
    # Opaque provider data that must be replayed unchanged on the next request.
    provider_state: dict[str, Any] | None = None


class ToolResultMessage(BaseModel):
    call_id: str
    name: str
    content: dict[str, Any]


Message = UserMessage | AssistantMessage | ToolResultMessage


class LLMResponse(BaseModel):
    message: AssistantMessage
    input_tokens: int = 0
    output_tokens: int = 0


class Usage(BaseModel):
    """Totals for one request, recorded for debugging and evals."""

    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0

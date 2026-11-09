"""The interface the agent loop uses, so LLM providers can be swapped."""

from typing import Protocol, Sequence

from app.models.llm import LLMResponse, Message, ToolDefinition


class LLMError(Exception):
    """The LLM provider failed or returned something unusable."""


class LLMRateLimitError(LLMError):
    """The provider's rate limit or quota was reached."""

    def __init__(self, retry_after_seconds: float | None = None) -> None:
        super().__init__("rate limited by LLM provider")
        self.retry_after_seconds = retry_after_seconds


class LLMClient(Protocol):
    async def generate(
        self,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition],
    ) -> LLMResponse: ...

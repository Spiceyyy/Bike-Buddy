"""The agent loop: call the LLM, run the tools it asks for, repeat until it answers."""

import json
import logging
import time
from typing import Any, Sequence

from pydantic import BaseModel, ValidationError

from app.agent.llm import LLMClient
from app.models.llm import Message, ToolCall, ToolResultMessage, Usage, UserMessage
from app.tools.base import Tool, to_definition

logger = logging.getLogger(__name__)

DEFAULT_MAX_TURNS = 6
FALLBACK_REPLY = "Sorry, I couldn't finish planning that ride. Please try rephrasing it."


class AgentRun(BaseModel):
    reply: str
    usage: Usage


async def run_agent(
    llm: LLMClient,
    system: str,
    user_message: str,
    tools: Sequence[Tool],
    max_turns: int = DEFAULT_MAX_TURNS,
) -> AgentRun:
    registry = {tool.name: tool for tool in tools}
    definitions = [to_definition(tool) for tool in tools]
    messages: list[Message] = [UserMessage(text=user_message)]
    usage = Usage()
    started = time.perf_counter()

    for _ in range(max_turns):
        call_started = time.perf_counter()
        response = await llm.generate(system, messages, definitions)
        assistant = response.message

        usage.llm_calls += 1
        usage.input_tokens += response.input_tokens
        usage.output_tokens += response.output_tokens
        _log(
            "llm_call",
            input=_describe_last(messages),
            text=assistant.text,
            tool_calls=[call.model_dump() for call in assistant.tool_calls],
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            latency_ms=_elapsed_ms(call_started),
        )

        if not assistant.tool_calls:
            usage.latency_ms = _elapsed_ms(started)
            return AgentRun(reply=assistant.text.strip() or FALLBACK_REPLY, usage=usage)

        messages.append(assistant)
        for call in assistant.tool_calls:
            messages.append(await _execute(call, registry))

    usage.latency_ms = _elapsed_ms(started)
    return AgentRun(reply=FALLBACK_REPLY, usage=usage)


async def _execute(call: ToolCall, registry: dict[str, Tool]) -> ToolResultMessage:
    """Run one tool call. Mistakes go back to the LLM so it can correct them."""
    started = time.perf_counter()
    tool = registry.get(call.name)

    if tool is None:
        content: dict[str, Any] = {"status": "invalid_arguments", "reason": "unknown tool"}
    else:
        try:
            arguments = tool.args_model.model_validate(call.arguments)
        except ValidationError as exc:
            content = {
                "status": "invalid_arguments",
                "errors": exc.errors(include_url=False, include_context=False, include_input=False),
            }
        else:
            content = await tool.run(arguments)

    _log(
        "tool_call",
        name=call.name,
        input=call.arguments,
        output=content,
        latency_ms=_elapsed_ms(started),
    )
    return ToolResultMessage(call_id=call.id, name=call.name, content=content)


def _describe_last(messages: Sequence[Message]) -> str:
    last = messages[-1]
    return last.text if isinstance(last, UserMessage) else last.model_dump_json()


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


def _log(event: str, **fields: Any) -> None:
    logger.info("%s %s", event, json.dumps(fields, ensure_ascii=False, default=str))

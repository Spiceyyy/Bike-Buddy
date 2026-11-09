from typing import Any, Protocol

from pydantic import BaseModel

from app.models.llm import ToolDefinition


class Tool(Protocol):
    """A deterministic capability the LLM can call."""

    name: str
    description: str
    args_model: type[BaseModel]

    async def run(self, args: Any) -> dict[str, Any]: ...


def to_definition(tool: Tool) -> ToolDefinition:
    return ToolDefinition(
        name=tool.name,
        description=tool.description,
        parameters=tool.args_model.model_json_schema(),
    )

from app.agent.orchestrator import FALLBACK_REPLY, run_agent
from app.models.llm import ToolResultMessage
from app.tools.plan_route import PlanRouteTool
from tests.fakes import FakeGeocoder, FakeLLM, FakeRouting, text_response, tool_call_response

VALID_ARGS = {"route_type": "loop", "start_place": "Old Port", "target_distance_km": 50}


def make_tool() -> PlanRouteTool:
    return PlanRouteTool(FakeRouting([50_000, 50_000]), FakeGeocoder())


async def test_answers_directly_when_no_tool_is_needed() -> None:
    llm = FakeLLM(text_response("I plan bike routes."))

    run = await run_agent(llm, "system", "Hello!", [make_tool()])

    assert run.reply == "I plan bike routes."
    assert run.usage.llm_calls == 1


async def test_runs_tool_and_returns_its_result_to_the_llm() -> None:
    llm = FakeLLM(tool_call_response(VALID_ARGS), text_response("50 km loop."))
    tool = make_tool()

    run = await run_agent(llm, "system", "50 km loop", [tool])

    assert run.reply == "50 km loop."
    assert tool.route is not None
    result = llm.conversations[1][-1]
    assert isinstance(result, ToolResultMessage)
    assert result.content["status"] == "ok"


async def test_route_geometry_is_never_sent_to_the_llm() -> None:
    llm = FakeLLM(tool_call_response(VALID_ARGS), text_response("Done."))

    await run_agent(llm, "system", "50 km loop", [make_tool()])

    result = llm.conversations[1][-1]
    assert "coordinates" not in result.model_dump_json()
    assert "geometry" not in result.model_dump_json()


async def test_invalid_arguments_go_back_to_the_llm() -> None:
    llm = FakeLLM(
        tool_call_response({"route_type": "teleport"}),
        tool_call_response(VALID_ARGS),
        text_response("Fixed."),
    )
    tool = make_tool()

    run = await run_agent(llm, "system", "ride", [tool])

    assert llm.conversations[1][-1].content["status"] == "invalid_arguments"
    assert run.reply == "Fixed."
    assert tool.route is not None


async def test_unknown_tool_goes_back_to_the_llm() -> None:
    llm = FakeLLM(tool_call_response({}, name="make_coffee"), text_response("Sorry."))

    await run_agent(llm, "system", "ride", [make_tool()])

    assert llm.conversations[1][-1].content["status"] == "invalid_arguments"


async def test_stops_after_max_turns() -> None:
    llm = FakeLLM(*[tool_call_response({"route_type": "bad"}) for _ in range(2)])

    run = await run_agent(llm, "system", "ride", [make_tool()], max_turns=2)

    assert run.reply == FALLBACK_REPLY
    assert run.usage.llm_calls == 2


async def test_empty_final_text_uses_fallback() -> None:
    run = await run_agent(FakeLLM(text_response("  ")), "system", "ride", [make_tool()])

    assert run.reply == FALLBACK_REPLY


async def test_adds_up_usage_across_turns() -> None:
    llm = FakeLLM(tool_call_response(VALID_ARGS), text_response("Done."))

    run = await run_agent(llm, "system", "ride", [make_tool()])

    assert run.usage.input_tokens == 220
    assert run.usage.output_tokens == 50

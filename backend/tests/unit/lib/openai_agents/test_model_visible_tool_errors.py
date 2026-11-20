"""Pin what the model receives when a function tool call fails.

The expected strings were captured from the v0.11.11 production image
(openai-agents 0.17.4, openai 2.41.0, pydantic 2.13) with the same fake-model turn
used here. A real Runner turn sends one tool call and records the
``function_call_output`` the second model request receives.
"""

import pytest
from agents import Agent, ModelResponse, RunConfig, Runner
from agents.exceptions import ModelBehaviorError
from agents.models.interface import Model
from agents.usage import Usage
from openai.types.responses import (
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
)
from pydantic import BaseModel, ValidationError

from agr_ai_curation_runtime.function_tools import function_tool


class Selection(BaseModel):
    chunk_index: int
    span_index: int


@function_tool
def strict_tool(entity: str, count: int) -> str:
    """Strict tool."""
    return f"ok {entity} {count}"


@function_tool(strict_mode=False)
def nonstrict_tool(entity: str, selections: list[Selection]) -> str:
    """Non-strict tool."""
    return "ok"


@function_tool
def raising_tool(entity: str) -> str:
    """Raises."""
    raise ValueError(f"lookup failed for {entity}: detail visible to model")


@function_tool(strict_mode=False)
def raising_nonstrict_tool(entity: str) -> str:
    """Raises (non-strict)."""
    raise RuntimeError("runtime problem detail")


_PREFIX = "An error occurred while running the tool. Please try again. Error: "

CASES = [
    (strict_tool, '{"entity": "a", "count": "not-a-number"}',
     _PREFIX + "Invalid JSON input for tool strict_tool: 1 validation error for strict_tool_args\n"
     "count\n  Input should be a valid integer, unable to parse string as an integer "
     "[type=int_parsing, input_value='not-a-number', input_type=str]\n"
     "    For further information visit https://errors.pydantic.dev/2.13/v/int_parsing"),
    (strict_tool, '{"entity": "a"}',
     _PREFIX + "Invalid JSON input for tool strict_tool: 1 validation error for strict_tool_args\n"
     "count\n  Field required [type=missing, input_value={'entity': 'a'}, input_type=dict]\n"
     "    For further information visit https://errors.pydantic.dev/2.13/v/missing"),
    (strict_tool, '{"entity": "a", ',
     _PREFIX + "Invalid JSON input for tool strict_tool"),
    (nonstrict_tool, '{"entity": "a", "selections": [{"chunk_index": 1, "span_index": "x"}]}',
     _PREFIX + "Invalid JSON input for tool nonstrict_tool: 1 validation error for nonstrict_tool_args\n"
     "selections.0.span_index\n  Input should be a valid integer, unable to parse string as an "
     "integer [type=int_parsing, input_value='x', input_type=str]\n"
     "    For further information visit https://errors.pydantic.dev/2.13/v/int_parsing"),
    (nonstrict_tool, '{"selections": []}',
     _PREFIX + "Invalid JSON input for tool nonstrict_tool: 1 validation error for nonstrict_tool_args\n"
     "entity\n  Field required [type=missing, input_value={'selections': []}, input_type=dict]\n"
     "    For further information visit https://errors.pydantic.dev/2.13/v/missing"),
    (raising_tool, '{"entity": "gene-x"}',
     _PREFIX + "lookup failed for gene-x: detail visible to model"),
    (raising_nonstrict_tool, '{"entity": "gene-x"}', _PREFIX + "runtime problem detail"),
]


def _response(output):
    return ModelResponse(output=output, usage=Usage(), response_id=None)


class ScriptedModel(Model):
    """Turn 1 calls the tool; turn 2 records its input and finishes."""

    def __init__(self, tool_name, arguments):
        self.tool_name, self.arguments, self.calls, self.seen = tool_name, arguments, 0, None

    async def get_response(self, system_instructions, input, *args, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return _response([ResponseFunctionToolCall(
                type="function_call", call_id="call_1", name=self.tool_name,
                arguments=self.arguments, id="fc_1", status="completed")])
        self.seen = input
        return _response([ResponseOutputMessage(
            id="msg_1", type="message", role="assistant", status="completed",
            content=[ResponseOutputText(type="output_text", text="done", annotations=[])])])

    def stream_response(self, *args, **kwargs):
        raise NotImplementedError


async def _model_visible_output(tool, arguments):
    model = ScriptedModel(tool.name, arguments)
    agent = Agent(name="capture", instructions="x", model=model, tools=[tool])
    await Runner.run(agent, "go", run_config=RunConfig(tracing_disabled=True))
    [output] = [item["output"] for item in model.seen
                if isinstance(item, dict) and item.get("type") == "function_call_output"]
    return output


@pytest.mark.asyncio
@pytest.mark.parametrize("tool,arguments,expected", CASES)
async def test_failed_tool_call_reaches_the_model_with_the_established_text(tool, arguments, expected):
    assert await _model_visible_output(tool, arguments) == expected


@pytest.mark.asyncio
async def test_explicit_failure_handler_receives_the_validation_error_as_cause():
    seen = []

    def handler(ctx, error):
        seen.append(error)
        return "handled"

    @function_tool(strict_mode=False, failure_error_function=handler)
    def record(entity: str, selections: list[Selection]) -> str:
        """Record."""
        return "ok"

    output = await _model_visible_output(
        record, '{"entity": "a", "selections": [{"chunk_index": 1, "span_index": "x"}]}')
    assert output == "handled"
    [error] = seen
    assert isinstance(error, ModelBehaviorError)
    assert isinstance(error.__cause__, ValidationError)
    assert [".".join(map(str, issue["loc"])) for issue in error.__cause__.errors()] == [
        "selections.0.span_index"]


@pytest.mark.asyncio
async def test_failure_handler_none_still_propagates():
    @function_tool(failure_error_function=None)
    def propagate(entity: str) -> str:
        """Propagate."""
        raise ValueError("boom")

    with pytest.raises(Exception, match="boom"):
        await _model_visible_output(propagate, '{"entity": "a"}')

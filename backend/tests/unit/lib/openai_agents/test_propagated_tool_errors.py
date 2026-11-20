"""Pin invalid-argument errors from tools declared with failure_error_function=None.

These tools propagate their errors. The expected text was captured from the v0.11.11
production image (openai-agents 0.17.4): the raised ModelBehaviorError, the error a
Runner turn raises, and what the supervisor's specialist error handler returns.
"""

from types import SimpleNamespace

import pytest
from agents import Agent, RunConfig, Runner
from agents.exceptions import ModelBehaviorError
from agents.tool_context import ToolContext
from pydantic import ValidationError

from src.lib.openai_agents.agents import supervisor_agent
from src.lib.openai_agents.tools.evidence_workspace import create_get_recorded_evidence_tool
from src.lib.validation_review.tools import create_tools
from tests.unit.lib.openai_agents.test_model_visible_tool_errors import ScriptedModel

CASES = [
    ('get_recorded_evidence', '{"evidence_record_id": "e1", "detail_cursor": "not-a-number"}',
     "Invalid JSON input for tool get_recorded_evidence: 1 validation error for get_recorded_evidence_args\ndetail_cursor\n  Input should be a valid integer, unable to parse string as an integer [type=int_parsing, input_value='not-a-number', input_type=str]\n    For further information visit https://errors.pydantic.dev/2.13/v/int_parsing",
     True),
    ('get_recorded_evidence', '{"detail_path": "x"}',
     "Invalid JSON input for tool get_recorded_evidence: 1 validation error for get_recorded_evidence_args\nevidence_record_id\n  Field required [type=missing, input_value={'detail_path': 'x'}, input_type=dict]\n    For further information visit https://errors.pydantic.dev/2.13/v/missing",
     True),
    ('get_recorded_evidence', '{"evidence_record_id": ',
     'Invalid JSON input for tool get_recorded_evidence',
     False),
    ('read_review_packet', '{"page": "x"}',
     "Invalid JSON input for tool read_review_packet: 1 validation error for read_review_packet_args\npage\n  Input should be a valid integer, unable to parse string as an integer [type=int_parsing, input_value='x', input_type=str]\n    For further information visit https://errors.pydantic.dev/2.13/v/int_parsing",
     True),
]


def _tool(name):
    if name == "get_recorded_evidence":
        return create_get_recorded_evidence_tool("doc-a", "user-a")
    return {tool.name: tool for tool in create_tools(None, checkpoint_provider=None)}[name]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name,arguments,expected,has_cause", CASES)
async def test_direct_invocation_raises_the_established_error(tool_name, arguments, expected, has_cause):
    tool = _tool(tool_name)
    context = ToolContext(context=None, tool_name=tool.name, tool_call_id="call_1",
                          tool_arguments=arguments)
    with pytest.raises(ModelBehaviorError) as raised:
        await tool.on_invoke_tool(context, arguments)
    assert str(raised.value) == expected
    assert isinstance(raised.value.__cause__, ValidationError) is has_cause


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name,arguments,expected,has_cause", CASES)
async def test_runner_turn_and_specialist_handler_keep_the_established_text(
    monkeypatch, tool_name, arguments, expected, has_cause,
):
    tool = _tool(tool_name)
    agent = Agent(name="capture", instructions="x", model=ScriptedModel(tool.name, arguments),
                  tools=[tool])
    with pytest.raises(ModelBehaviorError) as raised:
        await Runner.run(agent, "go", run_config=RunConfig(tracing_disabled=True))
    assert str(raised.value) == expected
    run_error = raised.value

    async def fail(**kwargs):
        raise run_error

    monkeypatch.setattr(supervisor_agent, "run_specialist_with_events", fail)
    specialist = supervisor_agent._create_streaming_tool(
        agent=SimpleNamespace(name="Specialist"), tool_name="ask_specialist",
        tool_description="Ask", specialist_name="Specialist", propagate_errors=False,
    )
    context = ToolContext(context=None, tool_name=specialist.name, tool_call_id="call_s",
                          tool_arguments='{"query":"x"}')
    assert await specialist.on_invoke_tool(context, '{"query":"x"}') == (
        "An error occurred while running the tool. Please try again. Error: " + expected)

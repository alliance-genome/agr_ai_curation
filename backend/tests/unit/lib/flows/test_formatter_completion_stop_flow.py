"""ALL-1496: a flow output step ends when its formatter saved or delivered output.

Drives the real flow file and chat output-step tools, the catalog build of the
packaged CSV and chat output formatters, the streaming specialist runner and the
formatter tools with a scripted provider model. Each scenario also runs once
with the previous behavior (the model is asked again after every tool) as a
baseline. After a saved file or delivered chat output the step result matches
it with one fewer model call; after formatter_cannot_complete the run is
unchanged.
"""

from __future__ import annotations

import importlib
import json
from types import SimpleNamespace
from typing import Any

import pytest
from agents import RunConfig
from agents.items import ModelResponse
from agents.models.interface import Model
from agents.usage import Usage
from openai.types.responses import (
    Response,
    ResponseCompletedEvent,
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
    ResponseTextDeltaEvent,
)

from src.lib.agent_studio import catalog_service
from src.lib.config.agent_loader import get_agent_definition
from src.lib.flows.chat_output_delivery import chat_output_delivery_scope
from src.lib.openai_agents.streaming_tools import set_live_event_list
from src.lib.openai_agents.tools.output_formatter_tools import (
    formatter_completion_tool_use_behavior,
)
from tests.fixtures.flows.semantic_chat_output import (
    SEMANTIC_CHAT_PLAN,
    build_semantic_output_step,
)
from tests.reviewed_output_unit_support import (
    mark_reviewed_output_bundle,
    reviewed_output_reader as reviewed_output_reader,
)
from tests.unit.lib.prompts.test_assembly import _prompt

pytestmark = pytest.mark.usefixtures("reviewed_output_reader")

_FORMATTER_ID = "csv_formatter"
_TOOL_NAME = "ask_csv_formatter_specialist"
_CHAT_FORMATTER_ID = "chat_output_formatter"
_CHAT_TOOL_NAME = "ask_chat_output_formatter_specialist"
_CLOSING_TEXT = "Output ready."


def _executor_module():
    return importlib.import_module("src.lib.flows.executor")


class _ScriptedStreamingFormatterModel(Model):
    """Streams scripted tool calls, then the closing text formatters write today."""

    def __init__(self, steps: list[tuple[str, dict[str, Any]]]):
        self.steps = steps
        self.requests: list[list[str]] = []

    async def get_response(self, system_instructions, input, model_settings, tools, *args, **kwargs):
        self.requests.append(sorted(getattr(tool, "name", "") for tool in tools))
        index = len(self.requests) - 1
        if index < len(self.steps):
            name, arguments = self.steps[index]
            output = [
                ResponseFunctionToolCall(
                    type="function_call",
                    name=name,
                    call_id=f"call-{index}",
                    arguments=json.dumps(arguments),
                )
            ]
        else:
            output = [
                ResponseOutputMessage(
                    id="msg-final",
                    type="message",
                    role="assistant",
                    status="completed",
                    content=[
                        ResponseOutputText(type="output_text", text=_CLOSING_TEXT, annotations=[])
                    ],
                )
            ]
        return ModelResponse(output=output, usage=Usage(requests=1), response_id=f"resp-{index}")

    async def stream_response(self, system_instructions, input, model_settings, tools, *args, **kwargs):
        response = await self.get_response(system_instructions, input, model_settings, tools)
        for item in response.output:
            if isinstance(item, ResponseOutputMessage):
                # Streamed closing text is what the audit view shows as "Agent reasoning".
                yield ResponseTextDeltaEvent(
                    type="response.output_text.delta",
                    content_index=0,
                    delta=_CLOSING_TEXT,
                    item_id=item.id,
                    logprobs=[],
                    output_index=0,
                    sequence_number=0,
                )
        terminal = Response(
            id=response.response_id,
            created_at=0,
            model="fixture",
            object="response",
            output=response.output,
            parallel_tool_calls=False,
            tool_choice="auto",
            tools=[],
            status="completed",
            temperature=None,
            top_p=None,
        )
        yield ResponseCompletedEvent(type="response.completed", sequence_number=1, response=terminal)


async def _deterministic_save(output_format, projection, filename_hint, formatter_agent_id):
    return {
        "file_id": "file-flow-csv-1",
        "filename": f"{filename_hint}.{output_format}",
        "format": output_format,
        "size_bytes": 512,
        "download_url": "/api/files/file-flow-csv-1/download",
        "formatter_label": "Expression CSV",
        "source_label": "Expression Statement Extractor",
    }


def _packaged_formatter_row(agent_id: str) -> SimpleNamespace:
    definition = get_agent_definition(agent_id)
    assert definition is not None
    return SimpleNamespace(
        id="formatter-agent-id",
        agent_key=agent_id,
        visibility="system",
        instructions="",
        group_prompt_overrides={},
        group_rules_enabled=False,
        template_source=None,
        group_rules_component=None,
        model_id=definition.model_config.model,
        model_temperature=definition.model_config.temperature,
        model_reasoning=definition.model_config.reasoning,
        output_schema_key=definition.output_schema,
        tool_ids=list(definition.tools),
        name=definition.name,
        category=definition.category,
    )


_BASE_CONTEXT = {
    "user_id": "unit-curator",
    "db_user_id": 7,
    "authenticated_groups": [],
    "active_groups": [],
    "database_url": "postgresql://unit-test/formatter",
}


@pytest.fixture
def install_formatter(monkeypatch):
    """Build the packaged formatter through the catalog with a scripted model.

    Returns a function that installs one scripted run and returns its model.
    ``previous_behavior`` restores the SDK default for the baseline run.
    """

    executor = _executor_module()
    monkeypatch.setattr(
        "src.lib.prompts.assembly.get_all_active_prompts",
        lambda: {
            f"{agent_id}:system:base": _prompt(agent_id, "system", "Format the saved results.")
            for agent_id in (_FORMATTER_ID, _CHAT_FORMATTER_ID, "chat_output")
        },
    )
    monkeypatch.setattr(
        "src.lib.openai_agents.tools.file_output_tools.save_projected_file_output",
        _deterministic_save,
    )

    def install(steps, *, agent_id: str, previous_behavior: bool):
        model = _ScriptedStreamingFormatterModel(steps)
        monkeypatch.setattr(
            "src.lib.openai_agents.config.get_model_for_agent",
            lambda *_args, **_kwargs: model,
        )

        def build_formatter(requested_agent_id, **kwargs):
            assert requested_agent_id == agent_id
            mark_reviewed_output_bundle(kwargs["formatter_bundle"])
            agent = catalog_service._create_db_agent(_packaged_formatter_row(agent_id), **kwargs)
            assert agent.tool_use_behavior is formatter_completion_tool_use_behavior
            if previous_behavior:
                agent.tool_use_behavior = "run_llm_again"
            return agent

        monkeypatch.setattr(executor, "get_agent_by_id", build_formatter)
        return model

    return install


@pytest.fixture
def run_output_step(install_formatter):
    """Run one CSV output step; returns (step result, scripted model, specialist events)."""

    executor = _executor_module()

    async def run(steps, *, previous_behavior: bool = False):
        model = install_formatter(
            steps, agent_id=_FORMATTER_ID, previous_behavior=previous_behavior
        )
        tool = executor._make_flow_runtime_formatter_tool(
            agent_id=_FORMATTER_ID,
            agent_name="CSV File Formatter",
            output_format="csv",
            tool_name=_TOOL_NAME,
            tool_description="Save a CSV file",
            specialist_name="CSV File Formatter",
            base_context=dict(_BASE_CONTEXT),
            step_instruction_prefix="",
            completed_steps=[build_semantic_output_step(min_diagnostic_chars=10_000)],
            flow_name="Synthetic expression flow",
            flow_run_id="semantic-flow-run",
            document_id="semantic-document",
            node_data={"step_goal": "Save the expression table"},
            source_node_ids=["extractor"],
        )
        events: list[dict[str, Any]] = []
        set_live_event_list(events)
        try:
            ctx = SimpleNamespace(tool_name=_TOOL_NAME, run_config=RunConfig(tracing_disabled=True))
            result = await tool.on_invoke_tool(
                ctx,
                json.dumps({"query": "Export the saved results", "output_filename_descriptor": "expression"}),
            )
        finally:
            set_live_event_list(None)
        return result, model, events

    return run


@pytest.fixture
def run_chat_output_step(install_formatter, monkeypatch):
    """Run one chat output step.

    Returns (step result, scripted model, delivery, runtime reports, specialist events).
    """

    executor = _executor_module()

    async def run(steps, *, previous_behavior: bool = False):
        model = install_formatter(
            steps, agent_id=_CHAT_FORMATTER_ID, previous_behavior=previous_behavior
        )
        reports: list[BaseException] = []
        monkeypatch.setattr(
            executor, "report_runtime_exception", lambda exc, **_kwargs: reports.append(exc)
        )
        tool = executor._make_flow_chat_output_tool(
            agent_id=_CHAT_FORMATTER_ID,
            agent_name="Chat Output Agent",
            output_format="chat",
            tool_name=_CHAT_TOOL_NAME,
            tool_description="Display results in chat",
            specialist_name="Chat Output",
            base_context=dict(_BASE_CONTEXT),
            step_instruction_prefix="",
            completed_steps=[build_semantic_output_step(min_diagnostic_chars=10_000)],
            flow_name="Synthetic expression flow",
            flow_run_id="semantic-flow-run",
            document_id="semantic-document",
            node_data={"step_goal": "Show the expression table"},
            source_node_ids=["extractor"],
        )
        events: list[dict[str, Any]] = []
        set_live_event_list(events)
        try:
            with chat_output_delivery_scope() as delivery:
                ctx = SimpleNamespace(
                    tool_name=_CHAT_TOOL_NAME, run_config=RunConfig(tracing_disabled=True)
                )
                result = await tool.on_invoke_tool(ctx, json.dumps({"query": "Show the table"}))
        finally:
            set_live_event_list(None)
        return result, model, delivery, reports, events

    return run


def _events_of(events: list[dict[str, Any]], event_type: str) -> list[dict[str, Any]]:
    return [event for event in events if event.get("type") == event_type]


def _internal_tool_output(events: list[dict[str, Any]], tool_name: str) -> Any:
    """What the executor reads back from a step's TOOL_COMPLETE events."""

    executor = _executor_module()
    return executor._internal_specialist_tool_output_since(
        {"live_events": events, "live_index": 0}, tool_name=tool_name
    )


def _without_timestamps(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{key: value for key, value in event.items() if key != "timestamp"} for event in events]


@pytest.mark.asyncio
async def test_output_step_returns_the_same_receipt_without_a_model_call_after_saving(run_output_step):
    steps = [("finalize_and_save", {"plan_json": "", "filename_hint": "expression"})]

    result, model, events = await run_output_step(steps)
    baseline_result, baseline_model, baseline_events = await run_output_step(
        steps, previous_behavior=True
    )

    # The run ended at the saved file: no model call after finalize_and_save.
    assert len(model.requests) == 1
    assert len(baseline_model.requests) == 2
    assert "finalize_and_save" in model.requests[0]

    # The flow step receives the same runtime receipt as before.
    assert result == baseline_result
    receipt = json.loads(result)
    assert receipt["status"] == "ok"
    assert receipt["saved_file"] is True
    assert receipt["file"]["file_id"] == "file-flow-csv-1"
    assert receipt["file"]["download_url"] == "/api/files/file-flow-csv-1/download"
    assert _CLOSING_TEXT not in result

    # The download card event and the finalize output the executor reads are unchanged.
    file_ready = _events_of(events, "FILE_READY")
    assert len(file_ready) == 1
    assert _without_timestamps(file_ready) == _without_timestamps(
        _events_of(baseline_events, "FILE_READY")
    )
    assert _internal_tool_output(events, "finalize_and_save") == _internal_tool_output(
        baseline_events, "finalize_and_save"
    )
    executor = _executor_module()
    assert not executor._formatter_failure_reported(result)
    assert executor._flow_formatter_failure_reason({"output": result}) is None

    # The closing turn's "Agent reasoning" row is the only audit event dropped.
    assert not _events_of(events, "AGENT_GENERATING")
    assert _events_of(baseline_events, "AGENT_GENERATING")
    summary = _events_of(events, "SPECIALIST_SUMMARY")[-1]["details"]
    assert [call["name"] for call in summary["toolCalls"]] == ["finalize_and_save"]


@pytest.mark.asyncio
async def test_cannot_complete_output_step_still_runs_the_closing_turn(run_output_step):
    reason = "The saved results have no PMID field for the requested column."
    steps = [
        (
            "formatter_cannot_complete",
            {"reason": reason, "suggested_next_step": "Add a PMID column to the extractor."},
        )
    ]

    result, model, events = await run_output_step(steps)
    baseline_result, baseline_model, baseline_events = await run_output_step(
        steps, previous_behavior=True
    )

    # formatter_cannot_complete does not end the run: the model still writes its
    # closing text, and the supervisor receives it exactly as before.
    assert len(model.requests) == len(baseline_model.requests) == 2
    assert result == baseline_result == _CLOSING_TEXT

    # The curator-facing failure reason comes from the same private payload.
    executor = _executor_module()
    failure_output = _internal_tool_output(events, "formatter_cannot_complete")
    assert failure_output == _internal_tool_output(baseline_events, "formatter_cannot_complete")
    failure_reason = executor._flow_formatter_failure_reason(
        {"formatter_failure_output": failure_output, "output": result}
    )
    assert reason in failure_reason
    assert _events_of(events, "AGENT_GENERATING")
    assert not _events_of(events, "FILE_READY")
    assert not executor._formatter_failure_reported(result)


@pytest.mark.asyncio
async def test_chat_output_step_returns_the_same_receipt_without_a_model_call_after_delivery(
    run_chat_output_step,
):
    steps = [("finalize_chat_output", {"plan_json": json.dumps(SEMANTIC_CHAT_PLAN)})]

    result, model, delivery, reports, events = await run_chat_output_step(steps)
    (
        baseline_result,
        baseline_model,
        baseline_delivery,
        baseline_reports,
        baseline_events,
    ) = await run_chat_output_step(steps, previous_behavior=True)

    # The run ended at the delivered chat output: no model call after finalize.
    assert len(model.requests) == 1
    assert len(baseline_model.requests) == 2
    assert "finalize_chat_output" in model.requests[0]

    # The flow step returns the same receipt; only the per-delivery id differs.
    receipt = json.loads(result)
    baseline_receipt = json.loads(baseline_result)
    assert receipt["delivered"] is True
    assert receipt.pop("chat_output_id") != baseline_receipt.pop("chat_output_id")
    assert receipt == baseline_receipt
    assert "post_delivery_error" not in receipt

    # The curator sees the same rendered output, delivered once without errors.
    assert delivery.delivered
    assert delivery.output == baseline_delivery.output
    assert reports == baseline_reports == []
    assert not _events_of(events, "SPECIALIST_ERROR")
    assert not _events_of(events, "AGENT_GENERATING")
    assert _events_of(baseline_events, "AGENT_GENERATING")


@pytest.mark.asyncio
async def test_chat_output_cannot_complete_step_is_unchanged(run_chat_output_step):
    reason = "The saved results have no PMID field for the requested column."
    steps = [("formatter_cannot_complete", {"reason": reason})]

    result, model, delivery, reports, _events = await run_chat_output_step(steps)
    baseline_result, baseline_model, *_rest = await run_chat_output_step(
        steps, previous_behavior=True
    )

    # The closing turn still runs, and the chat step returns the recorded
    # cannot-complete payload, not that closing text, exactly as before.
    assert len(model.requests) == len(baseline_model.requests) == 2
    assert result == baseline_result
    payload = json.loads(result)
    assert payload["status"] == "cannot_complete"
    assert payload["reason"] == reason
    assert delivery.output is None
    assert reports == []

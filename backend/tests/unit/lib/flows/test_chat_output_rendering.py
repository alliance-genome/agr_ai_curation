"""ALL-1275: flow chat output renders through tools over application-owned data.

Reproduces the Sep 22 production failure (1,422,809 instruction characters for a
seven-statement expression flow) with the synthetic semantic fixture, drives the
real chat formatter tools with a scripted provider model, and checks the actual
instructions, model inputs, tool returns, delivered content and receipts.
"""

from __future__ import annotations

import importlib
import json
from types import SimpleNamespace
from typing import Any

import pytest
from agents import Agent, RunConfig, Runner
from agents.items import ModelResponse
from agents.models.interface import Model
from agents.usage import Usage
from openai.types.responses import (
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
)

from src.lib.agent_studio.catalog_service import ToolExecutionContext, resolve_tools
from src.lib.config.agent_loader import get_agent_definition
from src.lib.flows.chat_output_delivery import chat_output_delivery_scope
from src.lib.openai_agents.config import get_output_tool_max_response_chars
from tests.fixtures.flows.semantic_chat_output import (
    CURATOR_REQUEST,
    EM_DASH,
    EXCLUDED_REAGENT_VALUES,
    EXPECTED_HEADERS,
    EXPECTED_ROWS,
    ORIGINAL_INSTRUCTIONS_CHARS,
    PROVIDER_INSTRUCTIONS_LIMIT,
    SEMANTIC_CHAT_PLAN,
    UNRESOLVED_CAVEAT,
    build_semantic_output_bundle,
    build_semantic_output_step,
)

# Compact instructions: curator request, inventory counts and tool contract.
_MAX_FORMATTER_INSTRUCTION_CHARS = 20_000
_MAX_RECEIPT_CHARS = 2_000


def _executor_module():
    return importlib.import_module("src.lib.flows.executor")


def _expected_table_lines() -> list[str]:
    header = "| " + " | ".join(EXPECTED_HEADERS) + " |"
    divider = "| " + " | ".join("---" for _ in EXPECTED_HEADERS) + " |"
    rows = [
        "| " + " | ".join(row[header_name] for header_name in EXPECTED_HEADERS) + " |"
        for row in EXPECTED_ROWS
    ]
    return [header, divider, *rows]


def _assistant_message(text: str) -> ResponseOutputMessage:
    return ResponseOutputMessage(
        id="msg-final",
        type="message",
        role="assistant",
        status="completed",
        content=[ResponseOutputText(type="output_text", text=text, annotations=[])],
    )


class _ScriptedFormatterModel(Model):
    """Provider stand-in that records every request and replays tool calls."""

    def __init__(self, steps: list[tuple[str, dict[str, Any]]]):
        self.steps = steps
        self.requests: list[dict[str, Any]] = []

    async def get_response(self, system_instructions, input, model_settings, tools, *args, **kwargs):
        self.requests.append(
            {
                "instructions": system_instructions or "",
                "input": input,
                "tool_names": sorted(getattr(tool, "name", "") for tool in tools),
            }
        )
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
            output = [_assistant_message("Chat output delivered.")]
        return ModelResponse(output=output, usage=Usage(requests=1), response_id=f"resp-{index}")

    async def stream_response(self, *args, **kwargs):
        raise AssertionError("The chat formatter test drives the non-streamed runner")
        yield  # pragma: no cover


def _tool_outputs(model_input: Any) -> dict[str, str]:
    if not isinstance(model_input, list):
        return {}
    return {
        str(item.get("call_id")): str(item.get("output"))
        for item in model_input
        if isinstance(item, dict) and item.get("type") == "function_call_output"
    }


def _install_scripted_formatter(monkeypatch, executor, model: _ScriptedFormatterModel) -> dict[str, Any]:
    """Build the chat agent from its packaged tool list with the scripted model."""

    captured: dict[str, Any] = {}

    def fake_get_agent(agent_id, **kwargs):
        definition = get_agent_definition(agent_id)
        assert definition is not None
        tools = resolve_tools(
            list(definition.tools),
            ToolExecutionContext(
                formatter_bundle=kwargs["formatter_bundle"],
                formatter_output_format=kwargs["formatter_output_format"],
                formatter_agent_id=kwargs["formatter_agent_id"],
                formatter_projection_plan=kwargs.get("formatter_projection_plan"),
            ),
        )
        captured.update(agent_id=agent_id, kwargs=kwargs, tool_ids=list(definition.tools))
        return Agent(
            name="Chat Output",
            instructions="\n\n".join(kwargs.get("additional_runtime_context") or []),
            model=model,
            tools=tools,
        )

    def fake_streaming_tool(*, agent, **kwargs):
        captured["streaming"] = kwargs

        async def on_invoke_tool(_ctx, arguments):
            result = await Runner.run(
                agent,
                json.loads(arguments)["query"],
                run_config=RunConfig(tracing_disabled=True),
            )
            captured["specialist_final_output"] = result.final_output
            return result.final_output

        return SimpleNamespace(on_invoke_tool=on_invoke_tool)

    monkeypatch.setattr(executor, "get_agent_by_id", fake_get_agent)
    monkeypatch.setattr(executor, "_create_streaming_tool", fake_streaming_tool)
    return captured


def _chat_tool(executor, *, completed_steps, node_data=None):
    return executor._make_flow_chat_output_tool(
        agent_id="chat_output_formatter",
        agent_name="Chat Output Agent",
        output_format="chat",
        tool_name="ask_chat_output_formatter_specialist",
        tool_description="Display results in chat",
        specialist_name="Chat Output",
        base_context={"db_user_id": 7, "authenticated_groups": []},
        step_instruction_prefix=CURATOR_REQUEST,
        completed_steps=completed_steps,
        flow_name="Synthetic expression flow",
        flow_run_id="semantic-flow-run",
        document_id="semantic-document",
        node_data=node_data or {"custom_instructions": CURATOR_REQUEST, "step_goal": "Show the table"},
        source_node_ids=["extractor"],
    )


async def _invoke(tool, query: str) -> str:
    ctx = SimpleNamespace(tool_name=tool.name, run_config=None)
    return await tool.on_invoke_tool(ctx, json.dumps({"query": query}))


def test_semantic_fixture_reproduces_original_payload_size():
    bundle = build_semantic_output_bundle()
    object_rows = bundle.rows_for_source("object")
    validation_rows = bundle.rows_for_source("validation_finding")

    assert len(object_rows) == 7
    serialized = json.dumps(
        {source: bundle.rows_for_source(source) for source in ("artifact", "object", "evidence", "validation_finding")},
        ensure_ascii=False,
        default=str,
    )
    assert len(serialized) > ORIGINAL_INSTRUCTIONS_CHARS
    assert {row["validation.status"] for row in validation_rows} == {"resolved", "open"}
    # Resolved and unresolved values share one statement.
    first = object_rows[0]
    assert [term["resolution_state"] for term in first["object.attribute.anatomy_term_terms"]] == [
        "resolved", "unresolved"]
    assert first["object.attribute.anatomy_term_terms"][1]["curie"] is None


@pytest.mark.asyncio
async def test_chat_output_uses_tools_over_bundle_not_instruction_payload(monkeypatch):
    """Regression: the chat formatter must not receive the bundle in instructions."""

    executor = _executor_module()
    budget = get_output_tool_max_response_chars()
    model = _ScriptedFormatterModel(
        [
            ("inspect_output_artifacts", {"catalog_query": "anatomy"}),
            ("inspect_output_artifacts", {"row_source": "validation_finding"}),
            ("inspect_output_rows", {"row_source": "validation_finding", "limit": 500}),
            (
                "read_output_value",
                {"row_ref": "validation_finding#1", "field_ref": "validation.candidate_matches"},
            ),
            ("preview_output_projection", {"plan_json": json.dumps(SEMANTIC_CHAT_PLAN)}),
            (
                "finalize_chat_output",
                {"plan_json": json.dumps(SEMANTIC_CHAT_PLAN), "notes": UNRESOLVED_CAVEAT},
            ),
        ]
    )
    captured = _install_scripted_formatter(monkeypatch, executor, model)
    tool = _chat_tool(executor, completed_steps=[build_semantic_output_step()])

    with chat_output_delivery_scope() as delivery:
        receipt_text = await _invoke(tool, "Render the requested expression table.")

    # Actual provider instructions stay compact and carry no diagnostics.
    assert len(model.requests) == 7
    instructions = model.requests[0]["instructions"]
    assert len(instructions) < _MAX_FORMATTER_INSTRUCTION_CHARS
    assert len(instructions) < PROVIDER_INSTRUCTIONS_LIMIT
    assert CURATOR_REQUEST in instructions
    assert "candidate_matches" not in instructions
    assert "lexical match" not in instructions
    assert "body wall musculature" not in instructions
    assert all(request["instructions"] == instructions for request in model.requests)
    assert "finalize_chat_output" in model.requests[0]["tool_names"]
    assert "read_output_value" in model.requests[0]["tool_names"]
    assert "finalize_and_save" not in model.requests[0]["tool_names"]

    # Every tool return the model sees obeys the configured total budget.
    final_input = model.requests[-1]["input"]
    outputs = _tool_outputs(final_input)
    assert len(outputs) == 6
    for output in outputs.values():
        assert len(output) <= budget
    catalog_page = json.loads(outputs["call-0"])["inventory"]["field_catalog"]
    # One resolvable-term field (ALL-1283) instead of parallel label/id/status lists.
    assert catalog_page["matching_fields"] >= 1
    assert all("anatomy" in entry["ref"] for entry in catalog_page["entries"])
    validation_catalog = json.loads(outputs["call-1"])["inventory"]["field_catalog"]
    assert validation_catalog["row_source"] == "validation_finding"
    row_page = json.loads(outputs["call-2"])
    assert row_page["status"] == "ok"
    assert row_page["next_cursor"]
    assert len(row_page["rows"]) < row_page["total_count"]
    assert len(row_page["row_refs"]) == len(row_page["rows"])
    value_slice = json.loads(outputs["call-3"])
    assert value_slice["status"] == "ok"
    assert value_slice["next_offset"] is not None
    assert value_slice["total_chars"] > len(value_slice["value_slice"])
    preview = json.loads(outputs["call-4"])["preview"]
    assert preview["total_count"] == 7
    finalize_receipt = json.loads(outputs["call-5"])
    assert finalize_receipt["delivered"] is True
    assert finalize_receipt["projection_summary"]["row_count"] == 7
    assert "body wall musculature" not in outputs["call-5"]
    total_input_chars = sum(len(json.dumps(item, default=str)) for item in final_input)
    assert total_input_chars < 8 * budget

    # The caller receives a compact receipt; the application holds the table.
    assert len(receipt_text) < _MAX_RECEIPT_CHARS
    receipt = json.loads(receipt_text)
    assert receipt["delivered"] is True
    assert receipt["projection_summary"]["columns"] == EXPECTED_HEADERS
    assert "body wall musculature" not in receipt_text
    assert captured["specialist_final_output"] == "Chat output delivered."

    assert delivery.delivered
    content = delivery.output or ""
    lines = content.split("\n")
    assert lines[: len(EXPECTED_ROWS) + 2] == _expected_table_lines()
    assert content.endswith(UNRESOLVED_CAVEAT)
    # An unresolved term reads UNRESOLVED; its paper wording never fills the cell (ALL-1283).
    assert "body wall musculature (WBbt:9000001); UNRESOLVED" in content
    assert "vulval muscle" not in content
    assert "body wall musculature (WBbt:9000001)" in content
    assert EM_DASH in content
    for reagent in EXCLUDED_REAGENT_VALUES:
        assert reagent not in content
    assert "Showing" not in content
    assert receipt["output_chars"] == len(content)


@pytest.mark.asyncio
async def test_chat_output_without_finalization_fails_explicitly_and_reports_once(monkeypatch):
    executor = _executor_module()
    reports: list[tuple[Any, dict[str, Any]]] = []
    monkeypatch.setattr(
        executor,
        "report_payload_contract_violation",
        lambda violation, **kwargs: reports.append((violation, kwargs)),
    )
    model = _ScriptedFormatterModel([("inspect_output_artifacts", {})])
    _install_scripted_formatter(monkeypatch, executor, model)
    tool = _chat_tool(executor, completed_steps=[build_semantic_output_step(min_diagnostic_chars=10_000)])

    with chat_output_delivery_scope() as delivery:
        result = json.loads(await _invoke(tool, "Render the table."))

    assert delivery.output is None
    assert result["status"] == "cannot_complete"
    assert result["delivered"] is False
    assert executor._flow_formatter_failure_reason({"output": json.dumps(result)})
    assert len(reports) == 1
    violation, kwargs = reports[0]
    assert violation.category == "output_delivery_failure"
    assert kwargs["correlation"]["flow_run_id"] == "semantic-flow-run"


@pytest.mark.asyncio
async def test_chat_output_cannot_complete_is_returned_without_delivery(monkeypatch):
    executor = _executor_module()
    reports: list[Any] = []
    monkeypatch.setattr(
        executor, "report_payload_contract_violation", lambda *args, **kwargs: reports.append(args)
    )
    model = _ScriptedFormatterModel(
        [("formatter_cannot_complete", {"reason": "No PMID field was saved."})]
    )
    _install_scripted_formatter(monkeypatch, executor, model)
    tool = _chat_tool(executor, completed_steps=[build_semantic_output_step(min_diagnostic_chars=10_000)])

    with chat_output_delivery_scope() as delivery:
        result = json.loads(await _invoke(tool, "Render the table."))

    assert delivery.output is None
    assert result["status"] == "cannot_complete"
    assert result["reason"] == "No PMID field was saved."
    assert reports == []
    assert "No PMID field was saved." in executor._flow_formatter_failure_reason(
        {"output": json.dumps(result)}
    )


@pytest.mark.asyncio
async def test_chat_output_tool_requires_executor_delivery_scope(monkeypatch):
    executor = _executor_module()
    _install_scripted_formatter(monkeypatch, executor, _ScriptedFormatterModel([]))
    tool = _chat_tool(executor, completed_steps=[build_semantic_output_step(min_diagnostic_chars=10_000)])

    with pytest.raises(RuntimeError, match="delivery scope"):
        await _invoke(tool, "Render the table.")


@pytest.mark.asyncio
async def test_operational_ceiling_in_flow_chat_step_fails_once_with_saved_data_intact(monkeypatch):
    from src.lib.flows import output_projection
    from src.lib.openai_agents.tools import output_formatter_tools

    executor = _executor_module()
    reports: list[tuple[Any, dict[str, Any]]] = []

    def record(violation, **kwargs):
        reports.append((violation, kwargs))

    monkeypatch.setattr(executor, "report_payload_contract_violation", record)
    monkeypatch.setattr(output_formatter_tools, "report_payload_contract_violation", record)
    monkeypatch.setattr(output_projection, "MAX_PROJECTION_ROWS", 5)
    model = _ScriptedFormatterModel(
        [("finalize_chat_output", {"plan_json": json.dumps(SEMANTIC_CHAT_PLAN)})]
    )
    _install_scripted_formatter(monkeypatch, executor, model)
    step = build_semantic_output_step(min_diagnostic_chars=10_000)
    tool = _chat_tool(executor, completed_steps=[step])

    with chat_output_delivery_scope() as delivery:
        result = json.loads(await _invoke(tool, "Render the table."))

    assert delivery.output is None
    assert result["status"] == "cannot_complete"
    assert result["code"] == "operational_ceiling_exceeded"
    assert "7 rows" in result["reason"]
    assert "operational ceiling" in executor._flow_formatter_failure_reason({"output": json.dumps(result)})
    assert len(reports) == 1
    assert reports[0][0].category == "output_delivery_failure"
    assert len(step["candidate"].payload_json["extracted_objects"]) == 7


@pytest.mark.asyncio
async def test_cannot_complete_after_reported_ceiling_keeps_one_capture(monkeypatch):
    """Following the prompt after a ceiling failure must not add a second capture."""
    from src.lib.flows import output_projection
    from src.lib.flows.outcome import FORMATTER_OUTPUT_FAILURE_REPORTED, FlowRunOutcome
    from src.lib.openai_agents.tools import output_formatter_tools

    executor = _executor_module()
    reports: list[tuple[Any, dict[str, Any]]] = []

    def record(violation, **kwargs):
        reports.append((violation, kwargs))

    monkeypatch.setattr(executor, "report_payload_contract_violation", record)
    monkeypatch.setattr(output_formatter_tools, "report_payload_contract_violation", record)
    monkeypatch.setattr(output_projection, "MAX_PROJECTION_ROWS", 5)
    model = _ScriptedFormatterModel(
        [
            ("finalize_chat_output", {"plan_json": json.dumps(SEMANTIC_CHAT_PLAN)}),
            (
                "formatter_cannot_complete",
                {"reason": "The requested table exceeds the operational row limit."},
            ),
        ]
    )
    _install_scripted_formatter(monkeypatch, executor, model)
    tool = _chat_tool(executor, completed_steps=[build_semantic_output_step(min_diagnostic_chars=10_000)])

    with chat_output_delivery_scope() as delivery:
        raw = await _invoke(tool, "Render the table.")
    result = json.loads(raw)

    assert delivery.output is None
    assert result["status"] == "cannot_complete"
    assert result["failure_reported"] is True
    assert result["code"] == "operational_ceiling_exceeded"
    assert "7 rows" in result["reason"]
    assert "operational row limit" in result["reason"]
    assert executor._formatter_failure_reported(raw)
    assert "operational ceiling" in executor._flow_formatter_failure_reason({"output": raw})
    assert len(reports) == 1

    outcome = FlowRunOutcome()
    outcome.observe({
        "type": "FLOW_ERROR",
        "details": {"reason": "missing_formatter_outputs", "error_type": FORMATTER_OUTPUT_FAILURE_REPORTED},
    })
    outcome.observe({"type": "FLOW_FINISHED", "status": "failed", "failure_reason": "Formatter could not create an output"})
    assert outcome.status == "failed"
    assert outcome.failure_already_reported is True


class _FailAfterFinalizeModel(_ScriptedFormatterModel):
    async def get_response(self, *args, **kwargs):
        if len(self.requests) >= len(self.steps):
            self.requests.append({"instructions": "", "input": [], "tool_names": []})
            raise RuntimeError("provider stream dropped after finalization")
        return await super().get_response(*args, **kwargs)


@pytest.mark.asyncio
async def test_specialist_error_after_delivery_still_delivers_table_once(monkeypatch):
    executor = _executor_module()
    runtime_reports: list[tuple[BaseException, dict[str, Any]]] = []
    monkeypatch.setattr(
        executor,
        "report_runtime_exception",
        lambda exc, **kwargs: runtime_reports.append((exc, kwargs)),
    )
    model = _FailAfterFinalizeModel(
        [("finalize_chat_output", {"plan_json": json.dumps(SEMANTIC_CHAT_PLAN)})]
    )
    _install_scripted_formatter(monkeypatch, executor, model)
    tool = _chat_tool(executor, completed_steps=[build_semantic_output_step(min_diagnostic_chars=10_000)])

    with chat_output_delivery_scope() as delivery:
        receipt = json.loads(await _invoke(tool, "Render the table."))

    assert delivery.delivered
    assert (delivery.output or "").split("\n")[: len(EXPECTED_ROWS) + 2] == _expected_table_lines()
    assert receipt["delivered"] is True
    assert receipt["post_delivery_error"] == "RuntimeError"
    assert len(runtime_reports) == 1
    assert runtime_reports[0][1]["operation"] == "specialist_error_after_delivery"


@pytest.mark.asyncio
async def test_specialist_error_before_delivery_still_propagates(monkeypatch):
    executor = _executor_module()
    model = _FailAfterFinalizeModel([])
    _install_scripted_formatter(monkeypatch, executor, model)
    tool = _chat_tool(executor, completed_steps=[build_semantic_output_step(min_diagnostic_chars=10_000)])

    with chat_output_delivery_scope() as delivery:
        with pytest.raises(Exception, match="provider stream dropped"):
            await _invoke(tool, "Render the table.")
    assert delivery.output is None

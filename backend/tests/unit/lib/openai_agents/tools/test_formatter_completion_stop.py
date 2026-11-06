"""ALL-1496: formatter runs end when the file is saved or chat output delivered.

The callers of a formatter run read the runtime's file handoff or chat
delivery, never the model's closing text, so the run must not spend another
model turn after a successful finalize. A failed finalize keeps the run going
so the model can repair, retry or explain why it cannot complete, and
formatter_cannot_complete still leads to the closing turn.
"""

from __future__ import annotations

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

from src.lib.openai_agents.tools import output_formatter_tools
from src.lib.openai_agents.tools.output_formatter_tools import (
    build_output_formatter_tools,
    formatter_completion_tool_use_behavior,
)
from tests.fixtures.flows.semantic_chat_output import build_semantic_output_step
from tests.reviewed_output_unit_support import (
    build_reviewed_output_bundle,
    reviewed_output_reader as reviewed_output_reader,
)

_SAVED_RECEIPT = json.dumps(
    {
        "file_id": "file-1",
        "filename": "export.csv",
        "format": "csv",
        "download_url": "/api/files/file-1/download",
        "status": "ok",
    }
)
_DELIVERED_RECEIPT = json.dumps(
    {"chat_output_id": "chat-1", "status": "ok", "delivered": True, "format": "chat"}
)
_CANNOT_COMPLETE = json.dumps(
    {
        "status": "cannot_complete",
        "format": "csv",
        "formatter_agent_id": "csv_formatter",
        "reason": "No PMID field was saved.",
        "missing_data": "",
        "suggested_next_step": "",
        "saved_file": False,
    }
)


def _result(tool_name: str, output: Any) -> SimpleNamespace:
    """A stand-in for the SDK's FunctionToolResult (tool, output)."""

    return SimpleNamespace(tool=SimpleNamespace(name=tool_name), output=output)


@pytest.mark.parametrize(
    ("tool_name", "output"),
    [
        ("finalize_and_save", _SAVED_RECEIPT),
        ("finalize_chat_output", _DELIVERED_RECEIPT),
    ],
)
def test_successful_finalize_ends_the_run_with_its_receipt(tool_name, output):
    decision = formatter_completion_tool_use_behavior(None, [_result(tool_name, output)])

    assert decision.is_final_output is True
    assert decision.final_output == output


@pytest.mark.parametrize(
    ("tool_name", "output"),
    [
        # A rejected plan is repaired and finalized again.
        (
            "finalize_and_save",
            json.dumps({"status": "invalid", "errors": ["unknown field_ref object.pmid"]}),
        ),
        # Failed finalizes are not completions: the model may retry the save
        # or call formatter_cannot_complete with the reason curators see.
        (
            "finalize_and_save",
            json.dumps({"status": "failed", "code": "save_failed", "saved_file": False}),
        ),
        (
            "finalize_and_save",
            json.dumps(
                {"status": "failed", "code": "operational_ceiling_exceeded", "saved_file": False}
            ),
        ),
        (
            "finalize_chat_output",
            json.dumps(
                {"status": "failed", "code": "operational_ceiling_exceeded", "delivered": False}
            ),
        ),
        ("finalize_chat_output", json.dumps({"status": "invalid", "errors": ["notes too long"]})),
        # The SDK's text for a finalize call that raised is not a receipt.
        ("finalize_and_save", "An error occurred while running the tool."),
        # formatter_cannot_complete still leads to the closing turn.
        ("formatter_cannot_complete", _CANNOT_COMPLETE),
        # Other formatter tools also report status ok; they never complete the run.
        ("validate_output_projection", json.dumps({"status": "ok", "errors": []})),
        ("preview_output_projection", json.dumps({"status": "ok"})),
        ("inspect_output_artifacts", json.dumps({"status": "ok"})),
    ],
)
def test_other_results_keep_the_run_going(tool_name, output):
    decision = formatter_completion_tool_use_behavior(None, [_result(tool_name, output)])

    assert decision.is_final_output is False
    assert decision.final_output is None


def test_no_tool_results_keep_the_run_going():
    assert formatter_completion_tool_use_behavior(None, []).is_final_output is False


def test_completion_is_found_among_several_results():
    preview = _result("preview_output_projection", json.dumps({"status": "ok"}))
    finalize = _result("finalize_and_save", _SAVED_RECEIPT)

    decision = formatter_completion_tool_use_behavior(None, [preview, finalize])

    assert decision.is_final_output is True
    assert decision.final_output == _SAVED_RECEIPT


# --- Real SDK runs over the real formatter tools -----------------------------


def _assistant_message(text: str) -> ResponseOutputMessage:
    return ResponseOutputMessage(
        id="msg-final",
        type="message",
        role="assistant",
        status="completed",
        content=[ResponseOutputText(type="output_text", text=text, annotations=[])],
    )


class _ScriptedFormatterModel(Model):
    """Replays tool calls, then the closing text a formatter writes today."""

    def __init__(self, steps: list[tuple[str, dict[str, Any]]]):
        self.steps = steps
        self.calls = 0

    async def get_response(self, *args, **kwargs):
        index = self.calls
        self.calls += 1
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
            output = [_assistant_message("CSV file ready.")]
        return ModelResponse(output=output, usage=Usage(requests=1), response_id=f"resp-{index}")

    async def stream_response(self, *args, **kwargs):
        raise AssertionError("These formatter runs use the non-streamed runner")
        yield  # pragma: no cover


class _Saver:
    """File save callback that fails a configured number of times first."""

    def __init__(self, failures: int = 0):
        self.failures = failures
        self.saved: list[str] = []

    async def __call__(self, output_format, projection, filename_hint, formatter_agent_id):
        if self.failures:
            self.failures -= 1
            raise OSError("storage unavailable")
        self.saved.append(filename_hint)
        return {
            "file_id": "file-1",
            "filename": f"{filename_hint}.{output_format}",
            "format": output_format,
            "size_bytes": 64,
            "download_url": "/api/files/file-1/download",
        }


def _formatter_agent(model: Model, saver: _Saver) -> Agent:
    bundle = build_reviewed_output_bundle(
        completed_steps=[build_semantic_output_step(min_diagnostic_chars=10_000)],
        flow_name="Synthetic expression flow",
        flow_run_id="semantic-flow-run",
        document_id="semantic-document",
        output_format="csv",
    )
    return Agent(
        name="CSV File Formatter",
        instructions="Save the CSV file.",
        model=model,
        tools=build_output_formatter_tools(
            bundle=bundle,
            output_format="csv",
            formatter_agent_id="csv_formatter",
            save_projected_output=saver,
        ),
        tool_use_behavior=formatter_completion_tool_use_behavior,
    )


async def _run(agent: Agent):
    return await Runner.run(agent, "Export the saved results", run_config=RunConfig(tracing_disabled=True))


@pytest.fixture
def reported_failures(monkeypatch):
    reports: list[Any] = []
    monkeypatch.setattr(
        output_formatter_tools,
        "report_payload_contract_violation",
        lambda violation, **kwargs: reports.append(violation),
    )
    return reports


@pytest.mark.asyncio
@pytest.mark.usefixtures("reviewed_output_reader")
async def test_saved_file_ends_the_run_without_another_model_call():
    saver = _Saver()
    model = _ScriptedFormatterModel(
        [
            ("build_default_projection_plan", {}),
            ("finalize_and_save", {"plan_json": "", "filename_hint": "export"}),
        ]
    )

    result = await _run(_formatter_agent(model, saver))

    assert model.calls == 2
    receipt = json.loads(result.final_output)
    assert receipt["status"] == "ok"
    assert receipt["file_id"] == "file-1"
    assert receipt["projection_summary"]["total_count"] == 7
    assert saver.saved == ["export"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("reviewed_output_reader")
async def test_failed_save_lets_the_model_retry_then_stops_at_the_saved_file(reported_failures):
    saver = _Saver(failures=1)
    model = _ScriptedFormatterModel(
        [
            ("finalize_and_save", {"plan_json": "", "filename_hint": "export"}),
            ("finalize_and_save", {"plan_json": "", "filename_hint": "export"}),
        ]
    )

    result = await _run(_formatter_agent(model, saver))

    assert model.calls == 2
    assert json.loads(result.final_output)["file_id"] == "file-1"
    assert saver.saved == ["export"]
    assert len(reported_failures) == 1


@pytest.mark.asyncio
@pytest.mark.usefixtures("reviewed_output_reader")
async def test_cannot_complete_still_continues_to_the_closing_turn():
    saver = _Saver()
    model = _ScriptedFormatterModel(
        [("formatter_cannot_complete", {"reason": "No PMID field was saved."})]
    )

    result = await _run(_formatter_agent(model, saver))

    # The model is asked once more and its closing text is the run's output.
    assert model.calls == 2
    assert result.final_output == "CSV file ready."
    assert saver.saved == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("reviewed_output_reader")
async def test_failed_save_lets_the_model_explain_why_it_cannot_complete(reported_failures):
    saver = _Saver(failures=1)
    model = _ScriptedFormatterModel(
        [
            ("finalize_and_save", {"plan_json": "", "filename_hint": "export"}),
            ("formatter_cannot_complete", {"reason": "File storage is unavailable."}),
        ]
    )

    result = await _run(_formatter_agent(model, saver))

    # Failed save, cannot-complete, then the closing turn as before.
    assert model.calls == 3
    assert result.final_output == "CSV file ready."
    assert saver.saved == []
    assert len(reported_failures) == 1


@pytest.mark.asyncio
@pytest.mark.usefixtures("reviewed_output_reader")
async def test_rejected_plan_is_repaired_before_the_run_stops():
    saver = _Saver()
    model = _ScriptedFormatterModel(
        [
            ("finalize_and_save", {"plan_json": json.dumps({"format": "csv", "columns": []})}),
            ("finalize_and_save", {"plan_json": "", "filename_hint": "export"}),
        ]
    )

    result = await _run(_formatter_agent(model, saver))

    assert model.calls == 2
    assert json.loads(result.final_output)["status"] == "ok"
    assert saver.saved == ["export"]

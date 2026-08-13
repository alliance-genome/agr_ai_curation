import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from agents import function_tool


def _executor_module():
    return importlib.import_module("src.lib.flows.executor")


def _completed_artifact_step():
    executor = _executor_module()
    payload = {
        "domain_pack_id": "gene",
        "envelope_id": "env-gene-1",
        "extracted_objects": [
            {"object_type": "Gene", "payload": {"symbol": "TP53"}},
            {"object_type": "Gene", "payload": {"symbol": "BRCA1"}},
        ],
    }
    return {
        "step": 1,
        "node_id": "source_1",
        "agent_id": "gene",
        "agent_name": "Gene Specialist",
        "output_preview": "Saved gene candidates for TP53 and BRCA1.",
        "candidate": executor.ExtractionEnvelopeCandidate(
            agent_key="gene",
            payload_json=payload,
            candidate_count=2,
            adapter_key="gene",
            conversation_summary="Extracted two gene candidates.",
        ),
    }


async def _invoke_tool(tool, payload: dict) -> str:
    tool_ctx = SimpleNamespace(tool_name=getattr(tool, "name", "tool"), run_config=None)
    return await tool.on_invoke_tool(tool_ctx, json.dumps(payload))


def test_terminal_formatter_bundle_requires_completed_artifacts():
    executor = _executor_module()

    with pytest.raises(
        executor.FlowTerminalOutputProjectionError,
        match="no completed structured artifacts",
    ):
        executor._build_terminal_flow_artifact_bundle(
            agent_id="csv_formatter",
            output_format="csv",
            completed_steps=[],
            flow_name="No Artifacts",
        )


def test_terminal_formatter_bundle_is_scoped_to_exact_bound_source_node():
    executor = _executor_module()
    first = _completed_artifact_step()
    second = _completed_artifact_step()
    second["step"] = 2
    second["node_id"] = "source_2"
    second["agent_id"] = "allele_extractor"
    second["candidate"] = executor.ExtractionEnvelopeCandidate(
        agent_key="allele_extractor",
        payload_json={
            "domain_pack_id": "allele",
            "envelope_id": "env-allele-1",
            "extracted_objects": [
                {"object_type": "Allele", "payload": {"symbol": "Dmel\\wg[1]"}},
            ],
        },
        candidate_count=1,
        adapter_key="allele",
        conversation_summary="Extracted one allele candidate.",
    )

    bundle = executor._build_terminal_flow_artifact_bundle(
        agent_id="tsv_formatter",
        output_format="tsv",
        completed_steps=[first, second],
        flow_name="Scoped Output",
        source_node_ids=("source_2",),
    )

    assert [artifact.agent_id for artifact in bundle.artifacts] == ["allele_extractor"]
    assert len(bundle.rows_for_source("object")) == 1

    with pytest.raises(
        executor.FlowTerminalOutputProjectionError,
        match="invalid source node\\(s\\): 'missing' \\(0\\)",
    ):
        executor._build_terminal_flow_artifact_bundle(
            agent_id="tsv_formatter",
            output_format="tsv",
            completed_steps=[first, second],
            flow_name="Scoped Output",
            source_node_ids=("missing",),
        )


@pytest.mark.asyncio
async def test_runtime_file_formatter_tool_binds_visible_agent_to_completed_bundle(
    monkeypatch,
):
    executor = _executor_module()
    captured = {}
    completed_steps = [_completed_artifact_step()]

    def _fake_get_agent_by_id(agent_id, **kwargs):
        captured["agent_id"] = agent_id
        captured["kwargs"] = kwargs
        return SimpleNamespace(
            name="CSV File Formatter",
            formatter_kwargs=kwargs,
        )

    def _fake_create_streaming_tool(
        agent,
        tool_name,
        tool_description,
        specialist_name,
        **kwargs,
    ):
        captured["streaming"] = {
            "agent": agent,
            "tool_name": tool_name,
            "tool_description": tool_description,
            "specialist_name": specialist_name,
            "kwargs": kwargs,
        }

        @function_tool(name_override=tool_name, description_override=tool_description)
        async def _tool(query: str) -> str:
            captured["query"] = query
            return json.dumps(
                {
                    "status": "ok",
                    "file_id": "file-visible-formatter",
                    "format": "csv",
                }
            )

        return _tool

    monkeypatch.setattr(executor, "get_agent_by_id", _fake_get_agent_by_id)
    monkeypatch.setattr(executor, "_create_streaming_tool", _fake_create_streaming_tool)

    tool = executor._make_flow_runtime_formatter_tool(
        agent_id="csv_formatter",
        agent_name="CSV File Formatter",
        output_format="csv",
        tool_name="ask_csv_formatter_specialist",
        tool_description="Ask CSV formatter",
        specialist_name="CSV File Formatter",
        base_context={"active_groups": ["demo_group"]},
        step_instruction_prefix="Step-local custom instructions: compact export",
        completed_steps=completed_steps,
        flow_name="Formatter Flow",
        flow_run_id="flow-run-123",
        document_id="doc-1",
        node_data={
            "custom_instructions": "Only include symbols.",
            "projection_plan": {
                "format": "csv",
                "row_source": "object",
                "columns": [
                    {
                        "key": "symbol",
                        "field_ref": "object.payload.symbol",
                    }
                ],
            },
        },
    )

    result = json.loads(
        await _invoke_tool(
            tool,
            {
                "query": "Create the CSV export.",
                "output_filename_descriptor": "gene_symbol_export",
            },
        )
    )

    assert result["file_id"] == "file-visible-formatter"
    assert captured["agent_id"] == "csv_formatter"
    kwargs = captured["kwargs"]
    assert kwargs["formatter_output_format"] == "csv"
    assert kwargs["formatter_agent_id"] == "csv_formatter"
    bundle = kwargs["formatter_bundle"]
    assert bundle.flow_name == "Formatter Flow"
    assert len(bundle.rows_for_source("object")) == 2
    runtime_context = "\n".join(kwargs["additional_runtime_context"])
    assert "FLOW FORMATTER SOURCE BUNDLE" in runtime_context
    assert "Only include symbols." in runtime_context
    assert "configured_projection_plan" in runtime_context
    assert "gene_symbol_export" in runtime_context
    assert captured["query"] == "Create the CSV export."
    assert captured["streaming"]["kwargs"]["inline_chat_persistence"] is False
    assert captured["streaming"]["kwargs"]["propagate_errors"] is True


@pytest.mark.asyncio
async def test_runtime_file_formatter_rejects_empty_bundle_before_agent(monkeypatch):
    executor = _executor_module()

    def _unexpected_get_agent_by_id(*_args, **_kwargs):
        raise AssertionError("formatter agent must not be created without a saved bundle")

    monkeypatch.setattr(executor, "get_agent_by_id", _unexpected_get_agent_by_id)

    tool = executor._make_flow_runtime_formatter_tool(
        agent_id="tsv_formatter",
        agent_name="TSV File Formatter",
        output_format="tsv",
        tool_name="ask_tsv_formatter_specialist",
        tool_description="Ask TSV formatter",
        specialist_name="TSV File Formatter",
        base_context={},
        step_instruction_prefix="",
        completed_steps=[],
        flow_name="Empty Formatter Flow",
        flow_run_id="flow-run-123",
        document_id=None,
        node_data={},
    )

    with pytest.raises(
        executor.FlowTerminalOutputProjectionError,
        match="no completed structured artifacts",
    ):
        await _invoke_tool(tool, {"query": "Create TSV."})


@pytest.mark.asyncio
async def test_chat_output_formatter_flow_output_renders_runtime_chat_table():
    executor = _executor_module()
    tool = executor._make_flow_chat_output_tool(
        agent_id="chat_output_formatter",
        output_format="chat",
        tool_name="ask_chat_output_formatter_specialist",
        tool_description="Ask chat output formatter",
        completed_steps=[_completed_artifact_step()],
        flow_name="Chat Flow",
        flow_run_id="flow-run-123",
        document_id="doc-1",
        node_data={},
    )

    result_text = await _invoke_tool(tool, {"query": "Summarize results."})

    assert "| Adapter | Object Type | Status | Symbol |" in result_text
    assert "TP53" in result_text
    assert "BRCA1" in result_text


def test_hidden_projection_planner_helpers_are_removed():
    executor = _executor_module()
    assert executor.__file__ is not None
    source = Path(executor.__file__).read_text(encoding="utf-8")

    forbidden = [
        "_try_project_terminal_flow_output",
        "_run_output_projection_planner",
        "_flow_output_should_run_projection_planner",
        "_build_output_projection_planner_tools",
        "Projection" + " Planner",
        "direct_formatter_result",
    ]
    for name in forbidden:
        assert name not in source

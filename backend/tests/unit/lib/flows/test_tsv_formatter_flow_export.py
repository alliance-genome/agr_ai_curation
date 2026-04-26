import asyncio
import importlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agents import Agent, function_tool


def _executor_module():
    return importlib.import_module("src.lib.flows.executor")


def _fixture_path() -> Path:
    return (
        Path(__file__).resolve().parents[3]
        / "fixtures"
        / "all_303_tsv_formatter_traces.json"
    )


def _load_traces() -> list[dict]:
    fixture = json.loads(_fixture_path().read_text(encoding="utf-8"))
    return fixture["traces"]


def _expected_rows(trace: dict) -> list[dict[str, str]]:
    columns = trace["columns"]
    return [
        {column: row[column] for column in columns}
        for row in trace["rows"]
    ]


def _make_flow() -> MagicMock:
    flow = MagicMock()
    flow.id = "11111111-1111-1111-1111-111111111111"
    flow.name = "ALL-303 TSV Regression"
    flow.flow_definition = {
        "nodes": [
            {
                "id": "n1",
                "type": "agent",
                "position": {"x": 0, "y": 0},
                "data": {
                    "agent_id": "tsv_formatter",
                    "agent_display_name": "TSV Formatter",
                    "output_key": "tsv_output",
                },
            }
        ],
    }
    return flow


@pytest.mark.parametrize("trace", _load_traces(), ids=lambda trace: trace["trace_id"])
def test_all_303_fixture_queries_parse_to_expected_tsv_rows(trace):
    executor = _executor_module()

    parsed = executor._parse_tsv_formatter_query_rows(trace["formatter_query"])

    assert parsed is not None
    columns, rows = parsed
    assert columns == trace["columns"]
    assert rows == _expected_rows(trace)
    assert len(rows) == trace["row_count"]


@pytest.mark.parametrize("trace", _load_traces(), ids=lambda trace: trace["trace_id"])
@patch("src.lib.flows.executor._resolve_flow_agent_entry")
@patch("src.lib.flows.executor._create_streaming_tool")
@patch("src.lib.flows.executor.get_agent_by_id")
def test_all_303_tsv_formatter_flow_step_saves_rows_without_model_round_trip(
    mock_get_agent,
    mock_create_streaming_tool,
    mock_resolve_flow_agent_entry,
    monkeypatch,
    trace,
):
    executor = _executor_module()
    mock_get_agent.return_value = MagicMock(spec=Agent, instructions="Base")
    mock_resolve_flow_agent_entry.return_value = {
        "name": "TSV Formatter",
        "description": "Format rows as TSV.",
        "requires_document": False,
        "category": "output",
        "subcategory": "format",
    }
    specialist_calls = []
    save_calls = []

    def _make_streaming_tool(agent, tool_name, tool_description, specialist_name):
        @function_tool(name_override=tool_name, description_override=tool_description)
        async def _tool(query: str) -> str:
            specialist_calls.append(query)
            return trace["observed_formatter_result"]

        return _tool

    async def _fake_save_tsv_impl(
        data_json: str,
        filename: str,
        columns: str | None = None,
    ) -> dict:
        save_calls.append(
            {
                "data": json.loads(data_json),
                "filename": filename,
                "columns": json.loads(columns or "[]"),
            }
        )
        return {
            "file_id": f"file-{trace['trace_id']}",
            "filename": "all_303_regression.tsv",
            "format": "tsv",
            "size_bytes": 1234,
            "hash_sha256": "hash",
            "mime_type": "text/tab-separated-values",
            "download_url": f"/api/files/file-{trace['trace_id']}/download",
            "created_at": "2026-04-26T00:00:00Z",
            "trace_id": trace["trace_id"],
            "session_id": trace["session_id"],
            "curator_id": "curator@example.org",
        }

    mock_create_streaming_tool.side_effect = _make_streaming_tool
    monkeypatch.setattr(
        "src.lib.openai_agents.tools.file_output_tools._save_tsv_impl",
        _fake_save_tsv_impl,
    )

    tools, created_names = executor.get_all_agent_tools(_make_flow())

    assert created_names == {"ask_tsv_formatter_specialist"}
    result_text = asyncio.run(
        tools[0].on_invoke_tool(
            SimpleNamespace(tool_name="ask_tsv_formatter_specialist"),
            json.dumps({"query": trace["formatter_query"]}),
        )
    )
    result = json.loads(result_text)

    assert specialist_calls == []
    assert len(save_calls) == 1
    assert save_calls[0]["data"] == _expected_rows(trace)
    assert save_calls[0]["columns"] == trace["columns"]
    assert result["format"] == "tsv"
    assert result["download_url"].endswith("/download")
    assert "Please provide the data" not in result_text

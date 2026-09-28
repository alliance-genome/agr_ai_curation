"""Prove TraceReview consumes bounded outputs as serialized by the installed SDK."""

import importlib
import json
from pathlib import Path

import pytest
from agents.items import ItemHelpers

from agr_ai_curation_runtime.tool_result_bounds import detail_chunk


@pytest.mark.parametrize("surface", ["builder", "groq", "captured_write"])
@pytest.mark.parametrize("budget", [2048, 8192])
def test_sdk_bounded_details_survive_trace_analysis(monkeypatch, surface, budget):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[5]))
    monkeypatch.syspath_prepend(str(
        Path(__file__).resolve().parents[5] / "packages/alliance/python/src"
    ))
    from agr_ai_curation_alliance.tools.agr_curation import AgrQueryResult

    analyzer = importlib.import_module(
        "trace_review.backend.src.analyzers.tool_calls"
    ).ToolCallAnalyzer
    content = ('Ω 表达 😀 O\'Brien "quoted" \\ path\n status=\'error\' data={} ' * 500)
    cursor = None
    chunks = []
    while True:
        page = detail_chunk(
            {"candidate": content}, path="candidate", cursor=cursor, budget=budget,
            extra={"result_ref": "opaque-ref", "page": {"next_cursor": None}},
        )
        if surface == "builder":
            output = AgrQueryResult(status="ok", data=page, count=1, lookup_status="success")
            expected = output.model_dump()
        else:
            output = page
            expected = {"json_data": page}
        raw = ItemHelpers._convert_tool_output(output)
        observations = [{
            "id": "gen-1", "type": "GENERATION", "name": "OpenAI-generation",
            "input": [
                {"type": "function_call", "call_id": "call-1", "name": surface,
                 "arguments": json.dumps({"detail_cursor": cursor})},
                {"type": "function_call_output", "call_id": "call-1", "output": raw},
            ], "output": {},
        }]
        result = analyzer.extract_tool_calls(observations)["tool_calls"][0]["tool_result"]
        assert result["raw"] == raw
        assert result["parse_status"] == "full"
        assert result["parsed"] == expected
        detail = page["detail"]
        chunks.append(detail["content"])
        cursor = detail["next_cursor"]
        if detail["complete"]:
            break
    assert len(chunks) > 1
    assert "".join(chunks) == content

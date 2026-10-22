"""Fresh-flow inspection remains visible as exact tool evidence in TraceReview."""
import json
from src.analyzers.tool_calls import ToolCallAnalyzer


def test_extract_inspect_format_trace_preserves_version_and_exact_output():
    args = {"action": "field", "result_ref": "extraction-result:11111111-1111-1111-1111-111111111111",
            "result_view": "post_validation", "envelope_revision": 2,
            "object_ref": "assertion-1", "field_path": "symbol"}
    output = {"status": "ok", **args, "value": "Exact original validator detail " * 1000}
    observations = []
    for index, name in enumerate(("ask_extractor_specialist", "inspect_results", "ask_formatter_specialist")):
        observations.append({"id": f"generation-{index}", "type": "GENERATION", "startTime": f"2026-10-22T10:00:0{index}Z",
            "output": [{"type": "function_call", "name": name, "call_id": f"call-{index}",
                        "arguments": json.dumps(args if index == 1 else {"query": name})}]})
    observations.append({"id": "return", "type": "GENERATION", "input": [
        {"type": "function_call_output", "call_id": "call-1", "output": json.dumps(output)}]})
    result = ToolCallAnalyzer.extract_tool_calls(observations)
    call = next(call for call in result["tool_calls"] if call["name"] == "inspect_results")
    assert call["call_id"] == "call-1"
    assert call["input"] == args
    assert call["tool_result"]["parsed"]["json_data"] == output

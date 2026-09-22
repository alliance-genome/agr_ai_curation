from copy import deepcopy
import json

from src.analyzers.compact_validators import annotate_compact_validator_calls
from src.analyzers.domain_envelopes import DomainEnvelopeTraceAnalyzer
from src.analyzers.tool_calls import ToolCallAnalyzer


def _lookup(request="a", value="EX:1", call_id="lookup"):
    return {"call_id": call_id, "input": {}, "tool_result": {"parsed": {"json_data": {
        "data": [{"id": value}],
        "validator_record_refs": [{"request_id": request, "record_ref": "vr:scope:0",
            "value": value, "source_path": "/data/0", "label": "Example"}],
        "validator_lookup_refs": [{"request_id": request, "lookup_ref": call_id}],
    }}}}


def _decision(request="a"):
    return {"call_id": "finalize", "input": {"result": {"request_id": request,
        "status": "unresolved", "candidates": [{"record_ref": "vr:scope:0"}],
        "components": [{"lookup_ref": "lookup"}]}}, "output": {"status": "accepted"}}


def test_joins_keep_raw_evidence_and_do_not_invent_findings():
    calls = [_lookup(), _decision()]
    original = deepcopy(calls)
    annotate_compact_validator_calls(calls)
    summary = calls[1].pop("compact_validation")[0]
    assert summary["decision_status"] == "unresolved"
    assert [row["status"] for row in summary["references"]] == ["matched", "matched"]
    assert summary["references"][0]["sources"][0]["catalog"]["value"] == "EX:1"
    assert calls == original


def test_foreign_missing_future_and_conflicting_catalogs_stay_explicit():
    for calls, status in [
        ([_lookup("b"), _decision()], "unmatched"),
        ([_decision(), _lookup()], "unmatched"),
        ([_lookup(), _lookup(value="EX:2", call_id="other"), _decision()], "conflicting"),
    ]:
        annotate_compact_validator_calls(calls)
        decision = next(call for call in calls if call["call_id"] == "finalize")
        assert decision["compact_validation"][0]["references"][0]["status"] == status


def test_full_analyzer_connects_function_call_outputs():
    lookup_output = _lookup()["tool_result"]["parsed"]["json_data"]
    observations = [{"id": "g1", "type": "GENERATION", "startTime": "2026-09-22T00:00:00Z",
        "output": [{"type": "function_call", "call_id": "lookup", "name": "lookup",
                    "arguments": "{}"}]},
        {"id": "g2", "type": "GENERATION", "startTime": "2026-09-22T00:00:01Z",
         "input": [{"type": "function_call_output", "call_id": "lookup", "output": json.dumps(lookup_output)}],
         "output": [{"type": "function_call", "call_id": "finalize", "name": "finalize_validator_result",
                     "arguments": json.dumps(_decision()["input"])}]}]
    analyzed = ToolCallAnalyzer.extract_tool_calls(observations)
    decision = next(call for call in analyzed["tool_calls"] if call["call_id"] == "finalize")
    assert decision["compact_validation"][0]["references"][0]["status"] == "matched"


def test_suppression_is_visible_without_fabricating_committed_custom_findings():
    record = {"validator_binding_id": "identity", "reason": "custom_replacement",
              "target": {"object_id": "object-1"}, "state": "replaced"}
    summary = DomainEnvelopeTraceAnalyzer.analyze_payload({"metadata": {
        "suppressed_upstream_validators": [record]}})
    assert summary["summary"]["suppressed_upstream_validator_count"] == 1
    assert summary["summary"]["finding_count"] == 0
    assert summary["suppressed_upstream_validators"][0]["reason"] == "custom_replacement"
    assert DomainEnvelopeTraceAnalyzer.compact(summary)["suppressed_upstream_validators"]


def test_real_tool_json_strings_and_component_lookup_refs_are_joined():
    lookup_output = _lookup()["tool_result"]["parsed"]["json_data"]
    decision = _decision()["input"]["result"]
    decision["components"] = [{"lookup_refs": ["lookup", "missing"]}]
    observations = [
        {"id": "lookup-span", "type": "TOOL", "name": "lookup", "startTime": "2026-09-22T00:00:00Z",
         "input": "{}", "output": json.dumps(lookup_output)},
        {"id": "finalize-span", "type": "TOOL", "name": "finalize_validator_batch_results",
         "startTime": "2026-09-22T00:00:01Z", "input": json.dumps({"results": [decision]}),
         "output": '{"status":"accepted"}'},
    ]
    analyzed = ToolCallAnalyzer.extract_tool_calls(observations)
    annotation = analyzed["tool_calls"][1]["compact_validation"][0]
    assert [row["status"] for row in annotation["references"]] == ["matched", "matched", "unmatched"]
    assert analyzed["tool_calls"][1]["input"] == observations[1]["input"]


def test_duplicate_sdk_observations_do_not_create_false_conflicts():
    duplicate = deepcopy(_lookup())
    duplicate["id"] = "lookup-span"
    duplicate.pop("call_id")
    calls = [_lookup(), duplicate, _decision()]
    annotate_compact_validator_calls(calls)
    joined = calls[2]["compact_validation"][0]["references"][0]
    assert joined["status"] == "matched"
    assert len(joined["sources"]) == 2

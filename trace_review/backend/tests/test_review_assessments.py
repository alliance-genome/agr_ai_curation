import json

from src.analyzers.tool_calls import ToolCallAnalyzer
from src.analyzers.domain_envelopes import DomainEnvelopeTraceAnalyzer


def test_review_tool_preserves_explicit_uncertainty_and_publication():
    arguments = {"operation_id": "decision", "entries": [{"action": "accept", "target_id": "record",
        "unresolved_fields": [{"field_path": "use", "explanation": "Not established"}]}]}
    result = {"decision_id": "fact", "ready": True, "publication": {"fact_id": "published"}}
    analyzed = ToolCallAnalyzer.extract_tool_calls([
        {"id": "call", "type": "GENERATION", "output": [{"type": "function_call", "name": "record_validation_review",
            "call_id": "c", "arguments": json.dumps(arguments)}]},
        {"id": "result", "type": "GENERATION", "input": [{"type": "function_call_output", "call_id": "c", "output": json.dumps(result)}]},
    ])
    entry = analyzed["tool_calls"][0]["validation_review_decisions"]
    assert entry["arguments"] == arguments
    assert entry["result"] == result


def test_finding_preserves_exact_assessment_provenance():
    provenance = {"artifact_id": "artifact", "decision_id": "decision", "selection_id": "selection",
                  "actor": {"type": "agent", "id": "reviewer"}, "anchor": {"target_id": "field", "suffix": []}}
    summary = DomainEnvelopeTraceAnalyzer.analyze_payload({"domain_pack_id": "fixture", "envelope_id": "env",
        "extracted_objects": [{"object_type": "record", "object_id": "one", "payload": {"use": None}}],
        "validation_findings": [{"finding_id": "finding", "code": "validation_review.assessment_unresolved",
            "status": "open", "severity": "warning", "message": "Not established",
            "field_ref": {"object_ref": {"object_type": "record", "object_id": "one"}, "field_path": "use"},
            "details": {"review_assessment": provenance}}]})
    assert summary["validation_findings"][0]["review_assessment"] == provenance


def test_optional_absence_decision_and_outstanding_page_preserve_exact_references():
    absence = {"fact_id": "absent-source", "sha256": "exact-source"}
    entries = [{"target_id": "binding", "decision_unit_id": "component", "expected_version": 2,
                "action": "accept", "applicability_ref": absence, "absence_assessment": "unresolved"}]
    page = {"ready": False, "artifact_ready": False, "rows": [{
        "target_id": "binding", "decision_unit_id": "component", "applicability_ref": absence}],
        "remaining": 1, "next_cursor": "opaque", "scan_complete": False,
        "global_blockers": [{"code": "document_retired", "count": 1}]}
    for name, arguments, output in [
        ("record_validation_review", {"operation_id": "review", "entries": entries}, {"ready": True}),
        ("get_outstanding_validation_reviews", {"cursor": None}, page),
    ]:
        analyzed = ToolCallAnalyzer.extract_tool_calls([
            {"id": "call", "type": "GENERATION", "output": [{"type": "function_call", "name": name,
                "call_id": "c", "arguments": json.dumps(arguments)}]},
            {"id": "result", "type": "GENERATION", "input": [{"type": "function_call_output",
                "call_id": "c", "output": json.dumps(output)}]},
        ])
        call = analyzed["tool_calls"][0]
        assert call["input"] == arguments
        assert call["tool_result"]["parsed"]["json_data"] == output
        if name == "record_validation_review":
            assert call["validation_review_decisions"]["arguments"]["entries"] == entries
            assert "absence_assessment=unresolved" in call["validation_review_decisions"]["note"]

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

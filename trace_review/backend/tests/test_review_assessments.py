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


def test_unsupported_lookup_history_is_preserved_without_a_validator_answer():
    fact = {"fact_id": "unsupported-source", "sha256": "exact-hash"}
    body = {"request": None, "input": {"binding_id": "term-lookup",
        "unsupported_preflight": {"policy": "provider_taxon_mapping_required",
                                  "explanation": "No configured mapping for this input"},
        "selected_inputs": {"data_provider": "example-provider"}}}
    output = {"reference": fact, "content": body, "next_offset": None}
    calls = ToolCallAnalyzer.extract_tool_calls([
        {"id": "call", "type": "GENERATION", "output": [{"type": "function_call",
            "name": "read_validation_history", "call_id": "c", "arguments": json.dumps(fact)}]},
        {"id": "result", "type": "GENERATION", "input": [{"type": "function_call_output",
            "call_id": "c", "output": json.dumps(output)}]},
    ])["tool_calls"]
    assert calls[0]["tool_result"]["parsed"]["json_data"] == output
    assert calls[0]["input"] == fact


def test_packet_save_and_read_keep_handles_resolvable_to_exact_targets():
    arguments = {"operation_id": "save", "packet_id": "packet-fact", "entries": None,
                 "packet_decisions": [{"handle": "D2", "action": "defer", "reason": "Scope unclear",
                                       "absence_assessment": None, "unresolved_fields": None}]}
    result = {"decision_id": "fact", "ready": False, "packet_id": "packet-fact", "applied": [
        {"decision_unit_id": "component", "action": "defer", "handle": "D2", "target_id": "binding"}]}
    packet = {"packet_id": "packet-fact", "page": 1, "pages": 1, "next_page": None, "records": [
        {"record": "R1", "target_id": "record", "values": {"name": "value"}, "evidence": ["E1"],
         "review": [{"review": "validator_answer", "handle": "D2", "status": "unresolved"}]}],
        "evidence": {"E1": {"verified_quote": "Quoted text", "page": 1}}}
    calls = ToolCallAnalyzer.extract_tool_calls([
        {"id": "call", "type": "GENERATION", "output": [
            {"type": "function_call", "name": "read_review_packet", "call_id": "r",
             "arguments": json.dumps({"packet_id": None, "page": None})},
            {"type": "function_call", "name": "record_validation_review", "call_id": "s",
             "arguments": json.dumps(arguments)}]},
        {"id": "result", "type": "GENERATION", "input": [
            {"type": "function_call_output", "call_id": "r", "output": json.dumps(packet)},
            {"type": "function_call_output", "call_id": "s", "output": json.dumps(result)}]},
    ])["tool_calls"]
    read, save = calls
    assert read["name"] == "read_review_packet"
    assert read["tool_result"]["parsed"]["json_data"] == packet
    decisions = save["validation_review_decisions"]
    assert decisions["arguments"] == arguments and decisions["result"] == result
    assert decisions["packet_handles"] == {"D2": {"decision_unit_id": "component", "target_id": "binding",
                                                  "action": "defer"}}
    assert "retired_records" not in decisions and "blocking" not in decisions


def test_packet_save_shows_reviewer_retirements_and_what_still_blocks_readiness():
    arguments = {"operation_id": "save", "packet_id": "packet-fact", "entries": None, "packet_decisions": [
        {"handle": "D1", "action": "retire", "reason": "A genetic control, not a transgenic fly",
         "evidence": ["E1"], "absence_assessment": None, "unresolved_fields": None},
        {"handle": "D2", "action": "reject", "reason": "Not shown in this paper",
         "absence_assessment": None, "unresolved_fields": None}]}
    blocking = [{"code": "unreviewed", "handles": ["D2"],
                 "clears_with": "accept, or retire if the record does not answer the question"}]
    result = {"decision_id": "fact", "ready": False, "packet_id": "packet-fact", "blocking": blocking, "applied": [
        {"decision_unit_id": "record-1", "action": "retire", "handle": "D1", "target_id": "record-1",
         "retirement_id": "retirement-fact"},
        {"decision_unit_id": "record-2", "action": "reject", "handle": "D2", "target_id": "record-2"}]}
    save, = ToolCallAnalyzer.extract_tool_calls([
        {"id": "call", "type": "GENERATION", "output": [{"type": "function_call", "name": "record_validation_review",
            "call_id": "s", "arguments": json.dumps(arguments)}]},
        {"id": "result", "type": "GENERATION", "input": [
            {"type": "function_call_output", "call_id": "s", "output": json.dumps(result)}]},
    ])["tool_calls"]
    decisions = save["validation_review_decisions"]
    assert decisions["packet_handles"]["D1"] == {"decision_unit_id": "record-1", "target_id": "record-1",
                                                 "action": "retire"}
    assert decisions["retired_records"] == [{"handle": "D1", "target_id": "record-1",
                                             "retirement_id": "retirement-fact"}]
    assert decisions["blocking"] == blocking
    assert "leaves an extraction-only record pending" in decisions["note"]

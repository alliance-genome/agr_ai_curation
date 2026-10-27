import json
from src.analyzers.tool_calls import ToolCallAnalyzer
from src.analyzers.domain_envelopes import DomainEnvelopeTraceAnalyzer
from src.analyzers.extraction_timeline import ExtractionTimelineAnalyzer


def test_tools_preserve_proposal_decision_and_publication_references():
    arguments = {"contribution": {"fact_id": "proposal", "sha256": "exact"}, "decision": "approve"}
    output = {"decision_id": "decision", "retired": True, "publication": {"revision": 2, "ready": True}}
    analyzed = ToolCallAnalyzer.extract_tool_calls([
        {"id": "call", "type": "GENERATION", "output": [{"type": "function_call", "name": "decide_record_retirement",
            "call_id": "c", "arguments": json.dumps(arguments)}]},
        {"id": "result", "type": "GENERATION", "input": [{"type": "function_call_output", "call_id": "c", "output": json.dumps(output)}]},
    ])
    item = analyzed["tool_calls"][0]["extraction_followup"]
    assert item["arguments"] == arguments
    assert item["result"] == output


def test_durable_lifecycle_is_visible_to_domain_and_timeline_analyzers(tmp_path, monkeypatch):
    monkeypatch.setenv("EXTRACTION_TRACE_EVENT_DIR", str(tmp_path))
    facts = ["extraction_followup_request", "extraction_contribution", "record_retirement_decision", "record_restoration"]
    events = []
    for index, kind in enumerate(facts):
        fact = {"kind": kind, "fact_id": f"fact-{index}", "sha256": "exact", "parent_id": f"fact-{index-1}",
                "content": {"identity": {"decision": "approve"}}}
        summary = DomainEnvelopeTraceAnalyzer.analyze_payload(fact)
        assert summary["found"] and summary["followup_history"][0]["kind"] == kind
        assert DomainEnvelopeTraceAnalyzer.compact(summary)["followup_history"]
        events.append({"schema_version": "extraction_trace_event.v1", "trace_id": "t", "event_id": str(index),
            "event_type": "validation_review." + kind, "sequence": index,
            "timestamp": "2026-10-26T00:00:00Z", "output_summary": fact})
    (tmp_path / 't.jsonl').write_text('\n'.join(json.dumps(event) for event in events))
    result = ExtractionTimelineAnalyzer.analyze(trace_id='t', raw_trace=None, observations=[])
    assert len(result["extraction_followup"]["events"]) == 4

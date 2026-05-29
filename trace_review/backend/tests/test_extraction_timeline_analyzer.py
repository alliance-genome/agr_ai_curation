import json

from src.analyzers.extraction_timeline import ExtractionTimelineAnalyzer, feedback_trace_sibling_ids


def _write_event(directory, trace_id, event):
    path = directory / f"{trace_id}.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event))
        handle.write("\n")


def _event(trace_id, sequence, event_type, **overrides):
    base = {
        "schema_version": "extraction_trace_event.v1",
        "event_type": event_type,
        "event_id": f"evt-{trace_id}-{sequence}",
        "sequence": sequence,
        "trace_id": trace_id,
        "observation_id": "obs-root",
        "domain_pack_id": "agr.alliance.gene_expression",
        "tool_call_id": None,
        "input_summary": {},
        "output_summary": {},
        "validation": {},
        "metadata": {},
        "timestamp": f"2026-05-29T00:00:0{sequence}Z",
    }
    base.update(overrides)
    return base


def test_analyzer_merges_durable_events_and_agents_sdk_tool_observations(tmp_path, monkeypatch):
    monkeypatch.setenv("EXTRACTION_TRACE_EVENT_DIR", str(tmp_path))
    _write_event(
        tmp_path,
        "trace-main",
        _event(
            "trace-main",
            1,
            "model.reasoning_summary.request",
            input_summary={
                "preview": {
                    "availability": "present",
                    "reasoning_effort": "medium",
                    "requested_summary": "detailed",
                }
            },
        ),
    )
    _write_event(
        tmp_path,
        "trace-main",
        _event(
            "trace-main",
            2,
            "model.reasoning_summary.output",
            output_summary={"preview": {"summary_text": "Checked resolver evidence."}},
        ),
    )
    _write_event(
        tmp_path,
        "trace-main",
        _event(
            "trace-main",
            3,
            "validation.failure",
            validation={"status": "needs_patch", "errors": [{"message": "missing evidence"}]},
            metadata={"tool_name": "validate_gene_expression_candidate"},
        ),
    )

    observations = [
        {
            "id": "gen-1",
            "type": "GENERATION",
            "startTime": "2026-05-29T00:00:04Z",
            "model": "gpt-5.4-mini",
            "input": [
                {
                    "type": "function_call",
                    "call_id": "call-resolve",
                    "name": "resolve_domain_field_term",
                    "arguments": json.dumps({"candidate_id": "gex-candidate-1"}),
                    "status": "completed",
                },
                {
                    "type": "function_call_output",
                    "call_id": "call-resolve",
                    "output": json.dumps({"status": "ok"}),
                },
            ],
            "output": {},
        }
    ]

    timeline = ExtractionTimelineAnalyzer.analyze(
        trace_id="trace-main",
        raw_trace={"name": "gene expression trace"},
        observations=observations,
    )

    assert timeline["schema_version"] == "extraction_timeline_analyzer.v1"
    assert timeline["durable_event_count"] == 3
    assert timeline["observation_event_count"] == 1
    assert timeline["reasoning_summary"]["status"] == "present"
    assert timeline["reasoning_summary"]["summaries"] == ["Checked resolver evidence."]
    assert timeline["event_type_counts"]["openai_agents.function_call"] == 1
    assert [item["event_type"] for item in timeline["timeline"]] == [
        "model.reasoning_summary.request",
        "model.reasoning_summary.output",
        "validation.failure",
        "openai_agents.function_call",
    ]


def test_analyzer_filters_and_expands_sibling_durable_events(tmp_path, monkeypatch):
    monkeypatch.setenv("EXTRACTION_TRACE_EVENT_DIR", str(tmp_path))
    _write_event(
        tmp_path,
        "trace-main",
        _event(
            "trace-main",
            1,
            "specialist_tool_call.completed",
            metadata={"tool_name": "resolve_domain_field_term"},
            input_summary={"preview": {"candidate_id": "gex-candidate-1"}},
            output_summary={"preview": {"message": "main resolver complete"}},
        ),
    )
    _write_event(
        tmp_path,
        "trace-sibling",
        _event(
            "trace-sibling",
            1,
            "specialist_tool_call.completed",
            metadata={"tool_name": "validate_gene_expression_candidate"},
            input_summary={"preview": {"candidate_id": "gex-candidate-2"}},
            output_summary={"preview": {"message": "sibling validator complete"}},
        ),
    )

    timeline = ExtractionTimelineAnalyzer.analyze(
        trace_id="trace-main",
        raw_trace={},
        observations=[],
        tool_name="validate_gene_expression_candidate",
        sibling_trace_ids=["trace-sibling"],
    )

    assert timeline["event_count"] == 1
    assert timeline["sibling_trace_ids"] == ["trace-sibling"]
    assert timeline["timeline"][0]["event_trace_id"] == "trace-sibling"
    assert timeline["timeline"][0]["tool_name"] == "validate_gene_expression_candidate"


def test_analyzer_renders_concise_structured_output_summaries(tmp_path, monkeypatch):
    monkeypatch.setenv("EXTRACTION_TRACE_EVENT_DIR", str(tmp_path))
    _write_event(
        tmp_path,
        "trace-main",
        _event(
            "trace-main",
            1,
            "specialist_tool_call.completed",
            metadata={"tool_name": "resolve_domain_field_term"},
            output_summary={
                "preview": {
                    "status": "ok",
                    "summary": "Resolved FBbt term.",
                    "term_id": "FBbt:00000001",
                }
            },
        ),
    )
    _write_event(
        tmp_path,
        "trace-main",
        _event(
            "trace-main",
            2,
            "specialist_tool_call.completed",
            metadata={"tool_name": "record_evidence"},
            output_summary={
                "preview": {
                    "evidence_record_id": "evidence-record-1",
                    "candidate_id": "gex-candidate-1",
                }
            },
        ),
    )

    timeline = ExtractionTimelineAnalyzer.analyze(
        trace_id="trace-main",
        raw_trace={},
        observations=[],
        include_raw_outputs=False,
    )

    assert timeline["timeline"][0]["output"] == "status: ok; summary: Resolved FBbt term."
    assert timeline["timeline"][1]["output"] == (
        '{"candidate_id":"gex-candidate-1","evidence_record_id":"evidence-record-1"}'
    )

    raw_timeline = ExtractionTimelineAnalyzer.analyze(
        trace_id="trace-main",
        raw_trace={},
        observations=[],
        include_raw_outputs=True,
    )
    assert raw_timeline["timeline"][0]["output"]["preview"]["term_id"] == "FBbt:00000001"


def test_analyzer_reads_langfuse_mirrored_extraction_trace_events(monkeypatch, tmp_path):
    monkeypatch.setenv("EXTRACTION_TRACE_EVENT_DIR", str(tmp_path))
    observations = [
        {
            "id": "obs-event",
            "name": "extraction_trace_event",
            "input": _event(
                "trace-main",
                1,
                "specialist_tool_call.started",
                tool_call_id="call-specialist-1",
                metadata={"tool_name": "search_document"},
            ),
        }
    ]

    timeline = ExtractionTimelineAnalyzer.analyze(
        trace_id="trace-main",
        raw_trace={},
        observations=observations,
    )

    assert timeline["durable_event_count"] == 1
    assert timeline["local_durable_event_count"] == 0
    assert timeline["langfuse_durable_event_count"] == 1
    assert timeline["timeline"][0]["tool_call_id"] == "call-specialist-1"


def test_analyzer_synthesizes_feedback_trace_artifact_events(monkeypatch, tmp_path):
    monkeypatch.setenv("EXTRACTION_TRACE_EVENT_DIR", str(tmp_path))
    feedback_trace_data = {
        "captured_at": "2026-05-29T00:00:00Z",
        "traces": [
            {
                "trace_id": "trace-main",
                "timestamp": "2026-05-29T00:00:01Z",
                "tool_calls": [
                    {
                        "name": "resolve_domain_field_term",
                        "duration_ms": 17,
                        "status": "ok",
                    }
                ],
            }
        ],
    }

    timeline = ExtractionTimelineAnalyzer.analyze(
        trace_id="trace-main",
        raw_trace={},
        observations=[],
        feedback_trace_data=feedback_trace_data,
    )

    assert timeline["feedback_artifact_event_count"] == 1
    assert timeline["timeline"][0]["event_type"] == "stored_feedback.tool_call"
    assert timeline["timeline"][0]["tool_name"] == "resolve_domain_field_term"


def test_analyzer_expands_stored_feedback_sibling_trace_artifacts(monkeypatch, tmp_path):
    monkeypatch.setenv("EXTRACTION_TRACE_EVENT_DIR", str(tmp_path))
    feedback_trace_data = {
        "captured_at": "2026-05-29T00:00:00Z",
        "traces": [
            {
                "trace_id": "trace-main",
                "timestamp": "2026-05-29T00:00:01Z",
                "tool_calls": [{"name": "resolve_domain_field_term", "status": "ok"}],
            },
            {
                "trace_id": "trace-sibling",
                "timestamp": "2026-05-29T00:00:02Z",
                "tool_calls": [{"name": "validate_gene_expression_candidate", "status": "failed"}],
            },
        ],
    }

    sibling_ids = feedback_trace_sibling_ids("trace-main", feedback_trace_data)
    timeline = ExtractionTimelineAnalyzer.analyze(
        trace_id="trace-main",
        raw_trace={},
        observations=[],
        sibling_trace_ids=sibling_ids,
        feedback_trace_data=feedback_trace_data,
    )

    assert sibling_ids == ["trace-sibling"]
    assert timeline["feedback_artifact_event_count"] == 2
    assert timeline["sibling_trace_ids"] == ["trace-sibling"]
    assert [item["event_trace_id"] for item in timeline["timeline"]] == ["trace-main", "trace-sibling"]

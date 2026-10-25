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
                    "requested_summary": "auto",
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
    assert timeline["size_summary"]["largest_events"]
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


def test_analyzer_expands_sibling_observation_events_without_local_durable_files(tmp_path, monkeypatch):
    monkeypatch.setenv("EXTRACTION_TRACE_EVENT_DIR", str(tmp_path))
    sibling_observations = {
        "trace-sibling": [
            {
                "id": "obs-mirrored-event",
                "name": "extraction_trace_event",
                "input": _event(
                    "trace-sibling",
                    1,
                    "specialist_tool_call.completed",
                    metadata={"tool_name": "record_evidence"},
                    output_summary={"preview": {"message": "mirrored sibling durable event"}},
                ),
            },
            {
                "id": "gen-sibling",
                "type": "GENERATION",
                "startTime": "2026-05-29T00:00:02Z",
                "model": "gpt-5.4-mini",
                "input": [
                    {
                        "type": "function_call",
                        "call_id": "call-sibling-resolve",
                        "name": "resolve_domain_field_term",
                        "arguments": json.dumps({"candidate_id": "gex-candidate-2"}),
                        "status": "completed",
                    },
                    {
                        "type": "function_call_output",
                        "call_id": "call-sibling-resolve",
                        "output": json.dumps({"status": "ok"}),
                    },
                ],
                "output": {},
            },
        ]
    }

    timeline = ExtractionTimelineAnalyzer.analyze(
        trace_id="trace-main",
        raw_trace={},
        observations=[],
        sibling_trace_ids=["trace-sibling"],
        sibling_observations_by_trace_id=sibling_observations,
    )

    assert timeline["local_durable_event_count"] == 0
    assert timeline["langfuse_durable_event_count"] == 1
    assert timeline["observation_event_count"] == 1
    assert timeline["event_type_counts"]["specialist_tool_call.completed"] == 1
    assert timeline["event_type_counts"]["openai_agents.function_call"] == 1
    assert [item["event_trace_id"] for item in timeline["timeline"]] == [
        "trace-sibling",
        "trace-sibling",
    ]
    assert [item["tool_name"] for item in timeline["timeline"]] == [
        "record_evidence",
        "resolve_domain_field_term",
    ]


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


def test_analyzer_reports_payload_sizes_from_new_and_legacy_trace_events(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("EXTRACTION_TRACE_EVENT_DIR", str(tmp_path))
    _write_event(
        tmp_path,
        "trace-main",
        _event(
            "trace-main",
            1,
            "specialist_tool_call.completed",
            metadata={"tool_name": "ask_gene_expression_specialist"},
            output_summary={
                "preview": {
                    "preview": "x" * 100,
                    "truncated": True,
                    "length": 6_700_000,
                },
                "bounded": True,
            },
        ),
    )
    _write_event(
        tmp_path,
        "trace-main",
        _event(
            "trace-main",
            2,
            "tool_call.completed",
            metadata={"tool_name": "compact_tool"},
            output_summary={
                "preview": {"status": "ok"},
                "size": {
                    "kind": "object",
                    "json_chars": 42,
                    "json_bytes": 42,
                    "estimated_tokens": 11,
                },
                "bounded": True,
            },
        ),
    )

    timeline = ExtractionTimelineAnalyzer.analyze(
        trace_id="trace-main",
        raw_trace={},
        observations=[],
    )

    size_summary = timeline["size_summary"]
    assert size_summary["output_json_chars"] >= 6_700_042
    assert size_summary["estimated_exchange_tokens"] >= 1_675_000
    assert size_summary["threshold_counts"]["1000000"] == 1
    largest = size_summary["largest_events"][0]
    assert largest["direction"] == "output"
    assert largest["json_chars"] == 6_700_000
    assert largest["event_type"] == "specialist_tool_call.completed"
    assert timeline["timeline"][0]["output_size"]["source"] == (
        "truncated_preview_length"
    )


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


def test_evidence_revisions_summarizes_history_and_scope_refusals():
    timeline = {
        "schema_version": "extraction_timeline_analyzer.v1",
        "trace_id": "trace-main",
        "query": {"tool_name": "record_evidence"},
        "timeline": [
            {
                "index": 1,
                "timestamp": "2026-06-08T00:00:01Z",
                "source": "durable_event",
                "event_trace_id": "trace-main",
                "event_type": "evidence.summary",
                "event_id": "evt-evidence",
                "tool_name": "record_evidence",
                "agent": "validator:anatomy",
                "output": {
                    "preview": {
                        "evidence_records": [
                            {
                                "evidence_record_id": "ev-1",
                                "entity": "pat-3 neuron",
                                "verified_quote": "Updated exact neuron expression quote.",
                                "page": 5,
                                "section": "Results",
                                "chunk_id": "chunk-new",
                                "source_span_ids": ["chunk-new:s0001:c0000-c0040:bbbbbbbb"],
                                "pending_ref_id": "gene-expression-annotation-pat-3-neuron",
                                "field_path": "expression_pattern.where_expressed.anatomical_structure",
                                "field_paths": [
                                    "expression_pattern.where_expressed.anatomical_structure"
                                ],
                                "envelope_targets": [
                                    {
                                        "pending_ref_id": "gene-expression-annotation-pat-3-neuron",
                                        "field_path": (
                                            "expression_pattern.where_expressed."
                                            "anatomical_structure"
                                        ),
                                    }
                                ],
                                "evidence_revision_history": [
                                    {
                                        "revision": 1,
                                        "replaced_at": "2026-06-08T00:00:00Z",
                                        "previous_source": {
                                            "verified_quote": "Old broad quote.",
                                            "page": 4,
                                            "section": "Results",
                                            "chunk_id": "chunk-old",
                                            "source_span_ids": [
                                                "chunk-old:s0001:c0000-c0020:aaaaaaaa"
                                            ],
                                        },
                                    }
                                ],
                            }
                        ]
                    },
                    "bounded": True,
                },
                "validation": {},
            },
            {
                "index": 2,
                "timestamp": "2026-06-08T00:00:02Z",
                "event_trace_id": "trace-main",
                "event_type": "tool_call.completed",
                "event_id": "evt-forbidden",
                "tool_name": "record_evidence",
                "agent": "validator:anatomy",
                "output": {
                    "preview": {
                        "status": "forbidden",
                        "message": (
                            "Validators may only update evidence records supplied "
                            "for this validation target."
                        ),
                        "evidence_record_id": "ev-outside",
                        "allowed_evidence_record_ids": ["ev-1"],
                        "target_field_path": (
                            "expression_pattern.where_expressed."
                            "anatomical_structure"
                        ),
                    }
                },
                "validation": {},
            },
        ],
    }

    revisions = ExtractionTimelineAnalyzer.evidence_revisions(timeline)

    assert revisions["schema_version"] == "evidence_revisions.v1"
    assert revisions["summary"]["evidence_record_count"] == 1
    assert revisions["summary"]["updated_evidence_record_count"] == 1
    assert revisions["summary"]["revision_count"] == 1
    assert revisions["summary"]["scope_refusal_count"] == 1
    assert revisions["query"] == {"tool_name": "record_evidence"}

    record = revisions["evidence_records"][0]
    assert record["evidence_record_id"] == "ev-1"
    assert record["current_source"]["verified_quote_preview"] == "Updated exact neuron expression quote."
    assert record["current_source"]["page"] == "5"
    assert record["target"]["field_paths"] == [
        "expression_pattern.where_expressed.anatomical_structure"
    ]
    assert record["target"]["envelope_targets"] == [
        {
            "pending_ref_id": "gene-expression-annotation-pat-3-neuron",
            "field_path": "expression_pattern.where_expressed.anatomical_structure",
        }
    ]

    revision = record["revisions"][0]
    assert revision["before_quote_preview"] == "Old broad quote."
    assert revision["after_quote_preview"] == "Updated exact neuron expression quote."
    assert revision["previous_source"]["chunk_id"] == "chunk-old"
    assert revision["current_source"]["chunk_id"] == "chunk-new"
    assert revision["changed_by"] == {
        "agent": "validator:anatomy",
        "tool_name": "record_evidence",
        "event_type": "evidence.summary",
        "event_trace_id": "trace-main",
    }

    refusal = revisions["scope_refusals"][0]
    assert refusal["evidence_record_id"] == "ev-outside"
    assert refusal["allowed_evidence_record_ids"] == ["ev-1"]
    assert refusal["target_field_path"] == (
        "expression_pattern.where_expressed.anatomical_structure"
    )
    assert "supplied target scope" in refusal["diagnostic"]
    assert "evidence_revision_history" not in json.dumps(revisions)


def test_context_compaction_measurements_survive_timeline_extraction(tmp_path, monkeypatch):
    monkeypatch.setenv("EXTRACTION_TRACE_EVENT_DIR", str(tmp_path))
    metrics = {"policy": "native_compaction", "native_compactions": 2,
               "compacted_prefix_items": 40,
               "archive_healthy": True, "input_item_count": 12}
    _write_event(tmp_path, "trace-context", _event("trace-context", 1, "runtime.context_view",
                 output_summary={"preview": metrics}, metadata={"agent": "extractor"}))
    timeline = ExtractionTimelineAnalyzer.analyze(trace_id="trace-context", raw_trace={}, observations=[], include_raw_outputs=True)
    assert timeline["event_type_counts"] == {"runtime.context_view": 1}
    assert timeline["timeline"][0]["output"]["preview"] == metrics


def test_capacity_event_writer_and_timeline_keep_counts_and_redact_credentials(tmp_path, monkeypatch):
    import importlib.util
    import sys
    from pathlib import Path
    # Load the actual application writer without colliding with TraceReview's src package.
    path = next((parent / "backend/src/lib/openai_agents/extraction_trace_events.py"
        for parent in Path(__file__).resolve().parents
        if (parent / "backend/src/lib/openai_agents/extraction_trace_events.py").exists()), Path("/missing"))
    if not path.exists():
        path = Path("/app/backend/src/lib/openai_agents/extraction_trace_events.py")
    spec = importlib.util.spec_from_file_location("capacity_event_writer_fixture", path)
    writer = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = writer
    spec.loader.exec_module(writer)
    monkeypatch.setenv("EXTRACTION_TRACE_EVENT_DIR", str(tmp_path))
    monkeypatch.setattr(writer, "_mirror_to_langfuse", lambda event: None)
    metrics = {"input_tokens_before": 950000, "input_tokens_after": 250000,
        "token_count_requests": 9, "token_count_duration_ms": 42.5,
        "standalone_compactions": 1, "capacity_ingress_payloads": 2, "pending_payloads": 1}
    writer.start_extraction_trace_run(trace_id="capacity")
    try:
        writer.write_extraction_trace_event(event_type="runtime.context_capacity",
            output_summary={**metrics, "access_token": "private"})
    finally:
        writer.clear_extraction_trace_run()
    result = ExtractionTimelineAnalyzer.analyze(trace_id="capacity", raw_trace={}, observations=[], include_raw_outputs=True)
    assert result["timeline"][0]["output"]["preview"] == {**metrics, "access_token": "<redacted>"}


def test_validation_review_operations_keep_exact_history_refs(tmp_path, monkeypatch):
    monkeypatch.setenv('EXTRACTION_TRACE_EVENT_DIR', str(tmp_path))
    _write_event(tmp_path, 'review', _event('review', 1, 'validation_review.review_response',
        output_summary={'preview': {'artifact_id': 'artifact', 'fact_id': 'decision-fact',
                                    'sha256': 'exact-body', 'content': {'reason': 'Caveat accepted'}}}))
    result = ExtractionTimelineAnalyzer.analyze(trace_id='review', raw_trace={}, observations=[], include_raw_outputs=True)
    assert result['validation_review']['event_count'] == 1
    assert result['validation_review']['events'][0]['output']['preview']['fact_id'] == 'decision-fact'


def test_chat_review_plan_coverage_and_phase_are_visible(tmp_path, monkeypatch):
    monkeypatch.setenv('EXTRACTION_TRACE_EVENT_DIR', str(tmp_path))
    kinds = ['review_chat_plan', 'review_coverage', 'review_validator_config', 'chat_phase']
    for sequence, kind in enumerate(kinds, 1):
        _write_event(tmp_path, 'chat-review', _event('chat-review', sequence, 'validation_review.' + kind,
            output_summary={'preview': {'artifact_id': 'artifact', 'episode_id': 'episode',
                                        'actor_id': 'supervisor', 'plan_ref': 'plan'}}))
    result = ExtractionTimelineAnalyzer.analyze(trace_id='chat-review', raw_trace={}, observations=[], include_raw_outputs=True)
    assert result['validation_review']['event_count'] == 4
    assert [item['event_type'] for item in result['validation_review']['events']] == ['validation_review.' + kind for kind in kinds]


def test_workspace_curator_rounds_are_visible_with_exact_request_history(tmp_path, monkeypatch):
    monkeypatch.setenv('EXTRACTION_TRACE_EVENT_DIR', str(tmp_path))
    for sequence, kind in enumerate(['workspace_operation', 'workspace_revalidation_request', 'review_episode_abandoned'], 1):
        _write_event(tmp_path, 'workspace-round', _event('workspace-round', sequence, 'validation_review.' + kind,
            output_summary={'preview': {'operation_id': 'round', 'fact_id': 'request', 'sha256': 'exact-request'}}))
    result = ExtractionTimelineAnalyzer.analyze(trace_id='workspace-round', raw_trace={}, observations=[], include_raw_outputs=True)
    assert result['validation_review']['event_count'] == 3
    assert len(result['workspace_validation_review']['events']) == 3
    assert result['workspace_validation_review']['events'][1]['output']['preview']['sha256'] == 'exact-request'

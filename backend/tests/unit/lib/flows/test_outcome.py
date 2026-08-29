"""Unit coverage for canonical flow terminal outcome reduction."""

from src.lib.flows.outcome import FlowRunOutcome, FlowTerminalOutcomeError


def test_failed_outcome_discards_earlier_success_candidate():
    outcome = FlowRunOutcome()
    outcome.observe(
        {
            "type": "RUN_FINISHED",
            "response": "The model declared success too early.",
        }
    )
    outcome.observe(
        {
            "type": "FLOW_FINISHED",
            "status": "failed",
            "failure_reason": "Required extraction persistence failed.",
        }
    )

    assert outcome.status == "failed"
    assert outcome.final_user_visible_text is None
    assert [event["type"] for event in outcome.events_for_persistence()] == [
        "FLOW_FINISHED"
    ]
    assert outcome.publishable_terminal_events() == []

    outcome.mark_persisted(transcript=True)

    assert [event["type"] for event in outcome.publishable_terminal_events()] == [
        "FLOW_FINISHED"
    ]


def test_failed_outcome_retains_payload_free_operator_context():
    outcome = FlowRunOutcome()
    outcome.observe(
        {
            "type": "RUN_ERROR",
            "data": {
                "message": "private provider response body",
                "error_type": "ProviderTimeout",
                "phase": "specialist_stream",
                "provider": "openai",
                "tool_name": "gene_extractor",
            },
        }
    )

    exc = outcome.terminal_failure_exception()

    assert isinstance(exc, FlowTerminalOutcomeError)
    assert str(exc) == "ProviderTimeout during specialist_stream"
    assert "private provider response body" not in str(exc)
    assert outcome.failure_provider == "openai"
    assert outcome.failure_tool == "gene_extractor"


def test_flow_error_reason_is_retained_until_failed_terminal_confirmation():
    outcome = FlowRunOutcome()
    outcome.observe(
        {
            "type": "FLOW_ERROR",
            "details": {
                "reason": "extraction_persistence_failed",
                "message": "private extraction failure details",
                "specialist": "Curator-created display name",
            },
        }
    )

    assert outcome.status == "running"
    outcome.observe(
        {
            "type": "FLOW_FINISHED",
            "data": {"status": "failed", "failure_reason": "Extraction failed."},
        }
    )

    assert outcome.failure_type == "extraction_persistence_failed"
    assert outcome.failure_phase == "flow_execution"
    assert outcome.failure_tool is None
    assert outcome.failure_already_reported is True
    assert "private extraction failure details" not in str(
        outcome.terminal_failure_exception()
    )


def test_failure_metadata_rejects_human_readable_tag_values():
    outcome = FlowRunOutcome()
    outcome.observe(
        {
            "type": "RUN_ERROR",
            "error_type": "Provider Timeout for curator@example.org",
            "phase": "specialist stream",
            "tool_name": "Dr. Curator's custom agent",
            "provider": "openai",
        }
    )

    assert outcome.failure_type == "FlowRunError"
    assert outcome.failure_phase == "runner"
    assert outcome.failure_tool is None
    assert outcome.failure_provider == "openai"


def test_flow_error_does_not_replace_higher_fidelity_run_error_metadata():
    outcome = FlowRunOutcome()
    outcome.observe(
        {
            "type": "RUN_ERROR",
            "error_type": "MissingEvidenceRecords",
            "phase": "specialist_stream",
            "tool_name": "gene_extractor",
            "provider": "openai",
        }
    )
    outcome.observe(
        {
            "type": "FLOW_ERROR",
            "details": {
                "reason": "run_error",
                "phase": "flow_execution",
                "tool_name": "supervisor",
            },
        }
    )
    outcome.observe(
        {
            "type": "FLOW_FINISHED",
            "data": {"status": "failed", "failure_reason": "Run failed."},
        }
    )

    assert outcome.failure_type == "MissingEvidenceRecords"
    assert outcome.failure_phase == "specialist_stream"
    assert outcome.failure_tool == "gene_extractor"
    assert outcome.failure_provider == "openai"


def test_completed_outcome_releases_exactly_one_preferred_result_after_persistence():
    outcome = FlowRunOutcome()
    outcome.observe({"type": "RUN_FINISHED", "response": "Raw model response."})
    outcome.observe(
        {
            "type": "CHAT_OUTPUT_READY",
            "details": {"output": "Canonical projected response."},
        }
    )
    outcome.observe({"type": "FLOW_FINISHED", "status": "completed"})

    assert outcome.final_user_visible_text == "Canonical projected response."
    assert outcome.publishable_terminal_events() == []

    outcome.mark_persisted(transcript=True)

    assert [event["type"] for event in outcome.publishable_terminal_events()] == [
        "CHAT_OUTPUT_READY",
        "FLOW_FINISHED",
    ]


def test_completed_outcome_preserves_multiple_typed_outputs_after_persistence():
    outcome = FlowRunOutcome()
    outcome.observe({"type": "RUN_FINISHED", "response": "Raw fallback."})
    outcome.observe(
        {
            "type": "FILE_READY",
            "details": {"file_id": "file-1", "filename": "alleles.tsv"},
        }
    )
    outcome.observe(
        {
            "type": "CHAT_OUTPUT_READY",
            "details": {"formatter_node_id": "chat-1", "output": "Allele answer."},
        }
    )
    outcome.observe(
        {
            "type": "CHAT_OUTPUT_READY",
            "details": {"formatter_node_id": "chat-2", "output": "Gene answer."},
        }
    )
    outcome.observe({"type": "FLOW_FINISHED", "status": "completed"})

    assert outcome.final_user_visible_text == "Allele answer.\n\nGene answer."
    outcome.mark_persisted(transcript=True)

    assert [event["type"] for event in outcome.publishable_terminal_events()] == [
        "FILE_READY",
        "CHAT_OUTPUT_READY",
        "CHAT_OUTPUT_READY",
        "FLOW_FINISHED",
    ]


def test_completed_outcome_releases_handoff_readiness_after_persistence():
    outcome = FlowRunOutcome()
    outcome.observe({"type": "RUN_FINISHED", "response": "Raw fallback."})
    outcome.observe(
        {
            "type": "CURATION_HANDOFF_READY",
            "details": {"review_session_ids": ["review-gene"]},
        }
    )
    outcome.observe({"type": "FLOW_FINISHED", "status": "completed"})

    assert outcome.final_user_visible_text == "Raw fallback."
    assert outcome.publishable_terminal_events() == []

    outcome.mark_persisted(transcript=True)

    assert [event["type"] for event in outcome.publishable_terminal_events()] == [
        "RUN_FINISHED",
        "CURATION_HANDOFF_READY",
        "FLOW_FINISHED",
    ]


def test_failed_terminals_discard_buffered_handoff_readiness():
    for terminal_event in (
        {"type": "RUN_ERROR", "message": "runner failed"},
        {
            "type": "FLOW_FINISHED",
            "status": "failed",
            "failure_reason": "final validation failed",
        },
    ):
        outcome = FlowRunOutcome()
        outcome.observe(
            {
                "type": "CURATION_HANDOFF_READY",
                "details": {"review_session_ids": ["review-gene"]},
            }
        )
        outcome.observe(terminal_event)
        outcome.mark_persisted(transcript=True)

        assert all(
            event["type"] != "CURATION_HANDOFF_READY"
            for event in outcome.publishable_terminal_events()
        )


def test_persistence_failure_replaces_stale_success_terminal_order():
    outcome = FlowRunOutcome()
    outcome.observe({"type": "CHAT_OUTPUT_READY", "details": {"output": "stale"}})
    outcome.observe({"type": "FLOW_FINISHED", "status": "completed"})

    outcome.replace_with_persistence_failure(
        "The final outcome was not durable.",
        terminal_events=[
            {"type": "SUPERVISOR_ERROR", "details": {"error": "failed"}},
            {"type": "RUN_ERROR", "message": "failed"},
        ],
    )

    assert outcome.status == "failed"
    assert outcome.final_user_visible_text is None
    assert [event["type"] for event in outcome.events_for_persistence()] == [
        "SUPERVISOR_ERROR",
        "RUN_ERROR",
    ]
    assert outcome.publishable_terminal_events() == []

    outcome.mark_persisted(transcript=True, recovered_failure=True)

    assert [event["type"] for event in outcome.publishable_terminal_events()] == [
        "SUPERVISOR_ERROR",
        "RUN_ERROR",
    ]

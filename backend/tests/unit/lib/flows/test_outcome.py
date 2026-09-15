"""Unit coverage for canonical flow terminal outcome reduction."""

import pytest

from src.lib.flows.outcome import FlowRunOutcome, FlowTerminalOutcomeError


@pytest.mark.parametrize("file_first", [False, True])
@pytest.mark.parametrize("canonical", [None, "Canonical answer.", " "])
def test_files_do_not_own_answer_precedence(file_first, canonical):
    outcome = FlowRunOutcome()
    file = {"type": "FILE_READY", "details": {"file_id": "one", "filename": "one.tsv"}}
    answer = {"type": "RUN_FINISHED", "response": "Supervisor answer."}
    for event in ([file, answer] if file_first else [answer, file]):
        outcome.observe(event)
    outcome.observe(file)  # Duplicate card must not duplicate output.
    outcome.observe({"type": "FILE_READY", "details": {"file_id": "two", "filename": "two.csv"}})
    if canonical is not None:
        outcome.observe({"type": "CHAT_OUTPUT_READY", "details": {"output": canonical}})
        outcome.observe(answer)  # A later supervisor completion cannot override it.
    outcome.observe({"type": "FLOW_FINISHED", "status": "completed"})
    assert outcome.final_user_visible_text == (
        (canonical.strip() or None) if canonical is not None else "Supervisor answer."
    )
    assert [event["type"] for event in outcome.events_for_persistence()] == [
        "FILE_READY", "FILE_READY",
        "CHAT_OUTPUT_READY" if canonical is not None else "RUN_FINISHED", "FLOW_FINISHED",
    ]
    assert outcome.publishable_terminal_events() == []
    outcome.mark_persisted(transcript=True)
    assert outcome.publishable_terminal_events() == outcome.events_for_persistence()


@pytest.mark.parametrize("response", [None, "", " \n "])
def test_file_without_substantive_answer_keeps_no_visible_text(response):
    outcome = FlowRunOutcome()
    outcome.observe({"type": "FILE_READY", "details": {"file_id": "one"}})
    if response is not None:
        outcome.observe({"type": "RUN_FINISHED", "response": response})
    outcome.observe({"type": "FLOW_FINISHED", "status": "completed"})
    assert outcome.final_user_visible_text is None


@pytest.mark.parametrize("persistence_failure", [False, True])
def test_failed_file_and_answer_candidates_never_publish(persistence_failure):
    outcome = FlowRunOutcome()
    outcome.observe({"type": "FILE_READY", "details": {"file_id": "one"}})
    outcome.observe({"type": "RUN_FINISHED", "response": "Do not publish."})
    if persistence_failure:
        outcome.observe({"type": "FLOW_FINISHED", "status": "completed"})
        outcome.replace_with_failure("Could not save", terminal_events=[{"type": "RUN_ERROR"}],
                                     failure_type="PersistenceError", phase="persistence")
    else:
        outcome.observe({"type": "FLOW_FINISHED", "status": "failed"})
    assert outcome.final_user_visible_text is None
    assert outcome.publishable_terminal_events() == []
    outcome.mark_persisted(transcript=True)
    assert all(event["type"] not in {"FILE_READY", "RUN_FINISHED"}
               for event in outcome.publishable_terminal_events())


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


def test_later_reported_persistence_failure_does_not_own_prior_runner_failure():
    outcome = FlowRunOutcome()
    outcome.observe(
        {
            "type": "RUN_ERROR",
            "data": {
                "message": "specialist failed",
                "error_type": "SpecialistOutputError",
                "phase": "specialist_stream",
            },
        }
    )
    outcome.observe(
        {
            "type": "FLOW_ERROR",
            "details": {
                "reason": "extraction_persistence_failed",
                "message": "Extraction persistence also failed.",
            },
        }
    )
    outcome.observe(
        {
            "type": "FLOW_FINISHED",
            "data": {"status": "failed", "failure_reason": "Flow failed."},
        }
    )

    assert outcome.failure_type == "SpecialistOutputError"
    assert outcome.failure_phase == "specialist_stream"
    assert outcome.failure_already_reported is False


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


def test_execution_failure_replaces_stale_success_and_preserves_cause():
    outcome = FlowRunOutcome()
    outcome.observe({"type": "CHAT_OUTPUT_READY", "details": {"output": "stale"}})
    outcome.observe({"type": "FLOW_FINISHED", "status": "completed"})

    outcome.replace_with_failure(
        "The AI service interrupted the run.",
        failure_type="UserError",
        phase="event_generator",
        provider="openai",
        terminal_events=[
            {"type": "SUPERVISOR_ERROR", "details": {"error": "failed"}},
            {"type": "RUN_ERROR", "message": "failed"},
        ],
    )

    assert outcome.status == "failed"
    assert outcome.failure_type == "UserError"
    assert outcome.failure_phase == "event_generator"
    assert outcome.failure_provider == "openai"
    assert outcome.persistence_status == "pending"
    assert outcome.final_user_visible_text is None
    assert [event["type"] for event in outcome.events_for_persistence()] == [
        "SUPERVISOR_ERROR",
        "RUN_ERROR",
    ]
    assert outcome.publishable_terminal_events() == []

    outcome.mark_persisted(transcript=True, recovered_failure=True)
    assert outcome.persistence_status == "succeeded"

    assert [event["type"] for event in outcome.publishable_terminal_events()] == [
        "SUPERVISOR_ERROR",
        "RUN_ERROR",
    ]

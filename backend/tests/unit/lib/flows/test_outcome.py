"""Unit coverage for canonical flow terminal outcome reduction."""

from src.lib.flows.outcome import FlowRunOutcome


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

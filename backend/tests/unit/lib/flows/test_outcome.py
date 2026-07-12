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

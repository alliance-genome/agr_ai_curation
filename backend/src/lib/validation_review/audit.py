"""Emit committed review facts into the existing durable TraceReview timeline."""

from sqlalchemy import event

from src.lib.validation_history import store


def report_operational_failure(error):
    """One sanitized owner; expected review stops and scientific outcomes never call this."""
    from src.lib.observability.runtime import report_runtime_exception

    from agents import UserError

    original = error
    seen = set()
    while isinstance(error, BaseException) and id(error) not in seen:
        seen.add(id(error))
        if getattr(error, "_ai_curation_sentry_captured", False):
            return
        # SDK tool wrappers carry the same fault; a new failure during handling
        # is a separate report even when its cause was already captured.
        if not isinstance(error, UserError):
            break
        error = error.__cause__ or error.__context__
    error = original
    try:
        raise RuntimeError(
            "Validation review operational failure (" + type(error).__name__ + ")"
        ) from None
    except RuntimeError as safe:
        safe.__context__ = None
        safe.__cause__ = None
        captured = report_runtime_exception(
            safe, component="validation_review", operation="execute_review_work"
        )
    if captured:
        error._ai_curation_sentry_captured = True


def append_fact(db, artifact_id, **kwargs):
    fact = store._append(db, artifact_id, **kwargs)
    events = db.info.setdefault("validation_review_events", {})
    events[fact.id] = {
        "artifact_id": artifact_id,
        "fact_id": fact.id,
        "sha256": fact.body_sha,
        "kind": fact.kind,
        "parent_id": fact.parent_id,
        "operation_id": fact.operation_id,
        "content": store._content(db, fact),
    }
    if not db.info.get("validation_review_listeners"):
        db.info["validation_review_listeners"] = True

        @event.listens_for(db, "after_commit")
        def committed(session):
            from src.lib.openai_agents.extraction_trace_events import (
                write_extraction_trace_event,
            )

            pending = session.info.pop("validation_review_events", {})
            for value in pending.values():
                write_extraction_trace_event(
                    event_type="validation_review." + value["kind"],
                    output_summary=value,
                    metadata={
                        "review_fact_id": value["fact_id"],
                        "review_artifact_id": value["artifact_id"],
                    },
                )

        @event.listens_for(db, "after_rollback")
        def rolled_back(session):
            session.info.pop("validation_review_events", None)

    return fact

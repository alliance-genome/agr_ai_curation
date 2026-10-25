"""Workspace rounds use the same Langfuse and durable review timeline as chat."""

from contextlib import contextmanager, ExitStack
from uuid import uuid4

from src.lib.context import get_current_trace_id, set_current_trace_id
from src.lib.openai_agents.extraction_trace_events import (
    start_extraction_trace_run,
    clear_extraction_trace_run,
    write_extraction_trace_event,
)
from src.lib.openai_agents.langfuse_client import get_langfuse


@contextmanager
def workspace_review_trace(*, operation_id, session_id, owner_id):
    previous = get_current_trace_id()
    with ExitStack() as stack:
        client = get_langfuse()
        trace_id = uuid4().hex
        if client is not None:
            from langfuse import propagate_attributes

            span = stack.enter_context(
                client.start_as_current_observation(
                    name="workspace-validation-review",
                    as_type="span",
                    metadata={
                        "workspace_operation_id": operation_id,
                        "runtime": "workspace_validation_review",
                    },
                )
            )
            trace_id = span.trace_id
            stack.enter_context(
                propagate_attributes(
                    user_id=owner_id,
                    session_id=session_id,
                    tags=["workspace-validation-review"],
                    trace_name="Workspace validation review",
                )
            )
        set_current_trace_id(trace_id)
        start_extraction_trace_run(
            trace_id=trace_id, session_id=session_id, user_id=owner_id
        )
        try:
            write_extraction_trace_event(
                event_type="validation_review.workspace_operation",
                output_summary={"operation_id": operation_id, "state": "started"},
            )
            yield trace_id
        finally:
            clear_extraction_trace_run()
            set_current_trace_id(previous)

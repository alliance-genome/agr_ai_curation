"""Presentation-only lifecycle for the shared supervisor review phase."""

from contextlib import contextmanager
from datetime import datetime, timezone


@contextmanager
def review_progress(episode_id):
    from src.lib.openai_agents.streaming_tools import add_specialist_event
    from .stops import review_stop

    def emit(kind, message, **details):
        add_specialist_event({
            "type": kind,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "details": {"agentDisplayName": "Supervisor review", "agentRole": "supervisor",
                        "message": message, "reviewPhase": "supervisor_review",
                        "episodeId": episode_id, **details},
        })

    progress = {}
    emit("AGENT_GENERATING", "Reviewing extracted findings and validation results")
    try:
        yield progress
    except BaseException as error:
        if review_stop(error) is not None:
            emit("AGENT_COMPLETE", "Supervisor review stopped; saved work is retained", success=False)
        else:
            emit("SUPERVISOR_ERROR", "Supervisor review failed; saved work is retained",
                 error="Review could not finish; saved work is retained")
        raise
    else:
        outcome = progress.get("outcome") or {}
        ready = outcome.get("review", {}).get("ready") is True
        emit("AGENT_COMPLETE",
             "Supervisor review complete" if ready else "Supervisor review ended with unresolved work",
             success=ready)

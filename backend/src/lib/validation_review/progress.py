"""Presentation-only lifecycle for the shared supervisor review phase."""

from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone


def _count(number, one, many):
    return f"{number} {one if number == 1 else many}"


def _start_line(tool, arguments):
    """What a review tool is about to do, in words a curator can follow."""
    if tool == "get_outstanding_validation_reviews":
        return "checking which records still need a decision"
    if tool == "get_validation_record_sources":
        return "reading " + _count(len(arguments.get("target_ids") or []), "saved record", "saved records")
    if tool == "get_validation_record_source":
        return "reading a saved record"
    if tool in {"read_validation_history", "get_validation_review_packet"}:
        return "reading saved validation history"
    if tool == "get_validation_review_status":
        return "checking review status"
    if tool == "record_validation_review":
        decisions = arguments.get("packet_decisions") or arguments.get("entries") or []
        return "saving " + _count(len(decisions), "decision", "decisions")
    if tool == "request_validation_reconsideration":
        return ("asking a validator to check "
                + _count(len(arguments.get("target_ids") or []), "result", "results") + " again")
    if tool == "request_targeted_extraction":
        return ("asking the extraction agent to re-check "
                + _count(len(arguments.get("entry_ids") or []), "record", "records"))
    if tool == "select_extraction_contribution":
        return "saving corrections proposed by the extraction agent"
    if tool == "decide_record_retirement":
        return ("removing a record from the results" if arguments.get("decision") == "approve"
                else "keeping a record the extraction agent suggested removing")
    if tool == "validate_selected_extraction_source":
        return ("asking a validator to check "
                + _count(len(arguments.get("target_ids") or []), "corrected value", "corrected values"))
    return None


def _finish_line(tool, result):
    """Packet reads are described by what they returned."""
    if tool != "read_review_packet" or not isinstance(result, dict) or "records" not in result:
        return None
    if result["page"] > 1:
        return f"reviewing more records (part {result['page']} of {result['pages']})"
    if not result["record_count"]:
        return "checking the remaining review work"
    line = "reviewing " + _count(result["record_count"], "record", "records")
    if result["answer_count"]:
        line += " and " + _count(result["answer_count"], "validation result", "validation results")
    if result["pages"] > 1:
        line += f" (part 1 of {result['pages']})"
    return line


class ReviewLines:
    """One plain progress line per burst of the same review tool.

    Calls the model issues together overlap, so a burst collapses into the line of
    its first call. Lines exist only for a live curator stream; they never change
    review outcomes.
    """

    def __init__(self, episode_id):
        self.episode_id = episode_id
        self._running = Counter()
        self._shown = set()

    def _emit(self, kind, tool, line):
        from src.lib.openai_agents.streaming_tools import add_specialist_event, get_live_event_list

        if get_live_event_list() is None:
            return
        add_specialist_event({
            "type": kind,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "details": {"toolName": tool, "friendlyName": "Supervisor review: " + line,
                        "agentDisplayName": "Supervisor review", "agentRole": "supervisor",
                        "reviewPhase": "supervisor_review", "episodeId": self.episode_id},
        })

    def start(self, tool, arguments):
        self._running[tool] += 1
        line = _start_line(tool, arguments)
        if line is not None and tool not in self._shown:
            self._shown.add(tool)
            self._emit("TOOL_START", tool, line)

    def finish(self, tool, result):
        line = _finish_line(tool, result)
        if line is not None and tool not in self._shown:
            self._shown.add(tool)
            self._emit("TOOL_COMPLETE", tool, line)
        self._running[tool] -= 1
        if not self._running[tool]:
            self._shown.discard(tool)


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

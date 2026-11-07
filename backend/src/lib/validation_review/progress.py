"""Presentation-only lifecycle for the shared supervisor review phase."""

import json
from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone

# The episode of the live review in this task. A targeted re-check runs inside a
# review tool call, so its paper work is labelled with the same review lines.
_LIVE_REVIEW_EPISODE = ContextVar("validation_review_live_episode", default=None)


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
        removing = sum(getattr(decision, "action", None) == "retire" for decision in decisions)
        saving = len(decisions) - removing
        removal = ("removing " + _count(removing, "record", "records")
                   + (" that doesn't" if removing == 1 else " that don't") + " answer the question")
        if not removing:
            return "saving " + _count(saving, "decision", "decisions")
        if not saving:
            return removal
        return "saving " + _count(saving, "decision", "decisions") + " and " + removal
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


# A re-check that did not run for some records is a warning, never a failure.
_WARNING = {"severity": "warning"}


def _recheck_finish_lines(result):
    """How a targeted re-check request ended, read from its result's structure only.

    Records with no re-check left are listed under exhausted, both when the rest
    ran and when none could. A stopped call (no result) has no line;
    review_progress reports the stop.
    """
    if not isinstance(result, dict):
        return []
    state, exhausted = result.get("state"), result.get("exhausted")
    none_left = (("no re-check left for " + _count(len(exhausted), "record", "records"), _WARNING)
                 if isinstance(exhausted, list) and exhausted else None)
    not_run = [("re-check did not run", _WARNING)]
    if state == "completed" and isinstance(result.get("contributions"), list):
        finished = ("re-check finished for " + _count(len(result["contributions"]), "record", "records"), {})
        return [finished] if none_left is None else [finished, none_left]
    if state == "not_applied" and result.get("code") == "extraction_attempts_exhausted" and none_left:
        return [none_left]
    if state in {"not_applied", "conflict", "not_claimed"}:
        # Refused for another reason, a conflicting request, or work another call
        # already holds: nothing new ran.
        return not_run
    # A shape this presentation module does not know never claims a re-check ran.
    return not_run


def _finish_lines(tool, result):
    """Packet reads are described by what they returned; re-checks by how they ended."""
    if tool == "request_targeted_extraction":
        return _recheck_finish_lines(result)
    if tool == "read_review_packet" and isinstance(result, dict) and "records" in result:
        return [(_packet_line(result), {})]
    return []


def _packet_line(result):
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


def _named(*values):
    """Plain names the model passed (section headings), joined; never interpreted."""
    names = [value.strip() for value in values if isinstance(value, str) and value.strip()]
    return ": ".join(names) or None


def recheck_line(tool, arguments):
    """What the extraction agent does during a re-check, from tool name and arguments.

    arguments is the tool call's raw JSON. Malformed arguments are the tool's own
    error to report; the line then omits the section name.
    """
    try:
        values = json.loads(arguments)
    except ValueError:
        values = None
    if not isinstance(values, dict):
        values = {}
    if tool == "read_section":
        return "extraction agent reading " + (_named(values.get("section_name")) or "a section")
    if tool == "read_subsection":
        return "extraction agent reading " + (
            _named(values.get("parent_section"), values.get("subsection")) or "a subsection")
    if tool == "read_chunk":
        return "extraction agent reading a passage"
    if tool == "search_document":
        return "extraction agent searching the paper"
    if tool == "record_evidence":
        return "extraction agent saving evidence"
    if tool in {"list_recorded_evidence", "get_recorded_evidence"}:
        return "extraction agent checking saved evidence"
    if tool in {"attach_evidence_to_object", "detach_evidence_from_object", "discard_recorded_evidence",
                "update_recorded_evidence_metadata"}:
        return "extraction agent updating saved evidence"
    if tool == "read_supplied_history":
        return "extraction agent reading the re-check request"
    # Any other configured paper tool reads the paper or manages its evidence.
    return "extraction agent working with the paper"


def _emit(kind, tool, line, episode_id, **details):
    from src.lib.openai_agents.streaming_tools import add_specialist_event, get_live_event_list

    if get_live_event_list() is None:
        return
    add_specialist_event({
        "type": kind,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "details": {"toolName": tool, "friendlyName": "Supervisor review: " + line,
                    "agentDisplayName": "Supervisor review", "agentRole": "supervisor",
                    "reviewPhase": "supervisor_review", "episodeId": episode_id, **details},
    })


class ReviewLines:
    """One plain progress line per burst of the same review tool.

    Calls the model issues together overlap, so a burst's start collapses into the
    line of its first call. Finish lines collapse only when their text is the same:
    two packet pages read together, or two re-checks that end differently, each
    show. Lines exist only for a live curator stream; they never change review
    outcomes.
    """

    def __init__(self, episode_id):
        self.episode_id = episode_id
        self._running = Counter()
        self._started = set()
        self._finished = set()

    def start(self, tool, arguments):
        self._running[tool] += 1
        line = _start_line(tool, arguments)
        if line is not None and tool not in self._started:
            self._started.add(tool)
            _emit("TOOL_START", tool, line, self.episode_id)

    def finish(self, tool, result):
        for line, details in _finish_lines(tool, result):
            if (tool, line) not in self._finished:
                self._finished.add((tool, line))
                _emit("TOOL_COMPLETE", tool, line, self.episode_id, **details)
        self._running[tool] -= 1
        if not self._running[tool]:
            self._started.discard(tool)
            self._finished = {shown for shown in self._finished if shown[0] != tool}


class RecheckLines:
    """The extraction agent's paper work during one re-check, as review lines.

    Bound to the live review this task runs under; with none, nothing is shown.
    Overlapping calls with the same line show it once.
    """

    def __init__(self):
        self.episode_id = _LIVE_REVIEW_EPISODE.get()
        self._running = Counter()

    @contextmanager
    def tool(self, tool, arguments):
        line = recheck_line(tool, arguments)
        if self.episode_id is not None and not self._running[line]:
            _emit("TOOL_START", tool, line, self.episode_id, isSpecialistInternal=True)
        self._running[line] += 1
        try:
            yield
        finally:
            self._running[line] -= 1


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
    # Bound only once the review is under way, so the finally below always resets it.
    live = _LIVE_REVIEW_EPISODE.set(episode_id)
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
    finally:
        _LIVE_REVIEW_EPISODE.reset(live)

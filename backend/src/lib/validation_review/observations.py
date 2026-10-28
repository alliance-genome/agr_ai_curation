"""Invocation-local observations of actual review tools, never predicted output."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import Lock


# Read pages of mutable-status snapshots must not manufacture useful progress.
SCIENTIFIC_READ_KINDS = frozenset({
    "baseline", "target", "attempt", "completion", "validation_source_input",
    "review_record_source", "extraction_followup_request", "extraction_contribution",
})
_current = ContextVar("validation_review_observations", default=None)


@dataclass
class Observations:
    reads: set[tuple] = field(default_factory=set)
    refusals: dict[tuple, dict] = field(default_factory=dict)
    _seen_states: set[str] = field(default_factory=set, repr=False)
    _seen_reads: set[tuple] = field(default_factory=set, repr=False)
    _lock: Lock = field(default_factory=Lock, repr=False)

    def progress(self, signature):
        """Seed once before running, then check novel state or exact scientific reads."""
        with self._lock:
            useful = signature not in self._seen_states or bool(self.reads - self._seen_reads)
            self._seen_states.add(signature)
            self._seen_reads.update(self.reads)
            return useful

    def record_read(self, kind, page):
        if kind not in SCIENTIFIC_READ_KINDS or not page["content"]:
            return
        # Body identity, not newly allocated snapshot IDs, defines the receipt.
        # Complete-body reads stay the same receipt when a normalized source
        # changes only bookkeeping byte width (e.g. prerequisite generation 9→10).
        end = None if page["offset"] == 0 and page.get("next_offset") is None else page["offset"] + len(page["content"])
        key = (page["sha256"], page["offset"], end)
        with self._lock:
            self.reads.add(key)

    def record_tool(self, name, arguments, result):
        if not isinstance(result, dict):
            return
        identities = set(arguments.get("target_ids", [])) | set(arguments.get("entry_ids", []))
        contribution = arguments.get("contribution")
        if contribution is not None:
            reference = contribution if isinstance(contribution, dict) else contribution.model_dump()
            identities.add(reference["fact_id"])
        for key in ("target_id", "fact_id", "entry_id"):
            if isinstance(arguments.get(key), str):
                identities.add(arguments[key])
        for entry in arguments.get("entries", []):
            value = entry if isinstance(entry, dict) else entry.model_dump()
            identities.add(value.get("decision_unit_id") or value["target_id"])
        # Never retain free text, evidence bodies, or operation IDs in diagnostics.
        keys = [(name, identity) for identity in sorted(identities)] or [(name, None)]
        with self._lock:
            for key in keys:
                if result.get("state") in {"not_applied", "conflict", "not_runnable"}:
                    self.refusals[key] = {
                        "tool": name, "target_id": key[1],
                        "code": result.get("code") or result["reason"], "resource": result.get("resource"),
                    }
                else:
                    self.refusals.pop(key, None)
        for outcome in result.get("dependent_work", []):
            # Parent decisions may succeed while a specific dependent cannot
            # run. Track and clear those typed outcomes by the child binding.
            self.record_tool("dependent_validation", {"target_id": outcome["target"]}, outcome)

    def snapshot(self):
        with self._lock:
            return frozenset(self.reads), list(self.refusals.values())


@contextmanager
def observation_scope(observations):
    token = _current.set(observations)
    try:
        yield observations
    finally:
        _current.reset(token)


def record_read(kind, page):
    observations = _current.get()
    if observations is not None:
        observations.record_read(kind, page)
    return page


def record_tool(name, arguments, result):
    observations = _current.get()
    if observations is not None:
        observations.record_tool(name, arguments, result)
    return result

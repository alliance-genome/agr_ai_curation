"""Durable, per-original-entry admission for targeted extractor follow-ups.

The authenticated adapter supplies the saved extractor receipt and selected
source. Model arguments can name existing entries and explain a question; they
cannot choose a different extractor, change originals, or replenish attempts.
"""

from sqlalchemy import select

from src.lib.validation_history import store
from src.models.sql.validation_history import ValidationFact
from src.models.sql.validation_review import ValidationReviewEpisode as Episode
from src.models.sql.validation_review import ValidationReviewWork as Work
from . import admission, specialist_context
from .audit import append_fact
from .decisions import bounded_text
from .work import enqueue_in_transaction


def attempt_counts(db, artifact_id, entry_ids):
    """Existing immutable request count, shared by admission and continuation."""
    counts = dict.fromkeys(entry_ids, 1)  # The immutable original is attempt 1.
    for fact in db.scalars(select(ValidationFact).where(
        ValidationFact.artifact_id == artifact_id,
        ValidationFact.kind == "extraction_followup_request",
    )):
        body = store._content(db, fact)
        # Artifact identity preserves entitlement across turns/episodes and
        # even independently admitted scopes for the same saved original.
        for entry_id in set(body["entry_ids"]).intersection(counts):
            counts[entry_id] += 1
    return counts


def request(context, *, operation_id, parent_decision_id, entry_ids, instructions,
            receipt, current_entries, contributions=(), expected_source_sha=None):
    """Atomically freeze context, consume entitlement and enqueue one invocation.

    receipt/current_entries/contributions are runtime-owned, not tool arguments.
    Counting the immutable request, rather than successful answers, also fences
    interrupted and ambiguous provider submissions. A retry reuses its work ID.
    """
    with store.SessionLocal.begin() as db:
        ep, _ = admission.lock_episode(
            db, context.history_scope, context.episode_id, require_open=False
        )
        bounded_text(operation_id, ep.policy["instruction_chars"], "Operation ID")
        bounded_text(instructions, ep.policy["instruction_chars"], "Follow-up instructions")
        identity = store.canonical({
            "entry_ids": entry_ids, "parent_decision_id": parent_decision_id,
            "instructions": instructions, "receipt": receipt,
            "actor": {"type": context.actor_type, "id": context.actor_id},
        })[1]
        prior = db.scalar(select(Work).join(Episode, Episode.id == Work.episode_id).where(
            Episode.scope_id == ep.scope_id, Episode.artifact_id == ep.artifact_id,
            Work.operation_id == operation_id,
        ))
        if prior is not None:
            if prior.kind != "extraction_followup" or prior.payload["identity"] != identity:
                raise store.HistoryConflict("Follow-up operation identity conflict")
            return prior.id
        admission.lock_episode(db, context.history_scope, ep.id)
        if expected_source_sha is not None:
            from src.lib.validation_history.selections import source_state_digest
            if source_state_digest(db, ep.artifact_id) != expected_source_sha:
                raise store.HistoryConflict("Source changed before targeted extraction admission")
        specialist_context.entry_coverage(db, ep.artifact_id, entry_ids)
        if len(entry_ids) > ep.policy["decision_entries"]:
            raise ValueError("Follow-up exceeds configured entry coverage")
        parent = store._fact(db, ep.artifact_id, parent_decision_id, kind="decision")
        disposition = db.scalar(select(ValidationFact).where(
            ValidationFact.artifact_id == ep.artifact_id,
            ValidationFact.operation_id == "episode-disposition:" + parent.operation_id.removeprefix("decision:"),
        ))
        if disposition is None or disposition.parent_id != ep.id:
            raise store.HistoryConflict("Parent decision is outside this review episode")
        named = {item["target_id"] for item in store._content(db, parent)["entries"]}
        # Every entry must have a reviewed descendant, not merely one entry in a
        # multi-entry request. No scientific inference about the decision reason.
        coverage = {entry_id: specialist_context.entry_coverage(db, ep.artifact_id, [entry_id])[1]
                    for entry_id in entry_ids}
        for descendants in coverage.values():
            if not named.intersection(descendants):
                raise store.HistoryConflict("Parent decision must concern every requested entry")
        from src.lib.validation_history.selections import effective_episode
        from .record_scope import allowed_units
        episode_body = effective_episode(db, store._fact(db, ep.artifact_id, ep.id))
        permitted = allowed_units(episode_body)
        declared = [item for item in episode_body["obligations"] if item["target_id"] in permitted]
        if not all(any(item["target_id"] in descendants for item in declared)
                   for descendants in coverage.values()):
            raise store.HistoryAccessError("Follow-up entry is outside this episode")
        counts = attempt_counts(db, ep.artifact_id, entry_ids)
        ceiling = ep.policy["extraction_attempts_per_entry"]
        for entry_id, count in counts.items():
            if count >= ceiling:
                raise admission.ReviewStopped("extraction_attempts_exhausted", resource=entry_id)
        attempt = {entry_id: {"ordinal": count + 1, "ceiling": ceiling,
                             "remaining_after": ceiling - count - 1}
                   for entry_id, count in counts.items()}
        packet = specialist_context.assemble_in_transaction(
            db, context.history_scope, ep, operation_id="extraction:" + operation_id,
            entry_ids=entry_ids, parent_decision_id=parent_decision_id,
            instructions=instructions, current_entries=current_entries,
            attempt=attempt, purpose="targeted_extraction", contributions=contributions,
        )
        fact = append_fact(db, ep.artifact_id,
            operation_id="extraction-followup:" + operation_id,
            kind="extraction_followup_request", parent_id=ep.id,
            body={"entry_ids": entry_ids, "identity": identity, "attempt": attempt,
                  "context_ref": packet["history"]["records"][0]})
        return enqueue_in_transaction(
            db, context.history_scope, ep.id, operation_id=operation_id,
            kind="extraction_followup", charges={"reviewer_tokens": 0},
            payload={"identity": identity, "receipt": receipt, "entry_ids": entry_ids,
                     "request_ref": specialist_context.reference(fact),
                     "specialist_input": packet, "token_pool": "reviewer_tokens"},
        )

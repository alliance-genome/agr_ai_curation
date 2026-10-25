"""Durable delivery fencing for admitted validation work.

Queued/claimed work may be delivered again. Submitted work may not: recovery
marks it uncertain and retains its reservation until an exact outcome arrives.
No database transaction is held during a provider call.
"""

from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select

from src.lib.validation_history import store
from .audit import append_fact
from src.models.sql.validation_review import ValidationReviewWork as Work
from .admission import ReviewStopped, lock_episode, now, reserve_in_transaction


def enqueue_in_transaction(db, scope, episode_id, *, operation_id, payload, charges, kind="validator"):
    if kind not in {"validator", "extraction_followup"}:
        raise ValueError("Unknown admitted work kind")
    episode, _ = lock_episode(db, scope, episode_id)
    payload = store.canonical(payload)[1]
    existing = db.scalar(
        select(Work).where(
            Work.episode_id == episode_id, Work.operation_id == operation_id
        )
    )
    if existing is not None:
        if existing.payload != payload or existing.kind != kind:
            raise store.HistoryConflict("Work operation identity conflict")
        return existing.id
    reservation = reserve_in_transaction(
        db,
        scope,
        episode_id,
        operation_id="work:" + operation_id,
        charges=charges,
        request=payload,
        start_work=False,
    )
    row = Work(
        id=str(uuid4()),
        episode_id=episode_id,
        operation_id=operation_id,
        payload=payload,
        state="queued",
        kind=kind,
    )
    db.add(row)
    append_fact(
        db,
        episode.artifact_id,
        operation_id="review-work:" + row.id,
        kind="review_work",
        parent_id=episode.id,
        body={"work_id": row.id, "reservation_id": reservation.id, "payload": payload},
    )
    return row.id


def _locked(db, scope, work_id, *, require_open):
    row = db.get(Work, work_id)
    if row is None:
        raise store.HistoryAccessError("Review work unavailable")
    episode, _ = lock_episode(db, scope, row.episode_id, require_open=require_open)
    row = db.scalar(
        select(Work)
        .where(Work.id == work_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return episode, row


def claim(scope, work_id):
    """A claim fences a worker; it is not permission to submit a provider call."""
    with store.SessionLocal.begin() as db:
        episode, row = _locked(db, scope, work_id, require_open=False)
        if row.state not in {"queued", "claimed"}:
            return None
        lock_episode(db, scope, episode.id)
        if row.state == "claimed" and row.lease_until > now():
            return None
        row.state = "claimed"
        row.lease_token = str(uuid4())
        row.lease_until = now() + timedelta(seconds=episode.policy["lease_seconds"])
        append_fact(
            db,
            episode.artifact_id,
            operation_id="review-claim:" + row.lease_token,
            kind="review_claim",
            parent_id=episode.id,
            body={"work_id": row.id, "lease_token": row.lease_token},
        )
        return {
            "work_id": row.id,
            "lease_token": row.lease_token,
            "payload": row.payload,
        }


def mark_submitted(scope, work_id, lease_token):
    """Commit before IO. An ambiguous send is retained as uncertain, never replayed."""
    with store.SessionLocal.begin() as db:
        episode, row = _locked(db, scope, work_id, require_open=True)
        if (
            row.lease_token != lease_token
            or row.state != "claimed"
            or row.lease_until <= now()
        ):
            raise store.HistoryConflict("Review work lease is stale")
        row.state = "submitted"
        row.lease_until = now() + timedelta(seconds=episode.policy["lease_seconds"])
        if episode.started_at is None:
            episode.started_at = now()
        append_fact(
            db,
            episode.artifact_id,
            operation_id="review-submit:" + row.id,
            kind="review_submission",
            parent_id=episode.id,
            body={"work_id": row.id, "lease_token": lease_token},
        )


def recover(scope, work_id):
    """Inspect one durable item; the scheduler owns enumeration and scheduling."""
    with store.SessionLocal.begin() as db:
        episode, row = _locked(db, scope, work_id, require_open=False)
        if row.state in {"completed", "uncertain", "cancelled"}:
            return row.state
        try:
            lock_episode(db, scope, episode.id)
        except ReviewStopped as exc:
            row.state = "uncertain" if row.state == "submitted" else "cancelled"
            reason = exc.reason
        else:
            if row.state == "queued" or row.lease_until > now():
                return row.state
            reason = "worker_lease_expired"
            row.state = "uncertain" if row.state == "submitted" else "queued"
        if row.state == "uncertain":
            _uncertainty(db, episode, row, reason)
        append_fact(
            db,
            episode.artifact_id,
            operation_id="review-recovery:"
            + row.id
            + ":"
            + str(row.lease_token)
            + ":"
            + row.state,
            kind="review_recovery",
            parent_id=episode.id,
            body={"work_id": row.id, "state": row.state, "reason": reason},
        )
        return row.state


def finish(scope, work_id, lease_token, *, outcome):
    """Retain exact late outcomes without granting publication authority."""
    outcome = store.canonical(outcome)[1]
    with store.SessionLocal.begin() as db:
        episode, row = _locked(db, scope, work_id, require_open=False)
        if row.lease_token != lease_token:
            raise store.HistoryConflict("Review completion lease is stale")
        if row.state == "completed":
            if row.outcome != outcome:
                raise store.HistoryConflict("Review completion identity conflict")
            return
        if row.state not in {"submitted", "uncertain"}:
            raise store.HistoryConflict("Review work was not submitted")
        row.state, row.outcome = "completed", outcome
        append_fact(
            db,
            episode.artifact_id,
            operation_id="review-outcome:" + row.id,
            kind="review_outcome",
            parent_id=episode.id,
            body={"work_id": row.id, "outcome": outcome},
        )


def _uncertainty(db, episode, row, reason):
    if row.kind == "extraction_followup":
        append_fact(db, episode.artifact_id, operation_id="followup-uncertain:" + row.id,
            kind="extraction_followup_uncertainty", parent_id=row.payload["request_ref"]["fact_id"],
            body={"work_id": row.id, "reason": reason, "lease_token": row.lease_token})
        return
    for attempt_id in row.payload["invocation"]["attempt_ids"]:
        append_fact(
            db,
            episode.artifact_id,
            operation_id="review-uncertain:" + row.id + ":" + attempt_id,
            kind="review_uncertainty",
            parent_id=attempt_id,
            body={"work_id": row.id, "reason": reason, "lease_token": row.lease_token},
        )


def mark_uncertain(scope, work_id, lease_token, *, reason):
    with store.SessionLocal.begin() as db:
        episode, row = _locked(db, scope, work_id, require_open=False)
        if row.lease_token != lease_token or row.state not in {
            "submitted",
            "uncertain",
        }:
            raise store.HistoryConflict(
                "Uncertain outcome does not match submitted work"
            )
        if row.state == "uncertain":
            return
        row.state = "uncertain"
        _uncertainty(db, episode, row, reason)


def heartbeat(scope, work_id, lease_token):
    """Extend delivery ownership, never the fixed scientific review deadline."""
    with store.SessionLocal.begin() as db:
        episode, row = _locked(db, scope, work_id, require_open=True)
        if (
            row.lease_token != lease_token
            or row.state not in {"claimed", "submitted"}
            or row.lease_until <= now()
        ):
            raise ReviewStopped("worker_lease_lost", resource=work_id)
        row.lease_until = now() + timedelta(seconds=episode.policy["lease_seconds"])


def mark_not_admitted(scope, work_id, lease_token, *, reason, resource=None):
    """A pre-send refusal is a known operational outcome, never a scientific answer."""
    with store.SessionLocal.begin() as db:
        episode, row = _locked(db, scope, work_id, require_open=False)
        if row.lease_token != lease_token or row.state != "submitted":
            raise store.HistoryConflict("Refusal does not match submitted work")
        # No provider invocation was admitted. The executor stores operational
        # failed completions separately, so a reviewer can acknowledge and retry.
        row.state = "completed"
        row.outcome = {"state": "not_admitted", "reason": reason, "resource": resource}
        append_fact(
            db,
            episode.artifact_id,
            operation_id="review-outcome:" + row.id,
            kind="review_outcome",
            parent_id=episode.id,
            body={"work_id": row.id, "outcome": row.outcome},
        )

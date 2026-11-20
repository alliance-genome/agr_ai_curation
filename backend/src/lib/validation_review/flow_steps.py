"""Durable flow submission fence; an interrupted extraction is never replayed.

Configured orchestration owns run/node/contract. Model tool arguments cannot
create another identity to bypass the fence. Saved review remains inspectable.
"""
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from src.lib.validation_history import store, selections
from src.models.sql.validation_review import FlowReviewStep
from . import admission


def _owned(db, scope, step_id, *, lock=True):
    store._authorize(db, scope)
    query = select(FlowReviewStep).where(FlowReviewStep.id == step_id)
    if lock:
        query = query.with_for_update()
    row = db.scalar(query)
    if row is None or (row.owner_id, row.document_id, row.session_id) != (
            scope.owner_id, scope.document_id, scope.session_id):
        raise store.HistoryAccessError("Flow review step is outside this operation")
    return row


def snapshot(row):
    return {"step_id": row.id, "state": row.state, "artifact_id": row.artifact_id,
            "episode_id": row.episode_id, "outcome": row.outcome}


def claim(scope, *, run_id, node_id, contract):
    if not run_id or not node_id:
        raise ValueError("Flow review requires durable run and node identities")
    identity = {"owner": scope.owner_id, "document": scope.document_id,
                "session": scope.session_id, "run": run_id, "node": node_id}
    step_id = store.canonical(identity)[0]
    contract = store.canonical(contract)[1]
    with store.SessionLocal.begin() as db:
        store._authorize(db, scope)
        created = db.execute(insert(FlowReviewStep).values(id=step_id,
            owner_id=scope.owner_id, document_id=scope.document_id, session_id=scope.session_id,
            run_id=run_id, node_id=node_id, contract=contract, state="started")
            .on_conflict_do_nothing().returning(FlowReviewStep.id)).scalar_one_or_none()
        row = _owned(db, scope, step_id)
        if row.contract != contract:
            raise store.HistoryConflict("Flow step configuration changed within the same run")
        return {**snapshot(row), "claimed": created is not None}


def bind(scope, step_id, *, artifact_id, episode_id, history_scope=None):
    """Called after immutable source and episode admission, before provider work."""
    with store.SessionLocal.begin() as db:
        bound_scope = history_scope or scope
        if (bound_scope.owner_id, bound_scope.document_id) != (scope.owner_id, scope.document_id):
            raise store.HistoryAccessError("Flow source belongs to another owner or document")
        ep, _ = admission.lock_episode(db, bound_scope, episode_id)
        if ep.artifact_id != artifact_id:
            raise store.HistoryConflict("Flow review episode belongs to another extraction")
        row = _owned(db, scope, step_id)
        if row.state == "review_pending" and (row.artifact_id, row.episode_id) == (artifact_id, episode_id):
            return snapshot(row)
        if row.state != "started":
            raise store.HistoryConflict("Flow extraction has already been bound or stopped")
        row.artifact_id, row.episode_id, row.state = artifact_id, episode_id, "review_pending"
        return snapshot(row)


def _bound_episode(db, scope, step_id):
    """The step's saved episode, locked under the owner's history scope of its source."""
    saved = _owned(db, scope, step_id, lock=False)
    if saved.episode_id is None:
        raise store.HistoryConflict("Flow step has no saved review episode")
    from src.models.sql.validation_history import ValidationArtifact
    artifact = db.get(ValidationArtifact, saved.artifact_id)
    if artifact is None or (artifact.owner_id, artifact.document_id) != (scope.owner_id, scope.document_id):
        raise store.HistoryAccessError("Flow source is unavailable")
    bound_scope = store.HistoryScope(scope.owner_id, scope.document_id, artifact.session_id)
    ep, _ = admission.lock_episode(db, bound_scope, saved.episode_id, require_open=False)
    return bound_scope, ep


def finish(scope, step_id, *, outcome, set_aside=None):
    """No downstream completion on model prose: require current recorded readiness.

    ``set_aside`` is the review outcome's structural flag
    (``continuation.set_aside``, ALL-1561). A not-ready manifest completes the
    step only when the flag was read from exactly this manifest. The same
    transaction then records one ``review_set_aside`` fact: bookkeeping, not a
    decision. The records keep their pending decisions, the manifest keeps its
    blockers, and the fact exists only for a step that completed.
    """
    with store.SessionLocal.begin() as db:
        bound_scope, ep = _bound_episode(db, scope, step_id)
        row = _owned(db, scope, step_id)
        if row.state == "completed":
            if row.outcome != outcome:
                raise store.HistoryConflict("Flow step already has a different result")
            return snapshot(row)
        if row.state != "review_pending":
            raise store.HistoryConflict("Stopped flow steps cannot complete automatically")
        if set_aside is not None:
            selections._lock_artifact(db, bound_scope, ep.artifact_id, write=True)
        manifest = selections.manifest_in_transaction(db, bound_scope, ep.artifact_id, episode_id=ep.id)
        if not manifest["ready"]:
            if set_aside is None or manifest["sha256"] != set_aside["manifest_sha256"]:
                raise admission.ReviewStopped("flow_review_pending", resource=step_id)
            from .audit import append_fact
            append_fact(db, ep.artifact_id,
                operation_id=f"review-set-aside:{step_id}:{manifest['sha256']}",
                kind=selections.SET_ASIDE_KIND, parent_id=ep.id,
                body={"episode_id": ep.id, "step_id": step_id, "manifest_sha256": manifest["sha256"],
                      "record_target_ids": set_aside["record_target_ids"],
                      "unit_target_ids": set_aside["unit_target_ids"],
                      "blockers": manifest["blockers"],
                      # Each blocked unit's decision version, so a later curator
                      # decision on some records leaves the rest covered.
                      "unit_versions": {
                          blocker["target_id"]: store._current(db, ep.artifact_id, blocker["target_id"])["version"]
                          for blocker in manifest["blockers"]}})
        row.state, row.outcome = "completed", store.canonical(outcome)[1]
        return snapshot(row)


def stop(scope, step_id, *, interrupted=False, reason):
    """Keep pending artifacts accessible; recovery cannot re-enter extraction."""
    with store.SessionLocal.begin() as db:
        row = _owned(db, scope, step_id)
        if row.state in {"completed", "failed", "interrupted"}:
            return snapshot(row)
        row.state = "interrupted" if interrupted else "failed"
        row.outcome = {"reason": reason}
        return snapshot(row)

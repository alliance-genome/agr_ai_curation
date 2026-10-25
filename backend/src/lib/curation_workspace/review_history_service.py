"""Curator-facing exact history reads; no model execution or status-fact writes."""

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select

from src.lib.validation_history import store
from src.lib.validation_review.policy import ReviewLimits
from src.lib.validation_review.reads import exact_page
from src.models.sql.validation_history import ValidationFact, ValidationBody
from src.lib.curation_workspace.models import CurationCandidate, DomainEnvelopeModel
from src.schemas.domain_envelope import DomainEnvelope


def candidate_binding(db, candidate_id, *, owner_id):
    candidate = db.get(CurationCandidate, UUID(str(candidate_id)))
    if candidate is None:
        raise HTTPException(404, "Curation candidate unavailable")
    row = (
        db.get(DomainEnvelopeModel, candidate.envelope_id)
        if candidate.envelope_id
        else None
    )
    if row is None:
        raise HTTPException(
            409, "Historical validation history is unavailable for this record"
        )
    envelope = DomainEnvelope.model_validate(row.envelope_json)
    ref = envelope.metadata.get("validation_history")
    if ref is None:
        raise HTTPException(
            409, "Historical validation history is unavailable for this record"
        )
    scope = store.HistoryScope(
        owner_id, str(row.document_id) if row.document_id else None, ref["session_id"]
    )
    store._artifact(db, ref["artifact_id"], scope)
    return candidate, row, envelope, scope, ref["artifact_id"]


def history_page(db, candidate_id, *, owner_id, after=0):
    from src.lib.openai_agents.config import get_validation_history_page_size

    candidate, _, envelope, scope, artifact_id = candidate_binding(
        db, candidate_id, owner_id=owner_id
    )
    targets = record_targets(db, envelope, artifact_id, candidate.object_id)
    artifact = store._artifact(db, artifact_id, scope)
    limit = get_validation_history_page_size()
    kinds = (
        "attempt",
        "completion",
        "decision",
        "curator_value",
        "curator_revalidation_intent",
        "workspace_revalidation_request",
        "extraction_followup_request",
        "extraction_contribution",
        "extraction_source_selection",
        "curator_revalidation_release",
    )
    rows = list(
        db.scalars(
            select(ValidationFact)
            .where(
                ValidationFact.artifact_id == artifact_id,
                ValidationFact.kind.in_(kinds),
                ValidationFact.sequence > after,
            )
            .order_by(ValidationFact.sequence)
            .limit(limit + 1)
        )
    )
    latest_episode = db.scalar(
        select(ValidationFact.id)
        .where(
            ValidationFact.artifact_id == artifact_id,
            ValidationFact.kind == "review_episode",
        )
        .order_by(ValidationFact.sequence.desc())
        .limit(1)
    )
    entries = []
    for fact in rows[:limit]:
        body = store._content(db, fact)
        decisions = (
            [
                entry
                for entry in body.get("entries", [])
                if entry["target_id"] in targets
            ]
            if fact.kind in {"decision", "workspace_revalidation_request"}
            else []
        )
        choices = []
        if fact.kind == "completion" and body.get("state") == "completed":
            attempt = store._fact(db, artifact_id, fact.parent_id, kind="attempt")
            frozen = store._content(db, attempt)
            choices = [
                {
                    "target_id": frozen["input"]["target_id"],
                    "decision_unit_id": unit,
                    "expected_version": store._current(db, artifact_id, unit)[
                        "version"
                    ],
                    "logical_attempt_id": attempt.id,
                }
                for unit in frozen["coverage"]
                if unit in targets
            ]
        visible = (
            fact.target_id in targets
            or bool(decisions)
            or bool(choices)
            or body.get("object_id") == candidate.object_id
            or bool(set(body.get("entry_ids", ())) & targets)
        )
        if fact.kind == "completion":
            visible = store._fact(db, artifact_id, fact.parent_id).target_id in targets
        if not visible:
            continue
        result = body.get("result") if fact.kind == "completion" else None
        entries.append(
            {
                "id": fact.id,
                "kind": fact.kind,
                "sequence": fact.sequence,
                "target_id": fact.target_id,
                "parent_id": fact.parent_id,
                "sha256": fact.body_sha,
                "created_at": fact.created_at.isoformat(),
                "actor": body.get("actor"),
                "reason": body.get("reason"),
                "state": body.get("state"),
                "decisions": decisions,
                "choices": choices,
                "scientific_status": result.get("status")
                if isinstance(result, dict)
                else None,
            }
        )
    return {
        "entries": entries,
        "next_after": rows[limit - 1].sequence if len(rows) > limit else None,
        "baseline": {"id": "baseline", "sha256": artifact.baseline_sha},
        "episode_id": latest_episode,
    }


def history_detail(db, candidate_id, *, owner_id, fact_id, sha256, offset=0):
    _, _, _, scope, artifact_id = candidate_binding(db, candidate_id, owner_id=owner_id)
    if fact_id == "baseline":
        artifact = store._artifact(db, artifact_id, scope)
        if artifact.baseline_sha != sha256:
            raise store.HistoryConflict("Original extraction reference changed")
        body = db.get(ValidationBody, sha256).content
    else:
        fact = store._fact(db, artifact_id, fact_id)
        if fact.body_sha != sha256:
            raise store.HistoryConflict(
                "Historical reference does not match its saved version"
            )
        body = store._content(db, fact)
    return exact_page(
        body,
        fact_id=fact_id,
        sha256=sha256,
        offset=offset,
        limit=ReviewLimits.configured().packet_bytes,
    )


def decide(candidate_id, *, owner_id, request, groups):
    from src.lib.validation_review.decisions import ReviewContext, record_decisions
    from .review_operations import checkpoint

    with store.SessionLocal() as db:
        candidate, _, envelope, scope, artifact_id = candidate_binding(
            db, candidate_id, owner_id=owner_id
        )
        targets = record_targets(db, envelope, artifact_id, candidate.object_id)
        if any(entry.decision_unit_id not in targets for entry in request.entries):
            raise store.HistoryAccessError("Decision is outside this workspace record")
        latest = db.scalar(
            select(ValidationFact.id)
            .where(
                ValidationFact.artifact_id == artifact_id,
                ValidationFact.kind == "review_episode",
            )
            .order_by(ValidationFact.sequence.desc())
            .limit(1)
        )
        if latest != str(request.episode_id):
            raise store.HistoryConflict(
                "A new review round has started; refresh before deciding"
            )
    item = {"candidate_id": str(candidate_id), "episode_id": latest}
    result = record_decisions(
        ReviewContext(scope, latest, owner_id, actor_type="curator"),
        checkpoint_request=checkpoint(owner_id, item, groups),
        operation_id=str(request.request_id),
        entries=[entry.model_dump(mode="json") for entry in request.entries],
    )
    from .review_operations import refresh_candidate_snapshot

    with store.SessionLocal.begin() as db:
        refresh_candidate_snapshot(db, candidate_id, owner_id=owner_id, groups=groups)
    return result


def record_targets(db, envelope, artifact_id, object_id):
    roots = [
        obj.metadata["validation_history"]["target_id"]
        for obj in envelope.extracted_objects
        if object_id in (obj.object_id, obj.pending_ref_id)
    ]
    if len(roots) != 1:
        raise store.HistoryConflict("Record has no unique current history identity")
    descendants = (
        select(ValidationFact.id)
        .where(ValidationFact.artifact_id == artifact_id, ValidationFact.id == roots[0])
        .cte(recursive=True)
    )
    descendants = descendants.union_all(
        select(ValidationFact.id)
        .join(descendants, ValidationFact.parent_id == descendants.c.id)
        .where(
            ValidationFact.artifact_id == artifact_id, ValidationFact.kind == "target"
        )
    )
    return set(db.scalars(select(descendants.c.id)))

"""Short committed transactions for immutable validation facts.

No caller-owned Session crosses this boundary. Model execution happens between
begin_execution and complete_attempt, never inside a history transaction.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from src.models.sql.database import SessionLocal
from src.models.sql.validation_history import (
    ValidationArtifact,
    ValidationBody,
    ValidationFact,
    ValidationHead,
)
from src.models.sql.user import User
from src.models.sql.pdf_document import PDFDocument
from src.schemas.validation_history import (
    HistoryActor,
    HistoryDecision,
    FrozenValidationInput,
)


class HistoryConflict(ValueError):
    """Conflicting immutable identity or stale current version."""


class SelectionNotReady(HistoryConflict):
    """Expected pending/stale selection state; not an operational failure."""


class HistoryRecordingError(RuntimeError):
    """Audit capture failed before model execution; not a validator answer."""


class HistoryAccessError(ValueError):
    """History is not accessible in the authenticated source scope."""


@dataclass(frozen=True)
class HistoryScope:
    owner_id: str
    document_id: str | None
    session_id: str

    def __post_init__(self):
        if not all(
            isinstance(v, str) and v.strip() for v in (self.owner_id, self.session_id)
        ):
            raise ValueError(
                "Validation history requires an authenticated document/session scope"
            )
        if self.document_id is not None:
            UUID(self.document_id)


def canonical(value: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    encoded = json.dumps(
        value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False
    )
    return sha256(encoded.encode()).hexdigest(), json.loads(encoded)


def _authorize(db, scope: HistoryScope):
    user = db.scalar(select(User.id).where(User.auth_sub == scope.owner_id))
    if user is None:
        raise HistoryAccessError("Validation history owner is not accessible")
    if scope.document_id is None:
        return None
    document = (
        None
        if user is None
        else db.scalar(
            select(PDFDocument)
            .where(
                PDFDocument.id == UUID(scope.document_id), PDFDocument.user_id == user
            )
            .with_for_update(read=True)
        )
    )
    # History is an internal owned read/append surface, not a library edit or
    # reprocessing route. Frozen benchmark copies retain their normal owner.
    if document is None:
        raise HistoryAccessError("Validation history source is not accessible")
    return document


def _document_retirement(db, artifact_id):
    return db.scalar(
        select(ValidationFact).where(
            ValidationFact.artifact_id == artifact_id,
            ValidationFact.operation_id == "document-retired",
        )
    )


def _require_live_artifact(db, artifact_id):
    if _document_retirement(db, artifact_id) is not None:
        raise HistoryConflict(
            "Document history is retired; new validation or selection is not allowed"
        )


def _artifact(db, artifact_id: str, scope: HistoryScope) -> ValidationArtifact:
    row = db.get(ValidationArtifact, artifact_id)
    if row is None or (row.owner_id, row.document_id, row.session_id) != (
        scope.owner_id,
        scope.document_id,
        scope.session_id,
    ):
        raise HistoryAccessError("Validation history artifact is not accessible")
    user_id = db.scalar(select(User.id).where(User.auth_sub == scope.owner_id))
    if user_id is None:
        raise HistoryAccessError("Validation history owner is not accessible")
    if scope.document_id is not None:
        document = db.get(PDFDocument, UUID(scope.document_id))
        if document is not None:
            if document.user_id != user_id:
                raise HistoryAccessError("Validation history source is not accessible")
        elif _document_retirement(db, artifact_id) is None:
            raise HistoryAccessError("Validation history source is not accessible")
    return row


def retire_document_history(db, document_id: UUID) -> int:
    """Retire audit artifacts in the same transaction as document cleanup.

    Preserve source/attempt/decision bodies. The trusted cleanup caller already
    checks document ownership; the actor is derived from that actual source row.
    """
    document = db.scalar(
        select(PDFDocument).where(PDFDocument.id == document_id).with_for_update()
    )
    if document is None:
        return 0
    owner = db.get(User, document.user_id)
    if owner is None:
        raise HistoryAccessError("Document retirement requires its recorded owner")
    artifacts = list(
        db.scalars(
            select(ValidationArtifact)
            .where(ValidationArtifact.document_id == str(document_id))
            .order_by(ValidationArtifact.id)
            .with_for_update()
        )
    )
    for artifact in artifacts:
        if artifact.owner_id != owner.auth_sub:
            raise HistoryAccessError("Document history ownership mismatch")
        _append(
            db,
            artifact.id,
            operation_id="document-retired",
            kind="document_retirement",
            body={
                "document_id": str(document_id),
                "actor": {"type": "curator", "id": owner.auth_sub},
                "reason": "Source document removed from the active library",
                "file_hash": document.file_hash,
            },
        )
    return len(artifacts)


def read_archived_checkpoint(scope, artifact_id, *, envelope_id, revision):
    from src.models.sql.validation_history import ValidationCheckpoint
    from .checkpoints import read_checkpoint_body

    with SessionLocal() as db:
        artifact = _artifact(db, artifact_id, scope)
        baseline = db.get(ValidationBody, artifact.baseline_sha).content
        if baseline["normalized"].get("envelope_id") != envelope_id:
            raise HistoryAccessError("Checkpoint does not belong to this artifact")
        checkpoint = db.get(ValidationCheckpoint, (envelope_id, revision))
        if checkpoint is None or checkpoint.document_id != str(scope.document_id):
            raise HistoryAccessError("Checkpoint is not accessible")
        return read_checkpoint_body(db, checkpoint.body_sha)


def _body(db, value: dict[str, Any]) -> str:
    digest, snapshot = canonical(value)
    db.execute(
        insert(ValidationBody)
        .values(sha256=digest, content=snapshot)
        .on_conflict_do_nothing()
    )
    if db.get(ValidationBody, digest).content != snapshot:
        raise HistoryConflict("History content identity conflict")
    return digest


def _fact(
    db, artifact_id: str, fact_id: str, *, kind: str | None = None
) -> ValidationFact:
    row = db.scalar(
        select(ValidationFact).where(
            ValidationFact.artifact_id == artifact_id, ValidationFact.id == fact_id
        )
    )
    if row is None or (kind is not None and row.kind != kind):
        raise HistoryConflict("History reference has incompatible scope or type")
    return row


def _content(db, fact: ValidationFact) -> dict:
    return deepcopy(db.get(ValidationBody, fact.body_sha).content)


def _append(
    db,
    artifact_id: str,
    *,
    operation_id: str,
    kind: str,
    body: dict,
    target_id: str | None = None,
    parent_id: str | None = None,
    fact_id: str | None = None,
) -> ValidationFact:
    if not operation_id.strip():
        raise ValueError("History operation identity is required")
    if target_id is not None:
        _fact(db, artifact_id, target_id, kind="target")
    if parent_id is not None:
        _fact(db, artifact_id, parent_id)
    digest = _body(db, body)
    db.execute(
        insert(ValidationFact)
        .values(
            artifact_id=artifact_id,
            id=fact_id or str(uuid4()),
            operation_id=operation_id,
            kind=kind,
            target_id=target_id,
            parent_id=parent_id,
            body_sha=digest,
        )
        .on_conflict_do_nothing(constraint="uq_validation_fact_operation")
    )
    row = db.scalar(
        select(ValidationFact).where(
            ValidationFact.artifact_id == artifact_id,
            ValidationFact.operation_id == operation_id,
        )
    )
    if fact_id is not None and row.id != fact_id:
        raise HistoryConflict("History identity conflict")
    if (row.kind, row.target_id, row.parent_id, row.body_sha) != (
        kind,
        target_id,
        parent_id,
        digest,
    ):
        raise HistoryConflict(
            "History operation was reused with different immutable content"
        )
    return row


def capture_baseline(
    scope: HistoryScope,
    *,
    producer_id: str,
    subject_kind: Literal["extracted", "curator_authored", "historical"],
    typed: dict | None,
    normalized: dict,
    provenance: dict,
) -> str:
    if (
        subject_kind not in {"extracted", "curator_authored", "historical"}
        or not producer_id.strip()
    ):
        raise ValueError("Explicit subject kind and producer identity are required")
    if (subject_kind == "historical") != (typed is None):
        raise ValueError(
            "Historical observations cannot claim an attested extraction original"
        )
    with SessionLocal.begin() as db:
        document = _authorize(db, scope)
        digest = _body(db, {"typed": typed, "normalized": normalized})
        _, pinned_provenance = canonical(
            {
                **provenance,
                "source_document": {
                    "id": str(document.id),
                    "file_hash": document.file_hash,
                    "viewer_mode": document.viewer_mode,
                }
                if document is not None
                else None,
            }
        )
        db.execute(
            insert(ValidationArtifact)
            .values(
                id=str(uuid4()),
                owner_id=scope.owner_id,
                document_id=scope.document_id,
                session_id=scope.session_id,
                producer_id=producer_id,
                subject_kind=subject_kind,
                baseline_sha=digest,
                provenance=pinned_provenance,
            )
            .on_conflict_do_nothing(constraint="uq_validation_producer")
        )
        row = db.scalar(
            select(ValidationArtifact).where(
                ValidationArtifact.owner_id == scope.owner_id,
                ValidationArtifact.session_id == scope.session_id,
                ValidationArtifact.producer_id == producer_id,
            )
        )
        if (row.document_id, row.subject_kind, row.baseline_sha, row.provenance) != (
            scope.document_id,
            subject_kind,
            digest,
            pinned_provenance,
        ):
            raise HistoryConflict(
                "Producer identity was reused for a different baseline"
            )
        return row.id


def read_baseline(scope: HistoryScope, artifact_id: str) -> dict:
    with SessionLocal() as db:
        row = _artifact(db, artifact_id, scope)
        return {
            "artifact_id": row.id,
            "retired": _document_retirement(db, row.id) is not None,
            "subject_kind": row.subject_kind,
            "original_available": row.subject_kind != "historical",
            "provenance": deepcopy(row.provenance),
            "sha256": row.baseline_sha,
            **deepcopy(db.get(ValidationBody, row.baseline_sha).content),
        }


def register_target(
    scope: HistoryScope,
    artifact_id: str,
    *,
    identity: str,
    original: dict,
    parent_target_id: str | None = None,
) -> str:
    """Identity is assigned at capture, never derived from mutable label/index content."""
    with SessionLocal.begin() as db:
        _artifact(db, artifact_id, scope)
        db.scalar(
            select(ValidationArtifact.id)
            .where(ValidationArtifact.id == artifact_id)
            .with_for_update()
        )
        _require_live_artifact(db, artifact_id)
        return register_target_in_transaction(
            db,
            artifact_id,
            identity=identity,
            original=original,
            parent_target_id=parent_target_id,
        )


def register_target_in_transaction(
    db, artifact_id, *, identity, original, parent_target_id=None
):
    if not identity.strip():
        raise ValueError("Stable target identity is required")
    return _append(
        db,
        artifact_id,
        operation_id="target:" + identity,
        kind="target",
        body=original,
        parent_id=parent_target_id,
    ).id


def _prerequisites_current(db, scope, references):
    for reference in references:
        aid, tid = reference["artifact_id"], reference["target_id"]
        _artifact(db, aid, scope)
        _require_active_target(db, aid, tid)
        selection = _fact(db, aid, reference["selection_id"], kind="selection")
        pinned, current = _content(db, selection), _current(db, aid, tid)
        if (
            selection.target_id != tid
            or pinned["selected"] is None
            or pinned["action"] != "accept"
            or pinned["selection_generation"] != reference["selection_generation"]
            or current["selection_generation"] != reference["selection_generation"]
            or current["selected"] != pinned["selected"]
            or _content(db, selection).get("lineage_generations")
            != _target_lineage(db, aid, tid)
        ):
            raise HistoryConflict("Prerequisite selection is stale or ineligible")

        if pinned["selected"] not in {"baseline", "curator"}:
            completion = _fact(db, aid, pinned["selected"], kind="completion")
            attempt = _fact(db, aid, completion.parent_id, kind="attempt")
            if not _attempt_is_current(db, scope, aid, _content(db, attempt), tid):
                raise HistoryConflict("Prerequisite inputs are no longer current")


def _attempt_is_current(db, scope, artifact_id, attempt_content, target_id=None):
    if _document_retirement(db, artifact_id) is not None:
        return False
    from src.lib.validation_review.source_inputs import current as source_input_current
    if not source_input_current(db, artifact_id, attempt_content["input"]):
        return False
    for tid, generations in attempt_content["coverage_lineages"].items():
        if target_id is not None and tid != target_id:
            continue
        if generations != _target_lineage(db, artifact_id, tid):
            return False
    try:
        _prerequisites_current(db, scope, attempt_content["input"]["prerequisites"])
    except HistoryConflict:
        return False
    return True


def begin_execution(
    scope: HistoryScope,
    artifact_id: str,
    *,
    operation_id: str,
    receipt: dict,
    requests: list[dict],
) -> dict:
    """Freeze every logical target, linking shared execution without false calls.

    requests entries: target_id, request, binding_id; receipt pins actual runner
    and model-facing representation. Initial/reconsidered calls have distinct
    operation IDs; redelivery of the same operation is idempotent.
    """
    with SessionLocal.begin() as db:
        return begin_execution_in_transaction(db, scope, artifact_id,
            operation_id=operation_id, receipt=receipt, requests=requests)


def begin_execution_in_transaction(db, scope, artifact_id, *, operation_id, receipt, requests):
    """Freeze attempts under the caller's admission/outbox transaction."""
    requests = [
        FrozenValidationInput.model_validate(r).model_dump(mode="json")
        for r in requests
    ]
    if not requests or len({r["target_id"] for r in requests}) != len(requests):
        raise ValueError("Execution must explicitly cover distinct targets")
    _artifact(db, artifact_id, scope)
    # Share the structural lock with retirement/selection so a dispatch
    # cannot freeze a mixture of versions from a grouped decision.
    db.scalar(
        select(ValidationArtifact.id)
        .where(ValidationArtifact.id == artifact_id)
        .with_for_update()
    )
    existing = db.scalar(
        select(ValidationFact).where(
            ValidationFact.artifact_id == artifact_id,
            ValidationFact.operation_id == "execution:" + operation_id,
        )
    )
    execution_body = {
        "receipt": receipt,
        "targets": [r["target_id"] for r in requests],
    }
    if existing is not None:
        if _content(db, existing) != canonical(execution_body)[1]:
            raise HistoryConflict("Execution identity conflict")
        attempts = []
        for request in requests:
            row = db.scalar(
                select(ValidationFact).where(
                    ValidationFact.artifact_id == artifact_id,
                    ValidationFact.operation_id
                    == "attempt:" + operation_id + ":" + request["target_id"],
                )
            )
            if row is None or _content(db, row)["input"] != canonical(request)[1]:
                raise HistoryConflict("Execution request identity conflict")
            attempts.append(row.id)
        # Redelivery reads the original frozen versions even after retirement.
        return {"execution_id": existing.id, "attempt_ids": attempts}
    for request in requests:
        from src.lib.validation_review.source_inputs import current as source_input_current
        if not source_input_current(db, artifact_id, request):
            raise HistoryConflict("Validator source input version is stale")
        if request["review_episode_id"] is not None:
            episode = _fact(db, artifact_id, request["review_episode_id"], kind="review_episode")
            from .episodes import latest_effective_episode
            latest = latest_effective_episode(db, artifact_id)
            if latest is None or episode.id != latest.id:
                raise HistoryConflict("Validation dispatch belongs to an obsolete review episode")
        _require_active_target(db, artifact_id, request["target_id"])
        # Prerequisites stay within the same artifact; one artifact lock makes
        # snapshotting and grouped selection updates atomic without lock cycles.
        if any(p["artifact_id"] != artifact_id for p in request["prerequisites"]):
            raise HistoryConflict("Prerequisites must belong to the same artifact")
        _prerequisites_current(db, scope, request["prerequisites"])
        for fact_id in request["available_prior_completions"]:
            _fact(db, artifact_id, fact_id, kind="completion")
    execution = _append(
        db,
        artifact_id,
        operation_id="execution:" + operation_id,
        kind="execution",
        body=execution_body,
    )
    attempts = []
    for request in requests:
        coverage = []
        for unit in request["decision_units"]:
            unit_target = _append(
                db,
                artifact_id,
                operation_id="unit:" + request["target_id"] + ":" + unit["key"],
                kind="target",
                parent_id=request["target_id"],
                body={"decision_unit": unit, "binding_id": request["binding_id"]},
            )
            _require_active_target(db, artifact_id, unit_target.id)
            coverage.append(unit_target.id)
        if not coverage:
            coverage = [request["target_id"]]
        attempt = _append(
            db,
            artifact_id,
            operation_id="attempt:" + operation_id + ":" + request["target_id"],
            kind="attempt",
            target_id=request["target_id"],
            parent_id=execution.id,
            body={
                "input": request,
                "coverage": coverage,
                "coverage_lineages": {
                    tid: _target_lineage(db, artifact_id, tid) for tid in coverage
                },
                "budget_scope_id": canonical(
                    {"artifact": artifact_id, "target": request["target_id"]}
                )[0],
                "target_version": _current(db, artifact_id, request["target_id"])[
                    "version"
                ],
                "lineage_versions": _target_lineage(
                    db, artifact_id, request["target_id"]
                ),
            },
        )
        attempts.append(attempt.id)
    return {"execution_id": execution.id, "attempt_ids": attempts}


def complete_attempt(
    scope: HistoryScope,
    artifact_id: str,
    attempt_id: str,
    *,
    state: Literal["completed", "incomplete", "failed", "interrupted", "unknown"],
    result: dict | None,
    diagnostics: dict,
    candidate: dict | None = None,
) -> str:
    if state not in {"completed", "incomplete", "failed", "interrupted", "unknown"}:
        raise ValueError("Unknown attempt execution state")
    if (state in {"completed", "incomplete"}) != (result is not None):
        raise ValueError("Only actual validator answers have a result body")
    with SessionLocal.begin() as db:
        _artifact(db, artifact_id, scope)
        attempt = _fact(db, artifact_id, attempt_id, kind="attempt")
        completion = _append(
            db,
            artifact_id,
            operation_id="completion:" + attempt_id,
            kind="completion",
            target_id=attempt.target_id,
            parent_id=attempt.id,
            body={"state": state, "result": result, "diagnostics": diagnostics},
        )
        if candidate is not None:
            if result is None:
                raise HistoryConflict("A failed attempt cannot have a candidate recipe")
            frozen = _content(db, attempt)["input"]["request"]
            from .candidates import validate_candidate_request
            validate_candidate_request(frozen, candidate["request"])
            reference = {
                "artifact_id": artifact_id, "session_id": scope.session_id,
                "attempt_id": attempt.id, "fact_id": completion.id,
                "body_sha256": completion.body_sha, "execution_state": state,
                "sequence": completion.sequence, "created_at": completion.created_at.isoformat(),
            }
            _append(db, artifact_id, operation_id="candidate:" + completion.id,
                kind="candidate", parent_id=completion.id,
                body={**candidate, "history_ref": reference})
        if not _attempt_is_current(db, scope, artifact_id, _content(db, attempt)):
            _append(
                db,
                artifact_id,
                operation_id="stale-completion:" + attempt_id,
                kind="staleness",
                target_id=attempt.target_id,
                parent_id=completion.id,
                body={"reason": "target_lineage_changed_after_dispatch"},
            )
        return completion.id


def record_scheduling(
    scope: HistoryScope,
    artifact_id: str,
    *,
    operation_id: str,
    outcome: str,
    details: dict,
) -> str:
    if outcome not in {
        "no_validator",
        "zero_item",
        "opted_out",
        "inactive",
        "missing_input",
        "unsupported_preflight",
        "flow_skipped",
        "not_validatable",
        "no_contract",
        "ineligible",
        "awaiting_review",
    }:
        raise ValueError("Unknown scheduling outcome")
    with SessionLocal.begin() as db:
        _artifact(db, artifact_id, scope)
        return _append(
            db,
            artifact_id,
            operation_id=operation_id,
            kind="scheduling",
            body={"outcome": outcome, "details": details},
        ).id


def _current(db, artifact_id: str, target_id: str) -> dict:
    head = db.get(ValidationHead, (artifact_id, target_id))
    if head is None:
        return {
            "version": 0,
            "lifecycle_generation": 0,
            "selection_generation": 0,
            "active": True,
            "selected": None,
            "review": "pending",
        }
    return {
        **_content(db, _fact(db, artifact_id, head.fact_id)),
        "version": head.version,
    }


def _target_lineage(db, artifact_id: str, target_id: str) -> dict[str, int]:
    versions = {}
    while target_id is not None:
        target = _fact(db, artifact_id, target_id, kind="target")
        versions[target_id] = _current(db, artifact_id, target_id)[
            "lifecycle_generation"
        ]
        target_id = target.parent_id
    return versions


def _require_active_target(db, artifact_id: str, target_id: str):
    _require_live_artifact(db, artifact_id)
    for ancestor in _target_lineage(db, artifact_id, target_id):
        if not _current(db, artifact_id, ancestor)["active"]:
            raise HistoryConflict(
                "Retired target or ancestor cannot be dispatched or selected"
            )


def _has_baseline_subject(db, artifact_id: str, target_id: str) -> bool:
    """Only targets registered at capture can refer to the extraction baseline."""
    while target_id is not None:
        fact = _fact(db, artifact_id, target_id, kind="target")
        if fact.operation_id.startswith("replacement-target:"):
            return False
        content = _content(db, fact)
        if "origin" in content:
            return content["origin"].get("baseline_target") is True
        if content.get("baseline_target") is True:
            return True
        target_id = fact.parent_id
    return False


def _has_curator_subject(db, artifact_id: str, target_id: str) -> bool:
    """A curator-authored source is explicit provenance, not a model judgment."""
    while target_id is not None:
        fact = _fact(db, artifact_id, target_id, kind="target")
        if _content(db, fact).get("origin", {}).get("kind") == "curator_authored":
            return True
        target_id = fact.parent_id
    return False


def apply_decisions(
    scope: HistoryScope,
    artifact_id: str,
    *,
    operation_id: str,
    actor: dict,
    entries: list[dict],
) -> list[str]:
    with SessionLocal.begin() as db:
        return apply_decisions_in_transaction(
            db,
            scope,
            artifact_id,
            operation_id=operation_id,
            actor=actor,
            entries=entries,
        )


def apply_decisions_in_transaction(
    db,
    scope: HistoryScope,
    artifact_id: str,
    *,
    operation_id: str,
    actor: dict,
    entries: list[dict],
) -> list[str]:
    """Atomic structural CAS, not a scientific decision engine.

    Trusted caller supplies actor. Entries name target, expected_version, action,
    reason and (for accept/reject) an exact completion or baseline subject.
    Retire/restore/revoke/reject never delete prior facts. Restore leaves review
    pending; a late completion is recorded but cannot reactivate/select a target.
    """
    actor = HistoryActor.model_validate(actor).model_dump(mode="json")
    entries = [
        HistoryDecision.model_validate(e).model_dump(mode="json", exclude_none=True)
        for e in entries
    ]
    if not entries or len({e["target_id"] for e in entries}) != len(entries):
        raise ValueError("Decision must explicitly cover distinct targets")
    artifact = _artifact(db, artifact_id, scope)
    # Serialize structural updates only, never calls. Also protects empty heads.
    db.scalar(
        select(ValidationArtifact.id)
        .where(ValidationArtifact.id == artifact.id)
        .with_for_update()
    )
    _require_live_artifact(db, artifact_id)
    operation = db.scalar(
        select(ValidationFact).where(
            ValidationFact.artifact_id == artifact_id,
            ValidationFact.operation_id == "decision:" + operation_id,
        )
    )
    body = {"actor": actor, "entries": entries}
    if operation is not None:
        if _content(db, operation) != canonical(body)[1]:
            raise HistoryConflict("Decision identity conflict")
        return [
            f.id
            for f in db.scalars(
                select(ValidationFact)
                .where(
                    ValidationFact.artifact_id == artifact_id,
                    ValidationFact.parent_id == operation.id,
                    ValidationFact.kind == "selection",
                )
                .order_by(ValidationFact.sequence)
            )
        ]
    decision = _append(
        db,
        artifact_id,
        operation_id="decision:" + operation_id,
        kind="decision",
        body=body,
    )
    versions = []
    for entry in entries:
        target = _fact(db, artifact_id, entry["target_id"], kind="target")
        state = _current(db, artifact_id, target.id)
        if state["version"] != entry["expected_version"]:
            raise HistoryConflict("Target version has changed")
        action = entry["action"]
        if action not in {
            "accept",
            "reject",
            "defer",
            "acknowledge_failure",
            "retire",
            "restore",
            "revoke",
            "replace",
            "invalidate",
        } or not isinstance(entry["reason"], str):
            raise ValueError("Explicit decision action and reason are required")
        if not state["active"] and action != "restore":
            raise HistoryConflict("Retired target requires explicit restoration")
        if action == "restore" and state["active"]:
            raise HistoryConflict("Only a retired target can be restored")
        selected = state["selected"]
        review = state["review"]
        active = state["active"]
        if action == "accept":
            _require_active_target(db, artifact_id, target.id)
        if action in {"accept", "reject", "acknowledge_failure"} or (action == "defer" and entry.get("subject")):
            subject = entry["subject"]
            uncertain_attempt = False
            if action == "acknowledge_failure" and subject not in {"baseline", "curator"}:
                named = _fact(db, artifact_id, subject)
                uncertain_attempt = named.kind == "attempt"
            if uncertain_attempt:
                if target.id not in _content(db, named)["coverage"] or not db.scalar(select(ValidationFact.id).where(
                    ValidationFact.artifact_id == artifact_id, ValidationFact.kind == "review_uncertainty",
                    ValidationFact.parent_id == named.id).limit(1)):
                    raise HistoryConflict("Attempt has no recorded operational uncertainty for this target")
            elif subject == "baseline":
                if artifact.subject_kind == "historical" or not _has_baseline_subject(
                    db, artifact_id, target.id
                ):
                    raise HistoryConflict(
                        "This target has no attested extraction baseline subject"
                    )
            elif subject == "curator":
                if (actor["type"] != "curator" or actor["id"] != scope.owner_id
                        or not _has_curator_subject(db, artifact_id, target.id)):
                    raise HistoryAccessError("Only the owner can select an attested curator source")
            else:
                completion = _fact(db, artifact_id, subject, kind="completion")
                attempt = _fact(db, artifact_id, completion.parent_id, kind="attempt")
                attempt_content = _content(db, attempt)
                if target.id not in attempt_content["coverage"] or (
                    action == "accept"
                    and not _attempt_is_current(
                        db, scope, artifact_id, attempt_content, target.id
                    )
                ):
                    raise HistoryConflict(
                        "Review subject is stale or belongs to another target"
                    )
                if (
                    _content(db, completion)["state"] != "completed"
                    and action == "accept"
                ):
                    raise HistoryConflict(
                        "Incomplete or failed execution cannot finalize a target"
                    )
            if action == "acknowledge_failure" and not uncertain_attempt and (
                subject in {"baseline", "curator"} or _content(db, completion)["state"] == "completed"
            ):
                raise HistoryConflict("Only an operationally incomplete attempt can be acknowledged")
            review = "pending" if action in {"defer", "acknowledge_failure"} else action
            if action == "accept":
                selected = subject
            # Reject records an opinion about this answer. Removing a current
            # selection is a separate explicit revoke decision.
        elif action in {"defer", "revoke", "restore", "invalidate"}:
            review = "pending"
            if action in {"revoke", "restore", "invalidate"}:
                selected = None
            if action == "restore":
                active = True
        elif action in {"retire", "replace"}:
            active = False
        value = {
            "active": active,
            "selected": selected,
            "review": review,
            "action": action,
            "reason": entry["reason"],
            "actor": actor,
            "lineage_generations": _target_lineage(db, artifact_id, target.id),
            "version": state["version"] + 1,
            "lifecycle_generation": state["lifecycle_generation"]
            + (action in {"retire", "restore", "replace", "invalidate"}),
            "selection_generation": state["selection_generation"]
            + (selected != state["selected"]),
        }
        if action == "replace":
            if not isinstance(entry.get("replacement_original"), dict):
                raise ValueError("Replacement requires its own original content")
            replacement = _append(
                db,
                artifact_id,
                operation_id="replacement-target:" + operation_id + ":" + target.id,
                kind="target",
                body=entry["replacement_original"],
                fact_id=entry.get("replacement_target_id"),
                parent_id=target.parent_id,
            )
            _append(
                db,
                artifact_id,
                operation_id="replacement-link:" + operation_id + ":" + target.id,
                kind="replacement",
                target_id=replacement.id,
                parent_id=target.id,
                body={"previous_target_id": target.id, "decision_id": decision.id},
            )
            value["replacement_target_id"] = replacement.id
        fact = _append(
            db,
            artifact_id,
            operation_id="version:" + operation_id + ":" + target.id,
            kind="selection",
            target_id=target.id,
            parent_id=decision.id,
            body=value,
        )
        db.execute(
            insert(ValidationHead)
            .values(
                artifact_id=artifact_id,
                target_id=target.id,
                version=value["version"],
                fact_id=fact.id,
            )
            .on_conflict_do_update(
                index_elements=["artifact_id", "target_id"],
                set_={"version": value["version"], "fact_id": fact.id},
            )
        )
        versions.append(fact.id)
    return versions


def read_fact(scope: HistoryScope, artifact_id: str, fact_id: str) -> dict:
    with SessionLocal() as db:
        _artifact(db, artifact_id, scope)
        fact = _fact(db, artifact_id, fact_id)
        return {
            "id": fact.id,
            "kind": fact.kind,
            "target_id": fact.target_id,
            "parent_id": fact.parent_id,
            "sequence": fact.sequence,
            "created_at": fact.created_at.isoformat(),
            "sha256": fact.body_sha,
            "content": _content(db, fact),
        }


def list_history(
    scope: HistoryScope, artifact_id: str, *, after: int = 0
) -> list[dict]:
    from src.lib.openai_agents.config import get_validation_history_page_size

    with SessionLocal() as db:
        _artifact(db, artifact_id, scope)
        rows = db.scalars(
            select(ValidationFact)
            .where(
                ValidationFact.artifact_id == artifact_id,
                ValidationFact.sequence > after,
            )
            .order_by(ValidationFact.sequence)
            .limit(get_validation_history_page_size())
        )
        return [
            {
                "id": f.id,
                "kind": f.kind,
                "target_id": f.target_id,
                "parent_id": f.parent_id,
                "sequence": f.sequence,
                "sha256": f.body_sha,
            }
            for f in rows
        ]


def completion_for_attempt(
    scope: HistoryScope, artifact_id: str, attempt_id: str
) -> dict:
    with SessionLocal() as db:
        _artifact(db, artifact_id, scope)
        _fact(db, artifact_id, attempt_id, kind="attempt")
        row = db.scalar(
            select(ValidationFact).where(
                ValidationFact.artifact_id == artifact_id,
                ValidationFact.operation_id == "completion:" + attempt_id,
            )
        )
        if row is None:
            raise HistoryConflict("Attempt has no recorded completion")
        return {
            "id": row.id,
            "sha256": row.body_sha,
            "sequence": row.sequence,
            "created_at": row.created_at.isoformat(),
            "state": _content(db, row)["state"],
        }

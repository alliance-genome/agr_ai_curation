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


class HistoryAccessError(ValueError):
    """History is not accessible in the authenticated source scope."""


@dataclass(frozen=True)
class HistoryScope:
    owner_id: str
    document_id: str
    session_id: str

    def __post_init__(self):
        if not all(
            isinstance(v, str) and v.strip()
            for v in (self.owner_id, self.document_id, self.session_id)
        ):
            raise ValueError(
                "Validation history requires an authenticated document/session scope"
            )
        UUID(self.document_id)


def canonical(value: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    encoded = json.dumps(
        value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False
    )
    return sha256(encoded.encode()).hexdigest(), json.loads(encoded)


def _authorize(db, scope: HistoryScope):
    user = db.scalar(select(User.id).where(User.auth_sub == scope.owner_id))
    document = (
        None
        if user is None
        else db.scalar(
            select(PDFDocument).where(
                PDFDocument.id == UUID(scope.document_id), PDFDocument.user_id == user
            )
        )
    )
    # History is an internal owned read/append surface, not a library edit or
    # reprocessing route. Frozen benchmark copies retain their normal owner.
    if document is None:
        raise HistoryAccessError("Validation history source is not accessible")
    return document


def _artifact(db, artifact_id: str, scope: HistoryScope) -> ValidationArtifact:
    _authorize(db, scope)
    row = db.get(ValidationArtifact, artifact_id)
    if row is None or (row.owner_id, row.document_id, row.session_id) != (
        scope.owner_id,
        scope.document_id,
        scope.session_id,
    ):
        raise HistoryAccessError("Validation history artifact is not accessible")
    return row


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
            id=str(uuid4()),
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
                },
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
        if not identity.strip():
            raise ValueError("Stable target identity is required")
        if parent_target_id is not None:
            _fact(db, artifact_id, parent_target_id, kind="target")
        target = _append(
            db,
            artifact_id,
            operation_id="target:" + identity,
            kind="target",
            body=original,
            parent_id=parent_target_id,
        )
        return target.id


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
    requests = [
        FrozenValidationInput.model_validate(r).model_dump(mode="json")
        for r in requests
    ]
    if not requests or len({r["target_id"] for r in requests}) != len(requests):
        raise ValueError("Execution must explicitly cover distinct targets")
    with SessionLocal.begin() as db:
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
            _require_active_target(db, artifact_id, request["target_id"])
        execution = _append(
            db,
            artifact_id,
            operation_id="execution:" + operation_id,
            kind="execution",
            body=execution_body,
        )
        attempts = []
        for request in requests:
            attempt = _append(
                db,
                artifact_id,
                operation_id="attempt:" + operation_id + ":" + request["target_id"],
                kind="attempt",
                target_id=request["target_id"],
                parent_id=execution.id,
                body={
                    "input": request,
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
        if _content(db, attempt)["lineage_versions"] != _target_lineage(
            db, artifact_id, attempt.target_id
        ):
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
        return {"version": 0, "active": True, "selected": None, "review": "pending"}
    return {
        **_content(db, _fact(db, artifact_id, head.fact_id)),
        "version": head.version,
    }


def _target_lineage(db, artifact_id: str, target_id: str) -> dict[str, int]:
    versions = {}
    while target_id is not None:
        target = _fact(db, artifact_id, target_id, kind="target")
        versions[target_id] = _current(db, artifact_id, target_id)["version"]
        target_id = target.parent_id
    return versions


def _require_active_target(db, artifact_id: str, target_id: str):
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
            "retire",
            "restore",
            "revoke",
            "replace",
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
        if action in {"accept", "reject"}:
            subject = entry["subject"]
            if subject == "baseline":
                if artifact.subject_kind == "historical" or not _has_baseline_subject(
                    db, artifact_id, target.id
                ):
                    raise HistoryConflict(
                        "This target has no attested extraction baseline subject"
                    )
            else:
                completion = _fact(db, artifact_id, subject, kind="completion")
                attempt = _fact(db, artifact_id, completion.parent_id, kind="attempt")
                if completion.target_id != target.id or (
                    action == "accept"
                    and _content(db, attempt)["lineage_versions"]
                    != _target_lineage(db, artifact_id, target.id)
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
            review = action
            if action == "accept":
                selected = subject
            elif selected == subject:
                selected = None
        elif action in {"defer", "revoke", "restore"}:
            review = "pending"
            if action in {"revoke", "restore"}:
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
            "version": state["version"] + 1,
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
    from src.lib.openai_agents.config import get_inspect_results_validation_page_size

    with SessionLocal() as db:
        _artifact(db, artifact_id, scope)
        rows = db.scalars(
            select(ValidationFact)
            .where(
                ValidationFact.artifact_id == artifact_id,
                ValidationFact.sequence > after,
            )
            .order_by(ValidationFact.sequence)
            .limit(get_inspect_results_validation_page_size())
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

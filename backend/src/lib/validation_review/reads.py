"""Exact, byte-bounded review snapshots. Continuations never truncate stored data."""

import json
from uuid import uuid4

from sqlalchemy import select

from src.lib.validation_history import store
from .audit import append_fact
from src.lib.validation_history.selections import manifest_in_transaction
from src.models.sql.validation_history import ValidationFact, ValidationBody
from .admission import lock_episode, _allowances, provider_usage
from .policy import TOKEN_USAGE_BUCKETS


def status_reference(scope, episode_id):
    """Return a compact status with an exact reference to arbitrarily large coverage."""
    from .admission import status

    value = status(scope, episode_id)
    with store.SessionLocal.begin() as db:
        ep, _ = lock_episode(db, scope, episode_id, require_open=False)
        fact = append_fact(
            db,
            ep.artifact_id,
            operation_id="review-status:" + str(uuid4()),
            kind="review_status",
            parent_id=ep.id,
            body=value,
        )
        return {
            "state": value["state"],
            "ready": value["ready"],
            "fact_id": fact.id,
            "sha256": fact.body_sha,
        }


def snapshot(scope, episode_id, *, operation_id):
    """Pin mutable review state once; all subsequent reads name its immutable SHA."""
    with store.SessionLocal.begin() as db:
        ep, chain = lock_episode(db, scope, episode_id, require_open=False)
        from .decisions import bounded_text

        bounded_text(operation_id, ep.policy["instruction_chars"], "Operation ID")
        key = "review-packet:" + ep.id + ":" + operation_id
        existing = db.scalar(
            select(ValidationFact).where(
                ValidationFact.artifact_id == ep.artifact_id,
                ValidationFact.operation_id == key,
            )
        )
        if existing is not None:
            if existing.parent_id != episode_id or existing.kind != "review_packet":
                raise store.HistoryConflict("Review packet identity conflict")
            return {"fact_id": existing.id, "sha256": existing.body_sha}
        artifact = store._artifact(db, ep.artifact_id, scope)
        facts = db.scalars(
            select(ValidationFact)
            .where(
                ValidationFact.artifact_id == ep.artifact_id,
                ValidationFact.kind != "review_packet",
            )
            .order_by(ValidationFact.sequence)
        ).all()
        body = {
            "episode_id": ep.id,
            "artifact_id": ep.artifact_id,
            "baseline": {
                "fact_id": "baseline",
                "sha256": artifact.baseline_sha,
                "subject_kind": artifact.subject_kind,
            },
            "manifest": manifest_in_transaction(
                db, scope, ep.artifact_id, episode_id=ep.id
            ),
            "budgets": [
                {
                    "scope_id": row.id,
                    "charged": {key: value for key, value in row.charged.items() if key not in TOKEN_USAGE_BUCKETS},
                    "provider_usage": provider_usage(db, row),
                    "allowances": _allowances(db, row),
                }
                for row in [*chain, ep]
            ],
            "facts": [
                {
                    "id": f.id,
                    "kind": f.kind,
                    "sha256": f.body_sha,
                    "target_id": f.target_id,
                    "parent_id": f.parent_id,
                    "sequence": f.sequence,
                }
                for f in facts
            ],
        }
        fact = append_fact(
            db,
            ep.artifact_id,
            operation_id=key,
            kind="review_packet",
            parent_id=ep.id,
            body=body,
        )
        return {"fact_id": fact.id, "sha256": fact.body_sha}


def _wire_size(value):
    return len(json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode())


def exact_page(body, *, fact_id, sha256, offset, limit):
    """Offset is in canonical ASCII JSON bytes, so it cannot bisect UTF-8."""
    digest, canonical = store.canonical(body)
    if digest != sha256:
        raise store.HistoryConflict("Exact history SHA mismatch")
    text = json.dumps(
        canonical, sort_keys=True, ensure_ascii=True, separators=(",", ":")
    )
    if type(offset) is not int or not 0 <= offset <= len(text):
        raise ValueError("Invalid history continuation offset")

    def page(end):
        return {
            "fact_id": fact_id,
            "sha256": digest,
            "encoding": "canonical-json-ascii",
            "offset": offset,
            "next_offset": end if end < len(text) else None,
            "total_bytes": len(text),
            "content": text[offset:end],
        }

    low, high = offset, min(len(text), offset + limit)
    while low < high:
        mid = (low + high + 1) // 2
        if _wire_size(page(mid)) <= limit:
            low = mid
        else:
            high = mid - 1
    result = page(low)
    if _wire_size(result) > limit or (low == offset and offset < len(text)):
        raise ValueError(
            "Configured packet size cannot carry history continuation metadata"
        )
    return result


def read(scope, episode_id, *, fact_id, sha256, offset=0):
    with store.SessionLocal.begin() as db:
        ep, _ = lock_episode(db, scope, episode_id, require_open=False)
        if fact_id == "baseline":
            artifact = store._artifact(db, ep.artifact_id, scope)
            if artifact.baseline_sha != sha256:
                raise store.HistoryConflict("Baseline SHA mismatch")
            return exact_page(
                db.get(ValidationBody, sha256).content,
                fact_id="baseline",
                sha256=sha256,
                offset=offset,
                limit=ep.policy["packet_bytes"],
            )
        fact = store._fact(db, ep.artifact_id, fact_id)
        if fact.body_sha != sha256:
            raise store.HistoryConflict("History reference SHA mismatch")
        return exact_page(
            store._content(db, fact),
            fact_id=fact.id,
            sha256=sha256,
            offset=offset,
            limit=ep.policy["packet_bytes"],
        )

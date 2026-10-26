"""Exact per-record views of immutable source; never a scientific selection."""

from copy import deepcopy

from sqlalchemy import select

from src.lib.domain_packs.evidence_snapshot import EvidenceSnapshot
from src.lib.validation_history import store
from src.models.sql.validation_history import ValidationBody, ValidationFact
from .admission import lock_episode
from .audit import append_fact


def _object_target(db, artifact_id, target_id):
    seen = set()
    while target_id is not None and target_id not in seen:
        seen.add(target_id)
        target = store._fact(db, artifact_id, target_id, kind="target")
        content = store._content(db, target)
        if "object_ref" in content:
            return target, content
        # A binding names its object explicitly; field/element targets use parents.
        target_id = content["object_target"] if "object_target" in content else target.parent_id
    raise store.HistoryConflict("Target does not identify an original source record")


def snapshot(scope, episode_id, *, target_id):
    """Pin one original normalized object and its exact attached evidence records.

    Retired records remain readable. This grants no selection/revalidation rights.
    Added/replacement objects must use their contribution facts, not an invented
    extraction original. The full typed source remains available by baseline SHA.
    """
    with store.SessionLocal.begin() as db:
        episode, _ = lock_episode(db, scope, episode_id, require_open=False)
        artifact = store._artifact(db, episode.artifact_id, scope)
        target, original = _object_target(db, artifact.id, target_id)
        if not all(store._has_baseline_subject(db, artifact.id, identity)
                   for identity in (target_id, target.id)):
            raise store.HistoryConflict(
                "Record has no baseline original; inspect its exact source contribution history"
            )
        operation_id = "review-record-source:" + artifact.baseline_sha + ":" + target.id
        existing = db.scalar(select(ValidationFact).where(
            ValidationFact.artifact_id == artifact.id,
            ValidationFact.operation_id == operation_id,
        ))
        if existing is not None:
            if existing.kind != "review_record_source" or existing.target_id != target.id:
                raise store.HistoryConflict("Source record reference identity conflict")
            return {"fact_id": existing.id, "sha256": existing.body_sha}
        baseline = db.get(ValidationBody, artifact.baseline_sha).content
        normalized = baseline["normalized"]
        matches = [item for item in normalized["extracted_objects"]
                   if (item.get("object_id") or item.get("pending_ref_id")) == original["object_ref"]]
        if len(matches) != 1:
            raise store.HistoryConflict("Original source record is missing or ambiguous")
        obj = matches[0]
        evidence_index = EvidenceSnapshot.from_payload(normalized, [obj])
        evidence = []
        for identity in dict.fromkeys(obj["evidence_record_ids"]):
            record, error = evidence_index.resolve(identity)
            if error is not None:
                raise store.HistoryConflict("Original record evidence is " + error)
            evidence.append(record)
        fact = append_fact(
            db, artifact.id,
            operation_id=operation_id,
            kind="review_record_source", target_id=target.id,
            body={
                "artifact_id": artifact.id,
                "object_target": {"fact_id": target.id, "sha256": target.body_sha},
                "baseline": {"fact_id": "baseline", "sha256": artifact.baseline_sha},
                "subject_kind": artifact.subject_kind,
                "original_available": baseline["typed"] is not None,
                "representation": "normalized_source_record",
                "record": deepcopy(obj),
                "evidence_records": evidence,
                "source_document": deepcopy(artifact.provenance["source_document"]),
                "provenance_sha256": store.canonical(artifact.provenance)[0],
            },
        )
        return {"fact_id": fact.id, "sha256": fact.body_sha}

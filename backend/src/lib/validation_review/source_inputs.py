"""Exact structural input versions for affected validator bindings."""
from sqlalchemy import select

from src.lib.validation_history import store
from src.models.sql.validation_history import ValidationFact
from .audit import append_fact


def latest(db, artifact_id, target_id):
    return db.scalar(select(ValidationFact).where(
        ValidationFact.artifact_id == artifact_id, ValidationFact.kind == "validation_source_input",
        ValidationFact.target_id == target_id).order_by(ValidationFact.sequence.desc()).limit(1))


def reference(fact):
    return {"fact_id": fact.id, "sha256": fact.body_sha} if fact is not None else None


def current(db, artifact_id, frozen):
    return frozen.get("source_input_ref") == reference(latest(db, artifact_id, frozen["target_id"]))


def scientific_input(frozen):
    """Ignore mutable output addresses; include supplied evidence and provenance.

    Selectors are trusted configured code. This comparison never interprets the
    scientific content or decides whether a change is important.
    """
    request = frozen["request"]
    return {"binding_id": frozen["binding_id"],
            "validator_agent": request.get("validator_agent"),
            "selected_inputs": request.get("selected_inputs", {}),
            "input_selectors": request.get("input_selectors", {}),
            "evidence": request.get("evidence", []),
            "validation_guidance": request.get("validation_guidance"),
            "source_provenance": frozen.get("source_provenance", [])}


def update(db, artifact_id, *, operation_id, before, after, selection_id=None):
    """Both maps come from the same trusted binding resolver, never the model."""
    changed = []
    for target in before.keys() - after.keys():
        try:
            store._require_active_target(db, artifact_id, target)
        except store.HistoryConflict:
            continue  # Retirement already invalidates every descendant attempt.
        fact = append_fact(db, artifact_id, operation_id="source-input:" + operation_id + ":" + target,
            kind="validation_source_input", target_id=target, parent_id=selection_id,
            body={"input": None, "request": None})
        changed.append({"target_id": target, "reference": reference(fact), "runnable": False})
    for target, frozen in after.items():
        if target != frozen["target_id"]:
            raise store.HistoryConflict("Binding input map has a mismatched target")
        if target in before and scientific_input(before[target]) == scientific_input(frozen):
            continue
        fact = append_fact(db, artifact_id,
            operation_id="source-input:" + operation_id + ":" + target,
            kind="validation_source_input", target_id=target, parent_id=selection_id,
            body={"input": scientific_input(frozen), "request": frozen})
        changed.append({"target_id": target, "reference": reference(fact)})
    return changed

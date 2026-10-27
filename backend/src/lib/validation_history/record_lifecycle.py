"""Read whole-record lifecycle from immutable history, never from scientific text."""
from sqlalchemy import select
from src.models.sql.validation_history import ValidationFact
from . import store


def records(db, artifact_id):
    result = []
    for target in db.scalars(select(ValidationFact).where(
            ValidationFact.artifact_id == artifact_id, ValidationFact.kind == "target")
            .order_by(ValidationFact.sequence)):
        original = store._content(db, target)
        if "object_ref" not in original:
            continue
        state = store._current(db, artifact_id, target.id)
        result.append({"target_id": target.id, "object_id": original["object_ref"],
                       "active": state["active"], "version": state["version"],
                       "review": state["review"], "reason": state.get("reason")})
    return result


def retired_records(db, artifact_id):
    result = []
    for record in records(db, artifact_id):
        if record["active"]:
            continue
        fact = db.scalar(select(ValidationFact).where(
            ValidationFact.artifact_id == artifact_id, ValidationFact.target_id == record["target_id"],
            ValidationFact.kind == "record_retirement_decision").order_by(ValidationFact.sequence.desc()).limit(1))
        if fact is None:
            continue  # Other lifecycle actions are not supervisor retirement proposals.
        body = store._content(db, fact)
        if body["identity"]["decision"] != "approve":
            raise store.HistoryConflict("Retired record has no approving disposition")
        proposal = store._fact(db, artifact_id, fact.parent_id, kind="extraction_contribution")
        result.append({**record, "retirement": {"fact_id": fact.id, "sha256": fact.body_sha},
                       "proposal": {"fact_id": proposal.id, "sha256": proposal.body_sha},
                       "original": {"fact_id": record["target_id"], "sha256": store._fact(db, artifact_id, record["target_id"]).body_sha}})
    return result


def counts(db, artifact_id, blockers):
    values = records(db, artifact_id)
    pending = set()
    active = {item["target_id"] for item in values if item["active"]}
    for blocker in blockers:
        if blocker["target_id"] is None:
            pending.update(active)
        else:
            pending.update(active.intersection(store._target_lineage(db, artifact_id, blocker["target_id"])))
    return {"active": sum(item["active"] for item in values),
            "retired": sum(not item["active"] for item in values),
            "pending": len(pending)}

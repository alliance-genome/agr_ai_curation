"""Actor-bound grouped decisions; current values and curator authority change atomically."""

from dataclasses import dataclass
from typing import Literal
from pydantic import BaseModel, ConfigDict, StrictInt, StrictStr
from sqlalchemy import select
from src.lib.validation_history import store
from .audit import append_fact
from src.lib.validation_history.projection import publish_decisions_in_transaction
from src.models.sql.validation_history import ValidationFact
from .admission import lock_episode
from .policy import ReviewLimits


@dataclass(frozen=True)
class ReviewContext:
    """Constructed by authenticated runtime, never deserialized from tool arguments."""

    history_scope: store.HistoryScope
    episode_id: str
    actor_id: str
    actor_type: str = "agent"


class ReviewEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    target_id: StrictStr
    decision_unit_id: StrictStr
    expected_version: StrictInt
    action: Literal["accept", "reject", "defer", "acknowledge_failure", "revoke"]
    reason: StrictStr
    logical_attempt_id: StrictStr | None = None
    extraction_ref: StrictStr | None = None


def bounded_text(value, limit, name):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(
            f"{name} must be nonempty and within its configured character limit"
        )
    return value


def record_decisions(context, *, checkpoint_request, operation_id, entries):
    with store.SessionLocal.begin() as db:
        episode, _ = lock_episode(
            db, context.history_scope, context.episode_id, require_open=False
        )
        entries = [
            ReviewEntry.model_validate(e).model_dump(mode="json") for e in entries
        ]
        identity = {
            "entries": entries,
            "actor": {"type": context.actor_type, "id": context.actor_id},
            "episode_id": episode.id,
        }
        previous = db.scalar(
            select(ValidationFact).where(
                ValidationFact.artifact_id == episode.artifact_id,
                ValidationFact.operation_id == "review-response:" + operation_id,
            )
        )
        if previous is not None:
            saved = store._content(db, previous)
            if saved["identity"] != store.canonical(identity)[1]:
                raise store.HistoryConflict("Review operation identity conflict")
            return saved["response"]
        lock_episode(db, context.history_scope, context.episode_id)
        limits = ReviewLimits(**episode.policy)
        if not entries or len(entries) > limits.decision_entries:
            raise ValueError("Review entries exceed configured bounds")
        bounded_text(operation_id, limits.instruction_chars, "Operation ID")
        resolved = []
        for entry in entries:
            action = entry["action"]
            if action not in {
                "accept",
                "reject",
                "defer",
                "acknowledge_failure",
                "revoke",
            }:
                raise ValueError("Unsupported supervisor review action")
            bounded_text(entry["reason"], limits.instruction_chars, "Review reason")
            unit = store._fact(
                db, episode.artifact_id, entry["decision_unit_id"], kind="target"
            )
            target = store._fact(
                db, episode.artifact_id, entry["target_id"], kind="target"
            )
            if unit.id != target.id and unit.parent_id != target.id:
                raise store.HistoryConflict("Decision unit is outside the named target")
            attempt_id = entry.get("logical_attempt_id")
            extraction_ref = entry.get("extraction_ref")
            subject = None
            if action == "revoke":
                if attempt_id or extraction_ref:
                    raise ValueError("Revocation does not select an answer")
            elif bool(attempt_id) == bool(extraction_ref):
                raise ValueError("Name exactly one attempt or extraction reference")
            elif attempt_id:
                attempt = store._fact(
                    db, episode.artifact_id, attempt_id, kind="attempt"
                )
                frozen = store._content(db, attempt)
                if (
                    frozen["input"]["target_id"] != target.id
                    or unit.id not in frozen["coverage"]
                ):
                    raise store.HistoryConflict(
                        "Answer is outside this episode/target/unit"
                    )
                completion = db.scalar(
                    select(ValidationFact).where(
                        ValidationFact.artifact_id == episode.artifact_id,
                        ValidationFact.kind == "completion",
                        ValidationFact.parent_id == attempt.id,
                    )
                )
                if completion is None:
                    if action != "acknowledge_failure":
                        raise store.HistoryConflict(
                            "Attempt has no recorded completion"
                        )
                    subject = (
                        attempt.id
                    )  # Store checks exact durable uncertainty; no invented answer.
                elif (
                    action == "reject"
                    and store._content(db, completion)["state"] != "completed"
                ):
                    raise store.HistoryConflict(
                        "Operationally incomplete answers require acknowledgement or deferral"
                    )
                if completion is not None:
                    subject = completion.id
            else:
                artifact = store._artifact(
                    db, episode.artifact_id, context.history_scope
                )
                if extraction_ref != artifact.baseline_sha:
                    raise store.HistoryConflict(
                        "Extraction reference does not match the immutable baseline"
                    )
                subject = "baseline"
            resolved.append(
                {
                    "target_id": unit.id,
                    "expected_version": entry["expected_version"],
                    "action": action,
                    "reason": entry["reason"],
                    **({"subject": subject} if subject else {}),
                }
            )
        # The model never chooses the checkpoint/source linkage or the actor.
        if (
            checkpoint_request.envelope.metadata["validation_history"]["artifact_id"]
            != episode.artifact_id
        ):
            raise store.HistoryConflict("Checkpoint belongs to a different artifact")
        result, manifest, checkpoint = publish_decisions_in_transaction(
            db,
            context.history_scope,
            checkpoint_request=checkpoint_request,
            episode_id=episode.id,
            operation_id=operation_id,
            actor={"type": context.actor_type, "id": context.actor_id},
            entries=resolved,
        )
        if manifest["ready"]:
            episode.state = "complete"
        decision = db.scalar(
            select(ValidationFact).where(
                ValidationFact.artifact_id == episode.artifact_id,
                ValidationFact.operation_id == "decision:" + operation_id,
            )
        )
        response = {
            "episode_id": episode.id,
            "decision_id": decision.id,
            "ready": manifest["ready"],
            "publication": result.metadata["selected_projection"],
            "envelope_revision": checkpoint.revision,
            "applied": [
                {"decision_unit_id": e["target_id"], "action": e["action"]}
                for e in resolved
            ],
        }

        append_fact(
            db,
            episode.artifact_id,
            operation_id="review-response:" + operation_id,
            kind="review_response",
            parent_id=episode.id,
            body={"identity": identity, "response": response},
        )
        return response

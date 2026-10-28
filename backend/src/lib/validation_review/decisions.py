"""Actor-bound grouped decisions; current values and curator authority change atomically."""

from dataclasses import dataclass
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr
from sqlalchemy import select
from src.lib.validation_history import store
from .audit import append_fact
from src.schemas.validation_history import UnresolvedFieldAssessment, HistoryFactReference
from src.lib.validation_history.projection import publish_decisions_in_transaction
from src.models.sql.validation_history import ValidationFact
from .admission import lock_episode
from .policy import ReviewLimits
from .record_scope import review_ready


@dataclass(frozen=True)
class ReviewContext:
    """Constructed by authenticated runtime, never deserialized from tool arguments."""

    history_scope: store.HistoryScope
    episode_id: str
    actor_id: str
    actor_type: str = "agent"


class ReviewEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    target_id: StrictStr = Field(description=(
        "Binding target: use the frozen attempt input.target_id for an answer; "
        "for extraction-only review use the authorized source target."
    ))
    decision_unit_id: StrictStr = Field(description=(
        "Reviewed unit: use an ID from the frozen attempt coverage and its manifest "
        "unit target_id. With no component units, decision_unit_id equals target_id: "
        "decision_unit: null and decision_units: [] mean whole-target review, not "
        "missing IDs. With components, use the covered child ID, not the parent. "
        "For extraction-only review use the authorized manifest unit target_id."
    ))
    expected_version: StrictInt = Field(description=(
        "Current version of this decision unit in the manifest, not selection_generation."
    ))
    action: Literal["accept", "reject", "defer", "acknowledge_failure", "revoke"]
    reason: StrictStr
    logical_attempt_id: StrictStr | None = Field(default=None, description=(
        "Exact attempt_ref fact_id for the answer, not its completion_ref fact_id. "
        "Name exactly one attempt, authorized extraction_ref or applicability_ref except for revoke."
    ))
    extraction_ref: StrictStr | None = None
    applicability_ref: HistoryFactReference | None = Field(default=None, description=(
        "Exact source-input fact and hash declaring optional-input absence. Use instead of an attempt or extraction reference."
    ))
    absence_assessment: Literal["omission_supported", "unresolved"] | None = Field(default=None, description=(
        "Required only when accepting optional-input absence: omission_supported or unresolved, with an evidence-grounded reason."
    ))
    unresolved_fields: list[UnresolvedFieldAssessment] | None = Field(default=None, description=(
        "Only with accept of extraction-only content: exact plain payload field paths "
        "and explanations of remaining scientific uncertainty after assessment. "
        "Acceptance completes review without claiming these fields are resolved. "
        "Do not use for structured validator identities or incomplete executions."
    ))


def bounded_text(value, limit, name):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(
            f"{name} must be nonempty and within its configured character limit"
        )
    return value


def record_decisions(context, *, checkpoint_request, operation_id, entries):
    operation_id = context.episode_id + ":" + operation_id
    with store.SessionLocal.begin() as db:
        episode, _ = lock_episode(
            db, context.history_scope, context.episode_id, require_open=False
        )
        entries = [
            ReviewEntry.model_validate(e).model_dump(mode="json") for e in entries
        ]
        # The new assessment is genuinely optional; absent annotations do not
        # change the identity of previously recorded requests.
        for entry in entries:
            for optional in ("unresolved_fields", "applicability_ref", "absence_assessment"):
                if entry[optional] is None:
                    del entry[optional]
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
        if context.actor_type == "curator":
            if context.actor_id != context.history_scope.owner_id:
                raise store.HistoryAccessError("Curator decisions require the authenticated owner")
            from src.lib.validation_history.episodes import latest_effective_episode
            latest = latest_effective_episode(db, episode.artifact_id)
            if latest is None or latest.id != episode.id:
                raise store.HistoryConflict("A new review round has started; refresh before deciding")
            # Human judgment is not gated by a machine deadline or allowance.
            # Publication still enforces live targets, ownership and exact versions.
        else:
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
            applicability_ref = entry.get("applicability_ref")
            subject = None
            if action == "revoke":
                if attempt_id or extraction_ref or applicability_ref:
                    raise ValueError("Revocation does not select an answer")
            elif sum(bool(ref) for ref in (attempt_id, extraction_ref, applicability_ref)) != 1:
                raise ValueError("Name exactly one attempt, extraction or applicability reference")
            elif applicability_ref:
                from . import source_inputs
                if action == "acknowledge_failure":
                    raise ValueError("Optional absence is not an execution failure")
                fact = source_inputs.require_absence(db, episode.artifact_id, unit.id,
                    applicability_ref["fact_id"], scope=context.history_scope, sha256=applicability_ref["sha256"])
                if fact.target_id != target.id:
                    raise store.HistoryConflict("Absence source belongs to another named target")
                subject = fact.id
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
            absence_assessment = entry.get("absence_assessment")
            if action == "accept" and applicability_ref:
                if absence_assessment is None:
                    raise ValueError("Accepting optional absence requires an explicit assessment")
            elif absence_assessment is not None:
                raise ValueError("Absence assessment requires optional-absence acceptance")
            assessments = entry.get("unresolved_fields")
            if assessments is not None:
                if action != "accept" or subject != "baseline":
                    raise ValueError("Unresolved fields require extraction-only acceptance")
                for assessment in assessments:
                    bounded_text(assessment["explanation"], limits.instruction_chars, "Field explanation")
            resolved.append(
                {
                    "target_id": unit.id,
                    "expected_version": entry["expected_version"],
                    "action": action,
                    "reason": entry["reason"],
                    **({"subject": subject} if subject else {}),
                    **({"absence_assessment": absence_assessment} if absence_assessment is not None else {}),
                    **({"unresolved_fields": assessments} if assessments is not None else {}),
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
        if review_ready(manifest):
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
            "ready": review_ready(manifest),
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

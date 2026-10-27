"""Trusted frozen requests plus model-authored reconsideration instructions.

The runtime supplies initial requests and pinned execution receipts. A review
tool can only reconsider an existing target using that saved receipt. It cannot
choose another validator, create an episode, or re-extract the paper.
"""

from copy import deepcopy

from sqlalchemy import select

from src.lib.validation_history import store
from src.models.sql.validation_history import ValidationFact
from src.models.sql.validation_review import (
    ValidationReviewWork as Work, ValidationReviewEpisode as Episode,
)
from .admission import ReviewStopped, lock_episode
from .decisions import bounded_text
from .work import enqueue_in_transaction


def _existing(db, ep, operation_id, identity):
    row = db.scalar(
        select(Work).join(Episode, Episode.id == Work.episode_id).where(
            Episode.scope_id == ep.scope_id, Episode.artifact_id == ep.artifact_id,
            Work.operation_id == operation_id,
        )
    )
    if row is not None and row.payload["identity"] != store.canonical(identity)[1]:
        raise store.HistoryConflict("Validation dispatch identity conflict")
    return row


def _enqueue(
    db,
    scope,
    ep,
    *,
    operation_id,
    requests,
    receipt,
    identity,
    reconsideration,
    followup=None,
    first_from_source=False,
):
    prior = _existing(db, ep, operation_id, identity)
    if prior is not None:
        return prior.id
    from src.lib.validation_history.selections import effective_episode
    declared = effective_episode(db, store._fact(db, ep.artifact_id, ep.id))["obligations"]
    episode_body = effective_episode(db, store._fact(db, ep.artifact_id, ep.id))
    obligations = {}
    for item in declared:
        if item["mode"] == "validation":
            unit = store._fact(db, ep.artifact_id, item["target_id"], kind="target")
            root = (
                unit.parent_id
                if "decision_unit" in store._content(db, unit)
                else unit.id
            )
            from .record_scope import allowed_units
            if item["target_id"] in allowed_units(episode_body):
                obligations[root] = item["binding_id"]
    for request in requests:
        tid = request["target_id"]
        if (
            obligations.get(tid) != request["binding_id"]
            or request["review_episode_id"] != ep.id
        ):
            raise store.HistoryConflict("Request is outside admitted binding coverage")
        from .attempt_lineage import counts
        count = counts(db, ep, [tid])[tid]
        if not reconsideration and count:
            raise store.HistoryConflict("Target already has its initial attempt")
        if reconsideration and not count and not first_from_source:
            raise store.HistoryConflict("Reconsideration requires an initial attempt")
        if count >= ep.policy["attempts_per_target"]:
            raise ReviewStopped("scientific_attempts_exhausted", resource=tid)
    invocation = store.begin_execution_in_transaction(
        db,
        scope,
        ep.artifact_id,
        operation_id="review:" + ep.id + ":" + operation_id,
        receipt=receipt,
        requests=requests,
    )
    payload = {
        "identity": identity,
        "receipt": receipt,
        "requests": requests,
        "invocation": invocation,
        "followup": followup,
        "token_pool": "reconsideration_tokens" if reconsideration else "initial_tokens",
    }
    return enqueue_in_transaction(
        db,
        scope,
        ep.id,
        operation_id=operation_id,
        payload=payload,
        charges={"extra_attempts": len(requests) if reconsideration else 0},
    )


def initial(scope, episode_id, *, operation_id, requests, receipt):
    """Called only by the runtime after constructing authoritative binding requests."""
    with store.SessionLocal.begin() as db:
        ep, _ = lock_episode(db, scope, episode_id, require_open=False)
        prior = _existing(
            db, ep, operation_id, {"requests": requests, "receipt": receipt}
        )
        if prior is not None:
            return prior.id
        lock_episode(db, scope, episode_id)
        return _enqueue(
            db,
            scope,
            ep,
            operation_id=operation_id,
            requests=requests,
            receipt=receipt,
            identity={"requests": requests, "receipt": receipt},
            reconsideration=False,
        )


def reconsider(
    context,
    *,
    operation_id,
    parent_decision_id,
    target_ids,
    instructions,
    evidence_refs,
    refresh_inputs=None,
    rebind_receipt=None,
):
    with store.SessionLocal.begin() as db:
        ep, _ = lock_episode(
            db, context.history_scope, context.episode_id, require_open=False
        )
        bounded_text(operation_id, ep.policy["instruction_chars"], "Operation ID")
        bounded_text(
            instructions, ep.policy["instruction_chars"], "Reconsideration instructions"
        )
        if not target_ids or len(target_ids) != len(set(target_ids)):
            raise ValueError("Reconsideration must name distinct targets")
        if (
            len(target_ids) > ep.policy["decision_entries"]
            or len(evidence_refs) > ep.policy["evidence_refs"]
        ):
            raise ValueError("Reconsideration exceeds configured request bounds")
        identity = {
            "parent_decision_id": parent_decision_id,
            "target_ids": target_ids,
            "instructions": instructions,
            "evidence_refs": evidence_refs,
            "actor": {"type": context.actor_type, "id": context.actor_id},
        }
        prior = _existing(db, ep, operation_id, identity)
        if prior is not None:
            return prior.id
        lock_episode(db, context.history_scope, context.episode_id)
        parent = store._fact(db, ep.artifact_id, parent_decision_id, kind="decision")
        disposition = db.scalar(
            select(ValidationFact).where(
                ValidationFact.artifact_id == ep.artifact_id,
                ValidationFact.operation_id
                == "episode-disposition:"
                + parent.operation_id.removeprefix("decision:"),
            )
        )
        if disposition is None or disposition.parent_id != ep.id:
            raise store.HistoryConflict(
                "Parent decision is outside this review episode"
            )
        # References are exact immutable facts, never model-supplied evidence text.
        evidence = []
        for ref in evidence_refs:
            if set(ref) != {"fact_id", "sha256"}:
                raise ValueError("Evidence reference requires exact fact ID and SHA")
            if ref["fact_id"] == "baseline":
                from src.models.sql.validation_history import ValidationBody

                artifact = store._artifact(db, ep.artifact_id, context.history_scope)
                if artifact.baseline_sha != ref["sha256"]:
                    raise store.HistoryConflict("Evidence baseline version changed")
                body = db.get(ValidationBody, artifact.baseline_sha).content
            else:
                fact = store._fact(db, ep.artifact_id, ref["fact_id"])
                if fact.body_sha != ref["sha256"]:
                    raise store.HistoryConflict("Evidence reference version changed")
                body = store._content(db, fact)
            evidence.append({"reference": ref, "content": body})
        entries = store._content(db, parent)["entries"]
        requests, receipts, previous_answers = [], [], []
        renewed = []
        for tid in target_ids:
            candidates = []
            for entry in entries:
                subject = entry.get("subject")
                if not subject or subject in {"baseline", "curator"}:
                    continue
                named = store._fact(db, ep.artifact_id, subject)
                if named.kind == "attempt" and entry["action"] == "acknowledge_failure":
                    attempt = named
                elif named.kind == "completion":
                    attempt = store._fact(
                        db, ep.artifact_id, named.parent_id, kind="attempt"
                    )
                else:
                    raise store.HistoryConflict(
                        "Parent decision has an invalid answer reference"
                    )
                if attempt.target_id == tid:
                    candidates.append(attempt)
            if not candidates or len({item.id for item in candidates}) != 1:
                raise store.HistoryConflict(
                    "Parent decision must identify one exact answer per target"
                )
            attempt = candidates[0]
            frozen = store._content(db, attempt)
            previous_episode = db.get(Episode, frozen["input"]["review_episode_id"])
            if previous_episode is None or previous_episode.artifact_id != ep.artifact_id:
                raise store.HistoryConflict("Reconsidered answer belongs to another artifact")
            fresh_scope = previous_episode.scope_id != ep.scope_id
            if fresh_scope:
                from src.lib.validation_history.selections import effective_episode
                from .record_scope import allowed_units
                body = effective_episode(db, store._fact(db, ep.artifact_id, ep.id))
                if rebind_receipt is None or body.get('review_target_ids') is None or not set(frozen['coverage']).issubset(allowed_units(body)):
                    raise store.HistoryConflict("Reconsidered answer belongs to another document operation")
            renewed.append(fresh_scope)
            if not store._attempt_is_current(
                db, context.history_scope, ep.artifact_id, frozen
            ):
                request = _refresh_prerequisites(
                    db, context, ep, frozen, refresh_inputs
                )
            else:
                request = deepcopy(frozen["input"])
            prior_completion = db.scalar(
                select(ValidationFact).where(
                    ValidationFact.artifact_id == ep.artifact_id,
                    ValidationFact.kind == "completion",
                    ValidationFact.parent_id == attempt.id,
                )
            )
            if prior_completion is None:
                uncertainty = db.scalar(
                    select(ValidationFact).where(
                        ValidationFact.artifact_id == ep.artifact_id,
                        ValidationFact.kind == "review_uncertainty",
                        ValidationFact.parent_id == attempt.id,
                    )
                )
                if uncertainty is None:
                    raise store.HistoryConflict(
                        "Attempt has neither an answer nor recorded operational uncertainty"
                    )
                previous_answers.append(
                    {
                        "attempt_id": attempt.id,
                        "state": "unknown",
                        "uncertainty_fact_id": uncertainty.id,
                    }
                )
            else:
                previous_answers.append(
                    {
                        "attempt_id": attempt.id,
                        "answer": store._content(db, prior_completion),
                    }
                )
            request["review_episode_id"] = ep.id
            # Immutable extraction/evidence and saved validator stay fixed.
            # Only a trusted selector adapter may refresh prerequisite inputs.
            requests.append(request)
            execution = store._fact(
                db, ep.artifact_id, attempt.parent_id, kind="execution"
            )
            receipts.append(store._content(db, execution)["receipt"])
        if any(receipt != receipts[0] for receipt in receipts):
            raise store.HistoryConflict(
                "Different saved validator receipts require separate operations"
            )
        from .attempt_lineage import counts
        initial_in_round = all(value == 0 for value in counts(db, ep, target_ids).values())
        if any(renewed):
            if not all(renewed) or not initial_in_round:
                raise store.HistoryConflict("Reconsider the current round's answer separately")
            receipts = [rebind_receipt(requests, receipts[0])]
        return _enqueue(
            db,
            context.history_scope,
            ep,
            operation_id=operation_id,
            requests=requests,
            receipt=receipts[0],
            identity=identity,
            reconsideration=not (any(renewed) and initial_in_round),
            followup={
                "instructions": instructions,
                "parent_decision_id": parent_decision_id,
                "parent_decision": store._content(db, parent),
                "previous_answers": previous_answers,
                "evidence": evidence,
            },
        )


def _refresh_prerequisites(db, context, ep, frozen, refresh_inputs):
    """Rebuild only selected_inputs through a trusted pure selector callback.

    No network/provider work inside this transaction. The model cannot supply the
    callback, values, pins, validator identity or extraction/evidence content.
    """
    from .admission import ReviewStopped
    from src.lib.validation_history import selections

    from .source_inputs import latest, reference
    current_source = latest(db, ep.artifact_id, frozen["input"]["target_id"])
    source_changed = frozen["input"].get("source_input_ref") != reference(current_source)
    request = deepcopy(frozen["input"])
    if source_changed:
        request = deepcopy(store._content(db, current_source)["request"])
        if request is None:
            raise ReviewStopped("required_inputs_unavailable")
        if (request["target_id"], request["binding_id"]) != (
                frozen["input"]["target_id"], frozen["input"]["binding_id"]):
            raise store.HistoryConflict("Source revision changes the authorized validator target")
        request["source_input_ref"] = reference(current_source)
    unchanged = deepcopy(frozen)
    unchanged["input"] = deepcopy(request)
    unchanged["input"]["prerequisites"] = []
    if not store._attempt_is_current(
        db, context.history_scope, ep.artifact_id, unchanged
    ):
        raise store.HistoryConflict("Reconsideration target lineage is stale")
    targets = [pin["target_id"] for pin in request["prerequisites"]]
    if not targets and source_changed:
        return request
    if not targets or refresh_inputs is None:
        raise ReviewStopped("fresh_prerequisite_adapter_required")
    manifest = selections.manifest_in_transaction(
        db, context.history_scope, ep.artifact_id, episode_id=ep.id
    )
    if any(item["target_id"] in targets for item in manifest["blockers"]):
        raise ReviewStopped("prerequisite_review_pending")
    pins = selections.selected_prerequisites(manifest, targets)
    units = {unit["target_id"]: unit for unit in manifest["units"]}
    sources = {}
    for tid in targets:
        selected = units[tid]["selected"]
        if selected in {"baseline", "curator"}:
            raise ReviewStopped("non_validator_prerequisite", resource=tid)
        sources[tid] = store._content(
            db, store._fact(db, ep.artifact_id, selected, kind="completion")
        )
    if not isinstance(request["request"].get("selected_inputs"), dict):
        raise ValueError("Dependent requests require a selected_inputs mapping")
    values = refresh_inputs(deepcopy(request["request"]), deepcopy(sources))
    if values is None:
        raise ReviewStopped("required_inputs_unavailable")
    if not isinstance(values, dict):
        raise ValueError("Trusted refresh adapter must return selected_inputs")
    request["request"]["selected_inputs"] = values
    # The outer frozen attempt input (including new pins and selected values)
    # supplies the new durable identity. Do not inject fields into validator JSON.
    request["prerequisites"] = pins
    return request

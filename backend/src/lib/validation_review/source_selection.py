"""Supervisor-authorized source contributions with structural conflict checks.

The runtime callback resolves actual binding inputs and commits coverage/projection
within this transaction. It cannot be supplied by the model or perform provider IO.
"""
from copy import deepcopy
from sqlalchemy import select

from src.lib.validation_history import store, selections
from src.lib.validation_history.curator_authority import active_overlays, anchored_path
from src.models.sql.validation_history import ValidationFact
from src.schemas.domain_envelope import parse_field_path
from src.schemas.validation_followup import FollowupEntryResult
from . import admission, source_projection, source_inputs
from .audit import append_fact
from .decisions import bounded_text


def selected_entries(db, artifact_id):
    selected = {}
    for fact in db.scalars(select(ValidationFact).where(
        ValidationFact.artifact_id == artifact_id,
        ValidationFact.kind == "extraction_source_selection").order_by(ValidationFact.sequence)):
        selected[fact.target_id] = (fact, store._content(db, fact))
    return selected


def _overlap(left, right):
    return left[:len(right)] == right or right[:len(left)] == left


def select_in_transaction(db, context, *, operation_id, contribution_ref, field_paths,
                          evidence_ids, expected_source_sha, reason, source_envelope,
                          rebuild_inputs, publish):
    """Callbacks supply trusted code, not values/identities chosen by the supervisor."""
    ep, _ = admission.lock_episode(db, context.history_scope, context.episode_id)
    artifact_id = ep.artifact_id
    bounded_text(operation_id, ep.policy["instruction_chars"], "Operation ID")
    bounded_text(reason, ep.policy["instruction_chars"], "Selection reason")
    identity = {"actor": {"type": context.actor_type, "id": context.actor_id},
                "contribution": contribution_ref, "fields": field_paths,
                "evidence": evidence_ids, "source_sha": expected_source_sha, "reason": reason}
    existing = db.scalar(select(ValidationFact).where(
        ValidationFact.artifact_id == artifact_id,
        ValidationFact.operation_id == "source-selection-response:" + operation_id))
    if existing is not None:
        body = store._content(db, existing)
        if body["identity"] != store.canonical(identity)[1]:
            raise store.HistoryConflict("Source selection identity conflict")
        return body["response"]
    if selections.source_state_digest(db, artifact_id) != expected_source_sha:
        raise store.HistoryConflict("Source selection version changed")
    contribution = store._fact(db, artifact_id, contribution_ref["fact_id"], kind="extraction_contribution")
    if contribution.body_sha != contribution_ref["sha256"]:
        raise store.HistoryConflict("Contribution reference changed")
    store._require_active_target(db, artifact_id, contribution.target_id)
    from .record_scope import allowed_units
    from .specialist_context import entry_coverage
    episode_body = selections.effective_episode(db, store._fact(db, artifact_id, ep.id))
    _, descendants = entry_coverage(db, artifact_id, [contribution.target_id])
    if not descendants.intersection(allowed_units(episode_body)):
        raise store.HistoryAccessError("Source contribution is outside the requested record scope")
    body = store._content(db, contribution)
    if body.get("selectable") is False:
        raise store.HistoryConflict("Contribution has an unresolved structural failure")
    proposal = FollowupEntryResult.model_validate(body["proposal"])
    if proposal.outcome == "propose_retirement":
        raise store.HistoryConflict("Use the explicit record retirement decision tool")
    offered = {item.field_path: item for item in proposal.proposed_values}
    if (not field_paths and not evidence_ids or len(field_paths) != len(set(field_paths))
            or len(evidence_ids) != len(set(evidence_ids)) or not set(field_paths).issubset(offered)):
        raise store.HistoryConflict("Selection must name distinct proposed fields or evidence")
    if len(field_paths) > ep.policy["decision_entries"] or len(evidence_ids) > ep.policy["evidence_refs"]:
        raise ValueError("Source selection exceeds configured request bounds")
    evidence = {item["record"]["evidence_record_id"]: item["record"] for item in body["evidence"]}
    required = {ref for path in field_paths for ref in offered[path].evidence_record_ids}
    if not set(evidence_ids).issubset(evidence) or not required.issubset(evidence_ids):
        raise store.HistoryConflict("Select the exact evidence attached to the proposed fields")
    matches = [obj for obj in source_envelope.extracted_objects
               if obj.metadata["validation_history"]["target_id"] == contribution.target_id]
    if len(matches) != 1:
        raise store.HistoryAccessError("Contribution entry is outside the current source")
    original = matches[0].model_dump(mode="json")
    supplied_fact = store._fact(db, artifact_id, body["context_ref"]["fact_id"], kind="specialist_context")
    if supplied_fact.body_sha != body["context_ref"]["sha256"]:
        raise store.HistoryConflict("Contribution input reference changed")
    supplied = store._content(db, supplied_fact)["current_selected_entries"][contribution.target_id]
    if (original["payload"], original["metadata"]["validation_history"]) != (
            supplied["payload"], supplied["metadata"]["validation_history"]):
        raise store.HistoryConflict("The entry changed after this extractor proposal")
    for _, overlay in active_overlays(db, artifact_id):
        for change in overlay["changes"]:
            if change["target_id"] == contribution.target_id:
                protected = parse_field_path(anchored_path(original["metadata"]["validation_history"], change["anchor"]))
                if any(_overlap(parse_field_path(path), protected) for path in field_paths):
                    raise store.HistoryConflict("Source proposal overlaps an authoritative curator edit")
    before_inputs = rebuild_inputs(db, source_envelope)
    planned = source_projection.plan(original, [offered[path] for path in field_paths], contribution_id=contribution.id)
    for addition in planned["additions"]:
        store._append(db, artifact_id, operation_id="contributed-target:" + addition["id"],
            kind="target", body=addition["body"], fact_id=addition["id"], parent_id=addition["parent_id"])
    if planned["retired_ids"]:
        store.apply_decisions_in_transaction(db, context.history_scope, artifact_id,
            operation_id="source-retire:" + operation_id, actor=identity["actor"], entries=[{
                "target_id": target, "expected_version": store._current(db, artifact_id, target)["version"],
                "action": "retire", "reason": reason} for target in planned["retired_ids"]])
    selected = planned["entry"]
    selected["evidence_record_ids"] = list(dict.fromkeys([*selected["evidence_record_ids"], *evidence_ids]))
    selected["metadata"]["selected_extractor_contribution"] = contribution_ref
    updated = source_envelope.model_copy(deep=True)
    for index, obj in enumerate(updated.extracted_objects):
        if obj.metadata["validation_history"]["target_id"] == contribution.target_id:
            updated.extracted_objects[index] = type(obj).model_validate(selected)
    records = updated.metadata.setdefault("evidence_records", [])
    by_id = {record["evidence_record_id"]: record for record in records}
    for evidence_id in evidence_ids:
        if evidence_id in by_id and by_id[evidence_id] != evidence[evidence_id]:
            raise store.HistoryConflict("Selected evidence conflicts with existing immutable evidence")
        if evidence_id not in by_id:
            records.append(deepcopy(evidence[evidence_id]))
    selection = append_fact(db, artifact_id, operation_id="source-selection:" + operation_id,
        kind="extraction_source_selection", target_id=contribution.target_id, parent_id=contribution.id,
        body={"identity": identity, "entry": selected, "evidence": [evidence[key] for key in evidence_ids],
              "relationships": planned["relationships"], "retired_ids": planned["retired_ids"]})
    from src.lib.validation_history.runtime import reconcile_absent_binding_targets
    reconcile_absent_binding_targets(db, context.history_scope, artifact_id,
        updated.extracted_objects, operation_id=operation_id, actor=identity["actor"], reason=reason)
    after_inputs = rebuild_inputs(db, updated)
    changed = source_inputs.update(db, artifact_id, operation_id=operation_id,
                                   before=before_inputs, after=after_inputs, selection_id=selection.id)
    publication = publish(db, updated, after_inputs, changed, selection.id)
    response = {"selection_id": selection.id, "affected_bindings": changed, "publication": publication}
    append_fact(db, artifact_id, operation_id="source-selection-response:" + operation_id,
        kind="source_selection_response", parent_id=selection.id,
        body={"identity": identity, "response": response})
    return response

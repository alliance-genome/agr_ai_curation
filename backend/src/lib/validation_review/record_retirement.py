"""Explicit supervisor disposition of a retained whole-record proposal."""
from sqlalchemy import select

from src.lib.validation_history import selections, store
from src.lib.validation_history.curator_authority import active_overlays
from src.models.sql.validation_history import ValidationFact
from src.schemas.validation_followup import FollowupEntryResult
from . import admission, source_inputs
from .audit import append_fact
from .decisions import bounded_text
from .record_scope import allowed_units
from .specialist_context import entry_coverage


def decide_in_transaction(db, context, *, operation_id, contribution_ref,
                          retirement_decision, expected_source_sha, reason,
                          source_envelope, rebuild_inputs, publish):
    """The model owns inclusion judgment; this boundary owns authority and history."""
    ep, _ = admission.lock_episode(db, context.history_scope, context.episode_id)
    artifact_id = ep.artifact_id
    bounded_text(operation_id, ep.policy["instruction_chars"], "Operation ID")
    bounded_text(reason, ep.policy["instruction_chars"], "Retirement decision reason")
    if retirement_decision not in {"approve", "decline"}:
        raise ValueError("Retirement decision must be approve or decline")
    identity = {"actor": {"type": context.actor_type, "id": context.actor_id},
                "contribution": contribution_ref, "decision": retirement_decision,
                "source_sha": expected_source_sha, "reason": reason}
    existing = db.scalar(select(ValidationFact).where(
        ValidationFact.artifact_id == artifact_id,
        ValidationFact.operation_id == "record-retirement-response:" + operation_id))
    if existing is not None:
        body = store._content(db, existing)
        if body["identity"] != store.canonical(identity)[1]:
            raise store.HistoryConflict("Retirement operation identity conflict")
        return body["response"]
    if selections.source_state_digest(db, artifact_id) != expected_source_sha:
        raise store.HistoryConflict("Retirement source version changed")
    contribution = store._fact(db, artifact_id, contribution_ref["fact_id"], kind="extraction_contribution")
    if contribution.body_sha != contribution_ref["sha256"]:
        raise store.HistoryConflict("Contribution reference changed")
    root = store._fact(db, artifact_id, contribution.target_id, kind="target")
    if "object_ref" not in store._content(db, root):
        raise store.HistoryConflict("Retirement requires a whole-record target")
    store._require_active_target(db, artifact_id, root.id)
    episode = selections.effective_episode(db, store._fact(db, artifact_id, ep.id))
    _, descendants = entry_coverage(db, artifact_id, [root.id])
    if not descendants.intersection(allowed_units(episode)):
        raise store.HistoryAccessError("Retirement is outside the requested record scope")
    body = store._content(db, contribution)
    if body.get("selectable") is False:
        raise store.HistoryConflict("Contribution has an unresolved structural failure")
    proposal = FollowupEntryResult.model_validate(body["proposal"])
    if proposal.outcome != "propose_retirement" or proposal.entry_id != root.id:
        raise store.HistoryConflict("A whole-record extractor retirement proposal is required")
    evidence = {item["record"]["evidence_record_id"] for item in body["evidence"]}
    if not set(proposal.evidence_record_ids).issubset(evidence):
        raise store.HistoryConflict("Retirement proposal evidence is unavailable")
    matches = [obj for obj in source_envelope.extracted_objects
               if obj.metadata["validation_history"]["target_id"] == root.id]
    if len(matches) != 1:
        raise store.HistoryConflict("Retirement entry is not in the current source")
    current = matches[0].model_dump(mode="json")
    supplied_fact = store._fact(db, artifact_id, body["context_ref"]["fact_id"], kind="specialist_context")
    if supplied_fact.body_sha != body["context_ref"]["sha256"]:
        raise store.HistoryConflict("Contribution input reference changed")
    supplied = store._content(db, supplied_fact)["current_selected_entries"][root.id]
    if (current["payload"], current["metadata"]["validation_history"]) != (
            supplied["payload"], supplied["metadata"]["validation_history"]):
        raise store.HistoryConflict("The entry changed after this retirement proposal")
    state = store._current(db, artifact_id, root.id)
    if retirement_decision == "approve":
        if any(store._current(db, artifact_id, target).get("actor", {}).get("type") == "curator"
               for target in descendants | {root.id}):
            raise store.HistoryConflict("Curator authority prevents supervisor retirement")
        from src.lib.curation_workspace.models import CurationCandidate
        from src.schemas.curation_workspace import CurationCandidateStatus
        reviewed = db.scalar(select(CurationCandidate.id).where(
            CurationCandidate.envelope_id == source_envelope.envelope_id,
            CurationCandidate.object_id == store._content(db, root)["object_ref"],
            CurationCandidate.status.in_([CurationCandidateStatus.ACCEPTED, CurationCandidateStatus.REJECTED])))
        if reviewed is not None:
            raise store.HistoryConflict("Curator row decision prevents supervisor retirement")
        for _, overlay in active_overlays(db, artifact_id):
            if any(change["target_id"] == root.id for change in overlay["changes"]):
                raise store.HistoryConflict("Curator edits prevent supervisor retirement")
    before = rebuild_inputs(db, source_envelope) if retirement_decision == "approve" else None
    fact = append_fact(db, artifact_id, operation_id="record-retirement:" + operation_id,
        kind="record_retirement_decision", target_id=root.id, parent_id=contribution.id,
        body={"identity": identity, "proposal": contribution_ref,
              "evidence_record_ids": proposal.evidence_record_ids,
              "previous_obligations": episode["obligations"], "entry": current})
    publication, changed = None, []
    if retirement_decision == "approve":
        store.apply_decisions_in_transaction(db, context.history_scope, artifact_id,
            operation_id="record-retire:" + operation_id, actor=identity["actor"], entries=[{
                "target_id": root.id, "expected_version": state["version"],
                "action": "retire", "reason": reason}])
        updated = source_envelope.model_copy(deep=True)
        updated.extracted_objects = [obj for obj in updated.extracted_objects
            if obj.metadata["validation_history"]["target_id"] != root.id]
        after = rebuild_inputs(db, updated)
        changed = source_inputs.update(db, artifact_id, operation_id=operation_id,
            before=before, after=after, selection_id=fact.id)
        publication = publish(db, updated, after, changed, fact.id)
    response = {"decision_id": fact.id, "decision": retirement_decision,
                "target_id": root.id, "retired": retirement_decision == "approve",
                "affected_bindings": changed, "publication": publication,
                "source_state_sha256": selections.source_state_digest(db, artifact_id)}
    append_fact(db, artifact_id, operation_id="record-retirement-response:" + operation_id,
        kind="record_retirement_response", parent_id=fact.id,
        body={"identity": identity, "response": response})
    return response


def restore_in_transaction(db, context, *, operation_id, restore_target_id,
                           expected_version, reason, source_envelope, rebuild_inputs, publish):
    """An authenticated curator restores identity, never acceptance or an allowance."""
    from . import admission
    from src.lib.validation_history.episodes import latest_effective_episode
    from src.schemas.domain_envelope import CuratableObjectEnvelope
    ep, _ = admission.lock_episode(db, context.history_scope, context.episode_id, require_open=False)
    artifact_id = ep.artifact_id
    if context.actor_type != "curator" or context.actor_id != context.history_scope.owner_id:
        raise store.HistoryAccessError("Restoration requires the authenticated curator")
    bounded_text(operation_id, ep.policy["instruction_chars"], "Operation ID")
    bounded_text(reason, ep.policy["instruction_chars"], "Restoration reason")
    identity = {"target_id": restore_target_id, "expected_version": expected_version,
                "reason": reason, "actor": {"type": "curator", "id": context.actor_id}}
    previous = db.scalar(select(ValidationFact).where(
        ValidationFact.artifact_id == artifact_id,
        ValidationFact.operation_id == "record-restore-response:" + operation_id))
    if previous is not None:
        saved = store._content(db, previous)
        if saved["identity"] != store.canonical(identity)[1]:
            raise store.HistoryConflict("Restoration operation identity conflict")
        return saved["response"]
    latest = latest_effective_episode(db, artifact_id)
    if latest is None or latest.id != ep.id:
        raise store.HistoryConflict("Review round changed; refresh before restoring")
    root = store._fact(db, artifact_id, restore_target_id, kind="target")
    if "object_ref" not in store._content(db, root):
        raise store.HistoryConflict("Restoration requires a whole-record target")
    state = store._current(db, artifact_id, root.id)
    if state["active"] or state["version"] != expected_version:
        raise store.HistoryConflict("Retirement version changed; refresh before restoring")
    retirement = db.scalar(select(ValidationFact).where(
        ValidationFact.artifact_id == artifact_id,
        ValidationFact.target_id == root.id,
        ValidationFact.kind == "record_retirement_decision").order_by(ValidationFact.sequence.desc()).limit(1))
    if retirement is None or store._content(db, retirement)["identity"]["decision"] != "approve":
        raise store.HistoryConflict("Record has no approved supervisor retirement")
    saved = store._content(db, retirement)
    before = rebuild_inputs(db, source_envelope)
    store.apply_decisions_in_transaction(db, context.history_scope, artifact_id,
        operation_id="record-restore:" + operation_id, actor=identity["actor"], entries=[{
            "target_id": root.id, "expected_version": expected_version, "action": "restore", "reason": reason}])
    updated = source_envelope.model_copy(deep=True)
    updated.extracted_objects.append(CuratableObjectEnvelope.model_validate(saved["entry"]))
    # Source projection replays the latest selected extraction for this stable ID.
    from src.lib.validation_history.projection import source_envelope_in_transaction
    updated = source_envelope_in_transaction(db, context.history_scope, artifact_id, updated)
    after = rebuild_inputs(db, updated)
    # Restore prior extraction-only/curator coverage; validation coverage is rebuilt
    # from the saved canonical binding definitions by the trusted publication callback.
    restored = [item for item in saved["previous_obligations"]
                if item["mode"] != "validation"
                and root.id in store._target_lineage(db, artifact_id, item["target_id"])]
    fact = append_fact(db, artifact_id, operation_id="record-restoration:" + operation_id,
        kind="record_restoration", target_id=root.id, parent_id=retirement.id,
        body={"identity": identity, "restored_obligations": restored})
    changed = source_inputs.update(db, artifact_id, operation_id=operation_id,
        before=before, after=after, selection_id=fact.id)
    publication = publish(db, updated, after, changed, fact.id)
    response = {"restoration_id": fact.id, "target_id": root.id, "publication": publication,
                "source_state_sha256": selections.source_state_digest(db, artifact_id)}
    if publication["ready"]:
        raise store.HistoryConflict("Restoration must reintroduce pending review coverage")
    append_fact(db, artifact_id, operation_id="record-restore-response:" + operation_id,
        kind="record_restoration_response", parent_id=fact.id, body={"identity": identity, "response": response})
    return response

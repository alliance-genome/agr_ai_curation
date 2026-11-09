"""Whole-record retirement: an approved extractor recommendation or the review actor's own decision."""
from sqlalchemy import select

from src.lib.validation_history import selections, store
from src.lib.validation_history.curator_authority import active_overlays, contribution_source
from src.lib.validation_history.projection import without_records
from src.lib.validation_history.record_lifecycle import RETIRING_DECISIONS, retired_records
from src.models.sql.validation_history import ValidationFact
from src.schemas.validation_followup import FollowupEntryResult
from . import admission, source_inputs
from .audit import append_fact
from .decisions import bounded_text
from .record_scope import allowed_units
from .specialist_context import entry_coverage


# A curator-requested round names the records the curator asked to re-review; the
# curator, not the review actor, decides whether those leave the results.
CURATOR_ROUND_REFUSAL = ("The curator asked to re-review this record in this round, so it cannot be retired here; "
                         "accept it or leave it pending with a reason, and the curator decides")


def _require_no_curator_authority(db, artifact_id, root, descendants, source_envelope):
    """A curator decision, row decision, edit or authored record outranks any review actor."""
    if store._has_curator_subject(db, artifact_id, root.id):
        raise store.HistoryConflict("Curator authority prevents supervisor retirement")
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
        # An edit on a validated reference this record's answer contributed is the
        # curator's edit of this record too.
        contributor = overlay.get("contributor_completion_id")
        if (any(change["target_id"] == root.id for change in overlay["changes"])
                or (contributor is not None and contribution_source(db, artifact_id, contributor)[0] == root.id)):
            raise store.HistoryConflict("Curator edits prevent supervisor retirement")


# The path that retired a record, by its saved decision: the review actor's own
# retire, or its approval of an extraction agent's recommendation.
RETIRED_BY = {"retire": "review", "approve": "extractor_recommendation"}


def retired_in_review(db, artifact_id, episode_id):
    """Records this review round retired that are still retired, in record order.

    The review actor's own retirement is saved under the round; an approved
    recommendation under its extraction contribution, whose follow-up request the
    round made. A declined recommendation retires nothing and a restored record is
    current again, so neither is listed. Each item keeps the saved reason and the
    record entry as it was retired.
    """
    result = []
    for record in retired_records(db, artifact_id):
        fact = store._fact(db, artifact_id, record["retirement"]["fact_id"], kind="record_retirement_decision")
        body = store._content(db, fact)
        round_id = fact.parent_id
        if record["proposal"] is not None:
            contribution = store._fact(db, artifact_id, record["proposal"]["fact_id"], kind="extraction_contribution")
            round_id = store._fact(db, artifact_id, contribution.parent_id,
                                   kind="extraction_followup_request").parent_id
        if round_id == episode_id:
            result.append({"record_target_id": record["target_id"], "reason": body["identity"]["reason"],
                           "retired_by": RETIRED_BY[body["identity"]["decision"]], "entry": body["entry"]})
    return result


def _unresolved_references(envelope):
    """Each record with references to a record absent from this envelope, and those reference keys.

    The envelope's own rule: a record's object references, and the objects its
    field references name, resolve by object_id or pending_ref_id.
    """
    present = {key for obj in envelope.extracted_objects for key in obj.ref_keys()}
    found = []
    for obj in envelope.extracted_objects:
        keys = [ref.ref_key() for ref in (*obj.object_refs, *(field.object_ref for field in obj.field_refs))]
        missing = list(dict.fromkeys(key for key in keys if key not in present))
        if missing:
            found.append((obj, missing))
    return found


def _record_label(domain_pack_id, entry):
    """The label a curator sees on this record's workspace row."""
    from src.lib.curation_workspace.adapter_registry import resolve_curation_domain_pack_by_id
    from src.lib.domain_packs.materialization import object_display_label

    pack = resolve_curation_domain_pack_by_id(domain_pack_id)
    if pack is None:
        raise store.HistoryConflict("Saved domain pack is unavailable")
    return object_display_label(pack.metadata, entry)


def _refuse_retiring_referenced(source_envelope, updated, retired_ids, *, approval):
    """A record another current record still refers to is retired only together with it.

    Mechanics only: which records belong together stays the reviewer's choice, and
    nothing is retired automatically.
    """
    retired = {key: obj for obj in source_envelope.extracted_objects
               if obj.metadata["validation_history"]["target_id"] in retired_ids for key in obj.ref_keys()}
    referrers = {}
    for referrer, keys in _unresolved_references(updated):
        for key in keys:
            if key in retired:
                target_id = retired[key].metadata["validation_history"]["target_id"]
                referrers.setdefault(target_id, {})[id(referrer)] = referrer
    if not referrers:
        return
    problems = []
    for target_id, found in referrers.items():
        names = " and ".join(
            f'record "{_record_label(source_envelope.domain_pack_id, obj)}" '
            f'({obj.metadata["validation_history"]["target_id"]})' for obj in found.values())
        verb, them = ("refers", "it") if len(found) == 1 else ("refer", "them")
        if approval:
            problems.append(f"{names} still {verb} to the record this recommendation retires; retire {them} "
                            "first with record_validation_review, then approve this recommendation")
        else:
            problems.append(f"{target_id}: {names} still {verb} to this record; retire them together in this save, "
                            "or keep this record")
    raise store.HistoryConflict("; ".join(problems))


def _refuse_restoring_with_retired_references(db, artifact_id, updated, root_id):
    """A restored record that refers to a record still retired waits for that record's restore."""
    missing = {key for obj, keys in _unresolved_references(updated)
               if obj.metadata["validation_history"]["target_id"] == root_id for key in keys}
    if not missing:
        return
    from src.schemas.domain_envelope import CuratableObjectEnvelope
    labels = []
    for record in retired_records(db, artifact_id):
        body = store._content(db, store._fact(db, artifact_id, record["retirement"]["fact_id"],
                                              kind="record_retirement_decision"))
        entry = CuratableObjectEnvelope.model_validate(body["entry"])
        if missing.intersection(entry.ref_keys()):
            labels.append(f'"{_record_label(updated.domain_pack_id, entry)}"')
    if not labels:
        # Not a retired record, so not the curator's to fix: publication's
        # integrity check stops it as a system error.
        return
    if len(labels) == 1:
        raise store.HistoryConflict(f"This record refers to {labels[0]}, which is still removed from the results; "
                                    "restore that record first")
    raise store.HistoryConflict("This record refers to " + " and ".join(labels) + ", which are still removed "
                                "from the results; restore those records first")


def _record_targets(targets, root_id):
    """The record root and every target beneath it, by recorded parent links."""
    covered = {root_id}
    while True:
        expanded = covered | {fact.id for fact in targets if fact.parent_id in covered}
        if expanded == covered:
            return covered
        covered = expanded


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
    if retirement_decision == "approve" and descendants.intersection(episode["review_target_ids"] or ()):
        raise store.HistoryConflict(CURATOR_ROUND_REFUSAL)
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
    updated = None
    if retirement_decision == "approve":
        _require_no_curator_authority(db, artifact_id, root, descendants, source_envelope)
        updated = without_records(source_envelope.model_copy(deep=True), {root.id})
        _refuse_retiring_referenced(source_envelope, updated, {root.id}, approval=True)
    before = rebuild_inputs(db, source_envelope) if retirement_decision == "approve" else None
    fact = append_fact(db, artifact_id, operation_id="record-retirement:" + operation_id,
        kind="record_retirement_decision", target_id=root.id, parent_id=contribution.id,
        body={"identity": identity, "origin": "extractor_recommendation", "proposal": contribution_ref,
              "evidence_record_ids": proposal.evidence_record_ids,
              "previous_obligations": episode["obligations"], "entry": current})
    publication, changed = None, []
    if retirement_decision == "approve":
        store.apply_decisions_in_transaction(db, context.history_scope, artifact_id,
            operation_id="record-retire:" + operation_id, actor=identity["actor"], entries=[{
                "target_id": root.id, "expected_version": state["version"],
                "action": "retire", "reason": reason}])
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


def retire_in_transaction(db, context, *, operation_id, records, expected_source_sha,
                          source_envelope, rebuild_inputs, publish):
    """The review actor retires whole records it judged not to answer the question.

    records: [{"target_id", "expected_version", "reason", "evidence"}]: the record
    root, its pinned version (None when the caller pinned only a validator field;
    the expected source digest then pins the record's lifecycle), and the packet E
    handles mapped to the exact entries the actor cited. The actor's scope judgment
    and reason are saved, never recomputed: code checks only authority (a review
    actor in its own open round, no curator decision or authorship on the record),
    the round's record scope, structure (an active whole record in the current
    source) and history. The record's validator bindings leave coverage through the
    same publication as an approved extractor recommendation. Authority comes from
    the review actor type, not a model name, so code may call this for another
    review actor.
    """
    ep, _ = admission.lock_episode(db, context.history_scope, context.episode_id)
    artifact_id = ep.artifact_id
    if context.actor_type != "agent":
        raise store.HistoryAccessError("Only a review actor can retire a record in review")
    bounded_text(operation_id, ep.policy["instruction_chars"], "Operation ID")
    if not records or len(records) > ep.policy["decision_entries"]:
        raise ValueError("Retirements exceed configured bounds")
    if len({record["target_id"] for record in records}) != len(records):
        raise ValueError("Name each retired record once")
    for record in records:
        bounded_text(record["reason"], ep.policy["instruction_chars"], "Retirement reason")
    actor = {"type": context.actor_type, "id": context.actor_id}
    identity = {"actor": actor, "decision": "retire", "source_sha": expected_source_sha, "records": records}
    existing = db.scalar(select(ValidationFact).where(
        ValidationFact.artifact_id == artifact_id,
        ValidationFact.operation_id == "record-retirement-response:" + operation_id))
    if existing is not None:
        body = store._content(db, existing)
        if body["identity"] != store.canonical(identity)[1]:
            raise store.HistoryConflict("Retirement operation identity conflict")
        return body["response"]
    if selections.source_state_digest(db, artifact_id) != expected_source_sha:
        raise store.HistoryConflict("Records changed after this packet was read; read a fresh packet")
    episode = selections.effective_episode(db, store._fact(db, artifact_id, ep.id))
    permitted = allowed_units(episode)
    requested = set(episode["review_target_ids"] or ())
    before = rebuild_inputs(db, source_envelope)
    updated = source_envelope.model_copy(deep=True)
    facts, entries = [], []
    targets = list(db.scalars(select(ValidationFact).where(
        ValidationFact.artifact_id == artifact_id, ValidationFact.kind == "target")))
    for record in records:
        try:
            root = store._fact(db, artifact_id, record["target_id"], kind="target")
            if "object_ref" not in store._content(db, root):
                raise store.HistoryConflict("Retirement requires a whole-record target")
            store._require_active_target(db, artifact_id, root.id)
            descendants = _record_targets(targets, root.id)
            # Authority and provenance are checked before scope, so a curator's
            # record or a later replacement is refused for what it is.
            _require_no_curator_authority(db, artifact_id, root, descendants, source_envelope)
            if not store._has_baseline_subject(db, artifact_id, root.id):
                raise store.HistoryConflict(
                    "This record is not an original extraction (a later correction added or replaced it), "
                    "so review cannot retire it; accept it or leave it pending with a reason")
            if not descendants.intersection(permitted):
                raise store.HistoryAccessError("Retirement is outside the requested record scope")
            if descendants.intersection(requested):
                raise store.HistoryConflict(CURATOR_ROUND_REFUSAL)
            matches = [obj for obj in source_envelope.extracted_objects
                       if obj.metadata["validation_history"]["target_id"] == root.id]
            if len(matches) != 1:
                raise store.HistoryConflict("Retirement entry is not in the current source")
        except store.HistoryAccessError:
            raise  # Authorization keeps its own type and never names a record.
        except store.HistoryConflict as error:
            raise store.HistoryConflict(f"{record['target_id']}: {error}") from error
        facts.append(append_fact(db, artifact_id,
            operation_id="record-retirement:" + operation_id + ":" + root.id,
            kind="record_retirement_decision", target_id=root.id, parent_id=ep.id,
            body={"identity": {"actor": actor, "decision": "retire", "target_id": root.id,
                               "source_sha": expected_source_sha, "reason": record["reason"]},
                  "origin": "review", "evidence": record["evidence"],
                  "previous_obligations": episode["obligations"],
                  "entry": matches[0].model_dump(mode="json")}))
        pinned = record["expected_version"]
        entries.append({"target_id": root.id, "action": "retire", "reason": record["reason"],
                        "expected_version": store._current(db, artifact_id, root.id)["version"]
                        if pinned is None else pinned})
    retired_ids = {fact.target_id for fact in facts}
    updated = without_records(updated, retired_ids)
    _refuse_retiring_referenced(source_envelope, updated, retired_ids, approval=False)
    store.apply_decisions_in_transaction(db, context.history_scope, artifact_id,
        operation_id="record-retire:" + operation_id, actor=actor, entries=entries)
    after = rebuild_inputs(db, updated)
    # One coverage revision and publication carry the whole batch.
    changed = source_inputs.update(db, artifact_id, operation_id=operation_id,
        before=before, after=after, selection_id=facts[0].id)
    publication = publish(db, updated, after, changed, facts[0].id)
    response = {"retirement_ids": {fact.target_id: fact.id for fact in facts},
                "selection_id": facts[0].id, "affected_bindings": changed, "publication": publication,
                "source_state_sha256": selections.source_state_digest(db, artifact_id)}
    append_fact(db, artifact_id, operation_id="record-retirement-response:" + operation_id,
        kind="record_retirement_response", parent_id=facts[0].id,
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
    if retirement is None or store._content(db, retirement)["identity"]["decision"] not in RETIRING_DECISIONS:
        raise store.HistoryConflict("Record has no approved supervisor retirement")
    saved = store._content(db, retirement)
    before = rebuild_inputs(db, source_envelope)
    store.apply_decisions_in_transaction(db, context.history_scope, artifact_id,
        operation_id="record-restore:" + operation_id, actor=identity["actor"], entries=[{
            "target_id": root.id, "expected_version": expected_version, "action": "restore", "reason": reason}])
    updated = source_envelope.model_copy(deep=True)
    updated.extracted_objects.append(CuratableObjectEnvelope.model_validate(saved["entry"]))
    # The saved entry keeps the source's references, so they are checked before replay.
    _refuse_restoring_with_retired_references(db, artifact_id, updated, root.id)
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

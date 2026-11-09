"""Explicit curator value overlays and version-scoped revalidation authority.

Overlays never rewrite extraction targets. They are replayed after selected
validator output until an authorized, accepted replacement supersedes them.
"""
from copy import deepcopy
from sqlalchemy import select
from src.models.sql.validation_history import ValidationFact
from src.schemas.domain_envelope import parse_field_path
from . import store


def _facts(db, artifact_id, kind, through_sequence=None):
    query = select(ValidationFact).where(
        ValidationFact.artifact_id == artifact_id, ValidationFact.kind == kind,
    )
    if through_sequence is not None:
        query = query.where(ValidationFact.sequence <= through_sequence)
    return db.scalars(query.order_by(ValidationFact.sequence))



def _path(parts):
    result = ""
    for part in parts:
        result += f"[{part}]" if isinstance(part, int) else (("." if result else "") + part)
    return result


def _parts(path):
    """Internal value containers may name the object root; public paths stay strict."""
    return [] if path == "" else parse_field_path(path)


def anchor_for_path(refs, path):
    parts = _parts(path)
    for length in range(len(parts), 0, -1):
        prefix = _path(parts[:length])
        target = refs["fields"].get(prefix)
        index = parts[length - 1]
        if target is None and isinstance(index, int):
            ids = refs["elements"].get(_path(parts[:length - 1]), [])
            target = ids[index] if 0 <= index < len(ids) else None
        if target is not None:
            return {"target_id": target, "suffix": list(parts[length:])}
    return {"target_id": refs["target_id"], "suffix": list(parts)}


def anchored_path(refs, anchor):
    if anchor["target_id"] == refs["target_id"]:
        return _path(anchor["suffix"])
    for path, target in refs["fields"].items():
        if target == anchor["target_id"]:
            return _path([*parse_field_path(path), *anchor["suffix"]])
    for path, targets in refs["elements"].items():
        if anchor["target_id"] in targets:
            return _path([*parse_field_path(path), targets.index(anchor["target_id"]), *anchor["suffix"]])
    raise store.HistoryConflict("Curator value anchor is no longer in this projection")



def rebind_output_path(path, old_target_path, current_target_path):
    """Move indexed addresses with their stable selected target, without matching text."""
    original = parse_field_path(path)
    old = parse_field_path(old_target_path) if old_target_path else []
    current = parse_field_path(current_target_path) if current_target_path else []
    rebound = list(original)
    for index, part in enumerate(old):
        if isinstance(part, int) and index < len(original) and index < len(current) and original[:index] == old[:index]:
            rebound[index] = current[index]
    return _path(rebound)


def overlay_refs(refs_by_target, refs_by_object, body):
    """The current object an edit applies to, or None when it is not in the projection.

    An edit on a validated reference follows that reference by its exact object ID
    (fixed by type and canonical ID) when another validator run materializes the
    same reference under a new target (Chris, 2026-11-09). Any other edit stays on
    its own target. No value or text is compared.
    """
    refs = refs_by_target.get(body["target_id"])
    if refs is None and body.get("contributor_completion_id") is not None:
        refs = refs_by_object.get(body["object_id"])
    return refs


def overlay_path(refs, body, anchor, recorded_path):
    """An anchor resolves on its own target; on a re-materialized reference, the recorded path does."""
    return anchored_path(refs, anchor) if refs["target_id"] == body["target_id"] else recorded_path


def projection_refs(envelope):
    """Each object's stable references, by its root target and by its exact object identity."""
    by_target, by_object = {}, {}
    for obj in envelope.extracted_objects:
        refs = obj.metadata["validation_history"]
        by_target[refs["target_id"]] = refs
        by_object[obj.object_id or obj.pending_ref_id] = refs
    return by_target, by_object


def bind_patch_anchors(envelope, body):
    body = deepcopy(body)
    refs = {obj.metadata["validation_history"]["target_id"]: obj.metadata["validation_history"]
            for obj in envelope.extracted_objects}
    body["anchor"] = anchor_for_path(refs[body["target_id"]], body["value_path"])
    for change in body["changes"]:
        change["anchor"] = anchor_for_path(refs[change["target_id"]], change["path"])
    return body

def active_overlays(db, artifact_id, *, through_sequence=None):
    heads = {}
    released = {item for fact in _facts(db, artifact_id, "curator_revalidation_release", through_sequence)
                for item in store._content(db, fact)["overlay_ids"]}
    for fact in _facts(db, artifact_id, "curator_value", through_sequence):
        body = store._content(db, fact)
        heads[(body["anchor"]["target_id"], tuple(body["anchor"]["suffix"]))] = (fact, body)
    active = []
    for fact, body in heads.values():
        if fact.id in released or body["action"] == "withdraw_override":
            continue
        try:
            store._require_active_target(db, artifact_id, body["anchor"]["target_id"])
        except store.HistoryConflict:
            continue  # Retiring a source also retires its override; history remains.
        active.append((fact, body))
    return sorted(active, key=lambda item: item[0].sequence)


def has_active_overlay(db, envelope, object_id, path):
    """Server-only authority context for a subsequent curator edit."""
    history = envelope.metadata.get("validation_history")
    if history is None:
        return False
    obj = next((obj for obj in envelope.extracted_objects
                if object_id in {obj.object_id, obj.pending_ref_id}), None)
    if obj is None:
        return False
    refs = obj.metadata.get("validation_history")
    if refs is None:
        return False
    by_target, by_object = projection_refs(envelope)
    return any(overlay_refs(by_target, by_object, body) is refs and
               _overlap(overlay_path(refs, body, body["anchor"], body["value_path"]), path)
               for _, body in active_overlays(db, history["artifact_id"]))


def record_patch(db, scope, artifact_id, *, event_id, actor_id, body):
    # One fact per independent change keeps authorization and replacement exact.
    # Declared compound identities and their mirrors are the only atomic group.
    body = {**body, "source_event_id": event_id}
    if body.get("atomic"):
        return _record_patch_unit(db, scope, artifact_id, event_id=event_id, actor_id=actor_id, body=body)
    ids = []
    for index, change in enumerate(body["changes"]):
        unit = {**deepcopy(body), "anchor": deepcopy(change["anchor"]),
                "value_path": change["path"], "changes": [deepcopy(change)],
                "source_event_id": event_id, "atomic": False}
        ids.append(_record_patch_unit(db, scope, artifact_id, event_id=f"{event_id}:change:{index}", actor_id=actor_id, body=unit))
    return ids[0] if ids else None


def _record_patch_unit(db, scope, artifact_id, *, event_id, actor_id, body):
    from .selections import _lock_artifact
    _lock_artifact(db, scope, artifact_id, write=True)
    if actor_id != scope.owner_id:
        raise store.HistoryAccessError("Curator authority requires the authenticated owner")
    store._require_active_target(db, artifact_id, body["target_id"])
    body = deepcopy(body)
    previous = next((old for _, old in active_overlays(db, artifact_id)
                     if old["anchor"] == body["anchor"]), None)
    if previous is not None and body["action"] != "withdraw_override":
        merged = deepcopy(previous["changes"])
        for change in body["changes"]:
            anchor = change["anchor"]
            child = tuple(anchor["suffix"])
            ancestor = next((old for old in merged if old["anchor"]["target_id"] == anchor["target_id"]
                and len(old["anchor"]["suffix"]) < len(child)
                and child[:len(old["anchor"]["suffix"])] == tuple(old["anchor"]["suffix"])
                and old["present"]), None)
            if ancestor is not None:
                relative = child[len(ancestor["anchor"]["suffix"]):]
                value = ancestor["value"]
                for token in relative[:-1]:
                    value = value[token]
                if change["present"]:
                    value[relative[-1]] = deepcopy(change["value"])
                else:
                    value.pop(relative[-1], None)
                continue
            merged = [old for old in merged if not (old["anchor"]["target_id"] == anchor["target_id"]
                      and tuple(old["anchor"]["suffix"])[:len(child)] == child)]
            merged.append(change)
        body["changes"] = merged
    return store._append(db, artifact_id, operation_id="curator-value:" + event_id,
        kind="curator_value", target_id=body["target_id"],
        body={**deepcopy(body), "actor_id": actor_id}).id


def _atomic_identity_anchors(db, artifact_id, body, checkpoints):
    """Read full identity membership from the exact immutable edit audit.

    Payload diffs omit unchanged members. Checkpoint-local audit append identity
    also avoids trusting a client-supplied patch_id to be globally unique.
    """
    from src.lib.curation_workspace.models import DomainEnvelopeHistory
    from src.lib.domain_envelopes.persistence import load_domain_envelope, DomainEnvelopePersistenceError
    from src.lib.domain_packs.resolvable_values import CURATOR_OVERRIDE_METADATA_KEY
    from src.models.sql.validation_history import ValidationArtifact, ValidationBody

    artifact = db.get(ValidationArtifact, artifact_id)
    baseline = db.get(ValidationBody, artifact.baseline_sha).content["normalized"]
    envelope_id = baseline["envelope_id"]
    event = db.get(DomainEnvelopeHistory, (envelope_id, body.get("source_event_id")))
    if event is None or event.actor_id != artifact.owner_id:
        raise store.HistoryConflict("Atomic curator identity has no exact owned edit event")
    details = event.event_json["details"]
    recorded = details.get("curator_value")
    if recorded is None or any(recorded.get(key) != body[key]
                               for key in ("target_id", "object_id", "value_path", "anchor")):
        raise store.HistoryConflict("Atomic curator identity event does not match its fact")

    def checkpoint(revision):
        key = (envelope_id, revision)
        if key not in checkpoints:
            try:
                checkpoints[key] = load_domain_envelope(db, envelope_id, revision=revision)
            except DomainEnvelopePersistenceError as error:
                raise store.HistoryConflict("Atomic curator identity checkpoint is unavailable") from error
        return checkpoints[key]

    envelope = checkpoint(event.envelope_revision)
    if envelope.metadata["validation_history"]["artifact_id"] != artifact_id:
        raise store.HistoryConflict("Atomic curator identity checkpoint belongs to another artifact")
    indexed = [item for item in envelope.history if item.event_id == event.event_id]
    if len(indexed) != 1 or indexed[0].model_dump(mode="json") != event.event_json:
        raise store.HistoryConflict("Atomic curator identity event does not match its checkpoint")

    def object_at(saved):
        objects = [obj for obj in saved.extracted_objects
                   if body["object_id"] in {obj.object_id, obj.pending_ref_id}]
        if len(objects) != 1:
            raise store.HistoryConflict("Atomic curator identity object is absent from its checkpoint")
        return objects[0]

    obj = object_at(envelope)
    if obj.metadata["validation_history"]["target_id"] != body["target_id"]:
        raise store.HistoryConflict("Atomic curator identity object target changed")
    previous = object_at(checkpoint(event.envelope_revision - 1))
    audits = obj.metadata.get(CURATOR_OVERRIDE_METADATA_KEY, [])
    old_audits = previous.metadata.get(CURATOR_OVERRIDE_METADATA_KEY, [])
    if audits[:len(old_audits)] != old_audits:
        raise store.HistoryConflict("Atomic curator identity audit prefix changed")
    # Draft materialization can edit distinct identities and checkpoint them once.
    # Other audits in this suffix are valid; this event still needs one exact match.
    matching = [audit for audit in audits[len(old_audits):]
                if details.get("patch_id") and audit.get("patch_id") == details["patch_id"]
                and audit.get("field_path") == details.get("field_path")
                and audit.get("value_path") == body["value_path"]
                and audit.get("actor_id") == event.actor_id]
    if len(matching) != 1:
        raise store.HistoryConflict("Atomic curator identity has no unique appended identity audit")
    audit = matching[0]
    if not isinstance(audit.get("identity"), dict) or not audit["identity"]:
        raise store.HistoryConflict("Atomic curator identity has no exact complete identity audit")
    prefix = body["value_path"] + "." if body["value_path"] else ""
    return [anchor_for_path(obj.metadata["validation_history"], prefix + member)
            for member in audit["identity"]]


def record_revalidation_intent(binding, *, envelope, episode_id, object_id, field_paths, actor_id, guidance=None):
    """Called only for an explicit authenticated revalidation request."""
    with store.SessionLocal.begin() as db:
        return record_revalidation_intent_in_transaction(db, binding, envelope=envelope,
            episode_id=episode_id, object_id=object_id, field_paths=field_paths, actor_id=actor_id, guidance=guidance)


def revalidation_intents(value):
    return [] if value is None else [value] if isinstance(value, dict) else list(value)


def record_revalidation_intent_in_transaction(db, binding, *, envelope, episode_id, object_id, field_paths, actor_id, guidance=None, through_sequence=None):
    from .selections import _lock_artifact
    _lock_artifact(db, binding.scope, binding.artifact_id, write=True)
    if actor_id != binding.scope.owner_id:
        raise store.HistoryAccessError("Revalidation intent requires the authenticated curator")
    store._fact(db, binding.artifact_id, episode_id, kind="review_episode")
    by_target, by_object = projection_refs(envelope)
    checkpoints = {}
    def refs_for(body):
        refs = overlay_refs(by_target, by_object, body)
        if refs is None:
            raise store.HistoryConflict("Curator value anchor is no longer in this projection")
        return refs
    def current_path(body):
        refs = overlay_refs(by_target, by_object, body)
        if refs is None and body.get("contributor_completion_id") is not None:
            # A contributed reference not in the results (its edit is not applied):
            # the edit is still named by the path it was recorded at.
            return body["value_path"]
        return overlay_path(refs_for(body), body, body["anchor"], body["value_path"])
    def requested(body):
        if body["object_id"] == object_id:
            if not field_paths:
                return True
            if body.get("atomic"):
                members = _atomic_identity_anchors(db, binding.artifact_id, body, checkpoints)
                return any(_anchor_covers(db, binding.artifact_id,
                    anchor_for_path(refs_for(body), path), member, atomic=True)
                    for path in field_paths for member in members)
            return any(_covers(path, current_path(body)) for path in field_paths)
        contributor = body.get("contributor_completion_id")
        if contributor is None:
            return False
        completion = store._fact(db, binding.artifact_id, contributor, kind="completion")
        frozen = store._content(db, store._fact(db, binding.artifact_id, completion.parent_id, kind="attempt"))["input"]
        target = store._content(db, store._fact(db, binding.artifact_id, frozen["target_id"], kind="target"))
        root_id = target.get("object_target")
        if root_id is None:
            return False
        root = store._content(db, store._fact(db, binding.artifact_id, root_id, kind="target"))
        if root["object_ref"] != object_id:
            return False
        destinations = {**frozen["request"]["expected_result_fields"], **(frozen["request"].get("optional_result_fields") or {})}
        return not field_paths or any(isinstance(destination, str) and _overlap(destination, path)
            for destination in destinations.values() for path in field_paths)
    active = active_overlays(db, binding.artifact_id, through_sequence=through_sequence)
    for _, body in active:
        if body["object_id"] != object_id or body.get("atomic"):
            continue
        if field_paths and not any(_overlap(current_path(body), path) for path in field_paths):
            continue
        path = current_path(body)
        nested_scope = any(path != requested and _covers(path, requested) for requested in field_paths)
        nested_edit = any(other["object_id"] == object_id and current_path(other) != path
            and _covers(path, current_path(other)) for _, other in active)
        if nested_scope or nested_edit or any(change.get("structural") or not change["present"]
                or isinstance(change.get("value"), (dict, list)) for change in body["changes"]):
            raise store.HistoryConflict("Revalidation cannot replace the structural curator edit at " + current_path(body)
                + "; individual-field validation cannot replace this aggregate edit")
    overlays = [(fact, body) for fact, body in active if requested(body)]
    return store._append(db, binding.artifact_id,
        operation_id="curator-revalidation:" + episode_id + ":" + object_id,
        kind="curator_revalidation_intent", parent_id=episode_id,
        body={"actor_id": actor_id, "object_id": object_id,
              "field_paths": list(field_paths), "overlay_ids": [f.id for f, _ in overlays],
              "overlay_paths": {f.id: current_path(body) for f, body in overlays},
              "guidance": guidance}).id


def _covers(parent, child):
    a, b = tuple(_parts(parent)), tuple(_parts(child))
    return b[:len(a)] == a


def _overlap(left, right):
    a, b = tuple(_parts(left)), tuple(_parts(right))
    return a[:len(b)] == b or b[:len(a)] == a


def _anchor_tokens(db, artifact_id, anchor):
    lineage = []
    target = store._fact(db, artifact_id, anchor["target_id"], kind="target")
    while target is not None:
        lineage.append(("target", target.id))
        target = store._fact(db, artifact_id, target.parent_id, kind="target") if target.parent_id else None
    return [*reversed(lineage), *(("path", part) for part in anchor["suffix"])]


def _anchor_covers(db, artifact_id, parent, child, *, atomic=False):
    a, b = _anchor_tokens(db, artifact_id, parent), _anchor_tokens(db, artifact_id, child)
    return b[:len(a)] == a or (atomic and a[:len(b)] == b)


def release_accepted_overlays(db, scope, artifact_id, *, episode_id, operation_id, accepted_entries):
    """Exact intent + newly accepted completion; never a generic force/refresh."""
    episode = store._fact(db, artifact_id, episode_id, kind="review_episode")
    intents = [store._content(db, fact) for fact in _facts(db, artifact_id, "curator_revalidation_intent")
               if fact.parent_id == episode_id]
    authorized = {oid: intent["overlay_paths"][oid] for intent in intents if intent["actor_id"] == scope.owner_id for oid in intent["overlay_ids"]}
    overlays = active_overlays(db, artifact_id)
    from .selections import manifest_in_transaction
    current_units = manifest_in_transaction(db, scope, artifact_id, episode_id=episode_id)["units"]
    released = set()
    checkpoints = {}
    for entry in accepted_entries:
        if entry["action"] != "accept" or entry.get("subject") in {None, "baseline", "curator"}:
            continue
        completion = store._fact(db, artifact_id, entry["subject"])
        if completion.kind == "validation_source_input":
            continue  # No validator result exists to authorize replacing curator values.
        if completion.kind != "completion":
            raise store.HistoryConflict("Curator revalidation requires a completion")
        attempt = store._fact(db, artifact_id, completion.parent_id, kind="attempt")
        if attempt.sequence <= episode.sequence or store._content(db, completion)["state"] != "completed":
            continue
        frozen = store._content(db, attempt)["input"]
        if frozen.get("review_episode_id") != episode_id:
            continue
        request = frozen["request"]
        target = store._content(db, store._fact(db, artifact_id, frozen["target_id"], kind="target"))
        destinations = list(frozen.get("result_anchors", {}).values())
        unit = store._content(db, store._fact(db, artifact_id, entry["target_id"], kind="target"))
        if unit.get("decision_unit"):
            selected_slots = unit["decision_unit"]["fields"]
            destinations = [anchor for slot, anchor in frozen.get("result_anchors", {}).items() if slot in selected_slots]
        for fact, body in overlays:
            if fact.id not in authorized:
                continue  # Includes a later curator edit: the older intent cannot consume it.
            same_source = target.get("object_target") == body["target_id"]
            contributor = body.get("contributor_completion_id")
            same_contributor = False
            if contributor:
                old_completion = store._fact(db, artifact_id, contributor, kind="completion")
                old_input = store._content(db, store._fact(db, artifact_id, old_completion.parent_id, kind="attempt"))["input"]
                selected_slots = {slot for selected in current_units
                    if selected["eligible"] and selected["selected"] == completion.id
                    for slot in (selected["decision_unit"]["fields"] if selected.get("decision_unit")
                                 else [*request["expected_result_fields"], *(request.get("optional_result_fields") or {})])}
                whole_result = set(request["expected_result_fields"]) | set(request.get("optional_result_fields") or {})
                same_contributor = old_input["target_id"] == frozen["target_id"] and whole_result <= selected_slots
            members = (_atomic_identity_anchors(db, artifact_id, body, checkpoints)
                       if same_source and body.get("atomic") else [body["anchor"]])
            if same_contributor or (same_source and any(_anchor_covers(db, artifact_id, destination, member,
                    atomic=body.get("atomic", False)) for destination in destinations for member in members)):
                released.add(fact.id)
    if released:
        store._append(db, artifact_id, operation_id="curator-release:" + operation_id,
            kind="curator_revalidation_release", parent_id=episode_id,
            body={"overlay_ids": sorted(released), "reason": "Explicit curator revalidation accepted"})


# Projection metadata listing the curator edits whose contributed reference is not
# in the current results, by contributing record. Nothing displays it yet (0.12.0).
NOT_APPLIED_METADATA_KEY = "curator_edits_not_applied"


def contribution_source(db, artifact_id, completion_id):
    """The record a validator answer belongs to, and the validator binding that produced it.

    The pair stays the same across validator runs, including runs whose input
    target changed (a replaced list item). The record is None for a validator
    bound to the envelope itself.
    """
    completion = store._fact(db, artifact_id, completion_id, kind="completion")
    frozen = store._content(db, store._fact(db, artifact_id, completion.parent_id, kind="attempt"))["input"]
    record = store._content(db, store._fact(db, artifact_id, frozen["target_id"], kind="target")).get("object_target")
    return record, frozen["binding_id"]


def _not_applied(db, artifact_id, body, selected_answers):
    """An edit on a contributed reference that no selected answer materializes.

    The reference is followed by its exact object ID, so reaching here means no
    selected answer resolves to it. If an answer is selected for the same record
    and validator, it is a different reference, which only the curator's own
    Revalidate may let replace the edit (D1): review stops. Otherwise (the record
    is retired, or restored and waiting for review) the edit is kept and reported,
    never written as a dangling reference.
    """
    from src.lib.validation_review.admission import CURATOR_REVALIDATION_REQUIRED, ReviewStopped
    contributor = body.get("contributor_completion_id")
    if contributor is None:
        raise store.HistoryConflict("A curator edit names a record that is absent from the selected projection")
    source = contribution_source(db, artifact_id, contributor)
    record = source[0]
    if selected_answers.get(source):
        raise ReviewStopped(CURATOR_REVALIDATION_REQUIRED, resource=record)
    # A validator bound to the envelope itself contributes for no single record.
    record_object = (store._content(db, store._fact(db, artifact_id, record, kind="target"))["object_ref"]
                     if record is not None else None)
    return {"record_object_id": record_object, "object_id": body["object_id"], "value_path": body["value_path"]}


def apply_overlays(db, artifact_id, envelope, selected_answers):
    """Apply only exact target edits. Never transfer a curator edit by similar text.

    selected_answers maps each (record, validator binding) to the completions
    selected for it (see contribution_source).
    """
    from src.lib.domain_envelopes.patches import set_payload_value, _payload_value
    from .assessments import open_assessments, settle_assessments
    result = envelope.model_copy(deep=True)
    result.metadata.pop(NOT_APPLIED_METADATA_KEY, None)
    not_applied = []
    objects = {obj.metadata["validation_history"]["target_id"]: obj for obj in result.extracted_objects}
    by_object = {obj.object_id or obj.pending_ref_id: obj for obj in result.extracted_objects}
    for _, body in active_overlays(db, artifact_id):
        store._require_active_target(db, artifact_id, body["target_id"])
        edited = objects.get(body["target_id"])
        if edited is None and body.get("contributor_completion_id") is not None:
            edited = by_object.get(body["object_id"])  # The same reference, materialized by another run.
        if edited is None:
            not_applied.append(_not_applied(db, artifact_id, body, selected_answers))
            continue
        for change in body["changes"]:
            try:
                store._require_active_target(db, artifact_id, change["anchor"]["target_id"])
            except store.HistoryConflict:
                continue  # Retired list elements remain in history, not current output.
            # A change may name another object (a mirrored value); the edited object may be re-materialized.
            if change["target_id"] == body["target_id"]:
                obj = edited
                current_path = overlay_path(obj.metadata["validation_history"], body, change["anchor"], change["path"])
            else:
                obj = objects.get(change["target_id"])
                if obj is None:
                    raise store.HistoryConflict("A curator edit change names an object absent from the projection")
                current_path = anchored_path(obj.metadata["validation_history"], change["anchor"])
            findings = open_assessments(result, obj, current_path)
            # A whole-collection value overlay also preserves removal/change of
            # assessed leaves. Capture exact old values before replay; unchanged
            # siblings require their own explicit acknowledgement overlay.
            structural_findings = open_assessments(result, obj, current_path, descendants=True) if change.get("structural") else []
            previous_values = {finding.finding_id: _payload_value(obj.payload, finding.field_ref.field_path)
                               for finding in structural_findings if finding.field_ref is not None}
            result = settle_assessments(result, {finding.finding_id for finding in findings})
            if change["present"]:
                if current_path:
                    set_payload_value(obj.payload, current_path, deepcopy(change["value"]))
                else:
                    obj.payload = deepcopy(change["value"])
            else:
                parts = parse_field_path(current_path)
                value = obj.payload
                for part in parts[:-1]:
                    value = value[part]
                value.pop(parts[-1], None)
            result = settle_assessments(result, {
                finding.finding_id for finding in structural_findings
                if finding.field_ref is not None and previous_values[finding.finding_id] != _payload_value(obj.payload, finding.field_ref.field_path)
            })
    if not_applied:
        result.metadata[NOT_APPLIED_METADATA_KEY] = not_applied
    return result


def payload_changes(before, after, target_id, path=""):
    """Exact structural patch, including removed keys, without semantic matching."""
    if before == after:
        return []
    if isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        return [change for index, (old, new) in enumerate(zip(before, after, strict=True))
                for change in payload_changes(old, new, target_id, f"{path}[{index}]")]
    if isinstance(after, dict) and (isinstance(before, dict) or (before is None and after)):
        before = before or {}
        changes = []
        for key in sorted(before.keys() | after.keys()):
            child = f"{path}.{key}" if path else key
            if key not in after:
                changes.append({"target_id": target_id, "path": child, "present": False})
            elif key not in before:
                if isinstance(after[key], dict) and after[key]:
                    changes.extend(payload_changes({}, after[key], target_id, child))
                else:
                    changes.append({"target_id": target_id, "path": child, "present": True, "value": deepcopy(after[key])})
            else:
                changes.extend(payload_changes(before[key], after[key], target_id, child))
        return changes
    return [{"target_id": target_id, "path": path, "present": True, "value": deepcopy(after),
             "structural": isinstance(before, (dict, list))}]

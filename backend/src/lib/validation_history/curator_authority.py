"""Explicit curator value overlays and version-scoped revalidation authority.

Overlays never rewrite extraction targets. They are replayed after selected
validator output until an authorized, accepted replacement supersedes them.
"""
from copy import deepcopy
from sqlalchemy import select
from src.models.sql.validation_history import ValidationFact
from src.schemas.domain_envelope import parse_field_path
from . import store


def _facts(db, artifact_id, kind):
    return db.scalars(select(ValidationFact).where(
        ValidationFact.artifact_id == artifact_id, ValidationFact.kind == kind,
    ).order_by(ValidationFact.sequence))



def _path(parts):
    result = ""
    for part in parts:
        result += f"[{part}]" if isinstance(part, int) else (("." if result else "") + part)
    return result


def anchor_for_path(refs, path):
    parts = parse_field_path(path)
    for length in range(len(parts), 0, -1):
        prefix = _path(parts[:length])
        target = refs["fields"].get(prefix)
        if target is None and isinstance(parts[length - 1], int):
            ids = refs["elements"].get(_path(parts[:length - 1]), [])
            index = parts[length - 1]
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
    old = parse_field_path(old_target_path or "")
    current = parse_field_path(current_target_path)
    rebound = list(original)
    for index, part in enumerate(old):
        if isinstance(part, int) and index < len(original) and index < len(current) and original[:index] == old[:index]:
            rebound[index] = current[index]
    return _path(rebound)


def bind_patch_anchors(envelope, body):
    body = deepcopy(body)
    refs = {obj.metadata["validation_history"]["target_id"]: obj.metadata["validation_history"]
            for obj in envelope.extracted_objects}
    body["anchor"] = anchor_for_path(refs[body["target_id"]], body["value_path"])
    for change in body["changes"]:
        change["anchor"] = anchor_for_path(refs[change["target_id"]], change["path"])
    return body

def active_overlays(db, artifact_id):
    heads = {}
    released = {item for fact in _facts(db, artifact_id, "curator_revalidation_release")
                for item in store._content(db, fact)["overlay_ids"]}
    for fact in _facts(db, artifact_id, "curator_value"):
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
    return active


def record_patch(db, scope, artifact_id, *, event_id, actor_id, body):
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


def record_revalidation_intent(binding, *, envelope, episode_id, object_id, field_paths, actor_id, guidance=None):
    """Called only for an explicit authenticated revalidation request."""
    from .selections import _lock_artifact
    with store.SessionLocal.begin() as db:
        _lock_artifact(db, binding.scope, binding.artifact_id, write=True)
        if actor_id != binding.scope.owner_id:
            raise store.HistoryAccessError("Revalidation intent requires the authenticated curator")
        store._fact(db, binding.artifact_id, episode_id, kind="review_episode")
        refs = {obj.metadata["validation_history"]["target_id"]: obj.metadata["validation_history"]
                for obj in envelope.extracted_objects}
        def current_path(body):
            return anchored_path(refs[body["target_id"]], body["anchor"])
        def requested(body):
            if body["object_id"] == object_id:
                return not field_paths or any(_overlap(current_path(body), path) for path in field_paths)
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
        overlays = [(fact, body) for fact, body in active_overlays(db, binding.artifact_id) if requested(body)]
        return store._append(db, binding.artifact_id,
            operation_id="curator-revalidation:" + episode_id + ":" + object_id,
            kind="curator_revalidation_intent", parent_id=episode_id,
            body={"actor_id": actor_id, "object_id": object_id,
                  "field_paths": list(field_paths), "overlay_ids": [f.id for f, _ in overlays],
                  "overlay_paths": {f.id: current_path(body) for f, body in overlays},
                  "guidance": guidance}).id


def _overlap(left, right):
    a, b = tuple(parse_field_path(left)), tuple(parse_field_path(right))
    return a[:len(b)] == b or b[:len(a)] == a


def release_accepted_overlays(db, scope, artifact_id, *, episode_id, operation_id, accepted_entries):
    """Exact intent + newly accepted completion; never a generic force/refresh."""
    episode = store._fact(db, artifact_id, episode_id, kind="review_episode")
    intents = [store._content(db, fact) for fact in _facts(db, artifact_id, "curator_revalidation_intent")
               if fact.parent_id == episode_id]
    authorized = {oid: intent["overlay_paths"][oid] for intent in intents if intent["actor_id"] == scope.owner_id for oid in intent["overlay_ids"]}
    overlays = active_overlays(db, artifact_id)
    released = set()
    for entry in accepted_entries:
        if entry["action"] != "accept" or entry.get("subject") in {None, "baseline", "curator"}:
            continue
        completion = store._fact(db, artifact_id, entry["subject"], kind="completion")
        attempt = store._fact(db, artifact_id, completion.parent_id, kind="attempt")
        if attempt.sequence <= episode.sequence or store._content(db, completion)["state"] != "completed":
            continue
        frozen = store._content(db, attempt)["input"]
        if frozen.get("review_episode_id") != episode_id:
            continue
        request = frozen["request"]
        target = store._content(db, store._fact(db, artifact_id, frozen["target_id"], kind="target"))
        destinations = list(request["expected_result_fields"].values()) + list((request.get("optional_result_fields") or {}).values())
        unit = store._content(db, store._fact(db, artifact_id, entry["target_id"], kind="target"))
        if unit.get("decision_unit"):
            selected_slots = unit["decision_unit"]["fields"]
            destinations = [path for slot, path in {**request["expected_result_fields"], **(request.get("optional_result_fields") or {})}.items() if slot in selected_slots]
        for fact, body in overlays:
            if fact.id not in authorized:
                continue  # Includes a later curator edit: the older intent cannot consume it.
            same_source = target.get("object_target") == body["target_id"]
            contributor = body.get("contributor_completion_id")
            same_contributor = False
            if contributor:
                old_completion = store._fact(db, artifact_id, contributor, kind="completion")
                old_input = store._content(db, store._fact(db, artifact_id, old_completion.parent_id, kind="attempt"))["input"]
                same_contributor = old_input["target_id"] == frozen["target_id"] and not unit.get("decision_unit")
            if same_contributor or (same_source and any(isinstance(path, str) and _overlap(path, authorized[fact.id]) for path in destinations)):
                released.add(fact.id)
    if released:
        store._append(db, artifact_id, operation_id="curator-release:" + operation_id,
            kind="curator_revalidation_release", parent_id=episode_id,
            body={"overlay_ids": sorted(released), "reason": "Explicit curator revalidation accepted"})


def apply_overlays(db, artifact_id, envelope):
    """Apply only exact target edits. Never transfer a curator edit by similar text."""
    from src.lib.domain_envelopes.patches import set_payload_value
    result = envelope.model_copy(deep=True)
    objects = {obj.metadata["validation_history"]["target_id"]: obj for obj in result.extracted_objects}
    for _, body in active_overlays(db, artifact_id):
        store._require_active_target(db, artifact_id, body["target_id"])
        for change in body["changes"]:
            obj = objects.get(change["target_id"])
            if obj is None:
                raise store.HistoryConflict("Replacing this record requires explicit curator revalidation authority")
            current_path = anchored_path(obj.metadata["validation_history"], change["anchor"])
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
    return result


def payload_changes(before, after, target_id, path=""):
    """Exact structural patch, including removed keys, without semantic matching."""
    if before == after:
        return []
    if isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        return [change for index, (old, new) in enumerate(zip(before, after, strict=True))
                for change in payload_changes(old, new, target_id, f"{path}[{index}]")]
    if isinstance(before, dict) and isinstance(after, dict):
        changes = []
        for key in sorted(before.keys() | after.keys()):
            child = f"{path}.{key}" if path else key
            if key not in after:
                changes.append({"target_id": target_id, "path": child, "present": False})
            elif key not in before:
                changes.append({"target_id": target_id, "path": child, "present": True, "value": deepcopy(after[key])})
            else:
                changes.extend(payload_changes(before[key], after[key], target_id, child))
        return changes
    return [{"target_id": target_id, "path": path, "present": True, "value": deepcopy(after)}]

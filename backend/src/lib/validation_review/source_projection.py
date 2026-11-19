"""Apply approved extractor relationships without changing any original target.

This planner is pure. The caller commits target additions, retirements, source
selection and affected validator input versions in one transaction.
"""
from copy import deepcopy
from uuid import NAMESPACE_URL, uuid5

from src.lib.domain_envelopes.patches import set_payload_value
from src.lib.validation_history import store
from src.lib.validation_history.curator_authority import anchor_for_path
from src.schemas.domain_envelope import parse_field_path
from .item_relationships import validate, value_at


def apply_value(payload, proposal, structure):
    """Write one proposed value as the saved structure reads it.

    A null, at any depth of the value, on a field the structure declares
    may-be-absent-but-not-null leaves that field out; only the declaration
    decides, never the value. Without a saved structure (built-in packs) the
    value is written as proposed. A path that cannot be written raises
    ValueError; leaving out a field whose object parent is already absent
    changes nothing, but a list index past the end cannot be written.
    """
    path, value = proposal.field_path, proposal.value
    if structure is None:
        set_payload_value(payload, path, deepcopy(value))
        return
    if value is not None or not structure.absent_when_null(path):
        set_payload_value(payload, path, structure.without_absent_nulls(path, value))
        return
    *parents, key = parse_field_path(path)
    container = payload
    for part in parents:
        if isinstance(part, int):
            if not isinstance(container, list) or part >= len(container):
                raise ValueError(f"Cannot leave out '{path}' because a list index is missing")
            container = container[part]
        elif isinstance(container, dict):
            container = container.get(part)
            if container is None:
                return  # The field's object is absent, so the field already is.
        else:
            raise ValueError(f"Cannot leave out '{path}' through a non-object parent")
    if not isinstance(container, dict):
        raise ValueError(f"Cannot leave out '{path}' through a non-object parent")
    container.pop(key, None)


def plan(source_entry, proposals, *, contribution_id, structure=None):
    result = deepcopy(source_entry)
    old_refs = source_entry["metadata"]["validation_history"]
    element_parents = {target: anchor_for_path(old_refs, path)["target_id"]
                       for path, targets in old_refs["elements"].items() for target in targets}
    fields_by_parent = {}
    for path, target in old_refs["fields"].items():
        parts = list(parse_field_path(path))
        if not parts:
            raise store.HistoryConflict("Source field must name a path")
        parent_path = path[:path.rfind(".")] if len(parts) > 1 else ""
        # For a field on a list item, the prefix ends before the final '.name'.
        parent = old_refs["target_id"] if not parent_path else anchor_for_path(old_refs, parent_path)["target_id"]
        fields_by_parent[(parent, parts[-1])] = target
    mappings = {}
    paths = [parse_field_path(item.field_path) for item in proposals]
    for index, path in enumerate(paths):
        if any(path == other[:len(path)] or other == path[:len(other)]
               for other in paths[index + 1:]):
            raise store.HistoryConflict("Selected source fields must not overlap")
    for proposal in proposals:
        mappings.update(validate(source_entry, proposal))
        apply_value(result["payload"], proposal, structure)
    refs = {"target_id": old_refs["target_id"], "fields": {}, "elements": {}}
    additions = []
    relationships = []

    def register(identity, *, parent, path, value, predecessors=()):
        tid = str(uuid5(NAMESPACE_URL, "extraction-contribution:" + contribution_id + ":" + identity))
        additions.append({"id": tid, "parent_id": parent,
            "body": {"initial_path": path, "value": deepcopy(value),
                     "object_target": old_refs["target_id"],
                     "origin": {"kind": "extraction_contribution", "contribution_id": contribution_id,
                                "predecessor_ids": list(predecessors)}}})
        return tid

    def walk(value, path, parent):
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = f"{path}.{key}" if path else key
                tid = fields_by_parent.get((parent, key))
                if tid is None:
                    tid = register("field:" + parent + ":" + key,
                        parent=parent, path=child_path, value=child)
                refs["fields"][child_path] = tid
                walk(child, child_path, tid)
        elif isinstance(value, list):
            mapping = mappings.get(path)
            if mapping is None:
                # Unchanged collections are relocated by their exact parent ID,
                # never by matching item content or the old/new array positions.
                prior_paths = [p for p in old_refs["elements"]
                               if anchor_for_path(old_refs, p)["target_id"] == parent]
                if len(prior_paths) != 1 or len(value) != len(value_at(source_entry["payload"], prior_paths[0])):
                    raise store.HistoryConflict("Changed collection has no explicit mapping")
                ids = list(old_refs["elements"][prior_paths[0]])
            else:
                ids = []
                for index, (item, child) in enumerate(zip(mapping["items"], value, strict=True)):
                    predecessors = item["predecessor_ids"]
                    if (item["relationship"] in {"retain", "revise"}
                            and element_parents[predecessors[0]] == parent):
                        tid = predecessors[0]
                    else:
                        tid = register("item:" + path + ":" + item["item_key"],
                            parent=parent, path=f"{path}[{index}]", value=child, predecessors=predecessors)
                    ids.append(tid)
                    relationships.append({**item, "target_id": tid, "collection_id": parent})
            if len(ids) != len(value):
                raise store.HistoryConflict("Collection identity coverage changed")
            refs["elements"][path] = ids
            for index, (tid, child) in enumerate(zip(ids, value, strict=True)):
                walk(child, f"{path}[{index}]", tid)

    walk(result["payload"], "", refs["target_id"])
    old_ids = set(old_refs["fields"].values()) | {tid for ids in old_refs["elements"].values() for tid in ids}
    new_ids = set(refs["fields"].values()) | {tid for ids in refs["elements"].values() for tid in ids}
    result["metadata"]["validation_history"] = refs
    return {"entry": result, "additions": additions, "retired_ids": sorted(old_ids - new_ids),
            "relationships": relationships}

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


def plan(source_entry, proposals, *, contribution_id):
    result = deepcopy(source_entry)
    old_refs = source_entry["metadata"]["validation_history"]
    fields_by_parent = {}
    for path, target in old_refs["fields"].items():
        parts = parse_field_path(path)
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
        set_payload_value(result["payload"], proposal.field_path, deepcopy(proposal.value))
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
                    if item["relationship"] in {"retain", "revise"}:
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

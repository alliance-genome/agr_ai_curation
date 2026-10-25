"""Validate explicit LLM item relationships without inferring scientific identity.

List positions describe only the proposed output order. Existing identities come
exclusively from named stable IDs, and new identities from the immutable proposal.
"""
from collections import Counter

from src.lib.validation_history import store
from src.lib.validation_history.curator_authority import anchor_for_path
from src.schemas.domain_envelope import parse_field_path


def value_at(payload, path):
    value = payload
    for part in parse_field_path(path):
        if isinstance(part, int):
            if not isinstance(value, list) or not 0 <= part < len(value):
                raise store.HistoryConflict("Collection path is outside the supplied entry")
            value = value[part]
        else:
            if not isinstance(value, dict) or part not in value:
                raise store.HistoryConflict("Field path is outside the supplied entry")
            value = value[part]
    return value


def _collections(value, path):
    if isinstance(value, list):
        yield path, value
        for index, child in enumerate(value):
            yield from _collections(child, f"{path}[{index}]")
    elif isinstance(value, dict):
        for key, child in value.items():
            yield from _collections(child, f"{path}.{key}" if path else key)


def validate(source_entry, proposed_value):
    """Return explicit output identities/ancestry, without mutating source data."""
    refs = source_entry["metadata"]["validation_history"]
    payload = source_entry["payload"]
    source = {}
    for path, ids in refs["elements"].items():
        anchor = anchor_for_path(refs, path)
        if anchor["suffix"]:
            raise store.HistoryConflict("Source collection lacks an exact stable identity")
        values = value_at(payload, path)
        if not isinstance(values, list) or len(values) != len(ids):
            raise store.HistoryConflict("Source collection identity/value coverage differs")
        source[anchor["target_id"]] = dict(zip(ids, values, strict=True))
    proposed = dict(_collections(proposed_value.value, proposed_value.field_path))
    try:
        old_value = value_at(payload, proposed_value.field_path)
    except store.HistoryConflict:
        old_value = None
    old_collections = dict(_collections(old_value, proposed_value.field_path))
    affected_ids = {anchor_for_path(refs, path)["target_id"] for path in old_collections}
    mappings = {item.collection_path: item for item in proposed_value.collection_mappings}
    if (len(mappings) != len(proposed_value.collection_mappings)
            or not set(proposed).issubset(mappings)
            or set(mappings) - set(proposed) - set(old_collections)):
        raise store.HistoryConflict("Every proposed collection requires one explicit item mapping")
    plans = {}
    used_sources = set()
    # Splitting an item can also split one of its nested collections into
    # multiple output collections. Account for that source once across all
    # explicitly named successors, rather than requiring the old list twice.
    source_counts = {}
    source_retired = {}
    for mapping in mappings.values():
        source_id = mapping.source_collection_id
        if source_id is None:
            if mapping.retired_item_ids or any(item.predecessor_ids for item in mapping.items):
                raise store.HistoryConflict("New collections cannot name predecessor items")
            continue
        if source_id not in affected_ids:
            raise store.HistoryAccessError("Collection is outside the supplied entry")
        source_counts.setdefault(source_id, Counter()).update(
            old for item in mapping.items for old in item.predecessor_ids)
        source_retired.setdefault(source_id, []).extend(mapping.retired_item_ids)
    for source_id, counts in source_counts.items():
        retired = source_retired[source_id]
        if (len(retired) != len(set(retired)) or set(counts).intersection(retired)
                or set(counts) | set(retired) != set(source[source_id])):
            raise store.HistoryConflict("Mapping must account for every existing item exactly")
    for path, mapping in mappings.items():
        values = proposed.get(path, [])
        if len(values) != len(mapping.items):
            raise store.HistoryConflict("Item mapping does not cover the proposed output")
        if mapping.source_collection_id is not None:
            if mapping.source_collection_id not in affected_ids:
                raise store.HistoryAccessError("Collection is outside the supplied entry")
            used_sources.add(mapping.source_collection_id)
            originals = source[mapping.source_collection_id]
        else:
            originals = {}
        counts = source_counts.get(mapping.source_collection_id, Counter())
        for item, value in zip(mapping.items, values, strict=True):
            if item.relationship == "split":
                if counts[item.predecessor_ids[0]] < 2:
                    raise store.HistoryConflict("A split requires multiple explicit successors")
            elif any(counts[old] != 1 for old in item.predecessor_ids):
                raise store.HistoryConflict("Repeated predecessors must be explicitly split")
            if item.relationship == "retain" and value != originals[item.predecessor_ids[0]]:
                raise store.HistoryConflict("A retained item must preserve its exact value; propose a revision")
        plans[path] = {
            "source_collection_id": mapping.source_collection_id,
            "items": [item.model_dump(mode="json") for item in mapping.items],
            "retired_item_ids": mapping.retired_item_ids,
            "retirement_reason": mapping.retirement_reason,
        }
    # Removing an entire list by changing its containing field must not bypass
    # explicit retirement. An empty list with an explicit retirement mapping is
    # an auditable replacement; absent/scalar replacement loses that contract.
    for old_id in affected_ids:
        if old_id not in used_sources:
            raise store.HistoryConflict("Existing collection requires explicit predecessor/retirement coverage")
    return plans

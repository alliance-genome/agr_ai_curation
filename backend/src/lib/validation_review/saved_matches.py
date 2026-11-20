"""Saved validator matches store each shared part once per plan body.

A ``ValidatorBindingMatch`` holds the whole envelope it was matched on, plus
its binding, object definition, field definition and policy. The matches of
one plan share those objects, so saving every match whole made a plan body
grow with matches times records: a 97-record paper produced a 2.2 GB body.
The field paths every active check on a record reads (``record_binding_paths``,
which decide which of the record's quotes each check receives) are shared by
all matches on that record, so they are a part too.

A saved body keeps every distinct part once in ``match_parts``, keyed by the
SHA-256 of its canonical JSON. Each saved match names its parts by key and its
object by position in its envelope's ``extracted_objects``. A match on the
body's own top-level ``envelope`` names it as ``PLAN_ENVELOPE`` instead of
storing a second copy. Reading parses each part once and rebuilds every match
on those shared parsed objects.
"""

from __future__ import annotations

from typing import Any, Mapping

from pydantic import TypeAdapter

from src.lib.domain_packs.validation_registry import (
    FieldValidationPolicy,
    ValidatorBinding,
    ValidatorBindingMatch,
)
from src.lib.validation_history import store
from src.schemas.domain_envelope import DomainEnvelope
from src.schemas.domain_pack_metadata import (
    DomainPackFieldDefinition,
    DomainPackObjectDefinition,
)

MATCH = TypeAdapter(ValidatorBindingMatch)
BINDING = TypeAdapter(ValidatorBinding)
PLAN_ENVELOPE = "envelope"

# Saved part kinds, each with the adapter that reads it back.
_READERS = {
    "envelopes": TypeAdapter(DomainEnvelope),
    "bindings": BINDING,
    "object_definitions": TypeAdapter(DomainPackObjectDefinition),
    "field_definitions": TypeAdapter(DomainPackFieldDefinition),
    "policies": TypeAdapter(FieldValidationPolicy),
    "record_binding_paths": TypeAdapter(tuple[str, ...]),
}
# Match attribute -> saved part kind, for the parts other than the envelope.
_PART_FIELDS = {
    "binding": "bindings",
    "object_definition": "object_definitions",
    "field_definition": "field_definitions",
    "policy": "policies",
    "record_binding_paths": "record_binding_paths",
}


def serialize_binding(binding):
    """Preserve selector field presence without stripping scientific null values."""
    value = BINDING.dump_python(binding, mode="json")
    value["input_fields"] = {
        key: selector.model_dump(mode="json", exclude_unset=True)
        for key, selector in binding.input_fields.items()
    }
    if binding.routes is not None:
        for key, route in binding.routes.items():
            value["routes"][key]["input_fields"] = {
                name: selector.model_dump(mode="json", exclude_unset=True)
                for name, selector in route.input_fields.items()
            }
    if binding.custom_profile_reuse is not None:
        for name, specification in binding.custom_profile_reuse.inputs.items():
            if specification.context_selector is not None:
                value["custom_profile_reuse"]["inputs"][name]["context_selector"] = (
                    specification.context_selector.model_dump(mode="json", exclude_unset=True)
                )
    return value


_WRITERS = {
    "envelopes": lambda envelope: envelope.model_dump(mode="json"),
    "bindings": serialize_binding,
    "object_definitions": lambda value: _READERS["object_definitions"].dump_python(value, mode="json"),
    "field_definitions": lambda value: _READERS["field_definitions"].dump_python(value, mode="json"),
    "policies": lambda value: _READERS["policies"].dump_python(value, mode="json"),
    "record_binding_paths": list,
}


def encode(matches: Mapping[str, ValidatorBindingMatch], *, plan_envelope: dict) -> dict:
    """Return ``{"match_parts", "matches"}`` for a body whose ``envelope`` is plan_envelope.

    Matches built together share their part objects, so each object is dumped
    and hashed once; equal content from different objects is still stored once.
    """
    plan_envelope_key = store.canonical(plan_envelope)[0]
    parts: dict[str, dict[str, Any]] = {kind: {} for kind in _READERS}
    # id() -> (key, object): holding the object keeps its id from being reused.
    keys: dict[tuple[str, int], tuple[str, object]] = {}

    def key_of(kind, value):
        known = keys.get((kind, id(value)))
        if known is not None:
            return known[0]
        digest, saved = store.canonical(_WRITERS[kind](value))
        if kind == "envelopes" and digest == plan_envelope_key:
            digest = PLAN_ENVELOPE
        else:
            parts[kind].setdefault(digest, saved)
        keys[(kind, id(value))] = (digest, value)
        return digest

    saved_matches = {}
    for target_id, match in matches.items():
        object_index = None
        if match.object_envelope is not None:
            positions = [index for index, obj in enumerate(match.envelope.extracted_objects)
                         if obj is match.object_envelope]
            if len(positions) != 1:
                raise store.HistoryConflict("Saved match object is not one object of its envelope")
            object_index = positions[0]
        saved_matches[target_id] = {
            "envelope": key_of("envelopes", match.envelope),
            "object_index": object_index,
            **{name: None if getattr(match, name) is None else key_of(kind, getattr(match, name))
               for name, kind in _PART_FIELDS.items()},
            "element_index": match.element_index,
            "resolved_field_path": match.resolved_field_path,
            "element_index_path": (list(match.element_index_path)
                                   if match.element_index_path is not None else None),
        }
    return {"match_parts": parts, "matches": saved_matches}


def decode(body: Mapping[str, Any]) -> dict[str, ValidatorBindingMatch]:
    """Rebuild every saved match of a body, parsing each shared part once."""
    parts = body["match_parts"]
    parsed: dict[tuple[str, str], Any] = {}

    def part(kind, key):
        if (kind, key) not in parsed:
            if kind == "envelopes" and key == PLAN_ENVELOPE:
                value = body["envelope"]
            else:
                value = parts[kind][key]
            parsed[(kind, key)] = _READERS[kind].validate_python(value)
        return parsed[(kind, key)]

    matches = {}
    for target_id, saved in body["matches"].items():
        envelope = part("envelopes", saved["envelope"])
        object_index = saved["object_index"]
        matches[target_id] = MATCH.validate_python({
            "envelope": envelope,
            "object_envelope": (envelope.extracted_objects[object_index]
                                if object_index is not None else None),
            **{name: None if saved[name] is None else part(kind, saved[name])
               for name, kind in _PART_FIELDS.items()},
            "element_index": saved["element_index"],
            "resolved_field_path": saved["resolved_field_path"],
            "element_index_path": saved["element_index_path"],
        })
    return matches

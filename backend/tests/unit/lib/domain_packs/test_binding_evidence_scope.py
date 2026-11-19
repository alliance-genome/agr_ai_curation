"""Each validator binding receives the evidence tagged to its own fields (ALL-1584).

Untagged evidence, and evidence tagged only to fields no active validator binding
on the record claims, still reaches every binding on the record. A binding that
declares a record-wide evidence selector keeps the whole record's evidence.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from src.lib.domain_packs.evidence_scope import field_paths_overlap
from src.lib.domain_packs.input_selectors import build_domain_validation_request
from src.lib.domain_packs.loader import load_domain_pack_metadata
from src.lib.domain_packs.registry import LoadedDomainPack
from src.lib.domain_packs.validation_registry import (
    DomainPackValidationRegistry,
    ValidationBindingState,
)
from src.schemas.domain_envelope import CuratableObjectEnvelope, DomainEnvelope

PACK_ID = "fixture.evidence_scope"


def _lookup(binding_id: str, field_path: str, inputs: str, *, extra: str = "") -> str:
    return f"""
      - binding_id: {binding_id}
        display_name: {binding_id}{extra}
        validator_agent:
          package_id: fixture.validators
          agent_id: {binding_id}_validator
        applies_to:
          domain_pack_id: {PACK_ID}
          object_types: [Record]
          field_paths: [{field_path}]
        input_fields:
{inputs}
        expected_result_fields:
          curie: {field_path}"""


BINDINGS = "".join([
    _lookup("anatomy", "site.anatomy", """          label:
            source: payload
            path: site.anatomy.mention
          provider:
            source: payload
            path: provider.mention
            required: false
            context_only: true
          evidence_quotes:
            source: evidence_record
            field_path: site.anatomy
            output: quote_bundle
            allow_multiple: true
            required: false
            context_only: true"""),
    _lookup("component", "site.component", """          label:
            source: payload
            path: site.component.mention"""),
    _lookup("stage", "stage", """          label:
            source: payload
            path: stage.mention"""),
    _lookup("provider", "provider", """          label:
            source: payload
            path: provider.mention"""),
    _lookup("terms", "terms", """          label:
            source: payload
            path: terms"""),
    _lookup("conditions", "relations.conditions", """          summary:
            source: payload
            path: relations.conditions.summary
          relation:
            source: payload
            path: relations.relation_type.mention
            required: false
            context_only: true"""),
    """
      - binding_id: whole_record
        display_name: whole_record
        validator_agent:
          package_id: fixture.validators
          agent_id: whole_record_validator
        applies_to:
          domain_pack_id: fixture.evidence_scope
          object_types: [Record]
        input_fields:
          label:
            source: payload
            path: stage.mention
          evidence_quotes:
            source: evidence_record
            output: quote_bundle
            allow_multiple: true
            required: false
            context_only: true
        expected_result_fields:
          verdict: verdict""",
])

UNDER_DEVELOPMENT = """
    under_development:""" + _lookup("draft_notes", "notes", """          label:
            source: payload
            path: notes""", extra="\n        state_explanation: Not yet active.")


def _pack(tmp_path: Path) -> LoadedDomainPack:
    fields = [
        ("statement", "string", False), ("notes", "string", False), ("verdict", "string", False),
        ("site.anatomy", "object", False), ("site.anatomy.mention", "string", False),
        ("site.component", "object", False), ("site.component.mention", "string", False),
        ("stage", "object", False), ("stage.mention", "string", False),
        ("provider", "object", False), ("provider.mention", "string", False),
        ("terms", "string", True),
        ("relations", "array", True), ("relations.relation_type", "object", False),
        ("relations.relation_type.mention", "string", False),
        ("relations.conditions", "object", True), ("relations.conditions.summary", "string", False),
    ]
    field_text = "".join(
        f"\n      - field_path: {path}\n        field_type: {kind}"
        + ("\n        metadata:\n          multivalued: true" if multivalued else "")
        for path, kind, multivalued in fields
    )
    text = f"""
pack_id: {PACK_ID}
display_name: Fixture Evidence Scope Pack
version: 0.1.0
metadata_api_version: 1.0.0
status: active
object_definitions:
  - object_type: Record
    display_name: Record
    fields:{field_text}
metadata:
  validator_bindings:
    active:{BINDINGS}{UNDER_DEVELOPMENT}
""".strip()
    pack_path = tmp_path / PACK_ID
    pack_path.mkdir(parents=True, exist_ok=True)
    metadata_path = pack_path / "domain_pack.yaml"
    metadata_path.write_text(text, encoding="utf-8")
    metadata = load_domain_pack_metadata(metadata_path)
    return LoadedDomainPack(
        pack_id=metadata.pack_id, display_name=metadata.display_name, version=metadata.version,
        pack_path=pack_path, metadata_path=metadata_path, metadata=metadata,
    )


def _record(record_id: str, *tags: str, targets: tuple[str, ...] = ()) -> dict:
    record = {"evidence_record_id": record_id, "verified_quote": f"Quote {record_id}."}
    if tags:
        record["field_path"] = tags[0]
        record["field_paths"] = list(tags)
    if targets:
        record["envelope_targets"] = [{"pending_ref_id": "record", "field_path": path} for path in targets]
    return record


PAYLOAD = {
    "statement": "expressed in the gut at stage 5",
    "notes": "draft",
    "site": {"anatomy": {"mention": "gut"}, "component": {"mention": "nucleus"}},
    "stage": {"mention": "stage 5"},
    "provider": {"mention": "provider-one"},
    "terms": ["t0", "t1"],
    "relations": [
        {"relation_type": {"mention": "has condition"},
         "conditions": [{"summary": "heat"}, {"summary": "cold"}]},
        {"relation_type": {"mention": "induced by"}, "conditions": [{"summary": "drug"}]},
    ],
}


def _envelope(records: list[dict]) -> DomainEnvelope:
    obj = CuratableObjectEnvelope(
        object_type="Record", pending_ref_id="record", payload=deepcopy(PAYLOAD),
        evidence_record_ids=[record["evidence_record_id"] for record in records],
    )
    return DomainEnvelope(envelope_id="evidence-scope", domain_pack_id=PACK_ID,
                          extracted_objects=[obj], metadata={"evidence_records": deepcopy(records)})


def _evidence_by_target(tmp_path: Path, records: list[dict], *, states=(ValidationBindingState.ACTIVE,)):
    registry = DomainPackValidationRegistry.from_domain_pack(_pack(tmp_path))
    delivered = {}
    for match in registry.match_bindings(_envelope(records), states=list(states)):
        result = build_domain_validation_request(match)
        assert result.request is not None, (match.binding.binding_id, result.findings)
        key = match.binding.binding_id if match.field_path in {None, *match.binding.field_paths} else (
            f"{match.binding.binding_id}:{match.field_path}")
        delivered[key] = [record["evidence_record_id"] for record in result.request.evidence]
        assert result.evidence == result.request.evidence
    return delivered


# --- the match rule -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("left", "right", "indexes", "expected"),
    [
        ("stage", "stage", True, True),                                   # exact
        ("site", "site.anatomy", True, True),                             # tag is a parent
        ("site.anatomy.mention", "site.anatomy", True, True),             # tag is a child
        ("site.component", "site.anatomy", True, False),                  # sibling
        ("site.anat", "site.anatomy", True, False),                       # name prefix is not a path prefix
        ("terms[1]", "terms[1]", True, True),
        ("terms[0]", "terms[1]", True, False),                            # other element
        ("terms", "terms[1]", True, True),                                # unindexed tag covers every element
        ("terms[0]", "terms", True, True),                                # unindexed binding path
        ("relations[0].conditions[1]", "relations[0].conditions[1].summary", True, True),
        ("relations[0].conditions[1]", "relations[0].conditions[0]", True, False),
        ("relations[1]", "relations[0].conditions[0]", True, False),
        ("relations.conditions", "relations[1].conditions[0]", True, True),
        ("terms[0]", "terms[1]", False, True),                            # ownership ignores indexes
        ("field[]", "field", True, False),                                # malformed tag compares literally
    ],
)
def test_field_path_overlap_rule(left, right, indexes, expected):
    assert field_paths_overlap(left, right, compare_indexes=indexes) is expected
    assert field_paths_overlap(right, left, compare_indexes=indexes) is expected


# --- delivery through the real selector path -------------------------------------------------


def test_scalar_bindings_receive_only_their_own_tagged_evidence(tmp_path):
    delivered = _evidence_by_target(tmp_path, [
        _record("ev-anatomy", "site.anatomy"),
        _record("ev-stage", "stage"),
        _record("ev-component-child", "site.component.mention"),
    ])
    assert delivered["anatomy"] == ["ev-anatomy"]
    assert delivered["stage"] == ["ev-stage"]
    assert delivered["component"] == ["ev-component-child"]
    assert delivered["provider"] == []


def test_untagged_and_unclaimed_evidence_reaches_every_binding(tmp_path):
    delivered = _evidence_by_target(tmp_path, [
        _record("ev-untagged"),
        _record("ev-statement", "statement"),            # a field no validator reads
        _record("ev-draft", "notes"),                    # only an under-development binding reads it
        _record("ev-stage", "stage"),
    ])
    for key, ids in delivered.items():
        expected = ["ev-untagged", "ev-statement", "ev-draft"]
        if key in {"stage", "whole_record"}:
            expected = [*expected[:3], "ev-stage"]
        assert ids == expected, key


def test_record_with_a_claimed_tag_goes_only_to_its_claimants_even_with_an_unclaimed_tag(tmp_path):
    delivered = _evidence_by_target(tmp_path, [_record("ev-mixed", "statement", "site.anatomy")])
    assert delivered["anatomy"] == ["ev-mixed"]
    assert delivered["stage"] == []
    assert delivered["whole_record"] == ["ev-mixed"]


def test_parent_tag_reaches_every_binding_under_it_and_envelope_target_tags_count(tmp_path):
    delivered = _evidence_by_target(tmp_path, [_record("ev-site", targets=("site",))])
    assert delivered["anatomy"] == delivered["component"] == ["ev-site"]
    assert delivered["stage"] == []


def test_declared_context_input_counts_as_an_own_field(tmp_path):
    delivered = _evidence_by_target(tmp_path, [_record("ev-provider", "provider.mention")])
    assert delivered["provider"] == ["ev-provider"]
    assert delivered["anatomy"] == ["ev-provider"]   # reads provider.mention as context
    assert delivered["stage"] == []


def test_multivalued_elements_receive_their_element_and_unindexed_evidence(tmp_path):
    delivered = _evidence_by_target(tmp_path, [_record("ev-t1", "terms[1]"), _record("ev-terms", "terms")])
    assert delivered["terms:terms[0]"] == ["ev-terms"]
    assert delivered["terms:terms[1]"] == ["ev-t1", "ev-terms"]


def test_composite_condition_binding_reads_its_element_and_its_relation_context(tmp_path):
    delivered = _evidence_by_target(tmp_path, [
        _record("ev-heat", "relations[0].conditions[0].summary"),
        _record("ev-cold", "relations[0].conditions[1]"),
        _record("ev-relation-1", "relations[1].relation_type"),
        _record("ev-all-conditions", "relations.conditions"),
    ])
    assert delivered["conditions:relations[0].conditions[0]"] == ["ev-heat", "ev-all-conditions"]
    assert delivered["conditions:relations[0].conditions[1]"] == ["ev-cold", "ev-all-conditions"]
    # The relation type is declared context of each condition in that relation only.
    assert delivered["conditions:relations[1].conditions[0]"] == ["ev-relation-1", "ev-all-conditions"]


def test_record_wide_evidence_selector_keeps_the_whole_record(tmp_path):
    records = [_record("ev-anatomy", "site.anatomy"), _record("ev-untagged"), _record("ev-stage", "stage")]
    delivered = _evidence_by_target(tmp_path, records)
    assert delivered["whole_record"] == ["ev-anatomy", "ev-untagged", "ev-stage"]


def test_field_scoped_quotes_stay_inside_the_request_evidence(tmp_path):
    registry = DomainPackValidationRegistry.from_domain_pack(_pack(tmp_path))
    envelope = _envelope([_record("ev-anatomy", "site.anatomy"), _record("ev-stage", "stage")])
    match = next(m for m in registry.match_bindings(envelope) if m.binding.binding_id == "anatomy")
    request = build_domain_validation_request(match).request
    quoted = {bundle["evidence_record_id"] for bundle in request.selected_inputs["evidence_quotes"]}
    assert quoted == {"ev-anatomy"}
    assert quoted <= {record["evidence_record_id"] for record in request.evidence}


def test_assignment_does_not_depend_on_the_states_a_caller_matches(tmp_path):
    records = [_record("ev-draft", "notes"), _record("ev-stage", "stage")]
    active_only = _evidence_by_target(tmp_path, records)
    every_state = _evidence_by_target(tmp_path, records, states=tuple(ValidationBindingState))
    assert {key: every_state[key] for key in active_only} == active_only
    assert every_state["draft_notes"] == ["ev-draft"]


def test_appending_evidence_for_one_field_leaves_sibling_inputs_identical(tmp_path):
    """The production shape: a correction appends evidence for two fields only."""
    original = [_record("ev-statement-1", "statement"), _record("ev-statement-2", "statement")]
    before = _evidence_by_target(tmp_path / "before", original)
    after = _evidence_by_target(tmp_path / "after", [
        *original, _record("ev-new-stage", "stage"), _record("ev-new-anatomy", "site.anatomy")])
    changed = {key for key in before if before[key] != after[key]}
    assert changed == {"stage", "anatomy", "whole_record"}


def test_selection_leaves_object_evidence_and_record_wide_selectors_unchanged(tmp_path):
    """Non-validator consumers read the object's evidence list and the envelope records."""
    records = [_record("ev-anatomy", "site.anatomy"), _record("ev-untagged")]
    envelope = _envelope(records)
    snapshot = envelope.model_dump(mode="json")
    registry = DomainPackValidationRegistry.from_domain_pack(_pack(tmp_path))
    for match in registry.match_bindings(envelope):
        build_domain_validation_request(match)
    assert envelope.model_dump(mode="json") == snapshot
    assert envelope.extracted_objects[0].evidence_record_ids == ["ev-anatomy", "ev-untagged"]

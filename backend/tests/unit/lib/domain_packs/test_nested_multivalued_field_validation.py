"""Unit tests for NESTED (multi-level) per-element validation fan-out.

A validatable field whose declared path traverses more than one ``multivalued: true``
segment (a multivalued ancestor AND a multivalued leaf, e.g.
``condition_relations.conditions.condition_class.curie`` where both ``condition_relations``
and ``condition_relations.conditions`` are multivalued) fans out to the CARTESIAN PRODUCT
of the actual list lengths at each level — one match per ``a[i].b[j]`` leaf, each carrying
the fully-resolved indexed path. Single-level multivalued fields and scalar fields are
unaffected; those regressions live in ``test_multivalued_field_validation.py`` and the
single-level assertions here.

These use a synthetic fixture pack (the disease conditions wiring is not yet shipped), so
the nested engine is exercised independently of any real domain pack.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.lib.domain_packs.input_selectors import build_domain_validation_request
from src.lib.domain_packs.loader import load_domain_pack_metadata
from src.lib.domain_packs.materialization import (
    _materialized_field_path,
    _set_payload_value,
)
from src.lib.domain_packs.registry import LoadedDomainPack
from src.lib.domain_packs.validation_registry import (
    DomainPackValidationRegistry,
    ValidationBindingState,
)
from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    DomainEnvelope,
)


# Field path of the validatable nested leaf and its two multivalued ancestors.
_LEAF = "condition_relations.conditions.condition_class.curie"
_OUTER = "condition_relations"
_INNER = "condition_relations.conditions"


def _nested_pack_text() -> str:
    return """
pack_id: fixture.nested_multivalued
display_name: Fixture Nested Multivalued Pack
version: 0.1.0
metadata_api_version: 1.0.0
status: active
object_definitions:
  - object_type: Annotation
    display_name: Annotation
    fields:
      - field_path: condition_relations
        field_type: array
        display_name: Condition relations
        metadata:
          multivalued: true
      - field_path: condition_relations.conditions
        field_type: array
        display_name: Conditions
        metadata:
          multivalued: true
      - field_path: condition_relations.conditions.condition_class.curie
        field_type: string
        display_name: Condition class CURIE
        metadata:
          validatable: true
          validator_binding_id: condition_class_lookup
          validator_state: active
metadata:
  validator_bindings:
    active:
      - binding_id: condition_class_lookup
        display_name: Condition class lookup
        validator_agent:
          package_id: org.validators
          agent_id: ontology_validator
        applies_to:
          domain_pack_id: fixture.nested_multivalued
          object_types:
            - Annotation
          field_paths:
            - condition_relations.conditions.condition_class.curie
        input_fields:
          curie:
            source: payload
            path: condition_relations.conditions.condition_class.curie
            required: false
        expected_result_fields:
          curie: condition_relations.conditions.condition_class.curie
        required: true
""".strip()


def _loaded_pack(tmp_path: Path) -> LoadedDomainPack:
    pack_path = tmp_path / "fixture.nested_multivalued"
    pack_path.mkdir()
    metadata_path = pack_path / "domain_pack.yaml"
    metadata_path.write_text(_nested_pack_text(), encoding="utf-8")
    metadata = load_domain_pack_metadata(metadata_path)
    return LoadedDomainPack(
        pack_id=metadata.pack_id,
        display_name=metadata.display_name,
        version=metadata.version,
        pack_path=pack_path,
        metadata_path=metadata_path,
        metadata=metadata,
    )


def _envelope(payload: dict) -> DomainEnvelope:
    return DomainEnvelope(
        envelope_id="nested-env",
        domain_pack_id="fixture.nested_multivalued",
        objects=[
            CuratableObjectEnvelope(
                object_type="Annotation",
                pending_ref_id="annotation-1",
                payload=payload,
            )
        ],
    )


def _condition(curie: str | None) -> dict:
    return {"condition_class": {"curie": curie}}


def _relation(*curies: str | None) -> dict:
    return {"conditions": [_condition(curie) for curie in curies]}


def _field_matches(tmp_path: Path, payload: dict):
    pack = _loaded_pack(tmp_path)
    registry = DomainPackValidationRegistry.from_domain_pack(pack)
    return [
        match
        for match in registry.match_bindings(
            _envelope(payload), states=[ValidationBindingState.ACTIVE]
        )
        if match.field_definition is not None
    ]


def test_two_by_two_nested_fan_out_is_the_cartesian_product(tmp_path: Path):
    payload = {
        "condition_relations": [
            _relation("CHEBI:0", "CHEBI:1"),
            _relation("CHEBI:2", "CHEBI:3"),
        ]
    }
    matches = _field_matches(tmp_path, payload)

    assert len(matches) == 4
    assert [match.element_index_path for match in matches] == [
        (0, 0),
        (0, 1),
        (1, 0),
        (1, 1),
    ]
    assert [match.field_path for match in matches] == [
        "condition_relations[0].conditions[0].condition_class.curie",
        "condition_relations[0].conditions[1].condition_class.curie",
        "condition_relations[1].conditions[0].condition_class.curie",
        "condition_relations[1].conditions[1].condition_class.curie",
    ]
    # Nested matches carry a resolved path, not the single-level element_index shape.
    assert [match.element_index for match in matches] == [None, None, None, None]


def test_ragged_inner_lists_fan_out_to_their_own_lengths(tmp_path: Path):
    payload = {
        "condition_relations": [
            _relation("CHEBI:0", "CHEBI:1", "CHEBI:2"),
            _relation("CHEBI:3"),
        ]
    }
    matches = _field_matches(tmp_path, payload)

    assert [match.field_path for match in matches] == [
        "condition_relations[0].conditions[0].condition_class.curie",
        "condition_relations[0].conditions[1].condition_class.curie",
        "condition_relations[0].conditions[2].condition_class.curie",
        "condition_relations[1].conditions[0].condition_class.curie",
    ]


def test_empty_inner_list_at_one_relation_yields_zero_for_that_branch(tmp_path: Path):
    payload = {
        "condition_relations": [
            _relation("CHEBI:0", "CHEBI:1"),
            {"conditions": []},
        ]
    }
    matches = _field_matches(tmp_path, payload)

    assert [match.field_path for match in matches] == [
        "condition_relations[0].conditions[0].condition_class.curie",
        "condition_relations[0].conditions[1].condition_class.curie",
    ]


def test_missing_inner_list_at_one_relation_yields_zero_for_that_branch(
    tmp_path: Path,
):
    payload = {
        "condition_relations": [
            _relation("CHEBI:0"),
            {},  # no conditions key at all
        ]
    }
    matches = _field_matches(tmp_path, payload)

    assert [match.field_path for match in matches] == [
        "condition_relations[0].conditions[0].condition_class.curie",
    ]


def test_empty_outer_list_yields_zero_matches(tmp_path: Path):
    matches = _field_matches(tmp_path, {"condition_relations": []})
    assert matches == []


def test_absent_outer_field_yields_zero_matches(tmp_path: Path):
    matches = _field_matches(tmp_path, {})
    assert matches == []


def test_each_nested_match_builds_a_fully_indexed_request(tmp_path: Path):
    payload = {
        "condition_relations": [
            _relation("CHEBI:0", "CHEBI:1"),
            _relation("CHEBI:2"),
        ]
    }
    matches = _field_matches(tmp_path, payload)
    results = [build_domain_validation_request(match) for match in matches]

    # The payload selector resolves the bare nested base to the element's indexed path.
    assert [result.request.selected_inputs["curie"] for result in results] == [
        "CHEBI:0",
        "CHEBI:1",
        "CHEBI:2",
    ]
    # The write-back target and validation target carry the full nested index path.
    assert [
        result.request.expected_result_fields["curie"] for result in results
    ] == [
        "condition_relations[0].conditions[0].condition_class.curie",
        "condition_relations[0].conditions[1].condition_class.curie",
        "condition_relations[1].conditions[0].condition_class.curie",
    ]
    assert [result.request.target.field_path for result in results] == [
        "condition_relations[0].conditions[0].condition_class.curie",
        "condition_relations[0].conditions[1].condition_class.curie",
        "condition_relations[1].conditions[0].condition_class.curie",
    ]


def test_per_element_findings_carry_the_nested_index_path(tmp_path: Path):
    # A null inner curie makes the required-curie selector fail for that element only,
    # and the finding must point at the specific nested element.
    payload = {
        "condition_relations": [
            _relation("CHEBI:0", None),
        ]
    }
    matches = _field_matches(tmp_path, payload)
    target_details = [match.target_details() for match in matches]

    assert target_details[0]["field_path"] == (
        "condition_relations[0].conditions[0].condition_class.curie"
    )
    assert target_details[0]["element_index_path"] == [0, 0]
    assert target_details[1]["field_path"] == (
        "condition_relations[0].conditions[1].condition_class.curie"
    )
    assert target_details[1]["element_index_path"] == [0, 1]
    # Nested matches do not emit the single-level element_index key.
    assert "element_index" not in target_details[0]


def test_write_back_to_a_nested_indexed_path_round_trips(tmp_path: Path):
    pack = _loaded_pack(tmp_path)
    declared_fields = {
        field.field_path: field
        for field in pack.metadata.object_definitions[0].fields
    }
    indexed_path = "condition_relations[0].conditions[1].condition_class.curie"

    # The indexed nested path is recognized as a materializable target.
    assert (
        _materialized_field_path(indexed_path, declared_fields=declared_fields)
        == indexed_path
    )

    # And the scalar-writer extends nested lists to write the resolved value in place.
    payload: dict = {"condition_relations": [{"conditions": []}]}
    _set_payload_value(payload, indexed_path, "CHEBI:RESOLVED")
    assert (
        payload["condition_relations"][0]["conditions"][1]["condition_class"]["curie"]
        == "CHEBI:RESOLVED"
    )


def test_indexed_base_rejects_non_multivalued_index_segments(tmp_path: Path):
    # An index on a segment that is NOT a declared multivalued field must not resolve as a
    # materializable nested write path (guards against accidental list writes).
    pack = _loaded_pack(tmp_path)
    declared_fields = {
        field.field_path: field
        for field in pack.metadata.object_definitions[0].fields
    }
    # condition_class is a scalar object key, not a declared multivalued field.
    bogus = "condition_relations[0].conditions[0].condition_class[0].curie"
    assert (
        _materialized_field_path(bogus, declared_fields=declared_fields) is None
    )

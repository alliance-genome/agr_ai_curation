"""Unit tests for generic per-element validation of multivalued fields.

These exercise the engine fan-out: a single binding match on a ``multivalued: true``
field expands into one match per present list element, each carrying ``element_index``,
and the per-element request resolves selector/expected-result paths to ``field[i]``.
Legacy ``[0]``-literal and scalar fields are unaffected.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.lib.domain_packs.input_selectors import build_domain_validation_request
from src.lib.domain_packs.loader import load_domain_pack_metadata
from src.lib.domain_packs.registry import LoadedDomainPack
from src.lib.domain_packs.validation_registry import (
    DomainPackValidationRegistry,
    ValidationBindingState,
)
from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    DomainEnvelope,
)


def _multivalued_pack_text(*, multivalued: bool) -> str:
    multivalued_line = "          multivalued: true\n" if multivalued else ""
    return f"""
pack_id: fixture.multivalued
display_name: Fixture Multivalued Pack
version: 0.1.0
metadata_api_version: 1.0.0
status: active
object_definitions:
  - object_type: Annotation
    display_name: Annotation
    fields:
      - field_path: evidence_code_curies
        field_type: string
        display_name: Evidence code CURIEs
        metadata:
          validatable: true
{multivalued_line}          validator_binding_id: evidence_code_lookup
          validator_state: active
metadata:
  validator_bindings:
    active:
      - binding_id: evidence_code_lookup
        display_name: Evidence code lookup
        validator_agent:
          package_id: org.validators
          agent_id: ontology_validator
        applies_to:
          domain_pack_id: fixture.multivalued
          object_types:
            - Annotation
          field_paths:
            - evidence_code_curies
        input_fields:
          curie:
            source: payload
            path: evidence_code_curies
            required: false
        expected_result_fields:
          curie: evidence_code_curies
        required: true
""".strip()


def _loaded_pack(tmp_path: Path, *, multivalued: bool) -> LoadedDomainPack:
    pack_path = tmp_path / "fixture.multivalued"
    pack_path.mkdir()
    metadata_path = pack_path / "domain_pack.yaml"
    metadata_path.write_text(
        _multivalued_pack_text(multivalued=multivalued), encoding="utf-8"
    )
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
        envelope_id="multivalued-env",
        domain_pack_id="fixture.multivalued",
        objects=[
            CuratableObjectEnvelope(
                object_type="Annotation",
                pending_ref_id="annotation-1",
                payload=payload,
            )
        ],
    )


def _matches(tmp_path: Path, payload: dict, *, multivalued: bool):
    pack = _loaded_pack(tmp_path, multivalued=multivalued)
    registry = DomainPackValidationRegistry.from_domain_pack(pack)
    return registry.match_bindings(
        _envelope(payload),
        states=[ValidationBindingState.ACTIVE],
    )


def test_multivalued_field_fans_out_one_match_per_element(tmp_path: Path):
    matches = _matches(
        tmp_path,
        {"evidence_code_curies": ["ECO:0000315", "ECO:0000316", "ECO:0000501"]},
        multivalued=True,
    )

    evidence_matches = [
        match for match in matches if match.field_definition is not None
    ]
    assert len(evidence_matches) == 3
    assert [match.element_index for match in evidence_matches] == [0, 1, 2]
    assert [match.field_path for match in evidence_matches] == [
        "evidence_code_curies[0]",
        "evidence_code_curies[1]",
        "evidence_code_curies[2]",
    ]


def test_multivalued_empty_list_yields_zero_field_matches(tmp_path: Path):
    matches = _matches(
        tmp_path,
        {"evidence_code_curies": []},
        multivalued=True,
    )

    assert [match for match in matches if match.field_definition is not None] == []


def test_multivalued_absent_field_yields_zero_field_matches(tmp_path: Path):
    matches = _matches(tmp_path, {}, multivalued=True)

    assert [match for match in matches if match.field_definition is not None] == []


def test_non_multivalued_field_is_not_fanned_out(tmp_path: Path):
    matches = _matches(
        tmp_path,
        {"evidence_code_curies": ["ECO:0000315", "ECO:0000316"]},
        multivalued=False,
    )

    field_matches = [
        match for match in matches if match.field_definition is not None
    ]
    assert len(field_matches) == 1
    assert field_matches[0].element_index is None
    assert field_matches[0].field_path == "evidence_code_curies"


def test_each_element_match_builds_an_indexed_request(tmp_path: Path):
    matches = _matches(
        tmp_path,
        {"evidence_code_curies": ["ECO:0000315", "ECO:0000316"]},
        multivalued=True,
    )
    evidence_matches = [
        match for match in matches if match.field_definition is not None
    ]

    results = [build_domain_validation_request(match) for match in evidence_matches]

    # Every element is sent to the validator with its own resolved curie.
    selected = [result.request.selected_inputs["curie"] for result in results]
    assert selected == ["ECO:0000315", "ECO:0000316"]

    # The write-back target and validation target carry the element index.
    assert [
        result.request.expected_result_fields["curie"] for result in results
    ] == ["evidence_code_curies[0]", "evidence_code_curies[1]"]
    assert [result.request.target.field_path for result in results] == [
        "evidence_code_curies[0]",
        "evidence_code_curies[1]",
    ]


def test_element_match_findings_carry_the_index(tmp_path: Path):
    # A required selector that cannot resolve emits a per-element finding whose
    # field_ref points at the specific element.
    pack = _loaded_pack(tmp_path, multivalued=True)
    registry = DomainPackValidationRegistry.from_domain_pack(pack)
    # Element 1 is null so its required-curie selector fails for that element only.
    envelope = _envelope({"evidence_code_curies": ["ECO:0000315", None]})
    matches = [
        match
        for match in registry.match_bindings(
            envelope, states=[ValidationBindingState.ACTIVE]
        )
        if match.field_definition is not None
    ]
    target_details = [match.target_details() for match in matches]

    assert target_details[0]["field_path"] == "evidence_code_curies[0]"
    assert target_details[0]["element_index"] == 0
    assert target_details[1]["field_path"] == "evidence_code_curies[1]"
    assert target_details[1]["element_index"] == 1

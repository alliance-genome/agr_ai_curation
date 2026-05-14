"""Unit tests for deterministic validator input selectors."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from src.lib.domain_packs.loader import load_domain_pack_metadata
from src.lib.domain_packs.registry import LoadedDomainPack
from src.lib.domain_packs.validation_registry import (
    DomainPackValidationRegistry,
    ValidationRegistryError,
)
from src.lib.domain_packs.validation_supervisor import run_validation_supervisor
from src.schemas.domain_envelope import CuratableObjectEnvelope, DomainEnvelope
from src.schemas.domain_pack_metadata import DomainPackInputSelector


def _selector_pack_text(input_selector_yaml: str) -> str:
    return f"""
pack_id: fixture.selectors
display_name: Fixture Selector Pack
version: 0.1.0
metadata_api_version: 1.0.0
status: active
model_definitions:
  - model_id: AssertionPayload
    display_name: Assertion payload
  - model_id: RefPayload
    display_name: Ref payload
object_definitions:
  - object_type: Assertion
    display_name: Assertion
    model_ref: AssertionPayload
    fields:
      - field_path: value
        field_type: string
        metadata:
          provider_refs:
            fixture_provider:
              schema_ref: fixture.schema
              slot: value
      - field_path: aliases
        field_type: array
      - field_path: ref
        field_type: object_ref
        object_type_ref: RefObject
  - object_type: RefObject
    display_name: Referenced object
    model_ref: RefPayload
    fields:
      - field_path: curie
        field_type: string
metadata:
  validator_bindings:
    active:
      - binding_id: fixture.selector
        display_name: Selector check
        validator_agent:
          package_id: org.validators
          agent_id: selector_validator
        applies_to:
          domain_pack_id: fixture.selectors
          object_types:
            - Assertion
        input_fields:
{input_selector_yaml}
        expected_result_fields:
          selected: selected
""".strip()


def _loaded_pack(tmp_path: Path, input_selector_yaml: str) -> LoadedDomainPack:
    pack_path = tmp_path / "fixture.selectors"
    pack_path.mkdir()
    metadata_path = pack_path / "domain_pack.yaml"
    metadata_path.write_text(_selector_pack_text(input_selector_yaml), encoding="utf-8")
    metadata = load_domain_pack_metadata(metadata_path)
    return LoadedDomainPack(
        pack_id=metadata.pack_id,
        display_name=metadata.display_name,
        version=metadata.version,
        pack_path=pack_path,
        metadata_path=metadata_path,
        metadata=metadata,
    )


def _run_selector(
    tmp_path: Path,
    input_selector_yaml: str,
    envelope: DomainEnvelope,
):
    pack = _loaded_pack(tmp_path, input_selector_yaml)
    registry = DomainPackValidationRegistry.from_domain_pack(pack)
    return run_validation_supervisor(envelope, pack, registry=registry)


def _assertion_envelope(
    *,
    payload: dict,
    object_metadata: dict | None = None,
    envelope_metadata: dict | None = None,
    evidence_record_ids: list[str] | None = None,
    extra_objects: list[CuratableObjectEnvelope] | None = None,
) -> DomainEnvelope:
    return DomainEnvelope(
        envelope_id="selector-env",
        domain_pack_id="fixture.selectors",
        objects=[
            CuratableObjectEnvelope(
                object_type="Assertion",
                pending_ref_id="assertion-1",
                payload=payload,
                metadata=object_metadata or {},
                evidence_record_ids=evidence_record_ids or [],
            ),
            *(extra_objects or []),
        ],
        metadata=envelope_metadata or {},
    )


@pytest.mark.parametrize(
    "selector",
    [
        {"source": "payload", "path": "value"},
        {"source": "envelope_metadata", "path": "run_id"},
        {"source": "object_metadata", "path": "status"},
        {"source": "evidence_record", "path": "quote", "record_id": "evidence-1"},
        {
            "source": "object_ref",
            "field_path": "ref",
            "object_type": "RefObject",
            "path": "curie",
        },
        {"source": "literal", "value": "constant"},
    ],
)
def test_input_selector_parser_accepts_every_source(selector: dict):
    parsed = DomainPackInputSelector.model_validate(selector)

    assert parsed.source == selector["source"]


@pytest.mark.parametrize(
    "selector",
    [
        {"source": "payload"},
        {"source": "envelope_metadata"},
        {"source": "object_metadata"},
        {"source": "evidence_record"},
        {"source": "object_ref"},
        {"source": "literal"},
    ],
)
def test_input_selector_parser_rejects_malformed_selectors(selector: dict):
    with pytest.raises(ValidationError):
        DomainPackInputSelector.model_validate(selector)


@pytest.mark.parametrize(
    "selector",
    [
        {"source": "literal", "value": "constant", "path": "value"},
        {"source": "payload", "path": "value", "field_path": "ref"},
        {"source": "payload", "path": "value", "record_id": "evidence-1"},
        {"source": "payload", "path": "value", "object_type": "Gene"},
        {"source": "payload", "path": "value", "value": "constant"},
        {"source": "envelope_metadata", "path": "run_id", "record_id": "evidence-1"},
        {"source": "object_metadata", "path": "status", "object_type": "Gene"},
        {"source": "evidence_record", "path": "quote", "object_type": "Gene"},
        {"source": "evidence_record", "path": "quote", "field_path": "ref"},
        {"source": "evidence_record", "path": "quote", "value": "constant"},
        {"source": "object_ref", "field_path": "ref", "value": "constant"},
        {"source": "object_ref", "object_type": "RefObject", "record_id": "evidence-1"},
    ],
)
def test_input_selector_parser_rejects_source_specific_extra_fields(selector: dict):
    with pytest.raises(ValidationError, match="do not support field"):
        DomainPackInputSelector.model_validate(selector)


def test_active_payload_selector_path_is_validated_at_registry_load(tmp_path: Path):
    pack = _loaded_pack(
        tmp_path,
        """
          selected:
            source: payload
            path: missing_value
""",
    )

    with pytest.raises(ValidationRegistryError, match="missing_value"):
        DomainPackValidationRegistry.from_domain_pack(pack)


def test_active_payload_selector_rejects_provider_slot_final_segment_guessing(
    tmp_path: Path,
):
    pack = _loaded_pack(
        tmp_path,
        """
          selected:
            source: payload
            path: bogus.value
""",
    )

    with pytest.raises(ValidationRegistryError, match="bogus.value"):
        DomainPackValidationRegistry.from_domain_pack(pack)


def test_active_object_ref_selector_path_uses_field_object_type_ref(tmp_path: Path):
    pack = _loaded_pack(
        tmp_path,
        """
          selected:
            source: object_ref
            field_path: ref
            path: missing_curie
""",
    )

    with pytest.raises(ValidationRegistryError, match="missing_curie"):
        DomainPackValidationRegistry.from_domain_pack(pack)


def test_validation_request_carries_selected_inputs_evidence_and_expected_fields(
    tmp_path: Path,
):
    result = _run_selector(
        tmp_path,
        """
          selected:
            source: payload
            path: value
""",
        _assertion_envelope(
            payload={
                "value": "AGR:1",
                "evidence_records": [
                    {"evidence_record_id": "evidence-1", "quote": "support"}
                ],
            },
            evidence_record_ids=["evidence-1"],
        ),
    )

    finding = next(
        item
        for item in result.envelope.validation_findings
        if item.code == "domain_pack.validator_dispatch_unavailable"
    )
    request = finding.details["validation_request"]
    assert request["validator_binding_id"] == "fixture.selector"
    assert request["validator_agent"] == {
        "package_id": "org.validators",
        "agent_id": "selector_validator",
    }
    assert request["target"]["object_type"] == "Assertion"
    assert request["selected_inputs"] == {"selected": "AGR:1"}
    assert request["evidence"] == [
        {"evidence_record_id": "evidence-1", "quote": "support"}
    ]
    assert request["expected_result_fields"] == {"selected": "selected"}


def test_runtime_selector_missing_field_becomes_structured_finding(tmp_path: Path):
    result = _run_selector(
        tmp_path,
        """
          selected:
            source: payload
            path: value
""",
        _assertion_envelope(payload={}),
    )

    assert {finding.code for finding in result.envelope.validation_findings} >= {
        "selector_missing_field"
    }
    assert not any(
        finding.code == "domain_pack.validator_dispatch_unavailable"
        for finding in result.envelope.validation_findings
    )


def test_runtime_selector_ambiguity_becomes_structured_finding(tmp_path: Path):
    result = _run_selector(
        tmp_path,
        """
          selected:
            source: payload
            path: aliases
""",
        _assertion_envelope(payload={"aliases": ["AGR:1", "AGR:2"]}),
    )

    finding = next(
        item
        for item in result.envelope.validation_findings
        if item.code == "selector_ambiguous"
    )
    assert finding.details["selector_problem"]["value_count"] == 2


def test_runtime_selector_unresolved_ref_becomes_structured_finding(tmp_path: Path):
    result = _run_selector(
        tmp_path,
        """
          selected:
            source: object_ref
            field_path: ref
            object_type: RefObject
            path: curie
""",
        _assertion_envelope(
            payload={
                "ref": {
                    "pending_ref_id": "missing-ref",
                    "object_type": "RefObject",
                }
            }
        ),
    )

    finding = next(
        item
        for item in result.envelope.validation_findings
        if item.code == "selector_unresolved_ref"
    )
    assert finding.details["selector_problem"]["ref"]["pending_ref_id"] == "missing-ref"


def test_runtime_selector_missing_evidence_becomes_structured_finding(tmp_path: Path):
    result = _run_selector(
        tmp_path,
        """
          selected:
            source: evidence_record
            path: quote
""",
        _assertion_envelope(payload={"value": "AGR:1"}),
    )

    assert any(
        finding.code == "selector_missing"
        for finding in result.envelope.validation_findings
    )


def test_evidence_selector_uses_canonical_evidence_record_id(tmp_path: Path):
    result = _run_selector(
        tmp_path,
        """
          selected:
            source: evidence_record
            path: quote
            record_id: evidence-1
""",
        _assertion_envelope(
            payload={
                "evidence_records": [
                    {"id": "evidence-1", "quote": "legacy alias is not canonical"}
                ]
            },
        ),
    )

    finding = next(
        item
        for item in result.envelope.validation_findings
        if item.code == "selector_missing"
    )
    assert finding.details["selector_problem"]["record_id"] == "evidence-1"
    assert not any(
        item.code == "domain_pack.validator_dispatch_unavailable"
        for item in result.envelope.validation_findings
    )


def test_object_ref_selector_uses_explicit_ref_not_sibling_guessing(tmp_path: Path):
    result = _run_selector(
        tmp_path,
        """
          selected:
            source: object_ref
            field_path: ref
            object_type: RefObject
            path: curie
""",
        _assertion_envelope(
            payload={},
            extra_objects=[
                CuratableObjectEnvelope(
                    object_type="RefObject",
                    pending_ref_id="available-but-unreferenced",
                    payload={"curie": "AGR:sideways"},
                )
            ],
        ),
    )

    assert any(
        finding.code == "selector_missing"
        for finding in result.envelope.validation_findings
    )

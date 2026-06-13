"""Unit tests for deterministic validator input selectors."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from src.lib.domain_packs.loader import load_domain_pack_metadata
from src.lib.domain_packs.input_selectors import build_domain_validation_request
from src.lib.domain_packs.registry import LoadedDomainPack
from src.lib.domain_packs.validation_registry import (
    DomainPackValidationRegistry,
    ValidationBindingState,
    ValidationRegistryError,
)
from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    DomainEnvelope,
    ValidationFindingSeverity,
)
from src.schemas.domain_pack_metadata import DomainPackInputSelector


def _selector_pack_text(
    input_selector_yaml: str, *, binding_policy_yaml: str = ""
) -> str:
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
      - field_path: data_provider
        field_type: string
      - field_path: organism_id
        field_type: string
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
{binding_policy_yaml}""".strip()


def _loaded_pack(
    tmp_path: Path, input_selector_yaml: str, *, binding_policy_yaml: str = ""
) -> LoadedDomainPack:
    pack_path = tmp_path / "fixture.selectors"
    pack_path.mkdir()
    metadata_path = pack_path / "domain_pack.yaml"
    metadata_path.write_text(
        _selector_pack_text(
            input_selector_yaml, binding_policy_yaml=binding_policy_yaml
        ),
        encoding="utf-8",
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


def _run_selector(
    tmp_path: Path,
    input_selector_yaml: str,
    envelope: DomainEnvelope,
    *,
    binding_policy_yaml: str = "",
):
    pack = _loaded_pack(
        tmp_path, input_selector_yaml, binding_policy_yaml=binding_policy_yaml
    )
    registry = DomainPackValidationRegistry.from_domain_pack(pack)
    match = registry.match_bindings(
        envelope,
        states=[ValidationBindingState.ACTIVE],
    )[0]
    return build_domain_validation_request(match)


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
        {
            "source": "evidence_record",
            "output": "quote_bundle",
            "field_path": "value",
        },
        {
            "source": "payload_keyed_literal",
            "path": "value",
            "key_map": {"gene": "Gene Disease Relation"},
        },
    ],
)
def test_input_selector_parser_accepts_every_source(selector: dict):
    parsed = DomainPackInputSelector.model_validate(selector)

    assert parsed.source == selector["source"]


@pytest.mark.parametrize(
    "selector",
    [
        # missing path
        {"source": "payload_keyed_literal", "key_map": {"gene": "x"}},
        # missing/empty key_map
        {"source": "payload_keyed_literal", "path": "value"},
        {"source": "payload_keyed_literal", "path": "value", "key_map": {}},
    ],
)
def test_input_selector_parser_rejects_malformed_payload_keyed_literal(selector: dict):
    with pytest.raises(ValidationError):
        DomainPackInputSelector.model_validate(selector)


def test_payload_keyed_literal_maps_sibling_value_to_subset(tmp_path: Path):
    result = _run_selector(
        tmp_path,
        """
          subset:
            source: payload_keyed_literal
            path: value
            key_map:
              gene:
                - Gene Disease Relation
                - Via Orthology Disease Relation
              agm: AGM Disease Relation
            required: false
          selected:
            source: payload
            path: value
""",
        _assertion_envelope(payload={"value": "agm"}),
    )

    assert result.findings == ()
    assert result.request is not None
    assert result.selected_inputs["subset"] == "AGM Disease Relation"
    assert result.selected_inputs["selected"] == "agm"


def test_payload_keyed_literal_unknown_value_is_omitted(tmp_path: Path):
    result = _run_selector(
        tmp_path,
        """
          subset:
            source: payload_keyed_literal
            path: value
            key_map:
              agm: AGM Disease Relation
            required: false
          selected:
            source: payload
            path: value
""",
        _assertion_envelope(payload={"value": "gene"}),
    )

    assert result.findings == ()
    assert result.request is not None
    # Unknown sibling value -> the keyed input is simply omitted (no guessed literal).
    assert "subset" not in result.selected_inputs
    assert result.selected_inputs["selected"] == "gene"


def test_payload_keyed_literal_list_value_unions_subsets(tmp_path: Path):
    result = _run_selector(
        tmp_path,
        """
          subset:
            source: payload_keyed_literal
            path: value
            key_map:
              gene:
                - Gene Disease Relation
                - Via Orthology Disease Relation
            required: false
          selected:
            source: payload
            path: value
""",
        _assertion_envelope(payload={"value": "gene"}),
    )

    assert result.findings == ()
    assert result.selected_inputs["subset"] == [
        "Gene Disease Relation",
        "Via Orthology Disease Relation",
    ]


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

    assert result.findings == ()
    assert result.request is not None
    request = result.request.model_dump(mode="json")
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


def test_optional_payload_selector_missing_suppresses_literal_only_request(
    tmp_path: Path,
):
    result = _run_selector(
        tmp_path,
        """
          curie:
            source: payload
            path: value
            required: false
          ontology_term_type:
            source: literal
            value: DOTerm
""",
        _assertion_envelope(payload={}),
    )

    assert result.findings == ()
    assert result.request is None
    assert result.selected_inputs == {"ontology_term_type": "DOTerm"}
    assert result.input_selectors["curie"] == {
        "source": "payload",
        "path": "value",
        "required": False,
    }


def test_optional_context_only_selectors_do_not_launch_request(tmp_path: Path):
    result = _run_selector(
        tmp_path,
        """
          curie:
            source: payload
            path: value
            required: false
          organism_id:
            source: payload
            path: organism_id
            required: false
            context_only: true
          ontology_term_type:
            source: literal
            value: DOTerm
""",
        _assertion_envelope(payload={"organism_id": "NCBITaxon:6239"}),
    )

    assert result.findings == ()
    assert result.request is None
    assert result.selected_inputs == {
        "organism_id": "NCBITaxon:6239",
        "ontology_term_type": "DOTerm",
    }


def test_optional_payload_selector_present_keeps_literal_context(
    tmp_path: Path,
):
    result = _run_selector(
        tmp_path,
        """
          curie:
            source: payload
            path: value
            required: false
          ontology_term_type:
            source: literal
            value: DOTerm
""",
        _assertion_envelope(payload={"value": "DOID:1"}),
    )

    assert result.findings == ()
    assert result.request is not None
    request = result.request.model_dump(mode="json")
    assert request["selected_inputs"] == {
        "curie": "DOID:1",
        "ontology_term_type": "DOTerm",
    }


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

    assert {finding.code for finding in result.findings} == {"selector_missing_field"}
    assert result.request is None
    # Default (non-blocking) binding: a missing required input is an ERROR, not a gate.
    assert all(
        finding.severity is ValidationFindingSeverity.ERROR
        for finding in result.findings
    )


def test_blocking_binding_selector_missing_field_is_blocker_severity(tmp_path: Path):
    # R3: a required input selector on a BLOCKING binding surfaces a submission BLOCKER, so the
    # displayed severity matches the readiness gate (which keys on the binding's blocking+required
    # policy, not the severity word). Mirrors structural_checks for required-field policies.
    result = _run_selector(
        tmp_path,
        """
          selected:
            source: payload
            path: value
""",
        _assertion_envelope(payload={}),
        binding_policy_yaml="        required: true\n        blocking: true",
    )

    assert {finding.code for finding in result.findings} == {"selector_missing_field"}
    assert result.findings
    assert all(
        finding.severity is ValidationFindingSeverity.BLOCKER
        for finding in result.findings
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
        for item in result.findings
        if item.code == "selector_ambiguous"
    )
    assert finding.details["selector_problem"]["value_count"] == 2


def test_allow_multiple_payload_selector_preserves_list_values(tmp_path: Path):
    result = _run_selector(
        tmp_path,
        """
          selected:
            source: payload
            path: aliases
            allow_multiple: true
""",
        _assertion_envelope(payload={"aliases": ["AGR:1", "AGR:2"]}),
    )

    assert result.findings == ()
    assert result.request is not None
    request = result.request.model_dump(mode="json")
    assert request["selected_inputs"] == {"selected": ["AGR:1", "AGR:2"]}
    assert request["target"]["input_values"] == {"selected": ["AGR:1", "AGR:2"]}


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
        for item in result.findings
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

    assert {finding.code for finding in result.findings} == {"selector_missing"}
    assert result.request is None


def test_optional_missing_selector_is_omitted_without_suppressing_request(
    tmp_path: Path,
):
    result = _run_selector(
        tmp_path,
        """
          selected:
            source: payload
            path: value
          optional_quote:
            source: evidence_record
            path: quote
            required: false
""",
        _assertion_envelope(payload={"value": "AGR:1"}),
    )

    assert result.findings == ()
    assert result.request is not None
    request = result.request.model_dump(mode="json")
    assert request["selected_inputs"] == {"selected": "AGR:1"}
    assert request["target"]["input_values"] == {"selected": "AGR:1"}
    assert "optional_quote" in request["input_selectors"]


def test_evidence_selector_reads_nested_extraction_metadata_records(tmp_path: Path):
    result = _run_selector(
        tmp_path,
        """
          selected:
            source: payload
            path: value
          evidence_quote:
            source: evidence_record
            path: verified_quote
            record_id: evidence-nested
            required: false
            context_only: true
""",
        _assertion_envelope(
            payload={"value": "AGR:1"},
            envelope_metadata={
                "extraction_metadata": {
                    "evidence_records": [
                        {
                            "evidence_record_id": "evidence-nested",
                            "verified_quote": "Nested extraction metadata quote.",
                        }
                    ]
                }
            },
            evidence_record_ids=["evidence-nested"],
        ),
    )

    assert result.findings == ()
    assert result.request is not None
    assert result.selected_inputs == {
        "selected": "AGR:1",
        "evidence_quote": "Nested extraction metadata quote.",
    }
    assert result.evidence == [
        {
            "evidence_record_id": "evidence-nested",
            "verified_quote": "Nested extraction metadata quote.",
        }
    ]


def test_evidence_selector_does_not_fallback_to_envelope_records_without_object_ids(
    tmp_path: Path,
):
    result = _run_selector(
        tmp_path,
        """
          selected:
            source: payload
            path: value
          evidence_quote:
            source: evidence_record
            path: verified_quote
            required: false
            context_only: true
""",
        _assertion_envelope(
            payload={"value": "AGR:1"},
            envelope_metadata={
                "evidence_records": [
                    {
                        "evidence_record_id": "evidence-1",
                        "verified_quote": "First envelope quote.",
                    },
                    {
                        "evidence_record_id": "evidence-2",
                        "verified_quote": "Second envelope quote.",
                    },
                ]
            },
        ),
    )

    assert result.findings == ()
    assert result.request is not None
    assert result.selected_inputs == {"selected": "AGR:1"}
    assert result.evidence == []


def test_evidence_selector_keeps_object_local_records_without_object_ids(
    tmp_path: Path,
):
    result = _run_selector(
        tmp_path,
        """
          selected:
            source: payload
            path: value
          evidence_quote:
            source: evidence_record
            path: verified_quote
            required: false
            context_only: true
""",
        _assertion_envelope(
            payload={
                "value": "AGR:1",
                "evidence_records": [
                    {
                        "evidence_record_id": "evidence-local",
                        "verified_quote": "Object-local quote.",
                    },
                ],
            },
            envelope_metadata={
                "evidence_records": [
                    {
                        "evidence_record_id": "evidence-envelope",
                        "verified_quote": "Envelope quote must not be inherited.",
                    }
                ]
            },
        ),
    )

    assert result.findings == ()
    assert result.request is not None
    assert result.selected_inputs == {
        "selected": "AGR:1",
        "evidence_quote": "Object-local quote.",
    }
    assert result.evidence == [
        {
            "evidence_record_id": "evidence-local",
            "verified_quote": "Object-local quote.",
        }
    ]


def test_evidence_selector_filters_by_field_path_without_unrelated_fallback(
    tmp_path: Path,
):
    result = _run_selector(
        tmp_path,
        """
          selected:
            source: payload
            path: value
          evidence_quote:
            source: evidence_record
            path: verified_quote
            field_path: value
            required: false
            context_only: true
""",
        _assertion_envelope(
            payload={"value": "AGR:1"},
            envelope_metadata={
                "evidence_records": [
                    {
                        "evidence_record_id": "evidence-alias",
                        "verified_quote": "Quote for a different field.",
                        "field_path": "aliases",
                    }
                ]
            },
            evidence_record_ids=["evidence-alias"],
        ),
    )

    assert result.findings == ()
    assert result.request is not None
    assert result.selected_inputs == {"selected": "AGR:1"}
    assert "evidence_quote" not in result.selected_inputs


def test_evidence_selector_allow_multiple_returns_all_field_matched_quotes(
    tmp_path: Path,
):
    result = _run_selector(
        tmp_path,
        """
          selected:
            source: payload
            path: value
          evidence_quotes:
            source: evidence_record
            path: verified_quote
            field_path: value
            allow_multiple: true
            required: false
            context_only: true
""",
        _assertion_envelope(
            payload={"value": "AGR:1"},
            envelope_metadata={
                "evidence_records": [
                    {
                        "evidence_record_id": "evidence-1",
                        "verified_quote": "First value quote.",
                        "field_paths": ["value"],
                    },
                    {
                        "evidence_record_id": "evidence-2",
                        "verified_quote": "Second value quote.",
                        "envelope_targets": [
                            {
                                "pending_ref_id": "assertion-1",
                                "field_path": "value",
                            }
                        ],
                    },
                ]
            },
            evidence_record_ids=["evidence-1", "evidence-2"],
        ),
    )

    assert result.findings == ()
    assert result.request is not None
    assert result.selected_inputs["evidence_quotes"] == [
        "First value quote.",
        "Second value quote.",
    ]


def test_evidence_quote_bundle_selector_returns_record_id_field_and_quote(
    tmp_path: Path,
):
    result = _run_selector(
        tmp_path,
        """
          selected:
            source: payload
            path: value
          evidence_quotes:
            source: evidence_record
            output: quote_bundle
            field_path: value
            allow_multiple: true
            required: false
            context_only: true
""",
        _assertion_envelope(
            payload={"value": "AGR:1"},
            envelope_metadata={
                "extraction_metadata": {
                    "evidence_records": [
                        {
                            "evidence_record_id": "evidence-1",
                            "verified_quote": "Verified value quote.",
                            "envelope_targets": [
                                {
                                    "pending_ref_id": "assertion-1",
                                    "field_path": "value",
                                }
                            ],
                        },
                        {
                            "evidence_record_id": "evidence-other",
                            "verified_quote": "Verified alias quote.",
                            "field_path": "aliases",
                        },
                    ]
                }
            },
            evidence_record_ids=["evidence-1", "evidence-other"],
        ),
    )

    assert result.findings == ()
    assert result.request is not None
    assert result.selected_inputs["evidence_quotes"] == [
        {
            "evidence_record_id": "evidence-1",
            "field_path": "value",
            "verified_quote": "Verified value quote.",
        }
    ]
    assert result.input_selectors["evidence_quotes"] == {
        "source": "evidence_record",
        "field_path": "value",
        "output": "quote_bundle",
        "required": False,
        "allow_multiple": True,
        "context_only": True,
    }


def test_optional_ambiguous_selector_still_becomes_structured_finding(
    tmp_path: Path,
):
    result = _run_selector(
        tmp_path,
        """
          selected:
            source: payload
            path: value
          optional_alias:
            source: payload
            path: aliases
            required: false
""",
        _assertion_envelope(payload={"value": "AGR:1", "aliases": ["A", "B"]}),
    )

    assert {finding.code for finding in result.findings} == {"selector_ambiguous"}
    assert result.request is None


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
        for item in result.findings
        if item.code == "selector_missing"
    )
    assert finding.details["selector_problem"]["record_id"] == "evidence-1"
    assert result.request is None


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

    assert {finding.code for finding in result.findings} == {"selector_missing"}
    assert result.request is None

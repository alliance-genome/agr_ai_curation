"""Unit tests for metadata-driven domain-pack validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from agr_ai_curation_runtime import agr_lookup
from src.lib import lookup_status
from src.lib.domain_packs.loader import load_domain_pack_metadata
from src.lib.domain_packs.registry import LoadedDomainPack
from src.lib.domain_packs.validation_registry import (
    DomainPackValidationRegistry,
    ValidationRegistryError,
    ValidationBindingState,
)
from src.lib.domain_packs import validation_supervisor
from src.lib.domain_packs.validation_supervisor import run_validation_supervisor
from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    DomainEnvelope,
    ObjectRef,
    ValidationFinding,
    ValidationFindingSeverity,
)


def test_lookup_status_constants_are_shared_with_agr_lookup_contract():
    assert (
        validation_supervisor.LOOKUP_STATUS_BLOCKED
        == lookup_status.LOOKUP_STATUS_BLOCKED
    )
    assert (
        validation_supervisor.LOOKUP_STATUS_UNDER_DEVELOPMENT
        == lookup_status.LOOKUP_STATUS_UNDER_DEVELOPMENT
    )
    assert agr_lookup.LOOKUP_STATUS_BLOCKED == lookup_status.LOOKUP_STATUS_BLOCKED
    assert (
        agr_lookup.LOOKUP_STATUS_UNDER_DEVELOPMENT
        == lookup_status.LOOKUP_STATUS_UNDER_DEVELOPMENT
    )


def _validation_pack_text() -> str:
    return """
pack_id: fixture.validation
display_name: Fixture Validation Pack
version: 0.1.0
metadata_api_version: 1.0.0
status: active
enum_definitions:
  - enum_id: ConfidenceLevel
    display_name: Confidence level
    values:
      - value: high
      - value: low
model_definitions:
  - model_id: GeneAssertionPayload
    display_name: Gene assertion payload
object_definitions:
  - object_type: GeneAssertion
    display_name: Gene assertion
    model_ref: GeneAssertionPayload
    metadata:
      object_role: curatable_unit
      provider_refs:
        fixture_provider:
          class: GeneAssertion
    fields:
      - field_path: gene.symbol
        field_type: string
        required: true
        metadata:
          provider_refs:
            fixture_provider:
              slot: symbol
      - field_path: gene.identifier
        field_type: string
        required: true
        metadata:
          export_blocking: true
          provider_refs:
            fixture_provider:
              slot: identifier
      - field_path: confidence
        field_type: enum
        enum_ref: ConfidenceLevel
metadata:
  validators:
    active:
      - validator_id: fixture.shape
        display_name: Fixture envelope shape
        description: Envelope shape validation is active.
    planned:
      - validator_id: fixture.future_lookup
        description: Planned lookup validator.
    blocked:
      - validator_id: fixture.export_projection
        blocked_by: ALL-999
        description: Export projection is intentionally blocked.
  validator_bindings:
    active:
      - binding_id: fixture.callable_validator
        display_name: Callable envelope validation
        validator: fixture.validators.validate
        applies_to:
          domain_pack_id: fixture.validation
          object_types:
            - GeneAssertion
      - binding_id: fixture.identifier_prefix
        validation_kind: curie_prefix_format
        prefix: AGR
        applies_to:
          domain_pack_id: fixture.validation
          object_types:
            - GeneAssertion
          object_roles:
            - curatable_unit
          field_paths:
            - gene.identifier
          field_types:
            - string
        blocking: true
        allow_opt_out: true
        opt_out_reason_required: true
      - binding_id: fixture.optional_confidence_check
        validation_kind: enum_value_check
        required: false
        applies_to:
          domain_pack_id: fixture.validation
          object_types:
            - GeneAssertion
          field_paths:
            - confidence
    planned:
      - binding_id: fixture.planned_symbol_lookup
        display_name: Gene symbol lookup
        validation_kind: db_backed_reference_lookup
        applies_to:
          domain_pack_id: fixture.validation
          object_types:
            - GeneAssertion
          field_paths:
            - gene.symbol
        definition_state: in_development
    blocked:
      - binding_id: fixture.blocked_export_validator
        validation_kind: export_projection
        applies_to:
          domain_pack_id: fixture.validation
          object_types:
            - GeneAssertion
        blocked_by: ALL-999
        reason: Export projection adapter is not available.
""".strip()


def _loaded_pack(tmp_path: Path, metadata_text: str | None = None) -> LoadedDomainPack:
    pack_path = tmp_path / "fixture.validation"
    pack_path.mkdir()
    metadata_path = pack_path / "domain_pack.yaml"
    metadata_path.write_text(metadata_text or _validation_pack_text(), encoding="utf-8")
    metadata = load_domain_pack_metadata(metadata_path)
    return LoadedDomainPack(
        pack_id=metadata.pack_id,
        display_name=metadata.display_name,
        version=metadata.version,
        pack_path=pack_path,
        metadata_path=metadata_path,
        metadata=metadata,
    )


def _envelope(
    *,
    payload: dict | None = None,
    object_role: str = "curatable_unit",
) -> DomainEnvelope:
    return DomainEnvelope(
        envelope_id="env-1",
        domain_pack_id="fixture.validation",
        objects=[
            CuratableObjectEnvelope(
                object_type="GeneAssertion",
                object_role=object_role,
                pending_ref_id="gene-assertion-1",
                model_ref="GeneAssertionPayload",
                payload=payload
                if payload is not None
                else {
                    "gene": {
                        "symbol": "abc-1",
                        "identifier": "AGR:0000001",
                    },
                    "confidence": "high",
                },
            )
        ],
    )


def test_registry_matches_bindings_by_state_field_type_and_object_role(tmp_path: Path):
    pack = _loaded_pack(tmp_path)
    registry = DomainPackValidationRegistry.from_domain_pack(pack)

    matches = registry.match_bindings(
        _envelope(),
        states=[ValidationBindingState.ACTIVE],
    )
    by_binding = {match.binding.binding_id: match for match in matches}

    assert by_binding["fixture.callable_validator"].object_type == "GeneAssertion"
    assert by_binding["fixture.identifier_prefix"].field_path == "gene.identifier"
    assert by_binding["fixture.identifier_prefix"].field_definition.field_type.value == "string"
    identifier_policy = registry.policy_for("GeneAssertion", "gene.identifier")
    assert identifier_policy is not None
    assert identifier_policy.required is True
    assert identifier_policy.export_blocking is True
    assert identifier_policy.allow_opt_out is True
    assert identifier_policy.opt_out_reason_required is True

    metadata_only_matches = registry.match_bindings(
        _envelope(object_role="metadata_only"),
        states=[ValidationBindingState.ACTIVE],
    )
    assert {
        match.binding.binding_id for match in metadata_only_matches
    } == {"fixture.callable_validator", "fixture.optional_confidence_check"}


def test_registry_exposes_planned_and_blocked_validator_metadata(tmp_path: Path):
    pack = _loaded_pack(tmp_path)
    registry = DomainPackValidationRegistry.from_domain_pack(pack)

    metadata_by_state = {
        entry.state: entry.validator_id for entry in registry.validator_metadata
    }
    binding_states = {binding.binding_id: binding.state for binding in registry.bindings}

    assert metadata_by_state[ValidationBindingState.PLANNED] == "fixture.future_lookup"
    assert metadata_by_state[ValidationBindingState.BLOCKED] == "fixture.export_projection"
    assert binding_states["fixture.planned_symbol_lookup"] is ValidationBindingState.PLANNED
    assert binding_states["fixture.blocked_export_validator"] is ValidationBindingState.BLOCKED


def test_registry_builds_flow_validation_attachment_options(tmp_path: Path):
    pack = _loaded_pack(tmp_path)
    registry = DomainPackValidationRegistry.from_domain_pack(pack)

    options = registry.validation_attachment_options()
    by_id = {option.attachment_id: option for option in options}

    identifier_option = by_id[
        "fixture.validation:binding:fixture.identifier_prefix:field:GeneAssertion:gene.identifier"
    ]
    assert identifier_option.state is ValidationBindingState.ACTIVE
    assert identifier_option.field_path == "gene.identifier"
    assert identifier_option.required is False
    assert identifier_option.export_blocking is True
    assert identifier_option.default_enabled is True
    assert identifier_option.allow_opt_out is True
    assert identifier_option.opt_out_reason_required is True

    callable_option = by_id[
        "fixture.validation:binding:fixture.callable_validator:object:GeneAssertion:*"
    ]
    assert callable_option.required is False
    assert callable_option.default_enabled is True
    assert callable_option.allow_opt_out is True
    assert callable_option.opt_out_reason_required is False

    optional_option = by_id[
        "fixture.validation:binding:fixture.optional_confidence_check:field:GeneAssertion:confidence"
    ]
    assert optional_option.required is False
    assert optional_option.default_enabled is True
    assert optional_option.allow_opt_out is True

    planned_option = by_id[
        "fixture.validation:binding:fixture.planned_symbol_lookup:field:GeneAssertion:gene.symbol"
    ]
    assert planned_option.state is ValidationBindingState.PLANNED
    assert planned_option.default_enabled is False
    assert planned_option.label == "Gene symbol lookup"

    blocked_option = by_id[
        "fixture.validation:binding:fixture.blocked_export_validator:object:GeneAssertion:*"
    ]
    assert blocked_option.state is ValidationBindingState.BLOCKED
    assert blocked_option.blocked_by == "ALL-999"

    metadata_option = by_id["fixture.validation:metadata:fixture.shape:pack:*:*"]
    assert metadata_option.label == "Fixture envelope shape"

    callable_option = by_id[
        "fixture.validation:binding:fixture.callable_validator:object:GeneAssertion:*"
    ]
    assert callable_option.label == "Callable envelope validation"


def test_registry_rejects_conflicting_status_and_state_metadata(tmp_path: Path):
    metadata_text = _validation_pack_text().replace(
        "validator_id: fixture.shape\n        display_name: Fixture envelope shape\n        description:",
        "\n".join(
            (
                "validator_id: fixture.shape",
                "        display_name: Fixture envelope shape",
                "        status: planned",
                "        state: active",
                "        description:",
            )
        ),
        1,
    )
    pack = _loaded_pack(tmp_path, metadata_text=metadata_text)

    with pytest.raises(
        ValidationRegistryError,
        match="validators item declares conflicting status/state values",
    ):
        DomainPackValidationRegistry.from_domain_pack(pack)


def test_registry_required_ids_report_edge_whitespace(tmp_path: Path):
    metadata_text = _validation_pack_text().replace(
        "validator_id: fixture.shape",
        "validator_id: ' fixture.shape'",
        1,
    )
    pack = _loaded_pack(tmp_path, metadata_text=metadata_text)

    with pytest.raises(
        ValidationRegistryError,
        match="validators.validator_id must not have leading or trailing whitespace",
    ):
        DomainPackValidationRegistry.from_domain_pack(pack)


def test_supervisor_appends_required_planned_blocked_findings_and_history(
    tmp_path: Path,
    monkeypatch,
):
    pack = _loaded_pack(tmp_path)
    envelope = _envelope(payload={"gene": {"symbol": "abc-1"}, "confidence": "high"})
    monkeypatch.setattr(
        validation_supervisor,
        "_load_validator_callable",
        lambda _path: lambda _envelope: (),
    )

    result = run_validation_supervisor(envelope, pack)
    findings_by_code = {finding.code: finding for finding in result.envelope.validation_findings}

    required_finding = findings_by_code["domain_pack.required_field_missing"]
    assert required_finding.severity is ValidationFindingSeverity.BLOCKER
    assert required_finding.field_ref.field_path == "gene.identifier"
    assert (
        required_finding.details["validation_metadata"]["metadata_source"]
        == "field_policy"
    )
    assert required_finding.details["validation_metadata"]["field_policy"][
        "policy_source"
    ] == "field_policy"
    assert required_finding.details["validation_metadata"]["field_policy"][
        "export_blocking"
    ] is True

    planned_binding = [
        finding
        for finding in result.envelope.validation_findings
        if finding.code == "domain_pack.validator_binding_planned"
    ][0]
    assert planned_binding.field_ref.field_path == "gene.symbol"
    assert planned_binding.details["validation_metadata"]["validator_binding_id"] == (
        "fixture.planned_symbol_lookup"
    )
    planned_attempt = planned_binding.details["lookup_attempts"][0]
    assert planned_attempt["provider"] is None
    assert "provider" not in planned_binding.details["provider_projections"][0]

    blocked_metadata = [
        finding
        for finding in result.envelope.validation_findings
        if finding.code == "domain_pack.validator_blocked"
    ][0]
    assert blocked_metadata.details["validation_metadata"]["blocked_by"] == "ALL-999"

    assert len(result.envelope.history) == len(result.appended_findings)
    assert {
        event.event_type.value for event in result.envelope.history
    } == {"validation_finding_added"}
    assert any(
        event.details["target"].get("field_path") == "gene.identifier"
        for event in result.envelope.history
    )


def test_supervisor_marks_field_definition_source_when_policy_absent(tmp_path: Path):
    pack = _loaded_pack(tmp_path)
    registry = DomainPackValidationRegistry.from_domain_pack(pack)
    envelope = _envelope(payload={"gene": {"symbol": "abc-1"}, "confidence": "high"})

    class RegistryWithoutFormalFieldPolicies:
        validator_metadata = ()

        def __init__(self, delegate: DomainPackValidationRegistry) -> None:
            self.object_definitions_by_type = delegate.object_definitions_by_type

        def match_bindings(self, _envelope: DomainEnvelope) -> tuple[object, ...]:
            return ()

        def policy_for(self, _object_type: str, _field_path: str) -> None:
            return None

    result = run_validation_supervisor(
        envelope,
        pack,
        registry=RegistryWithoutFormalFieldPolicies(registry),
    )

    required_finding = [
        finding
        for finding in result.envelope.validation_findings
        if finding.code == "domain_pack.required_field_missing"
    ][0]
    metadata = required_finding.details["validation_metadata"]

    assert metadata["metadata_source"] == "field_definition"
    assert metadata["field_policy"]["policy_source"] == "field_definition"
    assert metadata["field_policy"]["field_path"] == "gene.identifier"


def test_supervisor_runs_callable_and_field_prefix_bindings(tmp_path: Path, monkeypatch):
    pack = _loaded_pack(tmp_path)
    envelope = _envelope(
        payload={"gene": {"symbol": "abc-1", "identifier": "BAD:0001"}}
    )

    def _fake_validator(domain_envelope: DomainEnvelope):
        assert domain_envelope.envelope_id == "env-1"
        return (
            ValidationFinding(
                severity=ValidationFindingSeverity.WARNING,
                code="fixture.callable_warning",
                message="Callable validator warning.",
                object_ref=ObjectRef(
                    pending_ref_id="gene-assertion-1",
                    object_type="GeneAssertion",
                ),
            ),
        )

    monkeypatch.setattr(
        validation_supervisor,
        "_load_validator_callable",
        lambda _path: _fake_validator,
    )

    result = run_validation_supervisor(
        envelope,
        pack,
        provider_model_ref={"provider": "openai", "model": "gpt-test"},
    )
    findings_by_code = {finding.code: finding for finding in result.envelope.validation_findings}

    callable_finding = findings_by_code["fixture.callable_warning"]
    assert callable_finding.details["validation_metadata"]["validator_binding_id"] == (
        "fixture.callable_validator"
    )
    assert callable_finding.details["validation_metadata"]["provider_model_ref"] == {
        "provider": "openai",
        "model": "gpt-test",
    }

    prefix_finding = findings_by_code["domain_pack.curie_prefix_mismatch"]
    assert prefix_finding.severity is ValidationFindingSeverity.BLOCKER
    assert prefix_finding.field_ref.field_path == "gene.identifier"
    assert prefix_finding.details["observed_value"] == "BAD:0001"


def test_supervisor_does_not_fake_success_for_unsupported_active_binding(tmp_path: Path):
    metadata_text = _validation_pack_text().replace(
        "validator: fixture.validators.validate",
        "validation_kind: db_backed_reference_lookup",
        1,
    )
    pack = _loaded_pack(tmp_path, metadata_text=metadata_text)

    result = run_validation_supervisor(envelope=_envelope(), domain_pack=pack)

    dispatch_findings = [
        finding
        for finding in result.envelope.validation_findings
        if finding.code == "domain_pack.validator_dispatch_unavailable"
    ]
    assert len(dispatch_findings) == 2
    assert dispatch_findings[0].severity is ValidationFindingSeverity.WARNING
    assert dispatch_findings[0].object_ref.pending_ref_id == "gene-assertion-1"

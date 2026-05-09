"""Unit tests for metadata-driven domain-pack validation."""

from __future__ import annotations

import sys
from pathlib import Path

from src.lib.domain_packs.loader import load_domain_pack_metadata
from src.lib.domain_packs.registry import LoadedDomainPack
from src.lib.domain_packs.validation_registry import (
    DomainPackValidationRegistry,
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


REPO_ROOT = Path(__file__).resolve().parents[5]
ALLIANCE_PYTHON_SRC = REPO_ROOT / "packages" / "alliance" / "python" / "src"
if str(ALLIANCE_PYTHON_SRC) not in sys.path:
    sys.path.insert(0, str(ALLIANCE_PYTHON_SRC))


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
    planned:
      - binding_id: fixture.planned_symbol_lookup
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
    assert registry.policy_for("GeneAssertion", "gene.identifier").required is True
    assert registry.policy_for("GeneAssertion", "gene.identifier").export_blocking is True

    metadata_only_matches = registry.match_bindings(
        _envelope(object_role="metadata_only"),
        states=[ValidationBindingState.ACTIVE],
    )
    assert {
        match.binding.binding_id for match in metadata_only_matches
    } == {"fixture.callable_validator"}


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
    assert len(dispatch_findings) == 1
    assert dispatch_findings[0].severity is ValidationFindingSeverity.WARNING
    assert dispatch_findings[0].object_ref.pending_ref_id == "gene-assertion-1"


def test_alliance_domain_pack_validation_metadata_states_are_discoverable():
    from agr_ai_curation_alliance.domain_packs import load_alliance_domain_pack_registry

    alliance_registry = load_alliance_domain_pack_registry()
    expected_pack_ids = {
        "agr.alliance.gene_expression",
        "gene",
        "agr.alliance.allele",
        "agr.alliance.disease",
        "agr.alliance.chemical_condition",
        "agr.alliance.phenotype",
    }

    validation_registries = {
        pack_id: DomainPackValidationRegistry.from_domain_pack(
            alliance_registry.get_pack(pack_id)
        )
        for pack_id in expected_pack_ids
    }

    assert {
        entry.state
        for entry in validation_registries[
            "agr.alliance.gene_expression"
        ].validator_metadata
    } >= {ValidationBindingState.PLANNED, ValidationBindingState.BLOCKED}
    assert {
        binding.binding_id
        for binding in validation_registries["gene"].bindings
    } == {"alliance_gene_reference_lookup"}
    assert {
        binding.state
        for binding in validation_registries["agr.alliance.disease"].bindings
    } >= {
        ValidationBindingState.ACTIVE,
        ValidationBindingState.PLANNED,
        ValidationBindingState.BLOCKED,
    }
    assert {
        binding.state
        for binding in validation_registries[
            "agr.alliance.chemical_condition"
        ].bindings
    } == {
        ValidationBindingState.ACTIVE,
        ValidationBindingState.PLANNED,
        ValidationBindingState.BLOCKED,
    }
    assert {
        binding.binding_id
        for binding in validation_registries["agr.alliance.allele"].bindings
    } == {"allele_pending_envelope_validator"}
    assert {
        entry.state
        for entry in validation_registries["agr.alliance.phenotype"].validator_metadata
    } >= {ValidationBindingState.PLANNED, ValidationBindingState.BLOCKED}


def test_alliance_relative_validator_metadata_targets_fields_and_policies():
    from agr_ai_curation_alliance.domain_packs import load_alliance_domain_pack_registry

    alliance_registry = load_alliance_domain_pack_registry()
    registries = {
        pack_id: DomainPackValidationRegistry.from_domain_pack(
            alliance_registry.get_pack(pack_id)
        )
        for pack_id in (
            "gene",
            "agr.alliance.disease",
            "agr.alliance.chemical_condition",
        )
    }

    gene_binding = {
        binding.binding_id: binding for binding in registries["gene"].bindings
    }["alliance_gene_reference_lookup"]
    assert gene_binding.object_types == ("gene_mention_evidence",)
    assert gene_binding.field_paths == (
        "primary_external_id",
        "gene_symbol",
        "taxon",
    )
    assert "alliance_gene_reference_lookup" in registries["gene"].policy_for(
        "gene_mention_evidence",
        "primary_external_id",
    ).validator_binding_ids

    disease_bindings = {
        binding.binding_id: binding
        for binding in registries["agr.alliance.disease"].bindings
    }
    assert disease_bindings["disease_ontology_term_lookup"].object_types == (
        "DiseaseAnnotation",
    )
    assert disease_bindings["disease_ontology_term_lookup"].field_paths == (
        "disease_annotation_object.curie",
        "disease_annotation_object.name",
    )
    assert disease_bindings["disease_condition_relation_lookup"].object_types == (
        "DiseaseAnnotation",
    )
    assert disease_bindings["disease_condition_relation_lookup"].field_paths == (
        "condition_relations[0].condition_relation_type.name",
    )
    assert (
        disease_bindings["disease_reference_materialization"].state
        is ValidationBindingState.BLOCKED
    )
    assert disease_bindings["disease_reference_materialization"].field_paths == (
        "single_reference.curie",
    )
    assert "disease_ontology_term_lookup" in registries[
        "agr.alliance.disease"
    ].policy_for(
        "DiseaseAnnotation",
        "disease_annotation_object.curie",
    ).validator_binding_ids
    assert "disease_condition_relation_lookup" in registries[
        "agr.alliance.disease"
    ].policy_for(
        "DiseaseAnnotation",
        "condition_relations[0].condition_relation_type.name",
    ).validator_binding_ids
    assert "disease_reference_materialization" in registries[
        "agr.alliance.disease"
    ].policy_for(
        "DiseaseAnnotation",
        "single_reference.curie",
    ).validator_binding_ids

    disease_matches = registries["agr.alliance.disease"].match_bindings(
        DomainEnvelope(
            envelope_id="disease-env",
            domain_pack_id="agr.alliance.disease",
            objects=[
                CuratableObjectEnvelope(
                    object_type="DiseaseAnnotation",
                    pending_ref_id="disease-annotation-1",
                    payload={},
                )
            ],
        ),
        states=[ValidationBindingState.PLANNED, ValidationBindingState.BLOCKED],
    )
    disease_match_targets = {
        (match.binding.binding_id, match.object_type, match.field_path)
        for match in disease_matches
    }
    assert (
        "disease_ontology_term_lookup",
        "DiseaseAnnotation",
        "disease_annotation_object.curie",
    ) in disease_match_targets
    assert (
        "disease_reference_materialization",
        "DiseaseAnnotation",
        "single_reference.curie",
    ) in disease_match_targets

    chemical_condition_bindings = {
        binding.binding_id: binding
        for binding in registries["agr.alliance.chemical_condition"].bindings
    }
    assert chemical_condition_bindings[
        "chemical_condition.chebi_api_lookup"
    ].object_types == ("ChemicalCondition",)
    assert chemical_condition_bindings[
        "chemical_condition.chebi_api_lookup"
    ].field_paths == (
        "condition_chemical.curie",
        "condition_chemical.name",
    )
    assert chemical_condition_bindings[
        "chemical_condition.condition_ontology_lookup"
    ].object_types == ("ChemicalCondition",)
    assert chemical_condition_bindings[
        "chemical_condition.condition_ontology_lookup"
    ].field_paths == (
        "condition_class.curie",
    )
    assert "chemical_condition.chebi_api_lookup" in registries[
        "agr.alliance.chemical_condition"
    ].policy_for(
        "ChemicalCondition",
        "condition_chemical.name",
    ).validator_binding_ids
    assert "chemical_condition.condition_ontology_lookup" in registries[
        "agr.alliance.chemical_condition"
    ].policy_for(
        "ChemicalCondition",
        "condition_class.curie",
    ).validator_binding_ids

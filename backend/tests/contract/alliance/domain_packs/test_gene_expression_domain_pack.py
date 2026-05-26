"""Contract tests for the Alliance gene-expression domain pack."""

from __future__ import annotations

import copy
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml

from src.lib.domain_packs.input_selectors import build_domain_validation_request
from src.lib.domain_packs.loader import load_domain_fixture_pack
from src.lib.domain_packs.materialization import (
    ValidatorResultMaterializationInput,
    materialize_validator_results_into_envelope,
)
from src.lib.domain_packs.validation_registry import (
    DomainPackValidationRegistry,
    ValidationBindingState,
)
from src.schemas.domain_validator import DomainValidatorResultBase
from src.schemas.domain_envelope import (
    CuratableObjectStatus,
    ValidationFindingSeverity,
    field_path_exists,
)
from src.schemas.domain_envelope import DefinitionState
from src.schemas.domain_pack_metadata import DomainPackFieldType


REPO_ROOT = Path(__file__).resolve().parents[5]
ALLIANCE_PYTHON_SRC = REPO_ROOT / "packages" / "alliance" / "python" / "src"
if str(ALLIANCE_PYTHON_SRC) not in sys.path:
    sys.path.insert(0, str(ALLIANCE_PYTHON_SRC))

from agr_ai_curation_alliance.domain_packs import (  # noqa: E402
    OBJECT_ROLE_METADATA_KEY,
    PROVIDER_REFS_METADATA_KEY,
    load_alliance_domain_pack_registry,
)
from agr_ai_curation_alliance.domain_packs.gene_expression import (  # noqa: E402
    GENE_EXPRESSION_DOMAIN_PACK_ID,
    GENE_EXPRESSION_FIXTURE_PACK_ID,
    GENE_EXPRESSION_MODEL_ID,
    GENE_EXPRESSION_OBJECT_TYPE,
    GENE_EXPRESSION_VALIDATOR_STATES,
    VALID_GENE_EXPRESSION_RELATION_NAMES,
    gene_expression_extraction_output_to_pending_envelope,
    get_gene_expression_domain_pack_metadata_path,
    validate_pending_gene_expression_envelope,
)

from .test_alliance_domain_pack_scaffold import (  # noqa: E402
    _assert_range_exists,
    _assert_source_file_matches,
    _cache_schema,
    _iter_linkml_provider_refs,
    _load_linkml_index,
)


GENE_EXPRESSION_OUTPUT_FIXTURE_PATH = (
    REPO_ROOT
    / "backend"
    / "tests"
    / "fixtures"
    / "domain_packs"
    / "gene_expression"
    / "tmem67_gene_expression_output.yaml"
)
FORBIDDEN_LEGACY_COLLECTIONS = {
    "items",
    "annotations",
    "genes",
    "alleles",
    "diseases",
    "chemicals",
    "phenotypes",
    "CurationPrepCandidate",
    "NormalizedCandidate",
    "normalized_payload",
    "annotation_drafts",
}


def _gene_expression_pack():
    registry = load_alliance_domain_pack_registry()
    pack = registry.get_pack(GENE_EXPRESSION_DOMAIN_PACK_ID)
    assert pack is not None
    return pack


def _gene_expression_validation_registry():
    return DomainPackValidationRegistry.from_domain_pack(_gene_expression_pack())


def _iter_mapping_keys(value: Any):
    if isinstance(value, Mapping):
        yield from value.keys()
        for child in value.values():
            yield from _iter_mapping_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_mapping_keys(child)


def _assert_metadata_refs_resolve(envelope: Any) -> None:
    unresolved = [
        metadata_ref.metadata_path
        for annotation in envelope.objects
        for metadata_ref in annotation.metadata_refs
        if not field_path_exists(envelope.metadata, metadata_ref.metadata_path)
    ]
    assert unresolved == []


def _converted_tmem67_envelope():
    raw_fixture = yaml.safe_load(
        GENE_EXPRESSION_OUTPUT_FIXTURE_PATH.read_text(encoding="utf-8")
    )
    context = raw_fixture["envelope_context"]
    return gene_expression_extraction_output_to_pending_envelope(
        raw_fixture["output"],
        envelope_id=context["envelope_id"],
        document_id=context["document_id"],
        produced_by=context["produced_by"],
        produced_at=context["produced_at"],
    )


def _with_payload(envelope: Any, payload: Mapping[str, Any]):
    annotation = envelope.objects[0].model_copy(update={"payload": dict(payload)})
    return envelope.model_copy(update={"objects": [annotation]})


def _finding_by_code(findings: tuple[Any, ...], code: str):
    matches = [finding for finding in findings if finding.code == code]
    assert len(matches) == 1
    return matches[0]


def _active_binding_match(envelope: Any, binding_id: str):
    matches = [
        match
        for match in _gene_expression_validation_registry().match_bindings(
            envelope,
            states=[ValidationBindingState.ACTIVE],
        )
        if match.binding.binding_id == binding_id
    ]
    assert len(matches) == 1
    return matches[0]


def _validator_result(
    request: Any,
    *,
    status: str,
    resolved_values: Mapping[str, Any] | None = None,
    missing_expected_fields: list[str] | None = None,
    lookup_outcome: str = "success",
    candidates: list[dict[str, Any]] | None = None,
    curator_message: str | None = None,
) -> DomainValidatorResultBase:
    return DomainValidatorResultBase(
        status=status,
        request_id=request.request_id,
        validator_binding_id=request.validator_binding_id,
        validator_agent=request.validator_agent,
        target=request.target,
        resolved_values=dict(resolved_values or {}),
        resolved_objects=[],
        missing_expected_fields=(
            missing_expected_fields
            if missing_expected_fields is not None
            else list(request.expected_result_fields)
            if status != "resolved"
            else []
        ),
        candidates=list(candidates or []),
        lookup_attempts=[
            {
                "provider": "fixture",
                "method": request.selected_inputs.get("lookup_method", "fixture_lookup"),
                "query": dict(request.selected_inputs),
                "result_count": 1 if status == "resolved" else len(candidates or []),
                "outcome": lookup_outcome,
            }
        ],
        curator_message=curator_message or f"Fixture {status} ontology result.",
        explanation="Fixture-backed validator result for gene-expression contract tests.",
    )


def test_gene_expression_domain_pack_is_bundled_with_concrete_metadata():
    pack = _gene_expression_pack()
    metadata = pack.metadata

    assert pack.metadata_path == get_gene_expression_domain_pack_metadata_path()
    assert metadata.pack_id == GENE_EXPRESSION_DOMAIN_PACK_ID
    assert [item.object_type for item in metadata.object_definitions] == [
        GENE_EXPRESSION_OBJECT_TYPE
    ]

    curatable_unit = metadata.object_definitions[0]
    assert curatable_unit.metadata[OBJECT_ROLE_METADATA_KEY] == "curatable_unit"
    assert curatable_unit.model_ref == GENE_EXPRESSION_MODEL_ID
    assert curatable_unit.schema_ref.name == GENE_EXPRESSION_OBJECT_TYPE
    assert curatable_unit.definition_state is DefinitionState.STABLE

    validators = metadata.metadata["validators"]
    assert tuple(validators) == GENE_EXPRESSION_VALIDATOR_STATES
    assert validators["active"]
    assert validators["under_development"]
    active_validator_ids = {
        validator["validator_id"] for validator in validators["active"]
    }
    under_development_validator_ids = {
        validator["validator_id"] for validator in validators["under_development"]
    }
    live_write_validator = next(
        validator
        for validator in validators["under_development"]
        if validator["validator_id"] == "gene_expression.live_write_transport"
    )
    assert live_write_validator == {
        "validator_id": "gene_expression.live_write_transport",
        "display_name": "Live database write path",
        "blocked_by": "read_only_curation_db",
        "description": (
            "Direct live DB writes remain blocked until an approved Alliance "
            "write transport replaces the read-only submission handoff adapter."
        ),
    }
    assert "gene_expression.extractor_output_migration" in active_validator_ids
    assert "gene_expression.linkml_extraction_contract" in active_validator_ids
    assert "gene_expression.export_submission_projection" in active_validator_ids
    assert (
        "gene_expression.extractor_output_migration"
        not in under_development_validator_ids
    )
    assert (
        "gene_expression.export_submission_projection"
        not in under_development_validator_ids
    )
    assert (
        "gene_expression.linkml_anatomical_site_postcondition"
        not in under_development_validator_ids
    )
    assert all(
        validator.get("blocked_by") != "ALL-407"
        for validator in validators["under_development"]
    )
    assert VALID_GENE_EXPRESSION_RELATION_NAMES == frozenset({"is_expressed_in"})

    provider_ref = metadata.metadata[PROVIDER_REFS_METADATA_KEY]["alliance_linkml"]
    assert provider_ref["commit"] == curatable_unit.schema_ref.version

    fixture_ref = load_alliance_domain_pack_registry().get_fixture_pack_ref(
        GENE_EXPRESSION_DOMAIN_PACK_ID,
        GENE_EXPRESSION_FIXTURE_PACK_ID,
    )
    assert fixture_ref is not None
    assert fixture_ref.path == "fixtures/tmem67_pending.yaml"
    assert fixture_ref.object_types == [GENE_EXPRESSION_OBJECT_TYPE]


def test_gene_expression_object_embeds_required_experiment_and_context_fields():
    metadata = _gene_expression_pack().metadata
    curatable_unit = metadata.object_definitions[0]

    required_field_paths = {
        field.field_path
        for field in curatable_unit.fields
        if field.required
    }
    assert required_field_paths == {
        "date_created",
        "internal",
        "data_provider",
        "data_provider.abbreviation",
        "expression_annotation_subject",
        "expression_annotation_subject.primary_external_id",
        "expression_annotation_subject.gene_symbol",
        "relation",
        "relation.name",
        "single_reference",
        "single_reference.reference_id",
        "expression_experiment",
        "expression_experiment.unique_id",
        "expression_experiment.expression_assay_used",
        "expression_experiment.expression_assay_used.curie",
        "when_expressed_stage_name",
        "where_expressed_statement",
        "expression_pattern",
        "expression_pattern.where_expressed",
    }

    object_ref_fields = [
        field for field in curatable_unit.fields if field.field_type is DomainPackFieldType.OBJECT_REF
    ]
    assert object_ref_fields == []


def test_gene_expression_active_validation_scope_does_not_hide_planned_gaps():
    registry = _gene_expression_validation_registry()
    metadata = _gene_expression_pack().metadata
    curatable_unit = metadata.object_definitions[0]

    active_bindings = {
        binding.binding_id: binding
        for binding in registry.bindings
        if binding.state is ValidationBindingState.ACTIVE
    }
    assert set(active_bindings) == {
        "data_provider_validation",
        "expression_anatomical_structure_validation",
        "expression_anatomical_uberon_slim_validation",
        "expression_cellular_component_qualifier_validation",
        "expression_cellular_component_validation",
        "expression_stage_ontology_validation",
        "expression_stage_uberon_slim_validation",
        "relation_vocabulary_validation",
        "source_reference_validation",
        "subject_gene_validation",
    }
    assert active_bindings["data_provider_validation"].field_paths == (
        "data_provider.abbreviation",
    )
    assert active_bindings["relation_vocabulary_validation"].field_paths == (
        "relation.name",
    )
    assert active_bindings["subject_gene_validation"].validator_agent is not None
    assert active_bindings["subject_gene_validation"].validator_agent.agent_id == (
        "gene_validation"
    )
    assert active_bindings["subject_gene_validation"].field_paths == (
        "expression_annotation_subject.primary_external_id",
        "expression_annotation_subject.gene_symbol",
    )
    assert active_bindings["source_reference_validation"].validator_agent is not None
    assert active_bindings["source_reference_validation"].validator_agent.agent_id == (
        "reference_validation"
    )
    assert active_bindings["source_reference_validation"].field_paths == (
        "single_reference.reference_id",
        "single_reference.curie",
        "single_reference.title",
    )
    assert active_bindings["expression_stage_ontology_validation"].field_paths == (
        "when_expressed_stage_name",
    )
    assert active_bindings["expression_stage_ontology_validation"].validator_agent is not None
    assert active_bindings["expression_stage_ontology_validation"].validator_agent.agent_id == (
        "ontology_term_validation"
    )
    assert active_bindings[
        "expression_stage_ontology_validation"
    ].expected_result_fields == {
        "label": "when_expressed_stage_name",
        "curie": "expression_pattern.when_expressed.developmental_stage_start.curie",
        "name": "expression_pattern.when_expressed.developmental_stage_start.name",
    }
    assert active_bindings["expression_stage_uberon_slim_validation"].field_paths == (
        "expression_pattern.when_expressed.stage_uberon_slim_terms",
    )
    assert active_bindings["expression_anatomical_structure_validation"].field_paths == (
        "expression_pattern.where_expressed.anatomical_structure",
    )
    assert active_bindings[
        "expression_anatomical_uberon_slim_validation"
    ].field_paths == (
        "expression_pattern.where_expressed.anatomical_structure_uberon_terms",
    )
    assert active_bindings["expression_cellular_component_validation"].field_paths == (
        "expression_pattern.where_expressed.cellular_component",
    )
    assert active_bindings[
        "expression_cellular_component_qualifier_validation"
    ].field_paths == (
        "expression_pattern.where_expressed.cellular_component_qualifiers",
    )

    active_validator_ids = {
        entry.validator_id
        for entry in registry.validator_metadata
        if entry.state is ValidationBindingState.ACTIVE
    }
    assert {
        "data_provider_validation",
        "expression_anatomical_structure_validation",
        "expression_anatomical_uberon_slim_validation",
        "expression_cellular_component_qualifier_validation",
        "expression_cellular_component_validation",
        "expression_stage_ontology_validation",
        "expression_stage_uberon_slim_validation",
        "relation_vocabulary_validation",
        "source_reference_validation",
        "subject_gene_validation",
    } <= active_validator_ids

    under_development_binding_ids = {
        binding.binding_id
        for binding in registry.bindings
        if binding.state is ValidationBindingState.UNDER_DEVELOPMENT
    }
    assert {
        "expression_context_ontology_validation",
        "reagent_context_materialization",
    } <= under_development_binding_ids

    under_development_validator_ids = {
        entry.validator_id
        for entry in registry.validator_metadata
        if entry.state is ValidationBindingState.UNDER_DEVELOPMENT
    }
    assert {
        "gene_expression.ontology_term_resolution",
        "gene_expression.reagent_context_materialization",
    } <= under_development_validator_ids

    planned_gap_fields = {
        "expression_experiment.expression_assay_used.curie",
        "expression_experiment.expression_assay_used.name",
    }
    promoted_materialization_fields = {
        "when_expressed_stage_name",
        "expression_pattern.when_expressed.developmental_stage_start",
        "expression_pattern.when_expressed.developmental_stage_start.curie",
        "expression_pattern.when_expressed.developmental_stage_start.name",
        "expression_pattern.when_expressed.stage_uberon_slim_terms",
        "expression_pattern.where_expressed.anatomical_structure",
        "expression_pattern.where_expressed.anatomical_structure.curie",
        "expression_pattern.where_expressed.anatomical_structure.name",
        "expression_pattern.where_expressed.anatomical_structure_uberon_terms",
        "expression_pattern.where_expressed.cellular_component",
        "expression_pattern.where_expressed.cellular_component.curie",
        "expression_pattern.where_expressed.cellular_component.name",
        "expression_pattern.where_expressed.cellular_component_qualifiers",
    }
    promoted_normalization_fields = {
        "expression_annotation_subject.primary_external_id",
        "expression_annotation_subject.gene_symbol",
        "single_reference.reference_id",
        "single_reference.curie",
        "single_reference.title",
        "when_expressed_stage_name",
        "expression_pattern.when_expressed.stage_uberon_slim_terms",
        "expression_pattern.where_expressed.anatomical_structure",
        "expression_pattern.where_expressed.anatomical_structure_uberon_terms",
        "expression_pattern.where_expressed.cellular_component",
        "expression_pattern.where_expressed.cellular_component_qualifiers",
    }
    active_field_paths = {
        field_path
        for binding in active_bindings.values()
        for field_path in binding.field_paths
    }
    assert planned_gap_fields.isdisjoint(active_field_paths)
    assert promoted_normalization_fields <= active_field_paths

    under_development_field_paths = {
        field_path
        for binding in registry.bindings
        if binding.state is ValidationBindingState.UNDER_DEVELOPMENT
        for field_path in binding.field_paths
    }
    assert planned_gap_fields <= under_development_field_paths
    assert promoted_normalization_fields.isdisjoint(under_development_field_paths)

    fields_by_path = {field.field_path: field for field in curatable_unit.fields}
    silently_active_gap_fields = [
        field_path
        for field_path in sorted(planned_gap_fields)
        if fields_by_path[field_path].metadata.get("validator_state") == "active"
    ]
    assert silently_active_gap_fields == []
    for field_path in promoted_materialization_fields:
        assert fields_by_path[field_path].metadata["validator_state"] == "active"


def test_gene_expression_context_ontology_requests_are_field_scoped():
    envelope = _converted_tmem67_envelope()

    stage_match = _active_binding_match(
        envelope,
        "expression_stage_ontology_validation",
    )
    stage_request = build_domain_validation_request(stage_match).request
    assert stage_request is not None
    assert stage_request.target.field_path == "when_expressed_stage_name"
    assert stage_request.selected_inputs == {
        "label": "TS26",
        "data_provider": "MGI",
        "ontology_family": "life_stage",
        "lookup_method": "search_life_stage_terms",
    }
    assert stage_request.expected_result_fields == {
        "label": "when_expressed_stage_name",
        "curie": "expression_pattern.when_expressed.developmental_stage_start.curie",
        "name": "expression_pattern.when_expressed.developmental_stage_start.name",
    }

    anatomy_match = _active_binding_match(
        envelope,
        "expression_anatomical_structure_validation",
    )
    anatomy_request = build_domain_validation_request(anatomy_match).request
    assert anatomy_request is not None
    assert anatomy_request.target.field_path == (
        "expression_pattern.where_expressed.anatomical_structure"
    )
    assert anatomy_request.selected_inputs == {
        "curie": "EMAPA:17373",
        "label": "metanephros",
        "data_provider": "MGI",
        "ontology_family": "anatomy",
        "lookup_method": "search_anatomy_terms",
    }

    uberon_match = _active_binding_match(
        envelope,
        "expression_anatomical_uberon_slim_validation",
    )
    uberon_request = build_domain_validation_request(uberon_match).request
    assert uberon_request is not None
    assert uberon_request.selected_inputs["terms"] == [
        {"curie": "UBERON:0001008", "name": "renal system"}
    ]
    assert uberon_request.selected_inputs["ontology_term_type"] == "UBERONTerm"


def test_gene_expression_cellular_component_only_site_remains_validatable():
    envelope = _converted_tmem67_envelope()
    payload = copy.deepcopy(envelope.objects[0].payload)
    payload["expression_pattern"]["where_expressed"] = {
        "cellular_component": {"name": "nucleus"}
    }
    envelope = _with_payload(envelope, payload)

    findings = validate_pending_gene_expression_envelope(envelope)
    assert [
        finding
        for finding in findings
        if finding.code == "alliance.gene_expression.anatomical_site_missing"
    ] == []

    match = _active_binding_match(envelope, "expression_cellular_component_validation")
    request = build_domain_validation_request(match).request
    assert request is not None
    assert request.selected_inputs == {
        "label": "nucleus",
        "ontology_family": "go",
        "go_aspect": "cellular_component",
        "lookup_method": "search_go_terms",
    }

    result = materialize_validator_results_into_envelope(
        envelope,
        _gene_expression_pack().metadata,
        [
            ValidatorResultMaterializationInput(
                match=match,
                request=request,
                result=_validator_result(
                    request,
                    status="resolved",
                    resolved_values={
                        "curie": "GO:0005634",
                        "name": "nucleus",
                    },
                ),
            )
        ],
    )

    where_expressed = result.envelope.objects[0].payload["expression_pattern"][
        "where_expressed"
    ]
    assert where_expressed == {
        "cellular_component": {
            "curie": "GO:0005634",
            "name": "nucleus",
        }
    }
    patch_event = result.envelope.objects[0].metadata[
        "validator_resolved_value_materialization"
    ][0]
    assert patch_event["original_values"] == {
        "expression_pattern.where_expressed.cellular_component.name": "nucleus"
    }
    resolved_field_ref = result.appended_findings[0].field_ref
    assert resolved_field_ref is not None
    assert resolved_field_ref.field_path == (
        "expression_pattern.where_expressed.cellular_component"
    )


def test_gene_expression_stage_and_site_terms_materialize_from_validator_results():
    envelope = _converted_tmem67_envelope()
    stage_match = _active_binding_match(
        envelope,
        "expression_stage_ontology_validation",
    )
    anatomy_match = _active_binding_match(
        envelope,
        "expression_anatomical_structure_validation",
    )
    stage_request = build_domain_validation_request(stage_match).request
    anatomy_request = build_domain_validation_request(anatomy_match).request
    assert stage_request is not None
    assert anatomy_request is not None

    result = materialize_validator_results_into_envelope(
        envelope,
        _gene_expression_pack().metadata,
        [
            ValidatorResultMaterializationInput(
                match=stage_match,
                request=stage_request,
                result=_validator_result(
                    stage_request,
                    status="resolved",
                    resolved_values={
                        "label": "Theiler stage 26",
                        "curie": "FIXTURE_STAGE:00026",
                        "name": "Theiler stage 26",
                    },
                ),
            ),
            ValidatorResultMaterializationInput(
                match=anatomy_match,
                request=anatomy_request,
                result=_validator_result(
                    anatomy_request,
                    status="resolved",
                    resolved_values={
                        "curie": "EMAPA:17373",
                        "name": "metanephros",
                    },
                ),
            ),
        ],
    )

    payload = result.envelope.objects[0].payload
    assert payload["when_expressed_stage_name"] == "Theiler stage 26"
    assert payload["expression_pattern"]["when_expressed"][
        "developmental_stage_start"
    ] == {
        "curie": "FIXTURE_STAGE:00026",
        "name": "Theiler stage 26",
    }
    assert payload["expression_pattern"]["where_expressed"][
        "anatomical_structure"
    ] == {
        "curie": "EMAPA:17373",
        "name": "metanephros",
    }
    stage_patch = result.envelope.objects[0].metadata[
        "validator_resolved_value_materialization"
    ][0]
    assert stage_patch["original_values"] == {
        "when_expressed_stage_name": "TS26"
    }
    assert [finding.status.value for finding in result.appended_findings] == [
        "resolved",
        "resolved",
    ]


def test_gene_expression_slim_and_qualifier_arrays_materialize_from_validator_results():
    envelope = _converted_tmem67_envelope()
    payload = copy.deepcopy(envelope.objects[0].payload)
    payload["expression_pattern"]["when_expressed"] = {
        "stage_uberon_slim_terms": [{"name": "embryonic stage"}]
    }
    payload["expression_pattern"]["where_expressed"][
        "cellular_component_qualifiers"
    ] = [{"name": "nuclear lumen"}]
    envelope = _with_payload(envelope, payload)

    stage_uberon_match = _active_binding_match(
        envelope,
        "expression_stage_uberon_slim_validation",
    )
    anatomy_uberon_match = _active_binding_match(
        envelope,
        "expression_anatomical_uberon_slim_validation",
    )
    qualifier_match = _active_binding_match(
        envelope,
        "expression_cellular_component_qualifier_validation",
    )
    stage_request = build_domain_validation_request(stage_uberon_match).request
    anatomy_request = build_domain_validation_request(anatomy_uberon_match).request
    qualifier_request = build_domain_validation_request(qualifier_match).request
    assert stage_request is not None
    assert anatomy_request is not None
    assert qualifier_request is not None

    result = materialize_validator_results_into_envelope(
        envelope,
        _gene_expression_pack().metadata,
        [
            ValidatorResultMaterializationInput(
                match=stage_uberon_match,
                request=stage_request,
                result=_validator_result(
                    stage_request,
                    status="resolved",
                    resolved_values={
                        "terms": [
                            {
                                "curie": "UBERON:0000068",
                                "name": "embryonic stage",
                            }
                        ]
                    },
                ),
            ),
            ValidatorResultMaterializationInput(
                match=anatomy_uberon_match,
                request=anatomy_request,
                result=_validator_result(
                    anatomy_request,
                    status="resolved",
                    resolved_values={
                        "terms": [
                            {
                                "curie": "UBERON:0001008",
                                "name": "renal system",
                            }
                        ]
                    },
                ),
            ),
            ValidatorResultMaterializationInput(
                match=qualifier_match,
                request=qualifier_request,
                result=_validator_result(
                    qualifier_request,
                    status="resolved",
                    resolved_values={
                        "terms": [
                            {
                                "curie": "GO:0031981",
                                "name": "nuclear lumen",
                            }
                        ]
                    },
                ),
            ),
        ],
    )

    payload = result.envelope.objects[0].payload
    assert payload["expression_pattern"]["when_expressed"][
        "stage_uberon_slim_terms"
    ] == [{"curie": "UBERON:0000068", "name": "embryonic stage"}]
    assert payload["expression_pattern"]["where_expressed"][
        "anatomical_structure_uberon_terms"
    ] == [{"curie": "UBERON:0001008", "name": "renal system"}]
    assert payload["expression_pattern"]["where_expressed"][
        "cellular_component_qualifiers"
    ] == [{"curie": "GO:0031981", "name": "nuclear lumen"}]


@pytest.mark.parametrize(
    ("lookup_outcome", "expected_status"),
    [
        ("ambiguous", "ambiguous"),
        ("not_found", "not_found"),
        ("conflict", "blocked"),
    ],
)
def test_gene_expression_context_ontology_unresolved_outcomes_stay_field_addressed(
    lookup_outcome: str,
    expected_status: str,
):
    envelope = _converted_tmem67_envelope()
    match = _active_binding_match(
        envelope,
        "expression_anatomical_structure_validation",
    )
    request = build_domain_validation_request(match).request
    assert request is not None

    result = materialize_validator_results_into_envelope(
        envelope,
        _gene_expression_pack().metadata,
        [
            ValidatorResultMaterializationInput(
                match=match,
                request=request,
                result=_validator_result(
                    request,
                    status="unresolved",
                    missing_expected_fields=[],
                    lookup_outcome=lookup_outcome,
                    candidates=[
                        {
                            "value": "EMAPA:17373",
                            "label": "metanephros",
                            "object_type": "OntologyTerm",
                        }
                    ],
                    curator_message=(
                        "Paper-facing anatomy label requires curator review."
                    ),
                ),
            )
        ],
    )

    assert result.materialized_objects == ()
    assert result.envelope.objects[0].payload == envelope.objects[0].payload
    finding = result.appended_findings[0]
    assert finding.code == "domain_pack.validator_unresolved"
    assert finding.field_ref is not None
    assert finding.field_ref.field_path == (
        "expression_pattern.where_expressed.anatomical_structure"
    )
    assert finding.details["validation_result"]["status"] == "unresolved"
    assert finding.details["validation_result"]["lookup_attempts"][0]["outcome"] == (
        lookup_outcome
    )
    assert finding.details["lookup_attempts"][0]["lookup_status"] == expected_status


def test_tmem67_fixture_validates_as_pending_gene_expression_annotation():
    fixture_ref = load_alliance_domain_pack_registry().get_fixture_pack_ref(
        GENE_EXPRESSION_DOMAIN_PACK_ID,
        GENE_EXPRESSION_FIXTURE_PACK_ID,
    )
    assert fixture_ref is not None
    fixture_path = get_gene_expression_domain_pack_metadata_path().parent / fixture_ref.path
    fixture_pack = load_domain_fixture_pack(fixture_path)

    assert fixture_pack.fixture_pack_id == GENE_EXPRESSION_FIXTURE_PACK_ID
    assert fixture_pack.domain_pack_id == GENE_EXPRESSION_DOMAIN_PACK_ID
    fixture = fixture_pack.fixtures[0]
    envelope = fixture.envelope

    assert envelope.domain_pack_id == GENE_EXPRESSION_DOMAIN_PACK_ID
    assert len(envelope.objects) == 1

    annotation = envelope.objects[0]
    assert annotation.object_type == GENE_EXPRESSION_OBJECT_TYPE
    assert annotation.status is CuratableObjectStatus.PENDING
    assert annotation.object_role == "curatable_unit"
    assert annotation.model_ref == GENE_EXPRESSION_MODEL_ID
    assert annotation.object_refs == []
    assert annotation.field_refs == []
    assert annotation.evidence_record_ids == ["evidence-tmem67-metanephros-1"]
    assert annotation.metadata_refs[0].metadata_path == (
        "extraction_metadata.raw_mentions[0]"
    )
    assert annotation.metadata_refs[1].metadata_path == (
        "extraction_metadata.evidence_records[0]"
    )
    _assert_metadata_refs_resolve(envelope)
    assert annotation.payload["expression_annotation_subject"] == {
        "primary_external_id": "MGI:1923928",
        "gene_symbol": "Tmem67",
    }
    assert envelope.metadata["semantic_source"] == "domain_envelope.objects"
    assert envelope.metadata["legacy_semantic_lists"] == []
    assert envelope.metadata["extraction_metadata"]["raw_mentions"]
    assert envelope.metadata["extraction_metadata"]["evidence_records"][0][
        "evidence_record_id"
    ] == "evidence-tmem67-metanephros-1"
    assert envelope.metadata["extraction_metadata"]["exclusions"][0]["reason_code"] == (
        "rescue_experiment_not_expression"
    )

    curatable_unit = _gene_expression_pack().metadata.object_definitions[0]
    missing_required_fields = [
        field.field_path
        for field in curatable_unit.fields
        if field.required and not field_path_exists(annotation.payload, field.field_path)
    ]
    assert missing_required_fields == []

    observed_keys = set(_iter_mapping_keys(envelope.model_dump(mode="python")))
    assert FORBIDDEN_LEGACY_COLLECTIONS.isdisjoint(observed_keys)
    assert validate_pending_gene_expression_envelope(envelope) == ()


def test_tmem67_fixture_carries_anatomical_site_for_linkml_postcondition():
    fixture_ref = load_alliance_domain_pack_registry().get_fixture_pack_ref(
        GENE_EXPRESSION_DOMAIN_PACK_ID,
        GENE_EXPRESSION_FIXTURE_PACK_ID,
    )
    assert fixture_ref is not None
    fixture_path = get_gene_expression_domain_pack_metadata_path().parent / fixture_ref.path
    fixture_pack = load_domain_fixture_pack(fixture_path)
    annotation = fixture_pack.fixtures[0].envelope.objects[0]
    where_expressed = annotation.payload["expression_pattern"]["where_expressed"]

    assert (
        "anatomical_structure" in where_expressed
        or "cellular_component" in where_expressed
    )
    assert where_expressed["anatomical_structure_uberon_terms"] == [
        {"curie": "UBERON:0001008", "name": "renal system"}
    ]


def test_tmem67_extractor_output_converts_to_pending_gene_expression_envelope():
    converted = _converted_tmem67_envelope()

    assert converted.envelope_id == "gene-expression-tmem67-mgi-206552169"
    assert converted.domain_pack_id == GENE_EXPRESSION_DOMAIN_PACK_ID
    assert len(converted.objects) == 1
    annotation = converted.objects[0]
    assert annotation.object_type == GENE_EXPRESSION_OBJECT_TYPE
    assert annotation.status is CuratableObjectStatus.PENDING
    assert annotation.evidence_record_ids == ["evidence-tmem67-metanephros-1"]
    assert annotation.metadata_refs[0].metadata_path == (
        "extraction_metadata.raw_mentions[0]"
    )
    assert annotation.metadata_refs[1].metadata_path == (
        "extraction_metadata.evidence_records[0]"
    )
    assert converted.metadata["source_document_id"] == "document-tmem67-expression-fixture"
    assert converted.metadata["extraction_metadata"]["evidence_records"][0][
        "verified_quote"
    ].startswith("Tmem67 expression was detected")
    _assert_metadata_refs_resolve(converted)
    assert validate_pending_gene_expression_envelope(converted) == ()


def test_gene_expression_linkml_validator_reports_missing_gene_selector_field():
    envelope = _converted_tmem67_envelope()
    payload = copy.deepcopy(envelope.objects[0].payload)
    payload["expression_annotation_subject"]["primary_external_id"] = " "

    findings = validate_pending_gene_expression_envelope(_with_payload(envelope, payload))

    finding = _finding_by_code(
        findings,
        "alliance.gene_expression.subject_gene_missing",
    )
    assert finding.severity is ValidationFindingSeverity.BLOCKER
    assert finding.field_ref.field_path == (
        "expression_annotation_subject.primary_external_id"
    )
    assert finding.details["blocking"] is True
    assert finding.details["classification"] == "repairable_extraction_error"


def test_gene_expression_linkml_validator_reports_missing_reference_field():
    envelope = _converted_tmem67_envelope()
    payload = copy.deepcopy(envelope.objects[0].payload)
    payload["single_reference"]["reference_id"] = None

    findings = validate_pending_gene_expression_envelope(_with_payload(envelope, payload))

    finding = _finding_by_code(findings, "alliance.gene_expression.reference_missing")
    assert finding.field_ref.field_path == "single_reference.reference_id"
    assert finding.details["expected_selector"] == (
        "PMID or Alliance reference identifier"
    )


def test_gene_expression_linkml_validator_reports_missing_and_unknown_evidence():
    envelope = _converted_tmem67_envelope()
    annotation = envelope.objects[0].model_copy(update={"evidence_record_ids": []})
    missing_envelope = envelope.model_copy(update={"objects": [annotation]})

    missing_finding = _finding_by_code(
        validate_pending_gene_expression_envelope(missing_envelope),
        "alliance.gene_expression.evidence_record_ids_missing",
    )
    assert missing_finding.field_ref.field_path == "evidence_record_ids"
    assert (
        missing_finding.details["classification"]
        == "non_repairable_extraction_error"
    )

    unknown_annotation = envelope.objects[0].model_copy(
        update={"evidence_record_ids": ["evidence-not-in-metadata"]}
    )
    unknown_envelope = envelope.model_copy(update={"objects": [unknown_annotation]})
    unknown_finding = _finding_by_code(
        validate_pending_gene_expression_envelope(unknown_envelope),
        "alliance.gene_expression.evidence_records_missing",
    )
    assert unknown_finding.field_ref.field_path == "evidence_record_ids"
    assert unknown_finding.details["missing_evidence_record_ids"] == [
        "evidence-not-in-metadata"
    ]


def test_gene_expression_linkml_validator_reports_relation_missing_and_invalid():
    envelope = _converted_tmem67_envelope()
    missing_payload = copy.deepcopy(envelope.objects[0].payload)
    missing_payload["relation"]["name"] = " "

    missing_finding = _finding_by_code(
        validate_pending_gene_expression_envelope(
            _with_payload(envelope, missing_payload)
        ),
        "alliance.gene_expression.relation_name_missing",
    )
    assert missing_finding.field_ref.field_path == "relation.name"
    assert missing_finding.details["expected_vocabulary"] == "Expression Relation"
    assert missing_finding.details["expected_values"] == ["is_expressed_in"]

    invalid_payload = copy.deepcopy(envelope.objects[0].payload)
    invalid_payload["relation"]["name"] = "expressed_in"

    invalid_finding = _finding_by_code(
        validate_pending_gene_expression_envelope(
            _with_payload(envelope, invalid_payload)
        ),
        "alliance.gene_expression.relation_name_invalid",
    )
    assert invalid_finding.field_ref.field_path == "relation.name"
    assert invalid_finding.details["submitted_value"] == "expressed_in"
    assert invalid_finding.details["expected_vocabulary"] == "Expression Relation"


def test_gene_expression_linkml_validator_reports_invalid_and_ambiguous_assay():
    envelope = _converted_tmem67_envelope()
    invalid_payload = copy.deepcopy(envelope.objects[0].payload)
    invalid_payload["expression_experiment"]["expression_assay_used"][
        "curie"
    ] = "not-a-curie"

    invalid_finding = _finding_by_code(
        validate_pending_gene_expression_envelope(
            _with_payload(envelope, invalid_payload)
        ),
        "alliance.gene_expression.assay_method_invalid",
    )
    assert invalid_finding.field_ref.field_path == (
        "expression_experiment.expression_assay_used.curie"
    )

    ambiguous_payload = copy.deepcopy(envelope.objects[0].payload)
    ambiguous_payload["expression_experiment"]["expression_assay_used"] = {
        "candidates": [
            {"curie": "MMO:0000655", "name": "RT-PCR"},
            {"curie": "MMO:0000642", "name": "in situ hybridization"},
        ]
    }

    ambiguous_finding = _finding_by_code(
        validate_pending_gene_expression_envelope(
            _with_payload(envelope, ambiguous_payload)
        ),
        "alliance.gene_expression.assay_method_ambiguous",
    )
    assert ambiguous_finding.field_ref.field_path == (
        "expression_experiment.expression_assay_used"
    )
    assert ambiguous_finding.details["candidate_count"] == 2


def test_gene_expression_linkml_validator_reports_missing_expression_context():
    envelope = _converted_tmem67_envelope()
    payload = copy.deepcopy(envelope.objects[0].payload)
    payload["when_expressed_stage_name"] = ""
    payload["expression_pattern"]["where_expressed"] = {}

    findings = validate_pending_gene_expression_envelope(_with_payload(envelope, payload))
    findings_by_field = {
        finding.field_ref.field_path: finding
        for finding in findings
        if finding.field_ref is not None
    }

    assert (
        findings_by_field["when_expressed_stage_name"].code
        == "alliance.gene_expression.expression_context_missing"
    )
    assert (
        findings_by_field["expression_pattern.where_expressed"].code
        == "alliance.gene_expression.anatomical_site_missing"
    )


def test_gene_expression_linkml_validator_accepts_negated_and_mixed_site_context():
    envelope = _converted_tmem67_envelope()
    payload = copy.deepcopy(envelope.objects[0].payload)
    payload["negated"] = True
    payload["where_expressed_statement"] = "metanephros nucleus"
    payload["expression_pattern"]["where_expressed"]["cellular_component"] = {
        "curie": "GO:0005634",
        "name": "nucleus",
    }

    assert validate_pending_gene_expression_envelope(
        _with_payload(envelope, payload)
    ) == ()


def test_gene_expression_conversion_rejects_missing_relation_name():
    raw_fixture = yaml.safe_load(
        GENE_EXPRESSION_OUTPUT_FIXTURE_PATH.read_text(encoding="utf-8")
    )
    output = raw_fixture["output"]
    output["curatable_objects"][0]["payload"]["relation"]["name"] = None

    with pytest.raises(ValueError) as exc_info:
        gene_expression_extraction_output_to_pending_envelope(
            output,
            envelope_id="gene-expression-missing-relation",
        )

    assert "relation.name must be selected explicitly" in str(exc_info.value)


def test_gene_expression_conversion_accepts_cellular_component_only_site():
    raw_fixture = yaml.safe_load(
        GENE_EXPRESSION_OUTPUT_FIXTURE_PATH.read_text(encoding="utf-8")
    )
    output = raw_fixture["output"]
    payload = output["curatable_objects"][0]["payload"]
    payload["where_expressed_statement"] = "nucleus"
    payload["expression_pattern"]["where_expressed"] = {
        "cellular_component": {
            "name": "nucleus",
        }
    }

    converted = gene_expression_extraction_output_to_pending_envelope(
        output,
        envelope_id="gene-expression-cellular-component-only",
    )

    where_expressed = converted.objects[0].payload["expression_pattern"][
        "where_expressed"
    ]
    assert where_expressed == {"cellular_component": {"name": "nucleus"}}
    assert validate_pending_gene_expression_envelope(converted) == ()


def test_gene_expression_conversion_rejects_missing_anatomical_site_slots():
    raw_fixture = yaml.safe_load(
        GENE_EXPRESSION_OUTPUT_FIXTURE_PATH.read_text(encoding="utf-8")
    )
    output = raw_fixture["output"]
    output["curatable_objects"][0]["payload"]["expression_pattern"][
        "where_expressed"
    ] = {}

    with pytest.raises(ValueError) as exc_info:
        gene_expression_extraction_output_to_pending_envelope(
            output,
            envelope_id="gene-expression-missing-site",
        )

    assert "anatomical_structure or cellular_component" in str(exc_info.value)


def test_gene_expression_conversion_rejects_blank_anatomical_site_slots():
    raw_fixture = yaml.safe_load(
        GENE_EXPRESSION_OUTPUT_FIXTURE_PATH.read_text(encoding="utf-8")
    )
    output = raw_fixture["output"]
    output["curatable_objects"][0]["payload"]["expression_pattern"][
        "where_expressed"
    ] = {
        "anatomical_structure": {},
        "cellular_component": None,
    }

    with pytest.raises(ValueError) as exc_info:
        gene_expression_extraction_output_to_pending_envelope(
            output,
            envelope_id="gene-expression-blank-site",
        )

    assert "anatomical_structure or cellular_component" in str(exc_info.value)


def test_gene_expression_conversion_rejects_blank_nested_site_term():
    raw_fixture = yaml.safe_load(
        GENE_EXPRESSION_OUTPUT_FIXTURE_PATH.read_text(encoding="utf-8")
    )
    output = raw_fixture["output"]
    output["curatable_objects"][0]["payload"]["where_expressed_statement"] = "nucleus"
    output["curatable_objects"][0]["payload"]["expression_pattern"][
        "where_expressed"
    ] = {
        "cellular_component": {
            "name": "   ",
        },
    }

    with pytest.raises(ValueError) as exc_info:
        gene_expression_extraction_output_to_pending_envelope(
            output,
            envelope_id="gene-expression-blank-nested-site",
        )

    assert "anatomical_structure or cellular_component" in str(exc_info.value)


def test_gene_expression_conversion_rejects_legacy_semantic_lists():
    raw_fixture = yaml.safe_load(
        GENE_EXPRESSION_OUTPUT_FIXTURE_PATH.read_text(encoding="utf-8")
    )
    output = raw_fixture["output"]
    output["items"] = []

    with pytest.raises(ValueError) as exc_info:
        gene_expression_extraction_output_to_pending_envelope(
            output,
            envelope_id="gene-expression-invalid",
        )

    assert "curatable_objects[]" in str(exc_info.value)


def test_gene_expression_linkml_class_slot_and_range_refs_exist(tmp_path: Path):
    schema_cache_dir, _env_values = _cache_schema(tmp_path)
    index = _load_linkml_index(schema_cache_dir)
    metadata = _gene_expression_pack().metadata

    provider_refs = tuple(_iter_linkml_provider_refs(metadata))
    assert provider_refs

    for provider_ref in provider_refs:
        class_name = provider_ref.get("class")
        if class_name is not None:
            assert class_name in index["classes"], (
                f"LinkML class {class_name} is missing from pinned schema"
            )
            actual_file, _definition = index["classes"][class_name]
            if "slot" not in provider_ref:
                _assert_source_file_matches(
                    provider_ref=provider_ref,
                    actual_file=actual_file,
                    ref_kind="class",
                    ref_name=class_name,
                )

        slot_name = provider_ref.get("slot")
        if slot_name is not None:
            assert slot_name in index["slots"], (
                f"LinkML slot {slot_name} is missing from pinned schema"
            )
            actual_file, _definition = index["slots"][slot_name]
            _assert_source_file_matches(
                provider_ref=provider_ref,
                actual_file=actual_file,
                ref_kind="slot",
                ref_name=slot_name,
            )

        _assert_range_exists(index, provider_ref)

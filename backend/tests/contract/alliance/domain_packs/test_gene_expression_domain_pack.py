"""Contract tests for the Alliance gene-expression domain pack."""

from __future__ import annotations

import copy
import hashlib
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml

from src.lib.domain_packs.input_selectors import build_domain_validation_request
from src.lib.domain_packs.loader import load_domain_fixture_pack
from src.lib.domain_packs.materialization import (
    DomainPackMetadataReviewRowMaterializer,
    ValidatorResultMaterializationInput,
    materialize_validator_results_into_envelope,
    project_evidence_anchor_projections,
    project_validation_summary_projections,
)
from src.lib.domain_packs.validation_registry import (
    DomainPackValidationRegistry,
    ValidationBindingState,
)
from src.schemas.domain_validator import DomainValidatorResultBase
from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    CuratableObjectStatus,
    DomainEnvelope,
    ValidationFindingSeverity,
    field_path_exists,
)
from src.schemas.domain_envelope import DefinitionState
from src.schemas.domain_pack_metadata import DomainPackFieldType
from src.lib.openai_agents.extraction_builder_workspace import (
    CANDIDATE_STATUS_VALID,
    ExtractionBuilderWorkspace,
)


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
    GENE_EXPRESSION_CURATOR_GUIDANCE_FIXTURE_PACK_ID,
    GENE_EXPRESSION_DOMAIN_PACK_ID,
    GENE_EXPRESSION_FIXTURE_PACK_ID,
    GENE_EXPRESSION_MODEL_ID,
    GENE_EXPRESSION_MULTI_FIXTURE_PACK_ID,
    GENE_EXPRESSION_OBJECT_TYPE,
    GENE_EXPRESSION_VALIDATOR_STATES,
    VALID_GENE_EXPRESSION_RELATION_NAMES,
    gene_expression_extraction_output_to_pending_envelope,
    get_gene_expression_domain_pack_metadata_path,
    materialize_gene_expression_builder_state,
    validate_pending_gene_expression_envelope,
)
from agr_ai_curation_alliance.domain_packs._resolvable_payloads import (  # noqa: E402
    condition_relations_payload,
)
from agr_ai_curation_alliance.domain_packs.gene_expression.export import (  # noqa: E402
    gene_expression_export_blockers,
)
from agr_ai_curation_alliance.domain_packs.gene_expression.resolvable import (  # noqa: E402
    GENE_EXPRESSION_RESOLVABLE_VALUES,
    staged_value,
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
ANATOMICAL_UBERON_SLIM_ALLOWED_CURIES = [
    "UBERON:0001009",
    "UBERON:0005409",
    "UBERON:0000949",
    "UBERON:0001008",
    "UBERON:0002330",
    "UBERON:0002193",
    "UBERON:0002416",
    "UBERON:0002423",
    "UBERON:0002204",
    "UBERON:0001016",
    "UBERON:0000990",
    "UBERON:0001004",
    "UBERON:0001032",
    "UBERON:0005726",
    "UBERON:0007037",
    "UBERON:0002105",
    "UBERON:0002104",
    "UBERON:0000924",
    "UBERON:0000925",
    "UBERON:0000926",
    "UBERON:0003104",
    "UBERON:0001013",
    "UBERON:0000026",
    "UBERON:0016887",
    "UBERON:6005023",
    "UBERON:0002539",
]
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
    extraction_metadata = envelope.metadata.get("extraction_metadata")
    metadata_root = (
        extraction_metadata
        if isinstance(extraction_metadata, Mapping)
        else envelope.metadata
    )
    unresolved = [
        metadata_ref.metadata_path
        for annotation in envelope.extracted_objects
        for metadata_ref in annotation.metadata_refs
        if not field_path_exists(metadata_root, metadata_ref.metadata_path)
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


def _load_gene_expression_fixture_pack(fixture_pack_id: str):
    fixture_ref = load_alliance_domain_pack_registry().get_fixture_pack_ref(
        GENE_EXPRESSION_DOMAIN_PACK_ID,
        fixture_pack_id,
    )
    assert fixture_ref is not None
    fixture_path = get_gene_expression_domain_pack_metadata_path().parent / fixture_ref.path
    return load_domain_fixture_pack(fixture_path)


def _converted_tmem67_envelope_with_raw_assay(assay: Mapping[str, Any]):
    raw_fixture = yaml.safe_load(
        GENE_EXPRESSION_OUTPUT_FIXTURE_PATH.read_text(encoding="utf-8")
    )
    payload = raw_fixture["output"]["curatable_objects"][0]["payload"]
    payload["expression_experiment"]["expression_assay_used"] = dict(assay)
    context = raw_fixture["envelope_context"]
    return gene_expression_extraction_output_to_pending_envelope(
        raw_fixture["output"],
        envelope_id=context["envelope_id"],
        document_id=context["document_id"],
        produced_by=context["produced_by"],
        produced_at=context["produced_at"],
    )


def _with_payload(envelope: Any, payload: Mapping[str, Any]):
    annotation = envelope.extracted_objects[0].model_copy(update={"payload": dict(payload)})
    return envelope.model_copy(update={"extracted_objects": [annotation]})


_VALIDATOR_EXPLANATION = "Fixture-backed validator result for gene-expression contract tests."


def _validated(mention: str, **identity: Any) -> dict[str, Any]:
    """A value a fixture validator resolved: its identity, state and the validator's words."""

    return {
        **identity,
        "mention": mention,
        "resolution_state": "resolved",
        "lookup_outcome": "matched",
        "validator_explanation": _VALIDATOR_EXPLANATION,
        "validator_curator_message": "Fixture resolved ontology result.",
    }


def _grounded(mention: str, **identity: Any) -> dict[str, Any]:
    """A fixture value grounded to a curation DB row: resolved, with no validator text."""

    return {
        **identity,
        "mention": mention,
        "resolution_state": "resolved",
        "lookup_outcome": "matched",
        "validator_explanation": None,
    }


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
    assert curatable_unit.metadata["workspace_display"] == {
        "primary_label_field": "expression_annotation_subject.gene_symbol",
        "secondary_label_field": "where_expressed_statement",
        "summary_fields": [
            "expression_annotation_subject.gene_symbol",
            "where_expressed_statement",
            "when_expressed_stage_name",
            "relation.name",
            "expression_experiment.expression_assay_used.name",
        ],
        "groups": [
            {
                "id": "subject",
                "label": "Subject gene",
                "fields": [
                    "expression_annotation_subject.primary_external_id",
                    "expression_annotation_subject.gene_symbol",
                    "expression_annotation_subject.mention",
                ],
            },
            {
                "id": "reference",
                "label": "Reference",
                "fields": [
                    "single_reference.reference_id",
                    "single_reference.mention",
                ],
            },
            {
                "id": "assay",
                "label": "Assay",
                "fields": [
                    "expression_experiment.expression_assay_used.curie",
                    "expression_experiment.expression_assay_used.mention",
                ],
            },
            {
                "id": "expression_site",
                "label": "Expression site",
                "fields": [
                    "where_expressed_statement",
                    "expression_pattern.where_expressed.anatomical_structure.curie",
                    "expression_pattern.where_expressed.anatomical_structure.mention",
                    "expression_pattern.where_expressed.cellular_component.curie",
                    "expression_pattern.where_expressed.cellular_component.mention",
                    "expression_pattern.where_expressed.anatomical_structure_uberon_terms",
                    "expression_pattern.where_expressed.cellular_component_qualifiers",
                    "condition_relations",
                ],
            },
            {
                "id": "stage_relation",
                "label": "Stage & relation",
                "fields": [
                    "when_expressed_stage_name",
                    "expression_pattern.when_expressed.developmental_stage_start.curie",
                    "expression_pattern.when_expressed.developmental_stage_start.mention",
                    "expression_pattern.when_expressed.stage_uberon_slim_terms",
                    "relation.name",
                    "relation.mention",
                    "data_provider.abbreviation",
                    "data_provider.mention",
                ],
            },
            {
                "id": "rationale",
                "label": "Rationale",
                "fields": ["rationale"],
            },
        ],
    }
    fields_by_path = {field.field_path: field for field in curatable_unit.fields}
    assert fields_by_path["rationale"].field_type is DomainPackFieldType.STRING
    assert fields_by_path["rationale"].display_name == "Rationale"
    # Optional on read: annotations stored before the builder required a rationale still load.
    assert fields_by_path["rationale"].required is False
    assert fields_by_path["relation.vocabulary"].field_type is (
        DomainPackFieldType.STRING
    )
    assert fields_by_path["relation.vocabulary"].metadata[
        "validation_result_field"
    ] == "vocabulary"
    assert fields_by_path["relation.id"].field_type is DomainPackFieldType.INTEGER
    assert fields_by_path["relation.id"].metadata[
        "validation_result_field"
    ] == "internal_id"

    hidden_relation_fields = {"relation.vocabulary", "relation.id"}
    workspace_display = curatable_unit.metadata["workspace_display"]
    assert hidden_relation_fields.isdisjoint(workspace_display["summary_fields"])
    assert all(
        hidden_relation_fields.isdisjoint(group["fields"])
        for group in workspace_display["groups"]
    )

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

    multi_fixture_ref = load_alliance_domain_pack_registry().get_fixture_pack_ref(
        GENE_EXPRESSION_DOMAIN_PACK_ID,
        GENE_EXPRESSION_MULTI_FIXTURE_PACK_ID,
    )
    assert multi_fixture_ref is not None
    assert multi_fixture_ref.path == "fixtures/tmem67_multi_annotation_pending.yaml"
    assert multi_fixture_ref.object_types == [GENE_EXPRESSION_OBJECT_TYPE]

    curator_fixture_ref = load_alliance_domain_pack_registry().get_fixture_pack_ref(
        GENE_EXPRESSION_DOMAIN_PACK_ID,
        GENE_EXPRESSION_CURATOR_GUIDANCE_FIXTURE_PACK_ID,
    )
    assert curator_fixture_ref is not None
    assert curator_fixture_ref.path == "fixtures/curator_guidance_pending.yaml"
    assert curator_fixture_ref.object_types == [GENE_EXPRESSION_OBJECT_TYPE]


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
        "expression_experiment.single_reference",
        "expression_experiment.single_reference.reference_id",
        "expression_experiment.entity_assayed",
        "expression_experiment.entity_assayed.primary_external_id",
        "expression_experiment.entity_assayed.gene_symbol",
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


def test_gene_expression_exposes_linkml_experiment_context_targets():
    fields_by_path = {
        field.field_path: field
        for field in _gene_expression_pack().metadata.object_definitions[0].fields
    }

    assert fields_by_path["expression_experiment.detection_reagents"].model_ref == (
        "ReagentSnapshotPayload"
    )
    assert fields_by_path["expression_experiment.detection_reagents"].metadata[
        "preservation_policy"
    ] == {
        "unresolved_text_metadata_path": "extraction_metadata.reagent_context",
        "unresolved_reason_code": "reagent_lookup_or_export_mapping_unavailable",
    }
    assert fields_by_path["expression_experiment.specimen_genomic_model"].model_ref == (
        "AffectedGenomicModelSnapshotPayload"
    )
    specimen_agm_ref = fields_by_path[
        "expression_experiment.specimen_genomic_model"
    ].metadata["provider_refs"]["alliance_linkml"]
    assert "db_table" not in specimen_agm_ref
    assert "db_column" not in specimen_agm_ref
    assert fields_by_path["expression_experiment.specimen_alleles"].model_ref == (
        "AlleleSnapshotPayload"
    )
    assert fields_by_path["condition_relations"].model_ref == (
        "ConditionRelationPayload"
    )
    assert {
        path: fields_by_path[path].metadata["provider_refs"]["alliance_linkml"][
            "range"
        ]
        for path in (
            "expression_experiment.detection_reagents",
            "expression_experiment.specimen_genomic_model",
            "expression_experiment.specimen_alleles",
            "condition_relations",
        )
    } == {
        "expression_experiment.detection_reagents": "Reagent",
        "expression_experiment.specimen_genomic_model": "AffectedGenomicModel",
        "expression_experiment.specimen_alleles": "Allele",
        "condition_relations": "ConditionRelation",
    }


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
        "expression_assay_ontology_validation",
        "expression_stage_ontology_validation",
        "expression_stage_uberon_slim_validation",
        "relation_vocabulary_validation",
        "source_reference_validation",
        "subject_gene_validation",
        # Experimental conditions: composite per-condition validation + relation-type CV lookup.
        "experimental_condition_validation",
        "gene_expression_condition_relation_lookup",
    }
    assert active_bindings["experimental_condition_validation"].field_paths == (
        "condition_relations.conditions",
    )
    assert active_bindings["gene_expression_condition_relation_lookup"].field_paths == (
        "condition_relations.condition_relation_type.name",
    )
    assert active_bindings["data_provider_validation"].field_paths == (
        "data_provider.abbreviation",
    )
    assert active_bindings["relation_vocabulary_validation"].field_paths == (
        "relation.name",
    )
    assert active_bindings["relation_vocabulary_validation"].expected_result_fields == {
        "term_name": "relation.name",
        "vocabulary": "relation.vocabulary",
        "internal_id": "relation.id",
    }
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
        "expression_pattern.when_expressed.developmental_stage_start",
    )
    assert active_bindings["expression_assay_ontology_validation"].field_paths == (
        "expression_experiment.expression_assay_used",
    )
    assert active_bindings[
        "expression_assay_ontology_validation"
    ].expected_result_fields == {
        "curie": "expression_experiment.expression_assay_used.curie",
        "name": "expression_experiment.expression_assay_used.name",
    }
    assert active_bindings["expression_stage_ontology_validation"].validator_agent is not None
    assert active_bindings["expression_stage_ontology_validation"].validator_agent.agent_id == (
        "ontology_term_validation"
    )
    assert active_bindings[
        "expression_stage_ontology_validation"
    ].expected_result_fields == {
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
        "expression_assay_ontology_validation",
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
    assert {"reagent_context_materialization"} <= under_development_binding_ids

    under_development_validator_ids = {
        entry.validator_id
        for entry in registry.validator_metadata
        if entry.state is ValidationBindingState.UNDER_DEVELOPMENT
    }
    assert {"gene_expression.reagent_context_materialization"} <= (
        under_development_validator_ids
    )

    # condition_relations is no longer a planned gap — experimental conditions are now fully wired
    # (active composite + relation-type CV bindings), so it is removed from this set.
    planned_gap_fields = {
        "expression_experiment.detection_reagents",
        "expression_experiment.specimen_genomic_model",
        "expression_experiment.specimen_alleles",
    }
    promoted_materialization_fields = {
        "expression_experiment.expression_assay_used",
        "expression_experiment.expression_assay_used.curie",
        "expression_experiment.expression_assay_used.name",
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
        "expression_experiment.expression_assay_used",
        "expression_pattern.when_expressed.developmental_stage_start",
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
    assert {
        field_path: fields_by_path[field_path].metadata.get("validator_state")
        for field_path in sorted(planned_gap_fields)
    } == {
        "expression_experiment.detection_reagents": "under_development",
        "expression_experiment.specimen_alleles": "under_development",
        "expression_experiment.specimen_genomic_model": "under_development",
    }
    for field_path in promoted_materialization_fields:
        assert fields_by_path[field_path].metadata["validator_state"] == "active"


def test_gene_expression_context_ontology_requests_are_field_scoped():
    envelope = _converted_tmem67_envelope()
    payload = copy.deepcopy(envelope.extracted_objects[0].payload)
    when_expressed = payload["expression_pattern"].setdefault("when_expressed", {})
    when_expressed["developmental_stage_start"] = staged_value(
        "expression_pattern.when_expressed.developmental_stage_start", "TS26 embryos"
    )
    when_expressed["stage_uberon_slim_terms"] = [
        staged_value("expression_pattern.when_expressed.stage_uberon_slim_terms", "embryonic stage")
    ]
    payload["expression_pattern"]["where_expressed"][
        "cellular_component_qualifiers"
    ] = [
        staged_value(
            "expression_pattern.where_expressed.cellular_component_qualifiers", "nuclear"
        )
    ]
    envelope = _with_payload(envelope, payload)

    stage_match = _active_binding_match(
        envelope,
        "expression_stage_ontology_validation",
    )
    stage_request = build_domain_validation_request(stage_match).request
    assert stage_request is not None
    assert stage_request.target.field_path == (
        "expression_pattern.when_expressed.developmental_stage_start"
    )
    # The validator looks up the paper wording, not the stage statement column. Its species
    # context is the extractor's data provider, before the data-provider validator runs.
    assert stage_request.selected_inputs == {
        "label": "TS26 embryos",
        "data_provider": "MGI",
        "ontology_family": "life_stage",
        "lookup_method": "search_life_stage_terms",
    }
    assert stage_request.input_selectors["data_provider"]["context_only"] is True
    assert stage_request.expected_result_fields == {
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
    # No ID in the paper for the anatomy: the validator searches with the wording alone.
    assert anatomy_request.selected_inputs == {
        "label": "metanephros",
        "data_provider": "MGI",
        "ontology_family": "anatomy",
        "lookup_method": "search_anatomy_terms",
    }
    assert anatomy_request.input_selectors["data_provider"]["context_only"] is True
    assert anatomy_request.input_selectors["evidence_quotes"] == {
        "source": "evidence_record",
        "field_path": "expression_pattern.where_expressed.anatomical_structure",
        "output": "quote_bundle",
        "required": False,
        "allow_multiple": True,
        "context_only": True,
    }

    stage_uberon_match = _active_binding_match(
        envelope,
        "expression_stage_uberon_slim_validation",
    )
    stage_uberon_request = build_domain_validation_request(
        stage_uberon_match
    ).request
    assert stage_uberon_request is not None
    assert stage_uberon_request.selected_inputs == {
        "vocabulary": "Stage Uberon Slim Terms",
        "term_name": "embryonic stage",
    }
    assert stage_uberon_request.expected_result_fields == {
        "term_name": "expression_pattern.when_expressed.stage_uberon_slim_terms[0].name",
        "vocabulary": "expression_pattern.when_expressed.stage_uberon_slim_terms[0].vocabulary",
        "internal_id": "expression_pattern.when_expressed.stage_uberon_slim_terms[0].id",
    }

    anatomical_uberon_match = _active_binding_match(
        envelope,
        "expression_anatomical_uberon_slim_validation",
    )
    anatomical_uberon_request = build_domain_validation_request(
        anatomical_uberon_match
    ).request
    assert anatomical_uberon_request is not None
    assert anatomical_uberon_request.selected_inputs == {
        "label": "renal system",
        "ontology_family": "uberon",
        "ontology_term_type": "UBERONTerm",
        "lookup_method": "search_ontology_terms",
        "allowed_term_curies": ANATOMICAL_UBERON_SLIM_ALLOWED_CURIES,
    }
    assert anatomical_uberon_request.expected_result_fields == {
        "curie": "expression_pattern.where_expressed.anatomical_structure_uberon_terms[0].curie",
        "name": "expression_pattern.where_expressed.anatomical_structure_uberon_terms[0].name",
    }

    qualifier_match = _active_binding_match(
        envelope,
        "expression_cellular_component_qualifier_validation",
    )
    qualifier_request = build_domain_validation_request(qualifier_match).request
    assert qualifier_request is not None
    assert qualifier_request.selected_inputs == {
        "label": "nuclear",
        "ontology_family": "go",
        "go_aspect": "cellular_component",
        "lookup_method": "search_go_terms",
    }
    assert qualifier_request.expected_result_fields == {
        "curie": "expression_pattern.where_expressed.cellular_component_qualifiers[0].curie",
        "name": "expression_pattern.where_expressed.cellular_component_qualifiers[0].name",
    }


def test_gene_expression_field_scoped_evidence_quote_bundle_selected_from_nested_metadata():
    envelope = _converted_tmem67_envelope()
    evidence_records = envelope.metadata["extraction_metadata"]["evidence_records"]
    evidence_records[0]["field_paths"] = [
        "expression_pattern.where_expressed.anatomical_structure"
    ]

    match = _active_binding_match(
        envelope,
        "expression_anatomical_structure_validation",
    )
    request = build_domain_validation_request(match).request

    assert request is not None
    assert request.selected_inputs["evidence_quotes"] == [
        {
            "evidence_record_id": "evidence-tmem67-metanephros-1",
            "field_path": "expression_pattern.where_expressed.anatomical_structure",
            "page": 4,
            "section": "Results",
            "subsection": "Gene expression analysis",
            "chunk_id": "chunk-tmem67-metanephros",
            "verified_quote": (
                "Tmem67 expression was detected in the metanephros at TS26 "
                "by reverse transcription polymerase chain reaction assay."
            ),
        }
    ]


def test_gene_expression_assay_materializes_from_validator_result():
    envelope = _converted_tmem67_envelope()
    payload = copy.deepcopy(envelope.extracted_objects[0].payload)
    payload["expression_experiment"]["expression_assay_used"] = staged_value(
        "expression_experiment.expression_assay_used",
        "whole-mount in situ hybridization",
    )
    envelope = _with_payload(envelope, payload)
    match = _active_binding_match(envelope, "expression_assay_ontology_validation")
    request = build_domain_validation_request(match).request
    assert request is not None
    assert request.selected_inputs == {
        "label": "whole-mount in situ hybridization",
        "ontology_family": "assay",
        "ontology_term_type": "MMOTerm",
        "lookup_method": "search_ontology_terms",
    }
    assert request.expected_result_fields == {
        "curie": "expression_experiment.expression_assay_used.curie",
        "name": "expression_experiment.expression_assay_used.name",
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
                        "curie": "MMO:0000658",
                        "name": "whole mount in situ hybridization assay",
                    },
                ),
            )
        ],
    )

    assay = result.envelope.extracted_objects[0].payload["expression_experiment"][
        "expression_assay_used"
    ]
    # The paper wording stays; the validator's term fills the identity.
    assert assay == _validated(
        "whole-mount in situ hybridization",
        curie="MMO:0000658",
        name="whole mount in situ hybridization assay",
    )
    patch_event = result.envelope.extracted_objects[0].metadata[
        "validator_resolved_value_materialization"
    ][0]
    assert patch_event["materialized_field_paths"] == [
        "expression_experiment.expression_assay_used.curie",
        "expression_experiment.expression_assay_used.name",
    ]
    resolved_field_ref = result.appended_findings[0].field_ref
    assert resolved_field_ref is not None
    assert resolved_field_ref.field_path == "expression_experiment.expression_assay_used"
    summaries = project_validation_summary_projections(
        result.envelope,
        envelope_revision=1,
        object_id=result.envelope.extracted_objects[0].pending_ref_id,
    )
    assert {
        summary.field_path: summary.status.value
        for summary in summaries
        if summary.field_path is not None
    } == {
        "expression_experiment.expression_assay_used": "resolved",
        "expression_experiment.expression_assay_used.curie": "resolved",
        "expression_experiment.expression_assay_used.name": "resolved",
    }


def test_gene_expression_relation_identity_materializes_from_validator_result():
    envelope = _converted_tmem67_envelope()
    match = _active_binding_match(envelope, "relation_vocabulary_validation")
    request = build_domain_validation_request(match).request
    assert request is not None
    # The vocabulary lookup reads the extractor's chosen term name (its mention).
    assert request.selected_inputs == {
        "vocabulary": "Expression Relation",
        "term_name": "is_expressed_in",
    }
    assert request.expected_result_fields == {
        "term_name": "relation.name",
        "vocabulary": "relation.vocabulary",
        "internal_id": "relation.id",
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
                        "term_name": "is_expressed_in",
                        "vocabulary": "Expression Relation",
                        "internal_id": 12345,
                    },
                ),
            )
        ],
    )

    relation = result.envelope.extracted_objects[0].payload["relation"]
    assert relation == _validated(
        "is_expressed_in",
        name="is_expressed_in",
        vocabulary="Expression Relation",
        id=12345,
    )
    assert not any(
        finding.code == "domain_pack.validator_expected_field_unmapped"
        for finding in result.appended_findings
    )


def test_gene_expression_conversion_preserves_no_match_assay_label_for_validation():
    staged_assay = staged_value(
        "expression_experiment.expression_assay_used",
        "paper-only colorimetric staining assay",
    )
    envelope = _converted_tmem67_envelope_with_raw_assay(staged_assay)

    assay = envelope.extracted_objects[0].payload["expression_experiment"][
        "expression_assay_used"
    ]
    assert assay == staged_assay
    # An unresolved assay is present: its validator owns it, so no "missing" finding.
    assert not [
        finding
        for finding in validate_pending_gene_expression_envelope(envelope)
        if finding.code.startswith("alliance.gene_expression.assay_method")
    ]

    match = _active_binding_match(envelope, "expression_assay_ontology_validation")
    request = build_domain_validation_request(match).request
    assert request is not None
    assert request.selected_inputs["label"] == "paper-only colorimetric staining assay"


def test_gene_expression_conversion_rejects_assay_without_paper_wording_or_state():
    with pytest.raises(ValueError, match="expression_assay_used: resolution_state must be one of"):
        _converted_tmem67_envelope_with_raw_assay(
            {"curie": "MMO:0000655", "name": "RT-PCR"}
        )


def test_gene_expression_stored_ambiguous_assay_candidates_stay_field_addressed():
    envelope = _converted_tmem67_envelope()
    payload = copy.deepcopy(envelope.extracted_objects[0].payload)
    payload["expression_experiment"]["expression_assay_used"] = {
        "candidates": [
            {"curie": "MMO:0000655", "name": "RT-PCR"},
            {"curie": "MMO:0000642", "name": "in situ hybridization"},
        ]
    }
    envelope = _with_payload(envelope, payload)
    finding = _finding_by_code(
        validate_pending_gene_expression_envelope(envelope),
        "alliance.gene_expression.assay_method_ambiguous",
    )
    assert finding.field_ref.field_path == (
        "expression_experiment.expression_assay_used"
    )
    assert finding.details["candidate_count"] == 2


@pytest.mark.parametrize(
    ("lookup_outcome", "expected_status"),
    [
        ("ambiguous", "ambiguous"),
        ("not_found", "not_found"),
    ],
)
def test_gene_expression_assay_unresolved_outcomes_stay_field_addressed(
    lookup_outcome: str,
    expected_status: str,
):
    envelope = _converted_tmem67_envelope()
    match = _active_binding_match(envelope, "expression_assay_ontology_validation")
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
                    missing_expected_fields=["curie", "name"],
                    lookup_outcome=lookup_outcome,
                    candidates=[
                        {
                            "value": "MMO:0000658",
                            "label": "whole mount in situ hybridization assay",
                            "object_type": "OntologyTerm",
                        },
                        {
                            "value": "MMO:0000642",
                            "label": "in situ hybridization assay",
                            "object_type": "OntologyTerm",
                        },
                    ]
                    if lookup_outcome == "ambiguous"
                    else [],
                    curator_message="Assay label requires curator review.",
                ),
            )
        ],
    )

    assert result.materialized_objects == ()
    # The staged assay records the validator's outcome; its paper wording and every other
    # value are untouched.
    before = envelope.extracted_objects[0].payload
    after = result.envelope.extracted_objects[0].payload
    assay = after["expression_experiment"]["expression_assay_used"]
    assert (assay["curie"], assay["name"], assay["resolution_state"], assay["lookup_outcome"]) == (
        None, None, "unresolved", expected_status,
    )
    assert "overruled_curie" not in assay
    assert assay["mention"] == "reverse transcription polymerase chain reaction assay"
    assert assay["validator_curator_message"] == "Assay label requires curator review."
    unchanged = copy.deepcopy(after)
    unchanged["expression_experiment"]["expression_assay_used"] = before["expression_experiment"][
        "expression_assay_used"
    ]
    assert unchanged == before
    finding = result.appended_findings[0]
    assert finding.code == "domain_pack.validator_unresolved"
    assert finding.details["failure_classification"] == expected_status
    assert finding.field_ref is not None
    assert finding.field_ref.field_path == (
        "expression_experiment.expression_assay_used"
    )
    assert finding.details["lookup_attempts"][0]["lookup_status"] == expected_status
    summaries = project_validation_summary_projections(
        result.envelope,
        envelope_revision=1,
        object_id=result.envelope.extracted_objects[0].pending_ref_id,
    )
    assert {
        summary.field_path: summary.status.value
        for summary in summaries
        if summary.field_path is not None
    } == {
        "expression_experiment.expression_assay_used": "unresolved",
        "expression_experiment.expression_assay_used.curie": "unresolved",
        "expression_experiment.expression_assay_used.name": "unresolved",
    }


def test_gene_expression_uberon_slim_metadata_carries_linkml_allowlists():
    fields_by_path = {
        field.field_path: field
        for field in _gene_expression_pack().metadata.object_definitions[0].fields
    }

    # LinkML range VocabularyTerm: stage slims are terms of the Stage Uberon Slim Terms
    # vocabulary (UBERON:0000068, UBERON:0000113, post embryonic, pre-adult), not UBERON terms.
    stage_field = fields_by_path["expression_pattern.when_expressed.stage_uberon_slim_terms"]
    assert stage_field.model_ref == "VocabularyTermSnapshotPayload"
    assert stage_field.metadata["term_helper"]["term_source"] == {
        "kind": "controlled_vocabulary",
        "vocabulary": _STAGE_SLIM_VOCABULARY,
    }

    anatomical_helper = fields_by_path[
        "expression_pattern.where_expressed.anatomical_structure_uberon_terms"
    ].metadata["term_helper"]
    assert anatomical_helper["term_source"]["slim_membership"] == {
        "source": "alliance_linkml",
        "allowed_term_curies": ANATOMICAL_UBERON_SLIM_ALLOWED_CURIES,
    }


def test_gene_expression_cellular_component_only_site_remains_validatable():
    envelope = _converted_tmem67_envelope()
    payload = copy.deepcopy(envelope.extracted_objects[0].payload)
    payload["expression_pattern"]["where_expressed"] = {
        "cellular_component": staged_value(
            "expression_pattern.where_expressed.cellular_component", "nucleus"
        )
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

    where_expressed = result.envelope.extracted_objects[0].payload["expression_pattern"][
        "where_expressed"
    ]
    assert where_expressed == {
        "cellular_component": _validated("nucleus", curie="GO:0005634", name="nucleus")
    }
    patch_event = result.envelope.extracted_objects[0].metadata[
        "validator_resolved_value_materialization"
    ][0]
    assert patch_event["materialized_field_paths"] == [
        "expression_pattern.where_expressed.cellular_component.curie",
        "expression_pattern.where_expressed.cellular_component.name",
    ]
    resolved_field_ref = result.appended_findings[0].field_ref
    assert resolved_field_ref is not None
    assert resolved_field_ref.field_path == (
        "expression_pattern.where_expressed.cellular_component"
    )


def _with_staged_stage(envelope: Any, mention: str = "TS26"):
    payload = copy.deepcopy(envelope.extracted_objects[0].payload)
    payload["expression_pattern"].setdefault("when_expressed", {})[
        "developmental_stage_start"
    ] = staged_value("expression_pattern.when_expressed.developmental_stage_start", mention)
    return _with_payload(envelope, payload)


def test_gene_expression_stage_and_site_terms_materialize_from_validator_results():
    envelope = _with_staged_stage(_converted_tmem67_envelope())
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

    payload = result.envelope.extracted_objects[0].payload
    # The validated stage term's name fills the stage name (declared mirror); the paper's
    # stage wording stays the term's mention.
    assert payload["when_expressed_stage_name"] == "Theiler stage 26"
    assert payload["expression_pattern"]["when_expressed"][
        "developmental_stage_start"
    ] == _validated("TS26", curie="FIXTURE_STAGE:00026", name="Theiler stage 26")
    assert payload["expression_pattern"]["where_expressed"][
        "anatomical_structure"
    ] == _validated("metanephros", curie="EMAPA:17373", name="metanephros")
    stage_patch = result.envelope.extracted_objects[0].metadata[
        "validator_resolved_value_materialization"
    ][0]
    assert stage_patch["materialized_field_paths"] == [
        "expression_pattern.when_expressed.developmental_stage_start.curie",
        "expression_pattern.when_expressed.developmental_stage_start.name",
    ]
    resolved_status_by_path = {
        finding.field_ref.field_path: finding.status.value
        for finding in result.appended_findings
        if finding.field_ref is not None
    }
    assert resolved_status_by_path == {
        "when_expressed_stage_name": "resolved",
        "expression_pattern.when_expressed.developmental_stage_start": "resolved",
        "expression_pattern.when_expressed.developmental_stage_start.curie": (
            "resolved"
        ),
        "expression_pattern.when_expressed.developmental_stage_start.name": (
            "resolved"
        ),
        "expression_pattern.where_expressed.anatomical_structure": "resolved",
        "expression_pattern.where_expressed.anatomical_structure.curie": (
            "resolved"
        ),
        "expression_pattern.where_expressed.anatomical_structure.name": (
            "resolved"
        ),
    }


def _slim(field_path: str, mention: str) -> dict[str, Any]:
    return staged_value(field_path, mention)


_STAGE_SLIM = "expression_pattern.when_expressed.stage_uberon_slim_terms"
_STAGE_SLIM_VOCABULARY = "Stage Uberon Slim Terms"


def _stage_slim_term(name: str, internal_id: int) -> dict[str, Any]:
    """A Stage Uberon Slim Terms result: the curation DB names UBERON terms by their CURIE."""

    return {"term_name": name, "vocabulary": _STAGE_SLIM_VOCABULARY, "internal_id": internal_id}
_ANATOMY_SLIM = "expression_pattern.where_expressed.anatomical_structure_uberon_terms"
_QUALIFIERS = "expression_pattern.where_expressed.cellular_component_qualifiers"


def _materialize_one(envelope: Any, binding_id: str, **result_kwargs: Any):
    match = _active_binding_match(envelope, binding_id)
    request = build_domain_validation_request(match).request
    assert request is not None
    return materialize_validator_results_into_envelope(
        envelope,
        _gene_expression_pack().metadata,
        [
            ValidatorResultMaterializationInput(
                match=match,
                request=request,
                result=_validator_result(request, **result_kwargs),
            )
        ],
    )


def test_gene_expression_slim_and_qualifier_arrays_materialize_from_validator_results():
    envelope = _converted_tmem67_envelope()
    payload = copy.deepcopy(envelope.extracted_objects[0].payload)
    payload["expression_pattern"]["when_expressed"] = {
        "stage_uberon_slim_terms": [_slim(_STAGE_SLIM, "UBERON:0000068")]
    }
    payload["expression_pattern"]["where_expressed"][
        "cellular_component_qualifiers"
    ] = [_slim(_QUALIFIERS, "nuclear lumen")]
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
                    resolved_values=_stage_slim_term("UBERON:0000068", 200006300),
                ),
            ),
            ValidatorResultMaterializationInput(
                match=anatomy_uberon_match,
                request=anatomy_request,
                result=_validator_result(
                    anatomy_request,
                    status="resolved",
                    resolved_values={"curie": "UBERON:0001008", "name": "renal system"},
                ),
            ),
            ValidatorResultMaterializationInput(
                match=qualifier_match,
                request=qualifier_request,
                result=_validator_result(
                    qualifier_request,
                    status="resolved",
                    resolved_values={"curie": "GO:0031981", "name": "nuclear lumen"},
                ),
            ),
        ],
    )

    # Each element keeps its own paper wording and takes its own validator result.
    payload = result.envelope.extracted_objects[0].payload
    assert payload["expression_pattern"]["when_expressed"][
        "stage_uberon_slim_terms"
    ] == [
        _validated(
            "UBERON:0000068",
            name="UBERON:0000068",
            vocabulary=_STAGE_SLIM_VOCABULARY,
            id=200006300,
        )
    ]
    assert payload["expression_pattern"]["where_expressed"][
        "anatomical_structure_uberon_terms"
    ] == [_validated("renal system", curie="UBERON:0001008", name="renal system")]
    assert payload["expression_pattern"]["where_expressed"][
        "cellular_component_qualifiers"
    ] == [_validated("nuclear lumen", curie="GO:0031981", name="nuclear lumen")]


def _out_of_slim(element: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **element,
        "resolution_state": "unresolved",
        "lookup_outcome": "invalid_schema",
        "validator_explanation": _VALIDATOR_EXPLANATION,
        "validator_curator_message": "Fixture resolved ontology result.",
    }


def test_gene_expression_stage_slim_outside_the_vocabulary_stays_unresolved():
    envelope = _converted_tmem67_envelope()
    payload = copy.deepcopy(envelope.extracted_objects[0].payload)
    staged = _slim(_STAGE_SLIM, "adult")
    payload["expression_pattern"]["when_expressed"] = {"stage_uberon_slim_terms": [staged]}
    envelope = _with_payload(envelope, payload)

    result = _materialize_one(
        envelope,
        "expression_stage_uberon_slim_validation",
        status="unresolved",
        missing_expected_fields=[],
        lookup_outcome="not_found",
        curator_message="No Stage Uberon Slim Terms term matches this wording.",
    )

    # No identity is written; the element records why it stays UNRESOLVED.
    [element] = result.envelope.extracted_objects[0].payload["expression_pattern"]["when_expressed"][
        "stage_uberon_slim_terms"
    ]
    assert {key: element[key] for key in ("name", "vocabulary", "id", "mention")} == {
        "name": None, "vocabulary": None, "id": None, "mention": "adult",
    }
    assert (element["resolution_state"], element["lookup_outcome"]) == ("unresolved", "not_found")


def test_gene_expression_anatomical_uberon_slim_rejects_out_of_slim_materialization():
    envelope = _converted_tmem67_envelope()
    payload = copy.deepcopy(envelope.extracted_objects[0].payload)
    staged = _slim(_ANATOMY_SLIM, "kidney")
    payload["expression_pattern"]["where_expressed"]["anatomical_structure_uberon_terms"] = [staged]
    envelope = _with_payload(envelope, payload)

    result = _materialize_one(
        envelope,
        "expression_anatomical_uberon_slim_validation",
        status="resolved",
        resolved_values={"curie": "UBERON:0002113", "name": "kidney"},
    )

    assert result.envelope.extracted_objects[0].payload["expression_pattern"][
        "where_expressed"
    ]["anatomical_structure_uberon_terms"] == [_out_of_slim(staged)]
    finding = result.appended_findings[0]
    assert finding.code == "domain_pack.validator_materialization_invalid"
    assert "UBERON:0002113" in finding.details["materialization_error"]


@pytest.mark.parametrize(
    ("name", "internal_id"),
    [
        ("post embryonic, pre-adult", 200008800),
        ("UBERON:0000068", 200006300),
        ("UBERON:0000113", 200006250),
    ],
)
def test_gene_expression_every_stage_slim_vocabulary_term_resolves_and_exports(name, internal_id):
    """Review #4: the WB larval slim "post embryonic, pre-adult" is a real vocabulary term."""

    from agr_ai_curation_alliance.domain_packs.gene_expression.export import (
        _gene_expression_annotation_payload,
    )

    envelope = _converted_tmem67_envelope()
    payload = copy.deepcopy(envelope.extracted_objects[0].payload)
    payload["expression_pattern"]["when_expressed"]["stage_uberon_slim_terms"] = [
        _slim(_STAGE_SLIM, name)
    ]
    payload["when_expressed_stage_name"] = "TS26"
    envelope = _with_payload(envelope, payload)

    result = _materialize_one(
        envelope,
        "expression_stage_uberon_slim_validation",
        status="resolved",
        resolved_values=_stage_slim_term(name, internal_id),
    )

    annotation = result.envelope.extracted_objects[0]
    [element] = annotation.payload["expression_pattern"]["when_expressed"]["stage_uberon_slim_terms"]
    assert element == _validated(
        name, name=name, vocabulary=_STAGE_SLIM_VOCABULARY, id=internal_id
    )
    candidate = _export_candidate(annotation)
    assert not {
        blocker.field_path
        for blocker in gene_expression_export_blockers(candidate)
        if blocker.field_path.startswith(_STAGE_SLIM)
    }
    candidate.update(
        {
            "envelope_revision": 1,
            "domain_pack_id": GENE_EXPRESSION_DOMAIN_PACK_ID,
            "projection_ref": {"envelope_id": "envelope-1", "object_id": candidate["object_id"]},
        }
    )
    temporal = _gene_expression_annotation_payload(candidate)["target_rows"]["temporalcontext"]
    assert temporal["relationships"]["temporalcontext_stageuberonslimterms"] == [
        {"table": "vocabularyterm", "match": {"vocabulary": _STAGE_SLIM_VOCABULARY, "name": name}}
    ]


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
    # An anatomy term an earlier validation resolved, validated again.
    envelope = _converted_tmem67_envelope()
    payload = copy.deepcopy(envelope.extracted_objects[0].payload)
    payload["expression_pattern"]["where_expressed"]["anatomical_structure"] = _validated(
        "metanephros", curie="EMAPA:17373", name="metanephros"
    )
    envelope = _with_payload(envelope, payload)
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
    # A decisive outcome overrules the earlier resolution: the identity is kept only as
    # overruled_* hints, the paper wording is untouched and nothing else changes.
    before = envelope.extracted_objects[0].payload
    after = result.envelope.extracted_objects[0].payload
    site_before = before["expression_pattern"]["where_expressed"]["anatomical_structure"]
    site_after = after["expression_pattern"]["where_expressed"]["anatomical_structure"]
    assert site_before["resolution_state"] == "resolved"
    assert site_after == {
        **site_before,
        "curie": None,
        "name": None,
        "overruled_curie": site_before["curie"],
        "overruled_name": site_before["name"],
        "resolution_state": "unresolved",
        "lookup_outcome": lookup_outcome,
        "validator_explanation": "Fixture-backed validator result for gene-expression contract tests.",
        "validator_curator_message": "Paper-facing anatomy label requires curator review.",
    }
    unchanged = copy.deepcopy(after)
    unchanged["expression_pattern"]["where_expressed"]["anatomical_structure"] = site_before
    assert unchanged == before
    finding = result.appended_findings[0]
    assert finding.code == "domain_pack.validator_unresolved"
    assert finding.field_ref is not None
    assert finding.field_ref.field_path == (
        "expression_pattern.where_expressed.anatomical_structure"
    )
    assert finding.details["validation_result"]["status"] == "unresolved"
    assert finding.details["validation_result"]["lookup_attempt_count"] == 1
    assert finding.details["validation_result"]["candidate_count"] == 1
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
    assert len(envelope.extracted_objects) == 1

    annotation = envelope.extracted_objects[0]
    assert annotation.object_type == GENE_EXPRESSION_OBJECT_TYPE
    assert annotation.status is CuratableObjectStatus.PENDING
    assert annotation.object_role == "curatable_unit"
    assert annotation.model_ref == GENE_EXPRESSION_MODEL_ID
    assert annotation.object_refs == []
    assert annotation.field_refs == []
    assert annotation.evidence_record_ids == ["evidence-tmem67-metanephros-1"]
    assert annotation.metadata_refs[0].metadata_path == "raw_mentions[0]"
    assert annotation.metadata_refs[1].metadata_path == "evidence_records[0]"
    _assert_metadata_refs_resolve(envelope)
    assert annotation.payload["expression_annotation_subject"] == {
        "primary_external_id": "MGI:1923928",
        "gene_symbol": "Tmem67",
        "mention": "Tmem67",
        "resolution_state": "resolved",
        "lookup_outcome": "matched",
        "validator_explanation": None,
    }
    assert envelope.metadata["semantic_source"] == "domain_envelope.extracted_objects"
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


def test_multi_annotation_fixture_projects_one_review_row_per_expression_statement():
    fixture_ref = load_alliance_domain_pack_registry().get_fixture_pack_ref(
        GENE_EXPRESSION_DOMAIN_PACK_ID,
        GENE_EXPRESSION_MULTI_FIXTURE_PACK_ID,
    )
    assert fixture_ref is not None
    fixture_path = get_gene_expression_domain_pack_metadata_path().parent / fixture_ref.path
    fixture_pack = load_domain_fixture_pack(fixture_path)
    envelope = fixture_pack.fixtures[0].envelope

    annotations = [
        obj for obj in envelope.extracted_objects if obj.object_type == GENE_EXPRESSION_OBJECT_TYPE
    ]
    assert [annotation.pending_ref_id for annotation in annotations] == [
        "gene-expression-annotation-tmem67-metanephros",
        "gene-expression-annotation-tmem67-neural-tube",
    ]
    assert {
        annotation.payload["expression_annotation_subject"]["primary_external_id"]
        for annotation in annotations
    } == {"MGI:1923928"}
    assert {
        annotation.payload["where_expressed_statement"]
        for annotation in annotations
    } == {"metanephros", "neural tube"}
    assert all(
        exclusion["creates_candidate"] is False
        for exclusion in envelope.metadata["extraction_metadata"]["exclusions"]
    )
    _assert_metadata_refs_resolve(envelope)
    assert validate_pending_gene_expression_envelope(envelope) == ()

    rows = DomainPackMetadataReviewRowMaterializer(
        _gene_expression_pack().metadata,
    ).materialize(envelope, envelope_revision=2)

    assert len(rows) == 2
    assert [row.object_id for row in rows] == [
        "gene-expression-annotation-tmem67-metanephros",
        "gene-expression-annotation-tmem67-neural-tube",
    ]
    assert {row.object_type for row in rows} == {GENE_EXPRESSION_OBJECT_TYPE}
    assert {row.object_role for row in rows} == {"curatable_unit"}
    assert {row.domain_pack_id for row in rows} == {GENE_EXPRESSION_DOMAIN_PACK_ID}
    assert {row.schema_ref["schema_id"] for row in rows} == {
        "alliance.linkml.GeneExpressionAnnotation"
    }
    assert {row.display_label for row in rows} == {"Tmem67"}
    assert [row.secondary_label for row in rows] == ["metanephros", "neural tube"]
    assert [row.validation_state for row in rows] == ["clear", "warning"]
    assert rows[0].metadata["payload_path"] == "extracted_objects[0].payload"
    assert rows[0].metadata["evidence_record_ids"] == [
        "evidence-tmem67-metanephros-1"
    ]
    assert rows[1].metadata["metadata_refs"] == [
        {
            "metadata_path": "raw_mentions[1]",
            "role": "source_mention",
            "description": None,
        },
        {
            "metadata_path": "evidence_records[1]",
            "role": "verified_evidence",
            "description": None,
        },
        {
            "metadata_path": "ambiguities[0]",
            "role": "curator_context",
            "description": None,
        },
    ]
    assert [field.field_path for field in rows[0].summary_fields] == [
        "expression_annotation_subject.gene_symbol",
        "where_expressed_statement",
        "when_expressed_stage_name",
        "relation.name",
        "expression_experiment.expression_assay_used.name",
    ]
    workspace_fields = rows[0].metadata["workspace_fields"]
    workspace_paths = [field["field_path"] for field in workspace_fields]
    assert workspace_paths[:10] == [
        "expression_annotation_subject.primary_external_id",
        "expression_annotation_subject.gene_symbol",
        "expression_annotation_subject.mention",
        "single_reference.reference_id",
        "single_reference.mention",
        "expression_experiment.expression_assay_used.curie",
        "expression_experiment.expression_assay_used.mention",
        "where_expressed_statement",
        "expression_pattern.where_expressed.anatomical_structure.curie",
        "expression_pattern.where_expressed.anatomical_structure.mention",
    ]
    assert "expression_experiment.entity_assayed.primary_external_id" not in workspace_paths
    assert "expression_experiment.single_reference.reference_id" not in workspace_paths
    assert workspace_fields[0]["metadata"]["workspace_group"] == {
        "id": "subject",
        "label": "Subject gene",
        "order": 0,
        "field_order": 0,
    }
    assert workspace_fields[0]["metadata"]["required"] is True
    assert workspace_fields[0]["metadata"]["read_only"] is False
    assert workspace_fields[0]["metadata"]["materializes_to_field_paths"] == [
        "expression_experiment.entity_assayed.primary_external_id"
    ]
    reference_field = next(
        field
        for field in workspace_fields
        if field["field_path"] == "single_reference.reference_id"
    )
    assert reference_field["metadata"]["materializes_to_field_paths"] == [
        "expression_experiment.single_reference.reference_id"
    ]
    assay_field = next(
        field
        for field in workspace_fields
        if field["field_path"] == "expression_experiment.expression_assay_used.curie"
    )
    assert assay_field["metadata"]["render_as"] == "curie-chip"
    data_provider_field = next(
        field
        for field in workspace_fields
        if field["field_path"] == "data_provider.abbreviation"
    )
    assert data_provider_field["metadata"]["read_only"] is True

    metanephros_evidence = project_evidence_anchor_projections(
        envelope,
        envelope_revision=2,
        object_id="gene-expression-annotation-tmem67-metanephros",
    )
    neural_tube_evidence = project_evidence_anchor_projections(
        envelope,
        envelope_revision=2,
        object_id="gene-expression-annotation-tmem67-neural-tube",
    )

    assert {
        (projection.evidence_record_id, projection.field_path)
        for projection in metanephros_evidence
    } == {
        ("evidence-tmem67-metanephros-1", "where_expressed_statement"),
        (
            "evidence-tmem67-metanephros-1",
            "expression_pattern.where_expressed.anatomical_structure",
        ),
    }
    assert {
        (projection.evidence_record_id, projection.field_path)
        for projection in neural_tube_evidence
    } == {
        ("evidence-tmem67-neural-tube-1", "where_expressed_statement"),
        (
            "evidence-tmem67-neural-tube-1",
            "expression_pattern.where_expressed.anatomical_structure",
        ),
    }


def test_curator_guidance_fixture_covers_site_routing_and_context_preservation():
    fixture_pack = _load_gene_expression_fixture_pack(
        GENE_EXPRESSION_CURATOR_GUIDANCE_FIXTURE_PACK_ID
    )
    envelope = fixture_pack.fixtures[0].envelope

    assert fixture_pack.fixture_pack_id == (
        GENE_EXPRESSION_CURATOR_GUIDANCE_FIXTURE_PACK_ID
    )
    assert len(envelope.extracted_objects) == 3
    assert validate_pending_gene_expression_envelope(envelope) == ()
    _assert_metadata_refs_resolve(envelope)

    payloads = {obj.pending_ref_id: obj.payload for obj in envelope.extracted_objects}
    anatomy_only = payloads["gene-expression-annotation-flcn-pronephric-duct"][
        "expression_pattern"
    ]["where_expressed"]
    cellular_component_only = payloads["gene-expression-annotation-flcn-nucleus"][
        "expression_pattern"
    ]["where_expressed"]
    mixed_negated = payloads[
        "gene-expression-annotation-flcn-retina-cytoplasm-negated"
    ]

    assert anatomy_only == {
        "anatomical_structure": _grounded(
            "pronephric duct", curie="ZFA:0000260", name="pronephric duct"
        ),
        "anatomical_structure_uberon_terms": [
            _grounded("renal system", curie="UBERON:0001008", name="renal system")
        ],
    }
    assert cellular_component_only == {
        "cellular_component": _grounded("nucleus", curie="GO:0005634", name="nucleus")
    }
    assert mixed_negated["negated"] is True
    assert mixed_negated["uncertain"] is True
    assert mixed_negated["expression_pattern"]["where_expressed"] == {
        "anatomical_structure": _grounded("retina", curie="ZFA:0000151", name="retina"),
        "cellular_component": _grounded("cytoplasm", curie="GO:0005737", name="cytoplasm"),
        "cellular_component_qualifiers": [
            _grounded("present in", curie="RO:0002170", name="present in")
        ],
    }

    context_payload = payloads[
        "gene-expression-annotation-flcn-pronephric-duct"
    ]["expression_experiment"]
    assert context_payload["detection_reagents"] == [
        {
            "name": "flcn riboprobe",
            "placeholder": True,
            "source_text": "flcn riboprobe",
            "unresolved_reason_code": "reagent_lookup_or_export_mapping_unavailable",
        }
    ]
    assert "specimen_genomic_model" in context_payload
    assert "specimen_alleles" in context_payload
    assert payloads["gene-expression-annotation-flcn-pronephric-duct"][
        "condition_relations"
    ][0]["conditions"][0]["condition_free_text"] == "embryos raised at 28.5 C"

    helper_paths = {
        selection["field_path"]
        for selection in envelope.metadata["extraction_metadata"]["provenance"][
            "helper_selections"
        ]
    }
    assert {
        "relation.name",
        "expression_experiment.expression_assay_used",
        "when_expressed_stage_name",
        "expression_pattern.when_expressed.developmental_stage_start",
        "expression_pattern.where_expressed.anatomical_structure",
        "expression_pattern.where_expressed.cellular_component",
    }.issubset(helper_paths)
    assert envelope.metadata["extraction_metadata"]["provenance"][
        "reference_lookup"
    ]["source_tool"] == "agr_literature_reference_lookup"

    rows = DomainPackMetadataReviewRowMaterializer(
        _gene_expression_pack().metadata,
    ).materialize(envelope, envelope_revision=1)
    assert [row.object_id for row in rows] == [
        "gene-expression-annotation-flcn-pronephric-duct",
        "gene-expression-annotation-flcn-nucleus",
        "gene-expression-annotation-flcn-retina-cytoplasm-negated",
    ]
    assert {row.display_label for row in rows} == {"flcn"}
    assert [row.validation_state for row in rows] == ["clear", "clear", "clear"]


def test_gene_expression_validator_warns_when_expected_optional_context_is_dropped():
    fixture_pack = _load_gene_expression_fixture_pack(
        GENE_EXPRESSION_CURATOR_GUIDANCE_FIXTURE_PACK_ID
    )
    envelope = fixture_pack.fixtures[0].envelope
    annotation = envelope.extracted_objects[0]
    payload = copy.deepcopy(annotation.payload)
    del payload["expression_experiment"]["detection_reagents"]
    del payload["expression_experiment"]["specimen_genomic_model"]
    del payload["condition_relations"]
    changed_annotation = annotation.model_copy(update={"payload": payload})
    changed_envelope = envelope.model_copy(
        update={"extracted_objects": [changed_annotation, *envelope.extracted_objects[1:]]}
    )

    findings = validate_pending_gene_expression_envelope(changed_envelope)

    assert {
        (finding.field_ref.field_path, finding.severity, finding.details["blocking"])
        for finding in findings
        if finding.code == "alliance.gene_expression.experiment_context_dropped"
    } == {
        (
            "condition_relations",
            ValidationFindingSeverity.WARNING,
            False,
        ),
        (
            "expression_experiment.detection_reagents",
            ValidationFindingSeverity.WARNING,
            False,
        ),
        (
            "expression_experiment.specimen_genomic_model",
            ValidationFindingSeverity.WARNING,
            False,
        ),
    }


def test_tmem67_fixture_carries_anatomical_site_for_linkml_postcondition():
    fixture_ref = load_alliance_domain_pack_registry().get_fixture_pack_ref(
        GENE_EXPRESSION_DOMAIN_PACK_ID,
        GENE_EXPRESSION_FIXTURE_PACK_ID,
    )
    assert fixture_ref is not None
    fixture_path = get_gene_expression_domain_pack_metadata_path().parent / fixture_ref.path
    fixture_pack = load_domain_fixture_pack(fixture_path)
    annotation = fixture_pack.fixtures[0].envelope.extracted_objects[0]
    where_expressed = annotation.payload["expression_pattern"]["where_expressed"]

    assert (
        "anatomical_structure" in where_expressed
        or "cellular_component" in where_expressed
    )
    assert where_expressed["anatomical_structure_uberon_terms"] == [
        _grounded("renal system", curie="UBERON:0001008", name="renal system")
    ]


def test_tmem67_extractor_output_converts_to_pending_gene_expression_envelope():
    converted = _converted_tmem67_envelope()

    assert converted.envelope_id == "gene-expression-tmem67-mgi-206552169"
    assert converted.domain_pack_id == GENE_EXPRESSION_DOMAIN_PACK_ID
    assert len(converted.extracted_objects) == 1
    annotation = converted.extracted_objects[0]
    assert annotation.object_type == GENE_EXPRESSION_OBJECT_TYPE
    assert annotation.status is CuratableObjectStatus.PENDING
    assert annotation.evidence_record_ids == ["evidence-tmem67-metanephros-1"]
    assert annotation.metadata_refs[0].metadata_path == "raw_mentions[0]"
    assert annotation.metadata_refs[1].metadata_path == "evidence_records[0]"
    assert converted.metadata["source_document_id"] == "document-tmem67-expression-fixture"
    assert converted.metadata["extraction_metadata"]["evidence_records"][0][
        "verified_quote"
    ].startswith("Tmem67 expression was detected")
    _assert_metadata_refs_resolve(converted)
    assert validate_pending_gene_expression_envelope(converted) == ()


def test_gene_expression_linkml_validator_reports_missing_gene_selector_field():
    envelope = _converted_tmem67_envelope()
    payload = copy.deepcopy(envelope.extracted_objects[0].payload)
    payload.pop("expression_annotation_subject")

    findings = validate_pending_gene_expression_envelope(_with_payload(envelope, payload))

    finding = _finding_by_code(
        findings,
        "alliance.gene_expression.subject_gene_missing",
    )
    assert finding.severity is ValidationFindingSeverity.BLOCKER
    assert finding.field_ref.field_path == "expression_annotation_subject"
    assert finding.details["blocking"] is True
    assert finding.details["classification"] == "repairable_extraction_error"


def test_gene_expression_linkml_validator_reports_missing_reference_field():
    envelope = _converted_tmem67_envelope()
    payload = copy.deepcopy(envelope.extracted_objects[0].payload)
    payload.pop("single_reference")

    findings = validate_pending_gene_expression_envelope(_with_payload(envelope, payload))

    finding = _finding_by_code(findings, "alliance.gene_expression.reference_missing")
    assert finding.field_ref.field_path == "single_reference"
    assert finding.details["expected_selector"] == (
        "PMID or Alliance reference identifier"
    )


def test_gene_expression_linkml_validator_reports_missing_and_unknown_evidence():
    envelope = _converted_tmem67_envelope()
    annotation = envelope.extracted_objects[0].model_copy(update={"evidence_record_ids": []})
    missing_envelope = envelope.model_copy(update={"extracted_objects": [annotation]})

    missing_finding = _finding_by_code(
        validate_pending_gene_expression_envelope(missing_envelope),
        "alliance.gene_expression.evidence_record_ids_missing",
    )
    assert missing_finding.field_ref.field_path == "evidence_record_ids"
    assert (
        missing_finding.details["classification"]
        == "non_repairable_extraction_error"
    )

    unknown_annotation = envelope.extracted_objects[0].model_copy(
        update={"evidence_record_ids": ["evidence-not-in-metadata"]}
    )
    unknown_envelope = envelope.model_copy(update={"extracted_objects": [unknown_annotation]})
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
    missing_payload = copy.deepcopy(envelope.extracted_objects[0].payload)
    missing_payload.pop("relation")

    missing_finding = _finding_by_code(
        validate_pending_gene_expression_envelope(
            _with_payload(envelope, missing_payload)
        ),
        "alliance.gene_expression.relation_name_missing",
    )
    assert missing_finding.field_ref.field_path == "relation"
    assert missing_finding.details["expected_vocabulary"] == "Expression Relation"
    assert missing_finding.details["expected_values"] == ["is_expressed_in"]

    # A relation a validator resolved to a term outside the Expression Relation options.
    invalid_payload = copy.deepcopy(envelope.extracted_objects[0].payload)
    invalid_payload["relation"] = _validated(
        "is_expressed_in", name="expressed_in", vocabulary="Expression Relation", id=1
    )

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
    # An assay a validator resolved to something that is not a CURIE.
    invalid_payload = copy.deepcopy(envelope.extracted_objects[0].payload)
    invalid_payload["expression_experiment"]["expression_assay_used"] = _validated(
        "reverse transcription polymerase chain reaction assay",
        curie="not-a-curie",
        name="reverse transcription polymerase chain reaction assay",
    )

    invalid_finding = _finding_by_code(
        validate_pending_gene_expression_envelope(
            _with_payload(envelope, invalid_payload)
        ),
        "alliance.gene_expression.assay_method_invalid",
    )
    assert invalid_finding.field_ref.field_path == (
        "expression_experiment.expression_assay_used.curie"
    )

    ambiguous_payload = copy.deepcopy(envelope.extracted_objects[0].payload)
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


def test_gene_expression_linkml_validator_reports_experiment_projection_mismatch():
    envelope = _converted_tmem67_envelope()
    payload = copy.deepcopy(envelope.extracted_objects[0].payload)
    payload["single_reference"] = _grounded("AGRKB:101000000232912", reference_id=203506)
    payload["expression_experiment"]["single_reference"] = _grounded(
        "AGRKB:101000000232912", reference_id=999999
    )
    payload["expression_annotation_subject"] = _grounded(
        "Tmem67", primary_external_id="MGI:1923928", gene_symbol="Tmem67"
    )
    payload["expression_experiment"]["entity_assayed"] = _grounded(
        "Tmem67", primary_external_id="MGI:9999999", gene_symbol="Tmem67"
    )

    findings = validate_pending_gene_expression_envelope(_with_payload(envelope, payload))
    findings_by_field = {
        finding.field_ref.field_path: finding
        for finding in findings
        if finding.field_ref is not None
    }
    assert (
        findings_by_field[
            "expression_experiment.single_reference.reference_id"
        ].code
        == "alliance.gene_expression.experiment_reference_mismatch"
    )
    assert (
        findings_by_field[
            "expression_experiment.entity_assayed.primary_external_id"
        ].code
        == "alliance.gene_expression.entity_assayed_mismatch"
    )


def test_gene_expression_linkml_validator_reports_missing_expression_context():
    envelope = _converted_tmem67_envelope()
    payload = copy.deepcopy(envelope.extracted_objects[0].payload)
    payload["expression_pattern"].pop("when_expressed")
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
    payload = copy.deepcopy(envelope.extracted_objects[0].payload)
    payload["negated"] = True
    payload["where_expressed_statement"] = "metanephros nucleus"
    payload["expression_pattern"]["where_expressed"]["cellular_component"] = {
        "curie": "GO:0005634",
        "name": "nucleus",
    }

    assert validate_pending_gene_expression_envelope(
        _with_payload(envelope, payload)
    ) == ()


def test_gene_expression_conversion_rejects_missing_relation():
    raw_fixture = yaml.safe_load(
        GENE_EXPRESSION_OUTPUT_FIXTURE_PATH.read_text(encoding="utf-8")
    )
    output = raw_fixture["output"]
    output["curatable_objects"][0]["payload"].pop("relation")

    with pytest.raises(ValueError) as exc_info:
        gene_expression_extraction_output_to_pending_envelope(
            output,
            envelope_id="gene-expression-missing-relation",
        )

    assert "relation must be staged with its paper wording" in str(exc_info.value)


def test_gene_expression_conversion_keeps_unresolved_relation_for_its_validator():
    raw_fixture = yaml.safe_load(
        GENE_EXPRESSION_OUTPUT_FIXTURE_PATH.read_text(encoding="utf-8")
    )
    output = raw_fixture["output"]
    staged_relation = staged_value("relation", "was seen in")
    output["curatable_objects"][0]["payload"]["relation"] = staged_relation

    envelope = gene_expression_extraction_output_to_pending_envelope(
        output,
        envelope_id="gene-expression-unresolved-relation",
    )

    assert envelope.extracted_objects[0].payload["relation"] == staged_relation
    request = build_domain_validation_request(
        _active_binding_match(envelope, "relation_vocabulary_validation")
    ).request
    assert request is not None
    assert request.selected_inputs["term_name"] == "was seen in"


def test_gene_expression_conversion_accepts_cellular_component_only_site():
    raw_fixture = yaml.safe_load(
        GENE_EXPRESSION_OUTPUT_FIXTURE_PATH.read_text(encoding="utf-8")
    )
    output = raw_fixture["output"]
    payload = output["curatable_objects"][0]["payload"]
    payload["where_expressed_statement"] = "nucleus"
    staged_site = staged_value("expression_pattern.where_expressed.cellular_component", "nucleus")
    payload["expression_pattern"]["where_expressed"] = {"cellular_component": staged_site}

    converted = gene_expression_extraction_output_to_pending_envelope(
        output,
        envelope_id="gene-expression-cellular-component-only",
    )

    where_expressed = converted.extracted_objects[0].payload["expression_pattern"][
        "where_expressed"
    ]
    assert where_expressed == {"cellular_component": staged_site}
    assert validate_pending_gene_expression_envelope(converted) == ()


def test_gene_expression_conversion_retains_unresolved_site_with_blocking_finding():
    raw_fixture = yaml.safe_load(
        GENE_EXPRESSION_OUTPUT_FIXTURE_PATH.read_text(encoding="utf-8")
    )
    output = raw_fixture["output"]
    output["curatable_objects"][0]["payload"]["expression_pattern"][
        "where_expressed"
    ] = {}

    envelope = gene_expression_extraction_output_to_pending_envelope(
        output, envelope_id="gene-expression-missing-site",
    )
    finding = _finding_by_code(
        validate_pending_gene_expression_envelope(envelope),
        "alliance.gene_expression.anatomical_site_missing",
    )
    assert finding.severity is ValidationFindingSeverity.BLOCKER
    assert finding.details["blocking"] is True


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
            **staged_value("expression_pattern.where_expressed.cellular_component", "nucleus"),
            "mention": "   ",
        },
    }

    with pytest.raises(ValueError) as exc_info:
        gene_expression_extraction_output_to_pending_envelope(
            output,
            envelope_id="gene-expression-blank-nested-site",
        )

    assert "mention must be the non-empty paper wording" in str(exc_info.value)


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


def _gene_expression_condition_payload() -> dict[str, Any]:
    """A gene-expression annotation carrying one relation with TWO experimental conditions."""

    return {
        "where_expressed_statement": "PEF-1::GFP expression in the cilium",
        "relation": {"name": "is_expressed_in"},
        "data_provider": {"abbreviation": "WB"},
        "evidence_record_ids": ["evidence-1"],
        "evidence_records": [
            {
                "evidence_record_id": "evidence-1",
                "verified_quote": "expression after 3 pM rapamycin at 28C",
                "page": 3,
                "section": "Results",
                "chunk_id": "chunk-9",
            }
        ],
        "condition_relations": condition_relations_payload(
            [
                {
                    "condition_relation_type": "has_condition",
                    "conditions": [
                        {
                            "condition_class_mention": "chemical treatment",
                            "condition_class_curie": "ZECO:0000111",
                            "condition_chemical_mention": "rapamycin",
                            "condition_chemical_curie": "CHEBI:9168",
                            "condition_summary": "treated with 3 pM rapamycin",
                        },
                        {
                            "condition_class_mention": "temperature exposure",
                            "condition_class_curie": "ZECO:0000160",
                            "condition_summary": "reared at 28C",
                        },
                    ],
                }
            ]
        ),
    }


def test_gene_expression_pack_declares_condition_fields_multivalued_and_active():
    curatable_unit = _gene_expression_pack().metadata.object_definitions[0]
    assert curatable_unit.object_type == GENE_EXPRESSION_OBJECT_TYPE
    fields_by_path = {field.field_path: field for field in curatable_unit.fields}

    # Bare nested condition paths replace the legacy [0] convention.
    condition_fields = {
        "condition_relations",
        "condition_relations.conditions",
        "condition_relations.conditions.condition_class.curie",
        "condition_relations.conditions.condition_chemical.curie",
        "condition_relations.conditions.condition_taxon.curie",
        "condition_relations.condition_relation_type.name",
    }
    assert condition_fields.issubset(fields_by_path)
    assert not any("condition_relations[0]" in path for path in fields_by_path)

    for multivalued_path in ("condition_relations", "condition_relations.conditions"):
        field = fields_by_path[multivalued_path]
        assert field.metadata["multivalued"] is True
        assert field.multivalued is True

    conditions_field = fields_by_path["condition_relations.conditions"]
    assert conditions_field.metadata["validatable"] is True
    assert conditions_field.metadata["validator_state"] == "active"
    assert (
        conditions_field.metadata["validator_binding_id"]
        == "experimental_condition_validation"
    )
    relation_field = fields_by_path["condition_relations.condition_relation_type.name"]
    assert relation_field.metadata["validator_state"] == "active"
    assert (
        relation_field.metadata["validator_binding_id"]
        == "gene_expression_condition_relation_lookup"
    )


def test_gene_expression_condition_binding_scoped_and_shaped(monkeypatch):
    monkeypatch.delenv(
        "EXPERIMENTAL_CONDITION_VALIDATOR_BATCH_MAX_SIZE",
        raising=False,
    )
    raw_validator_bindings = _gene_expression_pack().metadata.metadata["validator_bindings"]
    bindings = {
        binding["binding_id"]: binding
        for binding in [
            *raw_validator_bindings["active"],
            *raw_validator_bindings["under_development"],
        ]
    }
    composite = bindings["experimental_condition_validation"]
    assert composite["validator_agent"]["agent_id"] == "experimental_condition_validation"
    # Scoped to the SINGLE curatable object type for this pack.
    assert composite["applies_to"]["object_types"] == [GENE_EXPRESSION_OBJECT_TYPE]
    assert composite["applies_to"]["field_paths"] == ["condition_relations.conditions"]
    # Each part's paper wording goes in as its name, the extractor's proposed CURIE as its CURIE.
    for part in ("condition_class", "condition_id", "condition_chemical"):
        assert composite["input_fields"][f"{part}_name"]["path"] == (
            f"condition_relations.conditions.{part}.mention"
        )
    for part in ("condition_class", "condition_id", "condition_chemical", "condition_taxon"):
        assert composite["input_fields"][f"{part}_curie"]["path"] == (
            f"condition_relations.conditions.{part}.proposed_curie"
        )
    assert composite["input_fields"]["condition_relation_type"]["path"] == (
        "condition_relations.condition_relation_type.mention"
    )
    assert composite["input_fields"]["condition_relation_type"]["context_only"] is True
    assert composite["input_fields"]["evidence_quotes"] == {
        "source": "evidence_record",
        "output": "quote_bundle",
        "field_path": "condition_relations.conditions.condition_summary",
        "allow_multiple": True,
        "required": False,
        "context_only": True,
    }
    # Every condition part the validator decides is written back (review S5).
    assert composite["expected_result_fields"] == {
        f"{part}_{key}": f"condition_relations.conditions.{part}.{key}"
        for part in ("condition_class", "condition_id", "condition_chemical", "condition_taxon")
        for key in ("curie", "name")
    }
    assert composite["batch"]["enabled"] is True
    assert composite["batch"]["family"] == "experimental_condition_validation"
    assert composite["batch"]["max_size"] == 4

    relation = bindings["gene_expression_condition_relation_lookup"]
    assert relation["applies_to"]["object_types"] == [GENE_EXPRESSION_OBJECT_TYPE]
    assert relation["applies_to"]["field_paths"] == [
        "condition_relations.condition_relation_type.name"
    ]


def test_gene_expression_condition_binding_fans_out_one_composite_per_condition():
    registry = _gene_expression_validation_registry()
    envelope = DomainEnvelope(
        envelope_id="gene-expression-conditions-env",
        domain_pack_id=GENE_EXPRESSION_DOMAIN_PACK_ID,
        extracted_objects=[
            CuratableObjectEnvelope(
                object_type=GENE_EXPRESSION_OBJECT_TYPE,
                pending_ref_id="gene-expression-conditions-1",
                payload=_gene_expression_condition_payload(),
            )
        ],
    )

    matches = registry.match_bindings(envelope, states=[ValidationBindingState.ACTIVE])

    composite_matches = [
        match for match in matches
        if match.binding.binding_id == "experimental_condition_validation"
    ]
    # 2 conditions -> 2 composite validations, each a distinct nested ExperimentalCondition.
    assert len(composite_matches) == 2
    assert [match.field_path for match in composite_matches] == [
        "condition_relations[0].conditions[0]",
        "condition_relations[0].conditions[1]",
    ]

    relation_matches = [
        match for match in matches
        if match.binding.binding_id == "gene_expression_condition_relation_lookup"
    ]
    assert len(relation_matches) == 1
    assert relation_matches[0].field_path == (
        "condition_relations[0].condition_relation_type.name"
    )

    requests = [build_domain_validation_request(match) for match in composite_matches]
    assert all(result.request is not None for result in requests)
    first, second = (result.request for result in requests)
    assert first.selected_inputs["condition_class_curie"] == "ZECO:0000111"
    assert first.selected_inputs["condition_class_name"] == "chemical treatment"
    assert first.selected_inputs["condition_chemical_curie"] == "CHEBI:9168"
    assert first.selected_inputs["condition_chemical_name"] == "rapamycin"
    assert first.selected_inputs["condition_relation_type"] == "has_condition"
    assert second.selected_inputs["condition_class_curie"] == "ZECO:0000160"
    assert "condition_chemical_curie" not in second.selected_inputs
    assert second.selected_inputs["condition_relation_type"] == "has_condition"
    assert first.evidence and first.evidence[0]["verified_quote"]


def _gene_expression_builder_evidence_records() -> list[dict[str, Any]]:
    return [
        {
            "evidence_record_id": "evidence-67598e5688f123c8",
            "entity": "pef-1",
            "verified_quote": "PEF-1::GFP expression was detected in the cilium.",
            "page": 3,
            "section": "Results",
            "chunk_id": "chunk-1",
            "envelope_target": {
                "pending_ref_id": "gene-expression-annotation-pef-1",
                "object_type": GENE_EXPRESSION_OBJECT_TYPE,
                "field_path": "expression_pattern.where_expressed.anatomical_structure",
            },
            "envelope_targets": [
                {
                    "pending_ref_id": "gene-expression-annotation-pef-1",
                    "object_type": GENE_EXPRESSION_OBJECT_TYPE,
                    "field_path": "expression_pattern.where_expressed.anatomical_structure",
                }
            ],
        }
    ]


def _gene_expression_builder_staged_fields(**overrides: Any) -> dict[str, Any]:
    """The staged-field shape the gene_expression builder writes after staging.

    Mirrors what ``_stage_payload_from_gene_expression_input`` produces: every value is the
    paper's wording, not yet validated (extraction never searches); validators supply each
    identity later. Tests append the flat staged ``condition_relations`` via overrides; the
    materializer rewrites those into the concrete nested annotation payload the active bindings
    read.
    """

    staged: dict[str, Any] = {
        "domain_pack_id": GENE_EXPRESSION_DOMAIN_PACK_ID,
        "object_type": GENE_EXPRESSION_OBJECT_TYPE,
        "pending_ref_id": "gene-expression-annotation-pef-1",
        "where_expressed_statement": "PEF-1::GFP expression in the cilium",
        "rationale": "Anti-GFP staining localizes the reporter to the cilium, not the cell body.",
        "data_provider": staged_value("data_provider", "WB"),
        "relation": staged_value("relation", "is_expressed_in"),
        "expression_annotation_subject": staged_value("expression_annotation_subject", "pef-1"),
        "single_reference": staged_value(
            "single_reference", "PMID 39550471", pmid="PMID:39550471"
        ),
        "expression_experiment": {
            "expression_assay_used": staged_value(
                "expression_experiment.expression_assay_used", "GFP reporter"
            ),
        },
        "expression_pattern": {
            "when_expressed": {
                "developmental_stage_start": staged_value(
                    "expression_pattern.when_expressed.developmental_stage_start", "L2 larvae"
                ),
            },
            "where_expressed": {
                "anatomical_structure": staged_value(
                    "expression_pattern.where_expressed.anatomical_structure", "cilia"
                ),
            },
        },
    }
    staged.update(overrides)
    return staged


def _staged_gene_expression_condition_relations() -> list[dict[str, Any]]:
    return [
        {
            "condition_relation_type": "has_condition",
            "conditions": [
                {
                    "condition_class_mention": "chemical treatment",
                    "condition_class_curie": "ZECO:0000111",
                    "condition_chemical_mention": "rapamycin",
                    "condition_chemical_curie": "CHEBI:9168",
                    "condition_summary": "treated with 3 pM rapamycin",
                },
                {
                    "condition_class_mention": "temperature exposure",
                    "condition_class_curie": "ZECO:0000160",
                    "condition_free_text": "28 degrees C",
                },
            ],
        }
    ]


def _materialize_gene_expression_candidate(staged_fields: dict[str, Any]) -> Any:
    workspace = ExtractionBuilderWorkspace(
        run_id="gene-expression-builder-conditions-run",
        domain_pack_id=GENE_EXPRESSION_DOMAIN_PACK_ID,
        agent_id="gene_expression_extraction",
    )
    workspace.upsert_candidate(
        candidate_id="gex-candidate-1",
        staged_fields=staged_fields,
        pending_ref_ids=["gene-expression-annotation-pef-1"],
        evidence_record_ids=["evidence-67598e5688f123c8"],
        resolver_selection_refs=[],
        status=CANDIDATE_STATUS_VALID,
    )
    return materialize_gene_expression_builder_state(
        workspace=workspace,
        candidate_ids=["gex-candidate-1"],
        evidence_records=_gene_expression_builder_evidence_records(),
    )


_RESIDUAL_BODY = "structures associated with the residual body"


@pytest.mark.parametrize("site_state", ["unresolved", "missing"])
def test_builder_preserves_unresolved_selectors_as_pending_blocked_observation(site_state):
    """Daniela's case: an unmatched anatomy term is kept UNRESOLVED with its paper wording."""

    staged = _gene_expression_builder_staged_fields()
    anatomy_path = "expression_pattern.where_expressed.anatomical_structure"
    if site_state == "unresolved":
        staged["expression_pattern"]["where_expressed"]["anatomical_structure"] = staged_value(
            anatomy_path, _RESIDUAL_BODY
        )
    else:
        staged["expression_pattern"].pop("where_expressed")
    result = _materialize_gene_expression_candidate(staged)
    assert result.ok, result.issues
    envelope = gene_expression_extraction_output_to_pending_envelope(
        result.payload, envelope_id="unresolved-gene-expression",
    )
    annotation = envelope.extracted_objects[0]
    assert annotation.status is CuratableObjectStatus.PENDING
    assert annotation.evidence_record_ids == ["evidence-67598e5688f123c8"]
    assert annotation.payload["where_expressed_statement"] == staged["where_expressed_statement"]
    # The subject is the paper's wording until its validator runs; the experiment copy matches.
    subject = annotation.payload["expression_annotation_subject"]
    assert (subject["mention"], subject["gene_symbol"], subject["resolution_state"]) == (
        "pef-1",
        None,
        "unresolved",
    )
    assert annotation.payload["expression_experiment"]["entity_assayed"] == subject

    findings = validate_pending_gene_expression_envelope(envelope)
    finding_codes = {finding.code for finding in findings}
    # Present-but-unresolved values belong to their validators, not to "missing" findings.
    assert "alliance.gene_expression.subject_gene_missing" not in finding_codes
    blockers = {
        blocker.field_path: blocker
        for blocker in gene_expression_export_blockers(annotation.model_dump(mode="json"))
    }
    for field_path in (
        "expression_annotation_subject",
        "expression_experiment.entity_assayed",
        "single_reference",
        "expression_experiment.single_reference",
    ):
        assert blockers[field_path].code == "alliance.gene_expression.value_unresolved"
    assert "expression_annotation_subject.primary_external_id" not in blockers
    if site_state == "unresolved":
        assert annotation.payload["expression_pattern"]["where_expressed"] == {
            "anatomical_structure": {
                "curie": None,
                "name": None,
                "mention": _RESIDUAL_BODY,
                "resolution_state": "unresolved",
                "lookup_outcome": "not_validated",
                "validator_explanation": "Not validated yet.",
            }
        }
        assert "alliance.gene_expression.anatomical_site_missing" not in finding_codes
        anatomy_blocker = blockers[anatomy_path]
        assert anatomy_blocker.code == "alliance.gene_expression.value_unresolved"
        assert anatomy_blocker.message == (
            "Anatomical structure is UNRESOLVED (lookup result: Not validated yet). "
            f"Paper wording: {_RESIDUAL_BODY!r}."
        )
        assert "expression_pattern.where_expressed" not in blockers
    else:
        assert annotation.payload["expression_pattern"]["where_expressed"] == {}
        finding = _finding_by_code(findings, "alliance.gene_expression.anatomical_site_missing")
        assert finding.severity is ValidationFindingSeverity.BLOCKER
        assert finding.details["blocking"] is True
        assert blockers["expression_pattern.where_expressed"].code == (
            "alliance.gene_expression.anatomical_site_required"
        )


def test_builder_preserves_missing_stage_with_submission_blocker():
    staged = _gene_expression_builder_staged_fields()
    staged["expression_pattern"].pop("when_expressed")
    staged["where_expressed_statement"] += " Stage hint: long-pec."
    result = _materialize_gene_expression_candidate(staged)
    assert result.ok, result.issues
    envelope = gene_expression_extraction_output_to_pending_envelope(
        result.payload, envelope_id="missing-stage-expression",
    )
    annotation = envelope.extracted_objects[0]
    assert annotation.status is CuratableObjectStatus.PENDING
    assert "when_expressed" not in annotation.payload["expression_pattern"]
    assert annotation.payload["where_expressed_statement"] == staged["where_expressed_statement"]
    assert annotation.evidence_record_ids == ["evidence-67598e5688f123c8"]
    # No stage term to fill it from: a curator enters the stage name.
    stage_path = "when_expressed_stage_name"
    finding = next(
        finding for finding in validate_pending_gene_expression_envelope(envelope)
        if finding.field_ref and finding.field_ref.field_path == stage_path
    )
    assert finding.code == "alliance.gene_expression.expression_context_missing"
    assert finding.severity is ValidationFindingSeverity.BLOCKER
    assert finding.details["blocking"] is True
    assert stage_path in {
        blocker.field_path
        for blocker in gene_expression_export_blockers(annotation.model_dump(mode="json"))
    }


def _exportable_candidate(stage: Mapping[str, Any] | None, stage_name: str | None) -> dict[str, Any]:
    """The tmem67 grounded annotation with the given stage term and stage name."""

    annotation = _load_gene_expression_fixture_pack(GENE_EXPRESSION_FIXTURE_PACK_ID).fixtures[
        0
    ].envelope.extracted_objects[0]
    payload = copy.deepcopy(annotation.payload)
    when_expressed = payload["expression_pattern"]["when_expressed"]
    when_expressed.pop("developmental_stage_start")
    if stage is not None:
        when_expressed["developmental_stage_start"] = dict(stage)
    payload.pop("when_expressed_stage_name")
    if stage_name is not None:
        payload["when_expressed_stage_name"] = stage_name
    candidate = _export_candidate(annotation.model_copy(update={"payload": payload}))
    candidate.update(
        {
            "envelope_revision": 1,
            "domain_pack_id": GENE_EXPRESSION_DOMAIN_PACK_ID,
            "projection_ref": {"envelope_id": "envelope-1", "object_id": candidate["object_id"]},
        }
    )
    return candidate


def _stage_name_column(candidate: Mapping[str, Any]) -> str:
    from agr_ai_curation_alliance.domain_packs.gene_expression.export import (
        _gene_expression_annotation_payload,
    )

    return _gene_expression_annotation_payload(candidate)["target_rows"][
        "geneexpressionannotation"
    ]["columns"]["whenexpressedstagename"]


def test_stated_stage_fills_the_stage_name_through_validation():
    """A stated stage that validates fills when_expressed_stage_name, which the export writes."""

    envelope = _with_staged_stage(_converted_tmem67_envelope(), "TS26 embryos")
    match = _active_binding_match(envelope, "expression_stage_ontology_validation")
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
                    status="resolved",
                    resolved_values={"curie": "FIXTURE_STAGE:00026", "name": "Theiler stage 26"},
                ),
            )
        ],
    )
    payload = result.envelope.extracted_objects[0].payload
    assert payload["when_expressed_stage_name"] == "Theiler stage 26"
    stage = payload["expression_pattern"]["when_expressed"]["developmental_stage_start"]
    assert stage["mention"] == "TS26 embryos"

    candidate = _exportable_candidate(stage, payload["when_expressed_stage_name"])
    assert gene_expression_export_blockers(candidate) == ()
    assert _stage_name_column(candidate) == "Theiler stage 26"


def test_stage_less_annotation_exports_after_a_curator_enters_the_stage_name():
    """Common for WB: no stage term; the curator-entered stage name is what exports."""

    assert {
        blocker.field_path: blocker.code
        for blocker in gene_expression_export_blockers(_exportable_candidate(None, None))
    } == {"when_expressed_stage_name": "alliance.gene_expression.required_field_missing"}

    edited = _exportable_candidate(None, "L4 larva")
    assert gene_expression_export_blockers(edited) == ()
    assert _stage_name_column(edited) == "L4 larva"


def test_stated_stage_that_stays_unresolved_blocks_export():
    stage = staged_value(
        "expression_pattern.when_expressed.developmental_stage_start", "TS26 embryos"
    )
    blockers = {
        blocker.field_path: blocker.code
        for blocker in gene_expression_export_blockers(_exportable_candidate(stage, None))
    }
    assert blockers == {
        "expression_pattern.when_expressed.developmental_stage_start": (
            "alliance.gene_expression.value_unresolved"
        ),
        "when_expressed_stage_name": "alliance.gene_expression.required_field_missing",
    }


def test_a_demoted_stage_blocks_export_instead_of_exporting_a_blank_stage_name():
    """Review S2: a stage the validator demotes empties the stage name (LinkML requires it).

    The export stops with curator-facing blockers on both; it never writes a row with an
    empty or stale stageName.
    """

    from agr_ai_curation_alliance.domain_packs.gene_expression import (
        GENE_EXPRESSION_TARGET_KEY,
        GeneExpressionExportAdapter,
        GeneExpressionExportValidationError,
    )
    from src.schemas.curation_workspace import SubmissionMode

    stage_path = "expression_pattern.when_expressed.developmental_stage_start"
    envelope = _with_staged_stage(_converted_tmem67_envelope(), "TS26 embryos")

    def validate(envelope: Any, **result: Any):
        match = _active_binding_match(envelope, "expression_stage_ontology_validation")
        request = build_domain_validation_request(match).request
        assert request is not None
        return materialize_validator_results_into_envelope(
            envelope,
            _gene_expression_pack().metadata,
            [
                ValidatorResultMaterializationInput(
                    match=match, request=request, result=_validator_result(request, **result)
                )
            ],
        ).envelope

    confirmed = validate(
        envelope,
        status="resolved",
        resolved_values={"curie": "FIXTURE_STAGE:00026", "name": "Theiler stage 26"},
    )
    assert confirmed.extracted_objects[0].payload["when_expressed_stage_name"] == "Theiler stage 26"

    demoted = validate(
        confirmed,
        status="unresolved",
        missing_expected_fields=[],
        lookup_outcome="not_found",
        candidates=[],
        curator_message="No stage term matches the paper's wording.",
    )
    payload = demoted.extracted_objects[0].payload
    stage = payload["expression_pattern"]["when_expressed"]["developmental_stage_start"]
    assert (stage["resolution_state"], stage["lookup_outcome"]) == ("unresolved", "not_found")
    assert payload["when_expressed_stage_name"] is None

    candidate = _exportable_candidate(stage, payload["when_expressed_stage_name"])
    blockers = {blocker.field_path: blocker for blocker in gene_expression_export_blockers(candidate)}
    assert {path: blocker.code for path, blocker in blockers.items()} == {
        stage_path: "alliance.gene_expression.value_unresolved",
        "when_expressed_stage_name": "alliance.gene_expression.required_field_missing",
    }
    assert blockers["when_expressed_stage_name"].message == (
        "The stage name is empty. It is filled in when the stage term is confirmed; if the "
        "stage term stays unconfirmed, or the paper states no stage, enter the stage name."
    )
    with pytest.raises(GeneExpressionExportValidationError):
        GeneExpressionExportAdapter().build_submission_payload(
            mode=SubmissionMode.EXPORT,
            target_key=GENE_EXPRESSION_TARGET_KEY,
            payload_context={
                "session_id": "session-gene-expression",
                "candidate_ids": [candidate["candidate_id"]],
                "candidate_count": 1,
                "candidates": [],
                "domain_envelope_candidates": [candidate],
                "domain_envelopes": [],
                "readiness_blockers": [],
                "warnings": [],
            },
        )


@pytest.mark.parametrize(
    ("field_path", "value"),
    [
        # Extraction never supplies an identity: a value staged resolved is rejected.
        (
            "expression_pattern.where_expressed.anatomical_structure",
            {
                "curie": "WBbt:0009999",
                "name": "unprovenanced",
                "mention": "cilia",
                "resolution_state": "resolved",
                "lookup_outcome": "matched",
                "validator_explanation": None,
            },
        ),
        # An unresolved value never carries an identity.
        (
            "expression_pattern.where_expressed.anatomical_structure",
            {
                "curie": "WBbt:0001234",
                "name": None,
                "mention": "cilia",
                "resolution_state": "unresolved",
                "lookup_outcome": "not_validated",
                "validator_explanation": "Not validated yet.",
            },
        ),
    ],
)
def test_builder_rejects_values_outside_the_resolution_contract(field_path, value):
    staged = _gene_expression_builder_staged_fields()
    staged["expression_pattern"]["where_expressed"]["anatomical_structure"] = value
    result = _materialize_gene_expression_candidate(staged)
    assert not result.ok
    assert field_path in str(result.issues)


def test_gene_expression_builder_rejects_object_level_only_evidence():
    staged_fields = _gene_expression_builder_staged_fields()
    workspace = ExtractionBuilderWorkspace(
        run_id="gene-expression-builder-missing-field-evidence-run",
        domain_pack_id=GENE_EXPRESSION_DOMAIN_PACK_ID,
        agent_id="gene_expression_extraction",
    )
    workspace.upsert_candidate(
        candidate_id="gex-candidate-1",
        staged_fields=staged_fields,
        pending_ref_ids=["gene-expression-annotation-pef-1"],
        evidence_record_ids=["evidence-67598e5688f123c8"],
        resolver_selection_refs=[],
        status=CANDIDATE_STATUS_VALID,
    )
    object_level_evidence = [
        {
            key: value
            for key, value in _gene_expression_builder_evidence_records()[0].items()
            if key not in {"envelope_target", "envelope_targets"}
        }
    ]

    result = materialize_gene_expression_builder_state(
        workspace=workspace,
        candidate_ids=["gex-candidate-1"],
        evidence_records=object_level_evidence,
    )

    assert not result.ok
    assert any(
        issue["reason"] == "missing_field_level_evidence_target"
        and issue["evidence_record_id"] == "evidence-67598e5688f123c8"
        and issue["pending_ref_id"] == "gene-expression-annotation-pef-1"
        for issue in result.issues
    )


def test_gene_expression_builder_materializes_staged_condition_relations():
    """Staged condition_relations land on the GeneExpressionAnnotation in validator shape.

    SDK-free: calls ``materialize_gene_expression_builder_state`` directly (mirrors the disease /
    phenotype builder materialization contract tests) rather than driving the strict stage tool
    through the SDK ``function_tool`` unwrap.
    """

    staged_fields = _gene_expression_builder_staged_fields(
        condition_relations=_staged_gene_expression_condition_relations(),
    )
    result = _materialize_gene_expression_candidate(staged_fields)
    assert result.ok, result.summary()

    annotation = next(
        obj
        for obj in result.payload["curatable_objects"]
        if obj["object_type"] == GENE_EXPRESSION_OBJECT_TYPE
    )
    relations = annotation["payload"]["condition_relations"]
    assert len(relations) == 1
    relation = relations[0]
    # Materialized by the shared Alliance condition helper, in the shape the active bindings read.
    assert relations == condition_relations_payload(_staged_gene_expression_condition_relations())
    assert relation["condition_relation_type"]["mention"] == "has_condition"
    conditions = relation["conditions"]
    assert len(conditions) == 2
    # Each part keeps the paper's wording as its mention; the extractor's CURIE is only a
    # proposal, and the part stays UNRESOLVED until the condition validator writes it back.
    condition_class = conditions[0]["condition_class"]
    assert (condition_class["mention"], condition_class["proposed_curie"]) == (
        "chemical treatment",
        "ZECO:0000111",
    )
    assert (condition_class["curie"], condition_class["name"]) == (None, None)
    assert condition_class["resolution_state"] == "unresolved"
    assert conditions[0]["condition_chemical"]["mention"] == "rapamycin"
    assert conditions[0]["condition_chemical"]["proposed_curie"] == "CHEBI:9168"
    assert conditions[0]["condition_summary"] == "treated with 3 pM rapamycin"
    assert conditions[1]["condition_class"]["mention"] == "temperature exposure"
    assert conditions[1]["condition_free_text"] == "28 degrees C"
    # Empty leaves are dropped (condition 2 had no chemical).
    assert "condition_chemical" not in conditions[1]

    evidence_record = result.payload["metadata"]["evidence_records"][0]
    assert evidence_record["envelope_targets"] == [
        {
            "pending_ref_id": "gene-expression-annotation-pef-1",
            "object_type": GENE_EXPRESSION_OBJECT_TYPE,
            "field_path": "expression_pattern.where_expressed.anatomical_structure",
        }
    ]


def test_every_condition_part_the_validator_matches_is_resolved():
    """Review S5: each stated condition part is written back, not only the condition class."""

    envelope = _converted_tmem67_envelope()
    payload = copy.deepcopy(envelope.extracted_objects[0].payload)
    payload["condition_relations"] = condition_relations_payload(
        [
            {
                "condition_relation_type": "has_condition",
                "conditions": [
                    {
                        "condition_class_mention": "chemical treatment",
                        "condition_class_curie": "ZECO:0000111",
                        "condition_id_mention": "rapamycin exposure",
                        "condition_id_curie": "XCO:0000108",
                        "condition_chemical_mention": "rapamycin",
                        "condition_chemical_curie": "CHEBI:9168",
                        "condition_taxon_mention": "E. coli",
                        "condition_taxon_curie": "NCBITaxon:562",
                        "condition_summary": "treated with 3 pM rapamycin",
                    }
                ],
            }
        ]
    )
    matched = {
        "condition_class": ("ZECO:0000111", "chemical treatment"),
        "condition_id": ("XCO:0000108", "rapamycin exposure"),
        "condition_chemical": ("CHEBI:9168", "rapamycin"),
        "condition_taxon": ("NCBITaxon:562", "Escherichia coli"),
    }
    result = _revalidate(
        _with_payload(envelope, payload),
        {
            "experimental_condition_validation": {
                f"{part}_{key}": value
                for part, (curie, name) in matched.items()
                for key, value in (("curie", curie), ("name", name))
            }
        },
    )

    [condition] = result.envelope.extracted_objects[0].payload["condition_relations"][0]["conditions"]
    for part, (curie, name) in matched.items():
        value = condition[part]
        assert (value["curie"], value["name"]) == (curie, name), part
        assert (value["resolution_state"], value["lookup_outcome"]) == ("resolved", "matched"), part
    assert condition["condition_taxon"]["mention"] == "E. coli"


def test_gene_expression_builder_omits_condition_relations_when_unstaged():
    """No conditions staged -> the annotation payload carries no condition_relations key."""

    result = _materialize_gene_expression_candidate(
        _gene_expression_builder_staged_fields()
    )
    assert result.ok, result.summary()

    annotation = next(
        obj
        for obj in result.payload["curatable_objects"]
        if obj["object_type"] == GENE_EXPRESSION_OBJECT_TYPE
    )
    assert "condition_relations" not in annotation["payload"]


# ---------------------------------------------------------------------------------------
# ALL-1283: extracted vs validated values (paper wording beside the validated identity).
# ---------------------------------------------------------------------------------------

_ANATOMY = "expression_pattern.where_expressed.anatomical_structure"


def _daniela_envelope():
    """An annotation whose anatomy term matched nothing: staged UNRESOLVED with its wording."""

    envelope = _converted_tmem67_envelope()
    payload = copy.deepcopy(envelope.extracted_objects[0].payload)
    payload["expression_pattern"]["where_expressed"]["anatomical_structure"] = staged_value(
        _ANATOMY, _RESIDUAL_BODY
    )
    return _with_payload(envelope, payload)


def _export_candidate(annotation: Any) -> dict[str, Any]:
    return {
        "candidate_id": "candidate-1",
        "envelope_id": "envelope-1",
        "object_id": annotation.pending_ref_id,
        "object_type": annotation.object_type,
        "schema_ref": {"schema_id": "alliance.linkml.GeneExpressionAnnotation"},
        "payload": annotation.payload,
        "object": annotation.model_dump(mode="json"),
    }


def test_daniela_unmatched_anatomy_is_validated_from_its_paper_wording():
    envelope = _daniela_envelope()
    match = _active_binding_match(envelope, "expression_anatomical_structure_validation")
    request = build_domain_validation_request(match).request
    assert request is not None
    # The validator runs on the unresolved value and reads the paper wording.
    assert request.selected_inputs["label"] == _RESIDUAL_BODY
    assert "curie" not in request.selected_inputs

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
                    lookup_outcome="not_found",
                    curator_message="No anatomy term matches this wording.",
                ),
            )
        ],
    )

    annotation = result.envelope.extracted_objects[0]
    anatomy = annotation.payload["expression_pattern"]["where_expressed"]["anatomical_structure"]
    assert anatomy == {
        "curie": None,
        "name": None,
        "mention": _RESIDUAL_BODY,
        "resolution_state": "unresolved",
        "lookup_outcome": "not_found",
        "validator_explanation": _VALIDATOR_EXPLANATION,
        "validator_curator_message": "No anatomy term matches this wording.",
    }
    blockers = {
        blocker.field_path: blocker
        for blocker in gene_expression_export_blockers(_export_candidate(annotation))
    }
    assert blockers[_ANATOMY].code == "alliance.gene_expression.value_unresolved"
    assert _RESIDUAL_BODY in blockers[_ANATOMY].message


def test_daniela_anatomy_resolved_later_keeps_the_paper_wording_and_unblocks_export():
    envelope = _daniela_envelope()
    match = _active_binding_match(envelope, "expression_anatomical_structure_validation")
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
                    status="resolved",
                    resolved_values={"curie": "WBbt:0006798", "name": "residual body"},
                ),
            )
        ],
    )

    annotation = result.envelope.extracted_objects[0]
    anatomy = annotation.payload["expression_pattern"]["where_expressed"]["anatomical_structure"]
    assert anatomy == _validated(_RESIDUAL_BODY, curie="WBbt:0006798", name="residual body")
    blocked_paths = {
        blocker.field_path
        for blocker in gene_expression_export_blockers(_export_candidate(annotation))
    }
    assert _ANATOMY not in blocked_paths


def test_daniela_anatomy_reads_unresolved_with_a_separate_paper_wording_column():
    from src.lib.flows.export_fields import PackagedExportSource, _pack_export_fields
    from src.lib.flows.value_display import display_text

    pack = _gene_expression_pack()
    source = PackagedExportSource(pack)
    anatomy = _daniela_envelope().extracted_objects[0].payload["expression_pattern"][
        "where_expressed"
    ]["anatomical_structure"]
    ref = f"object.pack.{GENE_EXPRESSION_OBJECT_TYPE}.{_ANATOMY}"

    assert display_text(anatomy, source.display_specs[ref]) == "UNRESOLVED"
    labels = {entry["ref"]: entry["label"] for entry in _pack_export_fields(pack)}
    assert labels[f"{ref}.mention"] == "Anatomical structure (paper wording)"
    assert labels[f"{ref}.lookup_outcome"] == "Anatomical structure (lookup result)"
    lookup_spec = source.display_specs[f"{ref}.lookup_outcome"]
    assert display_text("not_validated", lookup_spec) == "Not validated yet"
    resolved = _validated(_RESIDUAL_BODY, curie="WBbt:0006798", name="residual body")
    assert display_text(resolved, source.display_specs[ref]) == "residual body (WBbt:0006798)"


def test_pack_display_declarations_name_every_resolvable_value():
    from agr_ai_curation_alliance.domain_packs.gene_expression.resolvable import (
        GENE_EXPRESSION_RESOLVABLE_VALUES,
        declared_gene_expression_values,
    )

    declared = declared_gene_expression_values()
    # The builder's value table agrees with the pack's declarations (the pack adds
    # the mirror sources that also cover a copy).
    assert {
        path: (spec.id_key, spec.label_key, spec.mention_key, set(spec.identity_keys))
        for path, spec in declared.items()
    } == {
        value.field_path: (
            value.spec.id_key,
            value.spec.label_key,
            value.spec.mention_key,
            set(value.identity_keys),
        )
        for value in GENE_EXPRESSION_RESOLVABLE_VALUES
    }
    assert declared["expression_experiment.entity_assayed"].covered_by == (
        "expression_annotation_subject.primary_external_id",
        "expression_annotation_subject.gene_symbol",
    )
    fields = {
        field.field_path for field in _gene_expression_pack().metadata.object_definitions[0].fields
    }
    for field_path in declared:
        for key in (
            "mention",
            "resolution_state",
            "lookup_outcome",
            "validator_explanation",
            "validator_curator_message",
        ):
            assert f"{field_path}.{key}" in fields


def _contract_value_paths(value: Any, tokens: tuple[str | int, ...] = ()):
    """Every stored value in contract shape (it carries a resolution state), with its path."""

    if isinstance(value, Mapping):
        if "resolution_state" in value:
            yield tokens
        for key, child in value.items():
            yield from _contract_value_paths(child, (*tokens, key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _contract_value_paths(child, (*tokens, index))


def test_every_contract_value_the_builder_stages_is_declared():
    """A validator write into an undeclared contract value raises (core F2), so each is declared."""

    from src.lib.domain_packs.resolvable_values import declared_spec_for

    from agr_ai_curation_alliance.domain_packs.gene_expression.resolvable import (
        declared_gene_expression_values,
    )

    staged = _gene_expression_builder_staged_fields(
        condition_relations=[
            {
                "condition_relation_type": "has_condition",
                "conditions": [
                    {
                        "condition_class_mention": "chemical treatment",
                        "condition_class_curie": "ZECO:0000111",
                        "condition_id_mention": "rapamycin exposure",
                        "condition_id_curie": "XCO:0000108",
                        "condition_chemical_mention": "rapamycin",
                        "condition_chemical_curie": "CHEBI:9168",
                        "condition_taxon_mention": "E. coli",
                        "condition_taxon_curie": "NCBITaxon:562",
                        "condition_summary": "treated with 3 pM rapamycin",
                    }
                ],
            }
        ],
    )
    when = staged["expression_pattern"]["when_expressed"]
    where = staged["expression_pattern"]["where_expressed"]
    when["stage_uberon_slim_terms"] = [
        staged_value("expression_pattern.when_expressed.stage_uberon_slim_terms", "embryo stage")
    ]
    where["anatomical_structure_uberon_terms"] = [
        staged_value(
            "expression_pattern.where_expressed.anatomical_structure_uberon_terms", "nervous system"
        )
    ]
    where["cellular_component"] = staged_value(
        "expression_pattern.where_expressed.cellular_component", "cilium"
    )
    where["cellular_component_qualifiers"] = [
        staged_value(
            "expression_pattern.where_expressed.cellular_component_qualifiers", "base of cilium"
        )
    ]
    result = _materialize_gene_expression_candidate(staged)
    assert result.ok, result.summary()
    payload = next(
        obj["payload"]
        for obj in result.payload["curatable_objects"]
        if obj["object_type"] == GENE_EXPRESSION_OBJECT_TYPE
    )

    declared = declared_gene_expression_values()
    staged_paths = list(_contract_value_paths(payload))
    # Roots, mirrors, list elements and each condition component are all present.
    assert len(staged_paths) == len(
        {value.field_path for value in GENE_EXPRESSION_RESOLVABLE_VALUES}
    )
    undeclared = [tokens for tokens in staged_paths if declared_spec_for(declared, tokens) is None]
    assert undeclared == []

    # Every value the pack gives a resolution state is a declared resolvable value.
    state_fields = {
        field.field_path.removesuffix(".resolution_state")
        for field in _gene_expression_pack().metadata.object_definitions[0].fields
        if field.field_path.endswith(".resolution_state")
    }
    assert state_fields == set(declared)


_MIRROR_VALUES = {"expression_experiment.entity_assayed", "expression_experiment.single_reference"}


def test_curators_may_override_every_identity_but_not_the_wording_or_validation_state():
    """Identity leaves are editable (a curator override); the contract leaves stay read-only.

    Mirror copies follow their source's override, and the data provider stays protected
    because it routes the annotation to its member database.
    """

    from agr_ai_curation_alliance.domain_packs.gene_expression.resolvable import (
        declared_gene_expression_values,
    )

    fields = {
        field.field_path: field.metadata
        for field in _gene_expression_pack().metadata.object_definitions[0].fields
    }
    for value_path, spec in declared_gene_expression_values().items():
        # A protected value field would block a whole-value override (core).
        assert (fields[value_path].get("protected") is True) == (value_path == "data_provider"), value_path
        for key in spec.identity_keys:
            metadata = fields[f"{value_path}.{key}"]
            if value_path in _MIRROR_VALUES or value_path == "data_provider":
                assert metadata.get("editable") is not True, (value_path, key)
            else:
                assert metadata.get("editable") is True, (value_path, key)
                assert metadata["curator_action_note"].startswith("Curator override:")
        for key in (
            "mention",
            "resolution_state",
            "lookup_outcome",
            "validator_explanation",
            "validator_curator_message",
        ):
            metadata = fields[f"{value_path}.{key}"]
            assert metadata.get("protected") is True and metadata.get("editable") is not True
    assert fields["data_provider.abbreviation"]["protected"] is True


def _curator_patch(envelope: Any, field_path: str, value: Any, *, before: Any, identity: bool = False):
    from src.lib.domain_envelopes.patches import (
        EnvelopeFieldPatch,
        EnvelopeFieldPatchOperation,
        apply_curator_field_patch,
    )

    return apply_curator_field_patch(
        envelope,
        _gene_expression_pack(),
        EnvelopeFieldPatch(
            envelope_id=envelope.envelope_id,
            expected_revision=1,
            object_id=envelope.extracted_objects[0].pending_ref_id,
            field_path=field_path,
            before=before,
            value=value,
            operation=(
                EnvelopeFieldPatchOperation.REPLACE_IDENTITY
                if identity
                else EnvelopeFieldPatchOperation.REPLACE
            ),
        ),
        current_revision=1,
        actor_id="curator-7", actor_display_name="curator-7",
    )


def test_a_curator_override_resolves_daniela_anatomy_with_an_audit_event():
    from src.lib.domain_envelopes.patches import EnvelopeFieldPatchStatus
    from src.lib.domain_packs.resolvable_values import CURATOR_OVERRIDE_METADATA_KEY

    envelope = _daniela_envelope()
    result = _curator_patch(
        envelope,
        f"{_ANATOMY}.curie",
        {"curie": "EMAPA:17373", "name": "metanephros"},
        before={"curie": None, "name": None},
        identity=True,
    )
    assert result.status is EnvelopeFieldPatchStatus.ACCEPTED, result.errors

    annotation = result.envelope.extracted_objects[0]
    site = annotation.payload["expression_pattern"]["where_expressed"]["anatomical_structure"]
    assert (site["curie"], site["name"], site["mention"]) == ("EMAPA:17373", "metanephros", _RESIDUAL_BODY)
    assert (site["resolution_state"], site["lookup_outcome"]) == ("resolved", "curator_override")
    assert site["curator_override"]["actor_id"] == "curator-7"
    [event] = annotation.metadata[CURATOR_OVERRIDE_METADATA_KEY]
    assert (event["action"], event["field_path"], event["value_path"]) == (
        "override",
        f"{_ANATOMY}.curie",
        _ANATOMY,
    )
    assert event["previous"]["lookup_outcome"] == "not_validated"
    assert _ANATOMY not in {
        blocker.field_path for blocker in gene_expression_export_blockers(_export_candidate(annotation))
    }


def test_a_first_curator_override_needs_the_identifier_and_the_name_together():
    from src.lib.domain_envelopes.patches import EnvelopeFieldPatchStatus

    envelope = _daniela_envelope()
    for field_path, value, before, identity in (
        (f"{_ANATOMY}.curie", "EMAPA:17373", None, False),
        (f"{_ANATOMY}.curie", {"curie": "EMAPA:17373"}, {"curie": None}, True),
    ):
        result = _curator_patch(envelope, field_path, value, before=before, identity=identity)

        assert result.status is EnvelopeFieldPatchStatus.REJECTED
        assert result.errors == ("Enter both the identifier and the name for a curator override.",)
        assert result.envelope.extracted_objects[0].payload == envelope.extracted_objects[0].payload


def test_a_curator_override_changes_only_the_identity():
    from src.lib.domain_envelopes.patches import EnvelopeFieldPatchStatus

    envelope = _daniela_envelope()
    result = _curator_patch(
        envelope,
        f"{_ANATOMY}.curie",
        {"curie": "EMAPA:17373", "name": "metanephros", "mention": "residual body"},
        before={"curie": None, "name": None, "mention": _RESIDUAL_BODY},
        identity=True,
    )

    assert result.status is EnvelopeFieldPatchStatus.REJECTED
    assert result.errors == (
        f"field_path '{_ANATOMY}.curie' cannot change mention; only the identifier and name can be "
        "changed in a curator override",
    )
    assert result.envelope.extracted_objects[0].payload == envelope.extracted_objects[0].payload


def test_a_curator_override_of_the_subject_gene_carries_to_the_entity_assayed():
    from src.lib.domain_envelopes.patches import EnvelopeFieldPatchStatus

    envelope = _converted_tmem67_envelope()
    payload = copy.deepcopy(envelope.extracted_objects[0].payload)
    payload["expression_annotation_subject"] = staged_value("expression_annotation_subject", "Tmem67")
    payload["expression_experiment"]["entity_assayed"] = staged_value(
        "expression_experiment.entity_assayed", "Tmem67"
    )
    result = _curator_patch(
        _with_payload(envelope, payload),
        "expression_annotation_subject.primary_external_id",
        {"primary_external_id": "MGI:1923928", "gene_symbol": "Tmem67"},
        before={"primary_external_id": None, "gene_symbol": None},
        identity=True,
    )
    assert result.status is EnvelopeFieldPatchStatus.ACCEPTED, result.errors

    overridden = result.envelope.extracted_objects[0].payload
    for value in (
        overridden["expression_annotation_subject"],
        overridden["expression_experiment"]["entity_assayed"],
    ):
        assert (value["primary_external_id"], value["gene_symbol"]) == ("MGI:1923928", "Tmem67")
        assert (value["resolution_state"], value["lookup_outcome"]) == ("resolved", "curator_override")


_REFERENCE_IDENTITY = {
    "reference_id": 203506,
    "title": "Tmem67 expression in the developing kidney",
    "curie": "AGRKB:101000000232912",
}


def _experiment_reference_identity(payload: Mapping[str, Any]) -> dict[str, Any]:
    reference = payload["expression_experiment"]["single_reference"]
    return {key: reference.get(key) for key in _REFERENCE_IDENTITY}


def test_the_experiment_reference_copies_the_full_reference_identity():
    """N6: the experiment's copy holds the reference id, title and curie, never a stale title."""

    from src.lib.domain_envelopes.patches import EnvelopeFieldPatchStatus

    envelope = _converted_tmem67_envelope()
    payload = copy.deepcopy(envelope.extracted_objects[0].payload)
    payload["single_reference"] = staged_value(
        "single_reference", "PMID 203506", pmid="PMID:203506"
    )
    payload["expression_experiment"]["single_reference"] = staged_value(
        "expression_experiment.single_reference", "PMID 203506"
    )
    validated = _revalidate(
        _with_payload(envelope, payload), {"source_reference_validation": _REFERENCE_IDENTITY}
    ).envelope
    payload = validated.extracted_objects[0].payload
    assert _experiment_reference_identity(payload) == _REFERENCE_IDENTITY

    # A curator override (every identity key, the validated curie included) replaces the copy too.
    override = {
        "reference_id": 999001,
        "title": "Curator-chosen reference",
        "curie": "AGRKB:101000000999001",
    }
    result = _curator_patch(
        validated,
        "single_reference.reference_id",
        override,
        before={key: payload["single_reference"][key] for key in override},
        identity=True,
    )
    assert result.status is EnvelopeFieldPatchStatus.ACCEPTED, result.errors
    overridden = result.envelope.extracted_objects[0].payload
    assert _experiment_reference_identity(overridden) == override
    for reference in (overridden["single_reference"], overridden["expression_experiment"]["single_reference"]):
        assert (reference["resolution_state"], reference["lookup_outcome"]) == (
            "resolved",
            "curator_override",
        )


def _annotation_export_candidate(annotation: Any) -> dict[str, Any]:
    candidate = _export_candidate(annotation)
    candidate.update(
        {
            "envelope_revision": 1,
            "domain_pack_id": GENE_EXPRESSION_DOMAIN_PACK_ID,
            "projection_ref": {"envelope_id": "envelope-1", "object_id": candidate["object_id"]},
        }
    )
    return candidate


def test_an_override_without_the_vocabulary_blocks_only_where_the_export_needs_it():
    """B2: validated keys may be null in an override. The stage slim joins on its vocabulary,
    so a missing one is a curator-facing blocker; the relation joins on its term name alone."""

    from src.lib.domain_envelopes.patches import EnvelopeFieldPatchStatus
    from agr_ai_curation_alliance.domain_packs.gene_expression.export import (
        _gene_expression_annotation_payload,
    )

    # The grounded tmem67 annotation: every exported value resolved.
    envelope = _load_gene_expression_fixture_pack(GENE_EXPRESSION_FIXTURE_PACK_ID).fixtures[0].envelope
    payload = copy.deepcopy(envelope.extracted_objects[0].payload)
    slim_path = "expression_pattern.when_expressed.stage_uberon_slim_terms"
    payload["expression_pattern"]["when_expressed"]["stage_uberon_slim_terms"] = [
        staged_value(slim_path, "embryo stage")
    ]
    envelope = _with_payload(envelope, payload)
    relation = payload["relation"]
    result = _curator_patch(
        envelope,
        "relation.name",
        {"name": "is_expressed_in", "vocabulary": None, "id": None},
        before={key: relation.get(key) for key in ("name", "vocabulary", "id")},
        identity=True,
    )
    assert result.status is EnvelopeFieldPatchStatus.ACCEPTED, result.errors
    relation_only = copy.deepcopy(_annotation_export_candidate(result.envelope.extracted_objects[0]))
    relation_only["payload"]["expression_pattern"]["when_expressed"].pop("stage_uberon_slim_terms")
    # The relation is found by its term name: no blocker, and the export builds.
    assert gene_expression_export_blockers(relation_only) == ()
    relation_row = _gene_expression_annotation_payload(relation_only)
    assert "is_expressed_in" in str(relation_row)

    result = _curator_patch(
        result.envelope,
        f"{slim_path}[0].name",
        {"name": "UBERON:0000068", "vocabulary": None, "id": None},
        before={"name": None, "vocabulary": None, "id": None},
        identity=True,
    )
    assert result.status is EnvelopeFieldPatchStatus.ACCEPTED, result.errors
    blockers = gene_expression_export_blockers(
        _annotation_export_candidate(result.envelope.extracted_objects[0])
    )
    assert [(blocker.field_path, blocker.code) for blocker in blockers] == [
        (f"{slim_path}[0].vocabulary", "alliance.gene_expression.required_field_missing")
    ]
    assert blockers[0].message == (
        "Stage UBERON slim term has no vocabulary, which the export needs to find it in the "
        "curation database. Re-run validation, or enter it in a curator override."
    )


def test_a_stage_override_is_the_exported_stage_name():
    """S2: overriding the stage term also sets when_expressed_stage_name, which is exported."""

    from src.lib.domain_envelopes.patches import EnvelopeFieldPatchStatus

    envelope = _converted_tmem67_envelope()
    payload = copy.deepcopy(envelope.extracted_objects[0].payload)
    payload["expression_pattern"]["when_expressed"]["developmental_stage_start"] = staged_value(
        "expression_pattern.when_expressed.developmental_stage_start", "late embryos"
    )
    # Extraction never writes the stage name; only validation or a curator fills it.
    assert "when_expressed_stage_name" not in payload
    result = _curator_patch(
        _with_payload(envelope, payload),
        "expression_pattern.when_expressed.developmental_stage_start.curie",
        {"curie": "FIXTURE_STAGE:00026", "name": "Theiler stage 26"},
        before={"curie": None, "name": None},
        identity=True,
    )
    assert result.status is EnvelopeFieldPatchStatus.ACCEPTED, result.errors

    overridden = result.envelope.extracted_objects[0].payload
    assert overridden["when_expressed_stage_name"] == "Theiler stage 26"
    candidate = _exportable_candidate(
        overridden["expression_pattern"]["when_expressed"]["developmental_stage_start"],
        overridden["when_expressed_stage_name"],
    )
    assert "alliance.gene_expression.required_field_missing" not in {
        blocker.code for blocker in gene_expression_export_blockers(candidate)
    }
    assert gene_expression_export_blockers(candidate) == ()
    assert _stage_name_column(candidate) == "Theiler stage 26"


@pytest.mark.parametrize(
    ("field_path", "value", "before"),
    [
        (f"{_ANATOMY}.mention", "residual body", _RESIDUAL_BODY),
        (f"{_ANATOMY}.lookup_outcome", "matched", "not_validated"),
        (f"{_ANATOMY}.resolution_state", "resolved", "unresolved"),
        ("data_provider.abbreviation", "ZFIN", None),
    ],
)
def test_curators_cannot_edit_the_paper_wording_the_validation_state_or_the_data_provider(
    field_path, value, before
):
    from src.lib.domain_envelopes.patches import EnvelopeFieldPatchStatus

    envelope = _daniela_envelope()
    result = _curator_patch(envelope, field_path, value, before=before)

    assert result.status is EnvelopeFieldPatchStatus.REJECTED
    assert result.errors == (f"field_path '{field_path}' is protected",)
    assert result.envelope.extracted_objects[0].payload == envelope.extracted_objects[0].payload


def test_non_pinned_bindings_read_the_paper_wording_and_pinned_bindings_are_unchanged():
    bindings = {
        binding["binding_id"]: binding
        for binding in _gene_expression_pack().metadata.metadata["validator_bindings"]["active"]
    }
    wording_inputs = {
        "relation_vocabulary_validation": ("term_name", "relation.mention"),
        "data_provider_validation": ("abbreviation", "data_provider.mention"),
        "expression_stage_ontology_validation": (
            "label",
            "expression_pattern.when_expressed.developmental_stage_start.mention",
        ),
        "expression_assay_ontology_validation": (
            "label",
            "expression_experiment.expression_assay_used.mention",
        ),
        "expression_anatomical_structure_validation": ("label", f"{_ANATOMY}.mention"),
        "expression_cellular_component_validation": (
            "label",
            "expression_pattern.where_expressed.cellular_component.mention",
        ),
        "gene_expression_condition_relation_lookup": (
            "term_name",
            "condition_relations.condition_relation_type.mention",
        ),
    }
    for binding_id, (input_name, path) in wording_inputs.items():
        assert bindings[binding_id]["input_fields"][input_name]["path"] == path
    # The subject gene lookup reads the paper wording too (its saved-profile hash changes).
    assert bindings["subject_gene_validation"]["input_fields"]["gene_symbol"]["path"] == (
        "expression_annotation_subject.mention"
    )
    # Extraction never searches: a validator checks only an ID the paper states.
    for binding_id, value_path in (
        ("expression_assay_ontology_validation", "expression_experiment.expression_assay_used"),
        ("expression_anatomical_structure_validation", _ANATOMY),
        (
            "expression_anatomical_uberon_slim_validation",
            "expression_pattern.where_expressed.anatomical_structure_uberon_terms",
        ),
        ("expression_cellular_component_validation", "expression_pattern.where_expressed.cellular_component"),
        (
            "expression_cellular_component_qualifier_validation",
            "expression_pattern.where_expressed.cellular_component_qualifiers",
        ),
    ):
        assert bindings[binding_id]["input_fields"]["curie"]["path"] == f"{value_path}.proposed_curie"
    assert bindings["subject_gene_validation"]["input_fields"]["gene_id"]["path"] == (
        "expression_annotation_subject.proposed_primary_external_id"
    )
    # Species context is the extractor's data provider (from the paper or the species lookup).
    for binding_id, input_name in (
        ("subject_gene_validation", "data_provider"),
        ("expression_stage_ontology_validation", "data_provider"),
        ("expression_anatomical_structure_validation", "data_provider"),
        ("experimental_condition_validation", "data_provider_abbreviation"),
    ):
        assert bindings[binding_id]["input_fields"][input_name]["path"] == "data_provider.mention"
    # Hash-pinned and byte-identical: saved profile mappings pin sha256(binding.raw).
    from src.schemas.generic_extraction_profile import canonical_json

    assert hashlib.sha256(
        canonical_json(bindings["source_reference_validation"]).encode()
    ).hexdigest() == "3f1fe77b42cf5ddd8fb7121c56bd3f66b9473a8aeb14efc359c7a90528742e11"
    # No flat top-level scalar is a validator write target.
    expected_paths = [
        path
        for binding in bindings.values()
        for path in binding["expected_result_fields"].values()
    ]
    assert "when_expressed_stage_name" not in expected_paths


@pytest.mark.parametrize("covered", [True, False])
def test_legacy_annotation_exports_only_when_a_validator_event_covers_it(covered):
    fixture_pack = _load_gene_expression_fixture_pack(GENE_EXPRESSION_FIXTURE_PACK_ID)
    annotation = fixture_pack.fixtures[0].envelope.extracted_objects[0]
    legacy_payload = copy.deepcopy(annotation.payload)
    # Stored before ALL-1283: the anatomy carries an identity but no contract state.
    legacy_payload["expression_pattern"]["where_expressed"]["anatomical_structure"] = {
        "curie": "EMAPA:17373",
        "name": "metanephros",
    }
    metadata = dict(annotation.metadata)
    if covered:
        metadata["validator_resolved_value_materialization"] = [
            {"materialized_field_paths": [f"{_ANATOMY}.curie", f"{_ANATOMY}.name"]}
        ]
    legacy = annotation.model_copy(update={"payload": legacy_payload, "metadata": metadata})

    blockers = {
        blocker.field_path: blocker
        for blocker in gene_expression_export_blockers(_export_candidate(legacy))
    }
    if covered:
        assert _ANATOMY not in blockers
    else:
        assert blockers[_ANATOMY].code == "alliance.gene_expression.value_unresolved"
        assert "Legacy, unverified" in blockers[_ANATOMY].message
        assert "metanephros (EMAPA:17373) (legacy, unverified)" in blockers[_ANATOMY].message


def test_export_term_lookup_matches_only_the_validated_curie():
    """Regression (ALL-1283): no match-by-name fallback for a term without a CURIE."""

    from agr_ai_curation_alliance.domain_packs.gene_expression.export import _term_lookup

    assert _term_lookup(_grounded("cilia", curie="WBbt:0001234", name="cilium")) == {
        "table": "ontologyterm",
        "match": {"curie": "WBbt:0001234"},
        "projection": {"curie": "WBbt:0001234", "name": "cilium"},
    }
    assert _term_lookup(staged_value(_ANATOMY, "cilia")) is None
    assert _term_lookup({"name": "cilium"}) is None


def test_builder_staged_subject_is_looked_up_from_its_paper_wording_and_written_back():
    envelope = _converted_tmem67_envelope()
    subject = envelope.extracted_objects[0].payload["expression_annotation_subject"]
    assert (subject["mention"], subject["gene_symbol"]) == ("Tmem67", None)
    matches = [
        match
        for match in _gene_expression_validation_registry().match_bindings(
            envelope, states=[ValidationBindingState.ACTIVE]
        )
        if match.binding.binding_id == "subject_gene_validation"
    ]
    request = build_domain_validation_request(matches[0]).request
    assert request is not None
    assert request.selected_inputs == {"gene_symbol": "Tmem67", "data_provider": "MGI"}

    result = materialize_validator_results_into_envelope(
        envelope,
        _gene_expression_pack().metadata,
        [
            ValidatorResultMaterializationInput(
                match=matches[0],
                request=request,
                result=_validator_result(
                    request,
                    status="resolved",
                    resolved_values={"primary_external_id": "MGI:1923928", "gene_symbol": "Tmem67"},
                ),
            )
        ],
    )

    payload = result.envelope.extracted_objects[0].payload
    validated = _validated("Tmem67", primary_external_id="MGI:1923928", gene_symbol="Tmem67")
    assert payload["expression_annotation_subject"] == validated
    # The experiment's copy of the subject takes the same identity and state.
    assert payload["expression_experiment"]["entity_assayed"] == validated


# ---------------------------------------------------------------------------------------
# Records stored before ALL-1283 (read-time legacy rule; records are never rewritten).
# ---------------------------------------------------------------------------------------

_CONTRACT_KEYS = ("mention", "resolution_state", "lookup_outcome", "validator_explanation")


def _without_contract_keys(value: Any) -> Any:
    """A value as it was stored before ALL-1283: identity keys only."""

    if isinstance(value, list):
        return [_without_contract_keys(item) for item in value]
    if isinstance(value, Mapping):
        return {
            key: _without_contract_keys(item)
            for key, item in value.items()
            if key not in _CONTRACT_KEYS
        }
    return value


def _legacy_tmem67_envelope():
    """The grounded tmem67 annotation as stored before the contract, with an old-format-free stage."""

    envelope = _load_gene_expression_fixture_pack(GENE_EXPRESSION_FIXTURE_PACK_ID).fixtures[0].envelope
    annotation = envelope.extracted_objects[0]
    payload = _without_contract_keys(annotation.payload)
    return envelope.model_copy(
        update={"extracted_objects": [annotation.model_copy(update={"payload": payload})]}
    )


def _revalidate(envelope: Any, results: Mapping[str, Mapping[str, Any]]):
    """One matching validator result per binding (the first match of each)."""

    registry = _gene_expression_validation_registry()
    inputs = []
    for binding_id, resolved_values in results.items():
        match = next(
            match
            for match in registry.match_bindings(envelope, states=[ValidationBindingState.ACTIVE])
            if match.binding.binding_id == binding_id
        )
        request = build_domain_validation_request(match).request
        assert request is not None, binding_id
        inputs.append(
            ValidatorResultMaterializationInput(
                match=match,
                request=request,
                result=_validator_result(request, status="resolved", resolved_values=resolved_values),
            )
        )
    return materialize_validator_results_into_envelope(
        envelope, _gene_expression_pack().metadata, inputs
    )


def test_legacy_tmem67_record_exports_those_values_a_matching_revalidation_verified():
    """Core (i): a matching re-validation verifies a legacy record's values, mirrors included.

    Validators read the paper wording and any ID the paper states. A record stored before
    the contract has neither for its subject, terms, relation and data provider, so those
    cannot be looked up again and stay legacy until the paper is extracted again. The
    reference binding also reads the stored reference ID, so the references re-validate.
    """

    envelope = _legacy_tmem67_envelope()
    before = {
        blocker.field_path
        for blocker in gene_expression_export_blockers(_export_candidate(envelope.extracted_objects[0]))
    }
    verified_paths = {"single_reference", "expression_experiment.single_reference"}
    assert verified_paths <= before

    result = _revalidate(
        envelope,
        {
            "source_reference_validation": {
                "reference_id": 203506,
                "curie": "AGRKB:101000000232912",
                "title": "Tmem67 expression",
            },
        },
    )

    after = {
        blocker.field_path: blocker.code
        for blocker in gene_expression_export_blockers(
            _export_candidate(result.envelope.extracted_objects[0])
        )
    }
    assert verified_paths.isdisjoint(after)
    unverifiable = {
        "expression_annotation_subject": "subject_gene_validation",
        "data_provider": "data_provider_validation",
        "relation": "relation_vocabulary_validation",
        "expression_pattern.when_expressed.developmental_stage_start": (
            "expression_stage_ontology_validation"
        ),
        "expression_experiment.expression_assay_used": "expression_assay_ontology_validation",
        "expression_pattern.where_expressed.anatomical_structure": (
            "expression_anatomical_structure_validation"
        ),
        "expression_pattern.where_expressed.anatomical_structure_uberon_terms[0]": (
            "expression_anatomical_uberon_slim_validation"
        ),
    }
    assert after == {
        **{path: "alliance.gene_expression.value_unresolved" for path in unverifiable},
        # The experiment's copy of the subject follows the subject.
        "expression_experiment.entity_assayed": "alliance.gene_expression.value_unresolved",
    }
    # Their bindings read the paper wording (and a paper-stated ID), which a legacy record
    # never stored.
    registry = _gene_expression_validation_registry()
    for binding_id in unverifiable.values():
        assert all(
            build_domain_validation_request(match).request is None
            for match in registry.match_bindings(envelope, states=[ValidationBindingState.ACTIVE])
            if match.binding.binding_id == binding_id
        )


@pytest.mark.parametrize(
    ("stored", "term_name"),
    [
        ({"curie": "UBERON:0000068", "name": "embryo stage"}, "UBERON:0000068"),
        ("UBERON:0000113", "UBERON:0000113"),
        ({"curie": None, "name": "post embryonic, pre-adult"}, "post embryonic, pre-adult"),
        ("post embryonic, pre-adult", "post embryonic, pre-adult"),
        # A label that is not one of the vocabulary's terms names no term.
        ({"curie": None, "name": "embryo stage"}, None),
        ("embryo stage", None),
    ],
)
def test_previous_format_stage_slims_map_only_to_the_vocabulary_term_they_name(stored, term_name):
    """Review S9: the CURIE names the term; only the one non-CURIE term is read from a label."""

    from agr_ai_curation_alliance.domain_packs.gene_expression.legacy import legacy_display_payload

    payload = {"expression_pattern": {"when_expressed": {"stage_uberon_slim_terms": [stored]}}}
    [mapped] = legacy_display_payload(GENE_EXPRESSION_OBJECT_TYPE, payload)["expression_pattern"][
        "when_expressed"
    ]["stage_uberon_slim_terms"]
    assert mapped == {"name": term_name, "vocabulary": "Stage Uberon Slim Terms"}


def test_previous_format_stage_slims_display_as_legacy_vocabulary_terms_and_are_not_validatable():
    from agr_ai_curation_alliance.domain_packs.gene_expression.legacy import (
        PREVIOUS_FORMAT_FINDING_CODE,
        PREVIOUS_FORMAT_MESSAGE,
        GeneExpressionReviewRowMaterializer,
        legacy_display_payload,
    )
    from src.lib.curation_workspace.adapter_registry import (
        resolve_curation_legacy_display_mapper_by_id,
    )
    from src.lib.domain_packs.not_validatable import not_validatable_object_keys
    from src.lib.flows.export_fields import PackagedExportSource

    envelope = _legacy_tmem67_envelope()
    annotation = envelope.extracted_objects[0]
    payload = copy.deepcopy(annotation.payload)
    payload["expression_pattern"]["when_expressed"]["stage_uberon_slim_terms"] = [
        {"curie": "UBERON:0000068", "name": "embryo stage"}
    ]
    stored = copy.deepcopy(payload)
    annotation = annotation.model_copy(update={"payload": payload})
    envelope = envelope.model_copy(update={"extracted_objects": [annotation]})

    # The mapper only reshapes: the vocabulary term the slim names, with no state.
    [mapped] = legacy_display_payload(GENE_EXPRESSION_OBJECT_TYPE, payload)["expression_pattern"][
        "when_expressed"
    ]["stage_uberon_slim_terms"]
    assert mapped == {"name": "UBERON:0000068", "vocabulary": "Stage Uberon Slim Terms"}
    assert legacy_display_payload("Other", payload) is payload
    assert payload == stored  # the record itself is never rewritten

    # Exports read it through the registered mapper, then the shared legacy rule.
    assert (
        resolve_curation_legacy_display_mapper_by_id(GENE_EXPRESSION_DOMAIN_PACK_ID)
        is legacy_display_payload
    )
    item = PackagedExportSource(_gene_expression_pack()).effective_item(
        {
            "object_type": GENE_EXPRESSION_OBJECT_TYPE,
            "payload": payload,
            "metadata": dict(annotation.metadata),
        }
    )
    [slim] = item["payload"]["expression_pattern"]["when_expressed"]["stage_uberon_slim_terms"]
    assert slim["name"] is None
    assert slim["mention"] == "UBERON:0000068 (legacy, unverified)"
    assert (slim["resolution_state"], slim["lookup_outcome"]) == ("unresolved", "legacy_unverified")
    assert payload == stored

    findings = validate_pending_gene_expression_envelope(envelope)
    assert [(finding.code, finding.message) for finding in findings] == [
        (PREVIOUS_FORMAT_FINDING_CODE, PREVIOUS_FORMAT_MESSAGE)
    ]
    checked = envelope.model_copy(update={"validation_findings": list(findings)})
    assert not_validatable_object_keys(checked) & set(annotation.ref_keys())

    rows = GeneExpressionReviewRowMaterializer(metadata=_gene_expression_pack().metadata).materialize(
        envelope, envelope_revision=1
    )
    assert len(rows) == 1


# --- Fix wave: full-identity overrides export (B2) and the stage name follows (S2) ----------

_SLIM = "expression_pattern.when_expressed.stage_uberon_slim_terms"
_STAGE = "expression_pattern.when_expressed.developmental_stage_start"


def _override_blockers(payload_edit, field_path, identity):
    from src.lib.domain_envelopes.patches import EnvelopeFieldPatchStatus

    envelope = _converted_tmem67_envelope()
    payload = copy.deepcopy(envelope.extracted_objects[0].payload)
    payload_edit(payload)
    result = _curator_patch(
        _with_payload(envelope, payload), field_path, identity,
        before={key: None for key in identity}, identity=True,
    )
    assert result.status is EnvelopeFieldPatchStatus.ACCEPTED, result.errors
    annotation = result.envelope.extracted_objects[0]
    return annotation, {blocker.field_path for blocker in gene_expression_export_blockers(_export_candidate(annotation))}


def test_a_full_identity_override_of_the_relation_exports_its_vocabulary_term():
    def stage(payload):
        payload["relation"] = staged_value("relation", "expression was detected")

    annotation, blockers = _override_blockers(
        stage, "relation.name",
        {"name": "is_expressed_in", "vocabulary": "Expression Relation", "id": 12345},
    )
    relation = annotation.payload["relation"]
    assert (relation["name"], relation["vocabulary"], relation["id"]) == (
        "is_expressed_in", "Expression Relation", 12345)
    assert not any(path.startswith("relation") for path in blockers)


def test_a_full_identity_override_of_a_stage_slim_term_exports_it():
    def stage(payload):
        payload["expression_pattern"]["when_expressed"]["stage_uberon_slim_terms"] = [
            staged_value(_SLIM, "embryo")]

    annotation, blockers = _override_blockers(
        stage, f"{_SLIM}[0].name",
        {"name": "embryo stage", "vocabulary": _STAGE_SLIM_VOCABULARY, "id": 7},
    )
    [term] = annotation.payload["expression_pattern"]["when_expressed"]["stage_uberon_slim_terms"]
    assert (term["name"], term["vocabulary"], term["lookup_outcome"]) == (
        "embryo stage", _STAGE_SLIM_VOCABULARY, "curator_override")
    assert not any(path.startswith(_SLIM) for path in blockers)


def test_a_full_identity_override_of_the_reference_keeps_its_curie():
    def stage(payload):
        payload["single_reference"] = staged_value("single_reference", "PMID:1")

    annotation, _blockers = _override_blockers(
        stage, "single_reference.reference_id",
        {"reference_id": "12345", "title": "A paper", "curie": "PMID:1"},
    )
    reference = annotation.payload["single_reference"]
    # reference_id is declared an integer: the entry is stored as one.
    assert (reference["reference_id"], reference["title"], reference["curie"]) == (12345, "A paper", "PMID:1")


def test_a_first_override_that_leaves_out_a_validated_key_names_it():
    from src.lib.domain_envelopes.patches import EnvelopeFieldPatchStatus

    envelope = _converted_tmem67_envelope()
    payload = copy.deepcopy(envelope.extracted_objects[0].payload)
    payload["relation"] = staged_value("relation", "expression was detected")
    result = _curator_patch(_with_payload(envelope, payload), "relation.name", {"name": "is_expressed_in"},
                            before={"name": None}, identity=True)
    assert result.status is EnvelopeFieldPatchStatus.REJECTED
    assert result.errors == ("Enter the vocabulary and the id for a curator override.",)


def test_a_stage_override_updates_the_exported_stage_name():
    def stage(payload):
        payload["expression_pattern"]["when_expressed"]["developmental_stage_start"] = staged_value(
            _STAGE, "E14.5")
        payload["when_expressed_stage_name"] = None

    annotation, _blockers = _override_blockers(
        stage, f"{_STAGE}.curie", {"curie": "MmusDv:0000031", "name": "Theiler stage 22"},
    )
    assert annotation.payload["when_expressed_stage_name"] == "Theiler stage 22"


def test_a_demoted_stage_leaves_no_stale_stage_name():
    """Fix wave S3: the exported stage name follows the stage term through a decisive demotion."""

    envelope = _converted_tmem67_envelope()
    payload = copy.deepcopy(envelope.extracted_objects[0].payload)
    payload["expression_pattern"]["when_expressed"]["developmental_stage_start"] = staged_value(_STAGE, "TS9")
    payload["when_expressed_stage_name"] = None
    envelope = _with_payload(envelope, payload)

    def run(envelope, **result):
        match = _active_binding_match(envelope, "expression_stage_ontology_validation")
        request = build_domain_validation_request(match).request
        return materialize_validator_results_into_envelope(
            envelope, _gene_expression_pack().metadata,
            [ValidatorResultMaterializationInput(match=match, request=request,
                                                 result=_validator_result(request, **result))],
        ).envelope

    match = _active_binding_match(envelope, "expression_stage_ontology_validation")
    fields = build_domain_validation_request(match).request.expected_result_fields
    by_leaf = {path.rpartition(".")[2]: slot for slot, path in fields.items()}
    resolved = run(envelope, status="resolved",
                   resolved_values={by_leaf["curie"]: "MmusDv:0000009", by_leaf["name"]: "TS9"})
    assert resolved.extracted_objects[0].payload["when_expressed_stage_name"] == "TS9"

    demoted = run(resolved, status="unresolved", lookup_outcome="not_found",
                  missing_expected_fields=[]).extracted_objects[0].payload
    stage = demoted["expression_pattern"]["when_expressed"]["developmental_stage_start"]
    assert (stage["resolution_state"], stage["name"]) == ("unresolved", None)
    assert demoted["when_expressed_stage_name"] is None


def test_a_numeric_identity_entry_is_stored_as_a_number():
    """Fix wave 3 S1: the grid sends what the curator typed; an integer identity field stores an integer."""

    from src.lib.domain_envelopes.patches import EnvelopeFieldPatchStatus

    envelope = _converted_tmem67_envelope()
    payload = copy.deepcopy(envelope.extracted_objects[0].payload)
    payload["single_reference"] = staged_value("single_reference", "PMID:1")
    envelope = _with_payload(envelope, payload)
    identity = {"reference_id": " 203506 ", "title": "A paper", "curie": "PMID:1"}

    result = _curator_patch(envelope, "single_reference.reference_id", identity,
                            before={key: None for key in identity}, identity=True)
    assert result.status is EnvelopeFieldPatchStatus.ACCEPTED, result.errors
    reference = result.envelope.extracted_objects[0].payload["single_reference"]
    assert reference["reference_id"] == 203506 and isinstance(reference["reference_id"], int)

    refined = _curator_patch(result.envelope, "single_reference.reference_id", "203507", before=203506)
    assert refined.envelope.extracted_objects[0].payload["single_reference"]["reference_id"] == 203507

    rejected = _curator_patch(envelope, "single_reference.reference_id", {**identity, "reference_id": "20350x"},
                              before={key: None for key in identity}, identity=True)
    assert rejected.status is EnvelopeFieldPatchStatus.REJECTED
    assert rejected.errors == ("Enter a whole number for the reference ID.",)

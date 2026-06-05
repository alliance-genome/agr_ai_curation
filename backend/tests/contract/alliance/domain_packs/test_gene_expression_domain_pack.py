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
    DomainPackMetadataReviewRowMaterializer,
    ValidatorResultMaterializationInput,
    materialize_validator_results_into_envelope,
    project_evidence_anchor_projections,
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
STAGE_UBERON_SLIM_ALLOWED_CURIES = ["UBERON:0000068", "UBERON:0000113"]
STAGE_UBERON_SLIM_UNRESOLVED_LABELS = ["post embryonic, pre-adult"]
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
        for annotation in envelope.objects
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
                ],
            },
            {
                "id": "reference",
                "label": "Reference",
                "fields": [
                    "single_reference.reference_id",
                ],
            },
            {
                "id": "assay",
                "label": "Assay",
                "fields": [
                    "expression_experiment.expression_assay_used.curie",
                ],
            },
            {
                "id": "expression_site",
                "label": "Expression site",
                "fields": [
                    "where_expressed_statement",
                    "expression_pattern.where_expressed.anatomical_structure.curie",
                    "expression_pattern.where_expressed.cellular_component.curie",
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
                    "expression_pattern.when_expressed.stage_uberon_slim_terms",
                    "relation.name",
                    "data_provider.abbreviation",
                ],
            },
        ],
    }

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
        "expression_experiment.expression_assay_used",
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
    payload = copy.deepcopy(envelope.objects[0].payload)
    payload["expression_pattern"].setdefault("when_expressed", {})[
        "stage_uberon_slim_terms"
    ] = [{"curie": "UBERON:0000068", "name": "embryonic stage"}]
    payload["expression_pattern"]["where_expressed"][
        "cellular_component_qualifiers"
    ] = [{"curie": "GO:0005634", "name": "nucleus"}]
    envelope = _with_payload(envelope, payload)

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
    assert stage_request.input_selectors["data_provider"]["context_only"] is True
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
    assert anatomy_request.input_selectors["data_provider"]["context_only"] is True

    stage_uberon_match = _active_binding_match(
        envelope,
        "expression_stage_uberon_slim_validation",
    )
    stage_uberon_request = build_domain_validation_request(
        stage_uberon_match
    ).request
    assert stage_uberon_request is not None
    assert stage_uberon_request.selected_inputs == {
        "terms": [{"curie": "UBERON:0000068", "name": "embryonic stage"}],
        "ontology_family": "uberon",
        "ontology_term_type": "UBERONTerm",
        "lookup_method": "search_ontology_terms",
        "allowed_term_curies": STAGE_UBERON_SLIM_ALLOWED_CURIES,
        "unresolved_allowed_term_labels": STAGE_UBERON_SLIM_UNRESOLVED_LABELS,
    }
    assert stage_uberon_request.expected_result_fields == {
        "terms": "expression_pattern.when_expressed.stage_uberon_slim_terms"
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
        "terms": [{"curie": "UBERON:0001008", "name": "renal system"}],
        "ontology_family": "uberon",
        "ontology_term_type": "UBERONTerm",
        "lookup_method": "search_ontology_terms",
        "allowed_term_curies": ANATOMICAL_UBERON_SLIM_ALLOWED_CURIES,
    }
    assert anatomical_uberon_request.expected_result_fields == {
        "terms": "expression_pattern.where_expressed.anatomical_structure_uberon_terms"
    }

    qualifier_match = _active_binding_match(
        envelope,
        "expression_cellular_component_qualifier_validation",
    )
    qualifier_request = build_domain_validation_request(qualifier_match).request
    assert qualifier_request is not None
    assert qualifier_request.selected_inputs == {
        "terms": [{"curie": "GO:0005634", "name": "nucleus"}],
        "ontology_family": "go",
        "go_aspect": "cellular_component",
        "lookup_method": "search_go_terms",
    }
    assert qualifier_request.expected_result_fields == {
        "terms": (
            "expression_pattern.where_expressed.cellular_component_qualifiers"
        )
    }


def test_gene_expression_assay_materializes_from_validator_result():
    envelope = _converted_tmem67_envelope()
    payload = copy.deepcopy(envelope.objects[0].payload)
    payload["expression_experiment"]["expression_assay_used"] = {
        "name": "whole-mount in situ hybridization",
    }
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

    assay = result.envelope.objects[0].payload["expression_experiment"][
        "expression_assay_used"
    ]
    assert assay == {
        "curie": "MMO:0000658",
        "name": "whole mount in situ hybridization assay",
    }
    patch_event = result.envelope.objects[0].metadata[
        "validator_resolved_value_materialization"
    ][0]
    assert patch_event["original_values"] == {
        "expression_experiment.expression_assay_used.name": (
            "whole-mount in situ hybridization"
        )
    }
    resolved_field_ref = result.appended_findings[0].field_ref
    assert resolved_field_ref is not None
    assert resolved_field_ref.field_path == "expression_experiment.expression_assay_used"


def test_gene_expression_conversion_preserves_no_match_assay_label_for_validation():
    envelope = _converted_tmem67_envelope_with_raw_assay(
        {"name": "paper-only colorimetric staining assay"}
    )

    assay = envelope.objects[0].payload["expression_experiment"][
        "expression_assay_used"
    ]
    assert assay == {"name": "paper-only colorimetric staining assay"}
    finding = _finding_by_code(
        validate_pending_gene_expression_envelope(envelope),
        "alliance.gene_expression.assay_method_missing",
    )
    assert finding.field_ref.field_path == (
        "expression_experiment.expression_assay_used.curie"
    )
    assert finding.details["expected_selector"] == "MMO assay/method CURIE"

    match = _active_binding_match(envelope, "expression_assay_ontology_validation")
    request = build_domain_validation_request(match).request
    assert request is not None
    assert request.selected_inputs["label"] == "paper-only colorimetric staining assay"


def test_gene_expression_conversion_preserves_ambiguous_assay_candidates_for_validation():
    envelope = _converted_tmem67_envelope_with_raw_assay(
        {
            "candidates": [
                {"curie": "MMO:0000655", "name": "RT-PCR"},
                {"curie": "MMO:0000642", "name": "in situ hybridization"},
            ]
        }
    )

    assay = envelope.objects[0].payload["expression_experiment"][
        "expression_assay_used"
    ]
    assert assay == {
        "candidates": [
            {"curie": "MMO:0000655", "name": "RT-PCR"},
            {"curie": "MMO:0000642", "name": "in situ hybridization"},
        ]
    }
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
    assert result.envelope.objects[0].payload == envelope.objects[0].payload
    finding = result.appended_findings[0]
    assert finding.code == "domain_pack.validator_unresolved"
    assert finding.field_ref is not None
    assert finding.field_ref.field_path == (
        "expression_experiment.expression_assay_used"
    )
    assert finding.details["lookup_attempts"][0]["lookup_status"] == expected_status


def test_gene_expression_uberon_slim_metadata_carries_linkml_allowlists():
    fields_by_path = {
        field.field_path: field
        for field in _gene_expression_pack().metadata.object_definitions[0].fields
    }

    stage_helper = fields_by_path[
        "expression_pattern.when_expressed.stage_uberon_slim_terms"
    ].metadata["term_helper"]
    assert stage_helper["term_source"]["slim_membership"] == {
        "source": "alliance_linkml",
        "allowed_term_curies": STAGE_UBERON_SLIM_ALLOWED_CURIES,
        "unresolved_allowed_term_labels": STAGE_UBERON_SLIM_UNRESOLVED_LABELS,
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


def test_gene_expression_stage_uberon_slim_rejects_out_of_slim_materialization():
    envelope = _converted_tmem67_envelope()
    payload = copy.deepcopy(envelope.objects[0].payload)
    payload["expression_pattern"]["when_expressed"] = {
        "stage_uberon_slim_terms": [{"name": "adult"}]
    }
    envelope = _with_payload(envelope, payload)
    match = _active_binding_match(
        envelope,
        "expression_stage_uberon_slim_validation",
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
                    status="resolved",
                    resolved_values={
                        "terms": [
                            {
                                "curie": "UBERON:0000113",
                                "name": "post-embryonic organismal stage",
                            }
                        ]
                    },
                ),
            )
        ],
    )

    assert result.envelope.objects[0].payload["expression_pattern"]["when_expressed"][
        "stage_uberon_slim_terms"
    ] == [
        {
            "curie": "UBERON:0000113",
            "name": "post-embryonic organismal stage",
        }
    ]
    finding = result.appended_findings[0]
    assert finding.code == "domain_pack.validator_resolved"

    bad_result = materialize_validator_results_into_envelope(
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
                        "terms": [
                            {
                                "curie": "UBERON:0000105",
                                "name": "life cycle stage",
                            }
                        ]
                    },
                ),
            )
        ],
    )

    assert bad_result.envelope.objects[0].payload == envelope.objects[0].payload
    bad_finding = bad_result.appended_findings[0]
    assert bad_finding.code == "domain_pack.validator_materialization_invalid"
    assert "UBERON:0000105" in bad_finding.details["materialization_error"]


def test_gene_expression_anatomical_uberon_slim_rejects_out_of_slim_materialization():
    envelope = _converted_tmem67_envelope()
    payload = copy.deepcopy(envelope.objects[0].payload)
    payload["expression_pattern"]["where_expressed"][
        "anatomical_structure_uberon_terms"
    ] = [{"name": "kidney"}]
    envelope = _with_payload(envelope, payload)
    match = _active_binding_match(
        envelope,
        "expression_anatomical_uberon_slim_validation",
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
                    status="resolved",
                    resolved_values={
                        "terms": [
                            {
                                "curie": "UBERON:0002113",
                                "name": "kidney",
                            }
                        ]
                    },
                ),
            )
        ],
    )

    assert result.envelope.objects[0].payload == envelope.objects[0].payload
    finding = result.appended_findings[0]
    assert finding.code == "domain_pack.validator_materialization_invalid"
    assert "UBERON:0002113" in finding.details["materialization_error"]


def test_gene_expression_stage_uberon_slim_schema_allowed_non_uberon_stays_unresolved():
    envelope = _converted_tmem67_envelope()
    payload = copy.deepcopy(envelope.objects[0].payload)
    payload["expression_pattern"]["when_expressed"] = {
        "stage_uberon_slim_terms": [{"name": "post embryonic, pre-adult"}]
    }
    envelope = _with_payload(envelope, payload)
    match = _active_binding_match(
        envelope,
        "expression_stage_uberon_slim_validation",
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
                    status="resolved",
                    resolved_values={
                        "terms": [{"name": "post embryonic, pre-adult"}],
                    },
                ),
            )
        ],
    )

    assert result.envelope.objects[0].payload == envelope.objects[0].payload
    finding = result.appended_findings[0]
    assert finding.code == "domain_pack.validator_materialization_invalid"
    assert "post embryonic, pre-adult" in finding.details["materialization_error"]


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
    assert annotation.metadata_refs[0].metadata_path == "raw_mentions[0]"
    assert annotation.metadata_refs[1].metadata_path == "evidence_records[0]"
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
        obj for obj in envelope.objects if obj.object_type == GENE_EXPRESSION_OBJECT_TYPE
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
    assert rows[0].metadata["payload_path"] == "objects[0].payload"
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
    assert workspace_paths[:8] == [
        "expression_annotation_subject.primary_external_id",
        "expression_annotation_subject.gene_symbol",
        "single_reference.reference_id",
        "expression_experiment.expression_assay_used.curie",
        "where_expressed_statement",
        "expression_pattern.where_expressed.anatomical_structure.curie",
        "expression_pattern.where_expressed.cellular_component.curie",
        "expression_pattern.where_expressed.anatomical_structure_uberon_terms",
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
    assert len(envelope.objects) == 3
    assert validate_pending_gene_expression_envelope(envelope) == ()
    _assert_metadata_refs_resolve(envelope)

    payloads = {obj.pending_ref_id: obj.payload for obj in envelope.objects}
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
        "anatomical_structure": {
            "curie": "ZFA:0000260",
            "name": "pronephric duct",
        },
        "anatomical_structure_uberon_terms": [
            {"curie": "UBERON:0001008", "name": "renal system"}
        ],
    }
    assert cellular_component_only == {
        "cellular_component": {"curie": "GO:0005634", "name": "nucleus"}
    }
    assert mixed_negated["negated"] is True
    assert mixed_negated["uncertain"] is True
    assert mixed_negated["expression_pattern"]["where_expressed"] == {
        "anatomical_structure": {"curie": "ZFA:0000151", "name": "retina"},
        "cellular_component": {"curie": "GO:0005737", "name": "cytoplasm"},
        "cellular_component_qualifiers": [
            {"curie": "RO:0002170", "name": "present in"}
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
    annotation = envelope.objects[0]
    payload = copy.deepcopy(annotation.payload)
    del payload["expression_experiment"]["detection_reagents"]
    del payload["expression_experiment"]["specimen_genomic_model"]
    del payload["condition_relations"]
    changed_annotation = annotation.model_copy(update={"payload": payload})
    changed_envelope = envelope.model_copy(
        update={"objects": [changed_annotation, *envelope.objects[1:]]}
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


def test_gene_expression_linkml_validator_reports_experiment_projection_mismatch():
    envelope = _converted_tmem67_envelope()
    payload = copy.deepcopy(envelope.objects[0].payload)
    payload["expression_experiment"]["single_reference"]["reference_id"] = 999999
    payload["expression_experiment"]["entity_assayed"][
        "primary_external_id"
    ] = "MGI:9999999"

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
        "condition_relations": [
            {
                "condition_relation_type": {"name": "has_condition"},
                "conditions": [
                    {
                        "condition_class": {"curie": "ZECO:0000111"},
                        "condition_chemical": {"curie": "CHEBI:9168"},
                        "condition_summary": "treated with 3 pM rapamycin",
                    },
                    {
                        "condition_class": {"curie": "ZECO:0000160"},
                        "condition_summary": "reared at 28C",
                    },
                ],
            }
        ],
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


def test_gene_expression_condition_binding_scoped_and_shaped():
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
    assert composite["input_fields"]["condition_class_curie"]["path"] == (
        "condition_relations.conditions.condition_class.curie"
    )
    assert composite["input_fields"]["condition_relation_type"]["path"] == (
        "condition_relations.condition_relation_type.name"
    )
    assert composite["input_fields"]["condition_relation_type"]["context_only"] is True
    assert composite["expected_result_fields"] == {
        "condition_class_curie": "condition_relations.conditions.condition_class.curie"
    }
    assert "condition_id" not in composite["expected_result_fields"]
    assert composite["batch"]["enabled"] is True
    assert composite["batch"]["family"] == "experimental_condition_validation"

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
        objects=[
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
    assert first.selected_inputs["condition_chemical_curie"] == "CHEBI:9168"
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
        }
    ]


def _resolver_helper_selection(
    *,
    field_path: str,
    source_phrase: str,
    selected_value: str,
    term_source: dict[str, Any],
    selected_name: str | None = None,
    selected_curie: str | None = None,
) -> dict[str, Any]:
    """A resolve_domain_field_term provenance entry in the shape staging copies into staged state.

    Mirrors ``agr_curation._resolver_helper_selection`` so the materializer's resolver-provenance
    guard (``_has_helper_selection``) accepts the grounded controlled fields.
    """

    selection = {
        "field_path": field_path,
        "source_tool": "resolve_domain_field_term",
        "authority": "selector_evidence",
        "lookup_status": "success",
        "source_phrase": source_phrase,
        "term_source": term_source,
        "selected_value": selected_value,
        "selected_name": selected_name or selected_value,
    }
    if selected_curie:
        selection["selected_curie"] = selected_curie
    return selection


def _gene_expression_builder_staged_fields(**overrides: Any) -> dict[str, Any]:
    """Grounded staged-field shape the gene_expression builder writes after staging.

    Mirrors what ``_stage_payload_from_gene_expression_input`` produces: subject/reference plus the
    grounded controlled fields (relation / assay / stage / anatomy) and the matching
    ``metadata.provenance.helper_selections[]`` evidence the materializer's resolver-provenance guard
    requires. Tests append the flat staged ``condition_relations`` via overrides; the materializer
    rewrites those into the concrete nested annotation payload the active bindings read.
    """

    staged: dict[str, Any] = {
        "domain_pack_id": GENE_EXPRESSION_DOMAIN_PACK_ID,
        "object_type": GENE_EXPRESSION_OBJECT_TYPE,
        "pending_ref_id": "gene-expression-annotation-pef-1",
        "where_expressed_statement": "PEF-1::GFP expression in the cilium",
        "relation": {"name": "is_expressed_in"},
        "when_expressed_stage_name": "L2 larva",
        "expression_annotation_subject": {
            "source_phrase": "PEF-1::GFP",
            "gene_symbol": "pef-1",
            "primary_external_id": "WB:WBGene00000001",
        },
        "single_reference": {
            "source_phrase": "PMID 39550471",
            "reference_id": "PMID:39550471",
        },
        "expression_experiment": {
            "expression_assay_used": {"curie": "MMO:0000655", "name": "GFP reporter assay"},
        },
        "expression_pattern": {
            "where_expressed": {
                "anatomical_structure": {"curie": "WBbt:0001234", "name": "cilium"},
            },
        },
        "metadata": {
            "provenance": {
                "helper_selections": [
                    _resolver_helper_selection(
                        field_path="relation.name",
                        source_phrase="is_expressed_in",
                        selected_value="is_expressed_in",
                        term_source={
                            "kind": "controlled_vocabulary",
                            "vocabulary": "Expression Relation",
                        },
                    ),
                    _resolver_helper_selection(
                        field_path="expression_experiment.expression_assay_used",
                        source_phrase="GFP reporter assay",
                        selected_value="MMO:0000655",
                        selected_name="GFP reporter assay",
                        selected_curie="MMO:0000655",
                        term_source={"kind": "ontology", "ontology_family": "assay"},
                    ),
                    _resolver_helper_selection(
                        field_path="when_expressed_stage_name",
                        source_phrase="L2 larva",
                        selected_value="L2 larva",
                        term_source={"kind": "ontology", "ontology_family": "life_stage"},
                    ),
                    _resolver_helper_selection(
                        field_path="expression_pattern.where_expressed.anatomical_structure",
                        source_phrase="cilium",
                        selected_value="WBbt:0001234",
                        selected_name="cilium",
                        selected_curie="WBbt:0001234",
                        term_source={"kind": "ontology", "ontology_family": "anatomy"},
                    ),
                ]
            }
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
                    "condition_class_curie": "ZECO:0000111",
                    "condition_chemical_curie": "CHEBI:9168",
                    "condition_summary": "treated with 3 pM rapamycin",
                },
                {
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
        resolver_entry_lookup=None,
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
    # Materialized in the exact target shape the active bindings read.
    assert relation["condition_relation_type"] == {"name": "has_condition"}
    conditions = relation["conditions"]
    assert len(conditions) == 2
    assert conditions[0]["condition_class"] == {"curie": "ZECO:0000111"}
    assert conditions[0]["condition_chemical"] == {"curie": "CHEBI:9168"}
    assert conditions[0]["condition_summary"] == "treated with 3 pM rapamycin"
    assert conditions[1]["condition_class"] == {"curie": "ZECO:0000160"}
    assert conditions[1]["condition_free_text"] == "28 degrees C"
    # Empty leaves are dropped (condition 2 had no chemical).
    assert "condition_chemical" not in conditions[1]


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

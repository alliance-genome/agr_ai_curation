"""Contract tests for the Alliance gene-expression domain pack."""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml

from src.lib.domain_packs.loader import load_domain_fixture_pack
from src.schemas.domain_envelope import CuratableObjectStatus, field_path_exists
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
    assert validators["planned"]
    assert validators["blocked"] == []
    active_validator_ids = {
        validator["validator_id"] for validator in validators["active"]
    }
    blocked_validator_ids = {
        validator["validator_id"] for validator in validators["blocked"]
    }
    assert "gene_expression.extractor_output_migration" in active_validator_ids
    assert "gene_expression.export_submission_projection" in active_validator_ids
    assert "gene_expression.extractor_output_migration" not in blocked_validator_ids
    assert "gene_expression.export_submission_projection" not in blocked_validator_ids
    assert all(
        validator.get("blocked_by") != "ALL-407"
        for validator in validators["blocked"]
    )

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
    raw_fixture = yaml.safe_load(
        GENE_EXPRESSION_OUTPUT_FIXTURE_PATH.read_text(encoding="utf-8")
    )
    context = raw_fixture["envelope_context"]

    converted = gene_expression_extraction_output_to_pending_envelope(
        raw_fixture["output"],
        envelope_id=context["envelope_id"],
        document_id=context["document_id"],
        produced_by=context["produced_by"],
        produced_at=context["produced_at"],
    )

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

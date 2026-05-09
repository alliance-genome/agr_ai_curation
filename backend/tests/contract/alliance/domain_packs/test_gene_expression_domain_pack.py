"""Contract tests for the Alliance gene-expression domain pack."""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from src.lib.domain_packs.loader import load_domain_fixture_pack
from src.schemas.domain_envelope import CuratableObjectStatus, field_path_exists
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
    get_gene_expression_domain_pack_metadata_path,
)

from .test_alliance_domain_pack_scaffold import (  # noqa: E402
    _assert_range_exists,
    _assert_source_file_matches,
    _cache_schema,
    _iter_linkml_provider_refs,
    _load_linkml_index,
)


GENE_EXPRESSION_FIXTURE_PATH = (
    REPO_ROOT
    / "backend"
    / "tests"
    / "fixtures"
    / "domain_packs"
    / "gene_expression"
    / "tmem67_pending.yaml"
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

    validators = metadata.metadata["validators"]
    assert tuple(validators) == GENE_EXPRESSION_VALIDATOR_STATES
    assert all(validators[state] for state in GENE_EXPRESSION_VALIDATOR_STATES)

    provider_ref = metadata.metadata[PROVIDER_REFS_METADATA_KEY]["alliance_linkml"]
    assert provider_ref["commit"] == curatable_unit.schema_ref.version


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
    fixture_pack = load_domain_fixture_pack(GENE_EXPRESSION_FIXTURE_PATH)

    assert fixture_pack.fixture_pack_id == GENE_EXPRESSION_FIXTURE_PACK_ID
    assert fixture_pack.domain_pack_id == GENE_EXPRESSION_DOMAIN_PACK_ID
    fixture = fixture_pack.fixtures[0]
    envelope = fixture.envelope

    assert envelope.domain_pack_id == GENE_EXPRESSION_DOMAIN_PACK_ID
    assert len(envelope.objects) == 1

    annotation = envelope.objects[0]
    assert annotation.object_type == GENE_EXPRESSION_OBJECT_TYPE
    assert annotation.status is CuratableObjectStatus.PENDING
    assert annotation.object_refs == []
    assert annotation.field_refs == []
    assert annotation.payload["expression_annotation_subject"] == {
        "primary_external_id": "MGI:1923928",
        "gene_symbol": "Tmem67",
    }

    curatable_unit = _gene_expression_pack().metadata.object_definitions[0]
    missing_required_fields = [
        field.field_path
        for field in curatable_unit.fields
        if field.required and not field_path_exists(annotation.payload, field.field_path)
    ]
    assert missing_required_fields == []

    observed_keys = set(_iter_mapping_keys(envelope.model_dump(mode="python")))
    assert FORBIDDEN_LEGACY_COLLECTIONS.isdisjoint(observed_keys)


def test_tmem67_fixture_carries_anatomical_site_for_linkml_postcondition():
    fixture_pack = load_domain_fixture_pack(GENE_EXPRESSION_FIXTURE_PATH)
    annotation = fixture_pack.fixtures[0].envelope.objects[0]
    where_expressed = annotation.payload["expression_pattern"]["where_expressed"]

    assert (
        "anatomical_structure" in where_expressed
        or "cellular_component" in where_expressed
    )


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

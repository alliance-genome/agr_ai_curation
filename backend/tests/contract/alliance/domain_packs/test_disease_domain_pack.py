"""Contract tests for the Alliance disease domain pack."""

from __future__ import annotations

import copy
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from src.lib.domain_packs.loader import load_domain_fixture_pack
from src.schemas.domain_envelope import CuratableObjectStatus, field_path_exists
from src.schemas.domain_pack_metadata import DomainPackFieldType

REPO_ROOT = Path(__file__).resolve().parents[5]
ALLIANCE_PYTHON_SRC = REPO_ROOT / "packages" / "alliance" / "python" / "src"
if str(ALLIANCE_PYTHON_SRC) not in sys.path:
    sys.path.insert(0, str(ALLIANCE_PYTHON_SRC))

from agr_ai_curation_alliance.domain_packs import (  # noqa: E402
    ALLIANCE_LINKML_COMMIT,
    OBJECT_ROLE_METADATA_KEY,
    load_alliance_domain_pack_registry,
)
from agr_ai_curation_alliance.domain_packs.disease import (  # noqa: E402
    DISEASE_DOMAIN_PACK_ID,
    DISEASE_DOMAIN_PACK_VERSION,
    DISEASE_FIXTURE_PACK_ID,
    DISEASE_LINKML_SCHEMA_ID,
    DISEASE_MODEL_ID,
    DISEASE_OBJECT_TYPE,
    DISEASE_PENDING_ENVELOPE_VALIDATOR_BINDING_ID,
    DISEASE_VALIDATOR_STATES,
    get_disease_domain_pack_metadata_path,
    tool_verified_disease_output_to_pending_envelope,
    validate_pending_disease_envelope,
)

from .test_alliance_domain_pack_scaffold import (  # noqa: E402
    _assert_range_exists,
    _assert_source_file_matches,
    _cache_schema,
    _iter_linkml_provider_refs,
    _load_linkml_index,
)

DISEASE_PACK_DIR = REPO_ROOT / "packages" / "alliance" / "domain_packs" / "disease"
DISEASE_RAW_FIXTURE_PATH = (
    REPO_ROOT
    / "backend"
    / "tests"
    / "fixtures"
    / "domain_packs"
    / "disease"
    / "tool_verified_disease_output.yaml"
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


def _disease_pack():
    registry = load_alliance_domain_pack_registry()
    pack = registry.get_pack(DISEASE_DOMAIN_PACK_ID)
    assert pack is not None
    return pack


def _disease_object_definition():
    metadata = _disease_pack().metadata
    return next(
        item
        for item in metadata.object_definitions
        if item.object_type == DISEASE_OBJECT_TYPE
    )


def _load_raw_disease_fixture() -> dict[str, Any]:
    return yaml.safe_load(DISEASE_RAW_FIXTURE_PATH.read_text(encoding="utf-8"))


def _iter_mapping_keys(value: Any):
    if isinstance(value, Mapping):
        yield from value.keys()
        for child in value.values():
            yield from _iter_mapping_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_mapping_keys(child)


def test_disease_domain_pack_loads_from_alliance_registry():
    registry = load_alliance_domain_pack_registry()
    loaded_pack = registry.get_pack(DISEASE_DOMAIN_PACK_ID)

    assert registry.failed_packs == ()
    assert loaded_pack is not None
    assert loaded_pack.metadata_path == get_disease_domain_pack_metadata_path()
    assert loaded_pack.metadata.version == DISEASE_DOMAIN_PACK_VERSION

    fixture_ref = registry.get_fixture_pack_ref(
        DISEASE_DOMAIN_PACK_ID,
        DISEASE_FIXTURE_PACK_ID,
    )
    assert fixture_ref is not None
    assert fixture_ref.path == "fixtures/tool_verified.yaml"


def test_disease_pack_declares_pending_assertion_metadata_and_validator_states():
    metadata = _disease_pack().metadata
    disease_object = _disease_object_definition()
    object_metadata = disease_object.metadata

    assert metadata.pack_id == DISEASE_DOMAIN_PACK_ID
    assert [item.object_type for item in metadata.object_definitions] == [
        DISEASE_OBJECT_TYPE
    ]
    assert disease_object.model_ref == DISEASE_MODEL_ID
    assert disease_object.definition_state.value == "in_development"
    assert object_metadata[OBJECT_ROLE_METADATA_KEY] == "curatable_unit"
    assert object_metadata["assertion_kind"] == "pending_disease_assertion"

    write_behavior = object_metadata["write_behavior"]
    assert write_behavior["status"] == "blocked"
    assert write_behavior["blocked_by"] == "ALL-425"
    assert "insert public.diseaseannotation" in write_behavior["blocked_operations"]

    definition_state_summary = object_metadata["definition_state_summary"]
    assert "disease_annotation_object" in definition_state_summary["complete"]
    assert "condition_relations" in definition_state_summary["under_development"]
    assert "disease_annotation_subject" in definition_state_summary["blocked"]
    assert "single_reference" in definition_state_summary["blocked"]

    validators = metadata.metadata["validators"]
    assert tuple(validators) == DISEASE_VALIDATOR_STATES
    assert all(validators[state] for state in DISEASE_VALIDATOR_STATES)

    validator_bindings = metadata.metadata["validator_bindings"]
    binding_items = [
        *validator_bindings["active"],
        *validator_bindings["under_development"],
    ]
    binding_ids = {binding["binding_id"] for binding in binding_items}
    assert len(binding_items) == len(binding_ids)
    assert {
        "disease_pending_envelope_validator",
        "disease_ontology_term_lookup",
        "disease_relation_cv_lookup",
        "experimental_condition_validation",
        "disease_condition_relation_lookup",
        "disease_subject_materialization",
        "disease_reference_materialization",
        "disease_evidence_code_lookup",
        "disease_data_provider_lookup",
    } == binding_ids

    pending_validator = {
        binding["binding_id"]: binding
        for binding in validator_bindings["under_development"]
    }[DISEASE_PENDING_ENVELOPE_VALIDATOR_BINDING_ID]
    assert (
        pending_validator["binding_id"] == DISEASE_PENDING_ENVELOPE_VALIDATOR_BINDING_ID
    )
    assert pending_validator["display_name"] == "Data check"
    assert pending_validator["validator_agent"] == {
        "package_id": "agr.alliance",
        "agent_id": "disease_validation",
    }
    assert "must not dispatch" in pending_validator["state_explanation"]
    assert pending_validator["input_fields"] == {}
    assert pending_validator["expected_result_fields"] == {}
    assert pending_validator["applies_to"]["domain_pack_id"] == DISEASE_DOMAIN_PACK_ID
    assert pending_validator["applies_to"]["object_types"] == [DISEASE_OBJECT_TYPE]
    assert pending_validator["definition_state"] == "in_development"

    active_binding_ids = {
        binding["binding_id"] for binding in validator_bindings["active"]
    }
    assert {
        "disease_ontology_term_lookup",
        "disease_relation_cv_lookup",
        "disease_condition_relation_lookup",
        "disease_data_provider_lookup",
    }.issubset(active_binding_ids)
    assert {
        "disease_pending_envelope_validator",
        "experimental_condition_validation",
        "disease_subject_materialization",
        "disease_reference_materialization",
        "disease_evidence_code_lookup",
    } == {binding["binding_id"] for binding in validator_bindings["under_development"]}
    disease_term_binding = next(
        binding
        for binding in validator_bindings["active"]
        if binding["binding_id"] == "disease_ontology_term_lookup"
    )
    assert disease_term_binding["validator_agent"] == {
        "package_id": "agr.alliance",
        "agent_id": "ontology_term_validation",
    }
    assert disease_term_binding["input_fields"]["curie"] == {
        "source": "payload",
        "path": "disease_annotation_object.curie",
        "required": False,
    }
    assert disease_term_binding["input_fields"]["label"] == {
        "source": "payload",
        "path": "disease_annotation_object.name",
        "required": False,
    }
    assert disease_term_binding["input_fields"]["ontology_term_type"]["source"] == (
        "literal"
    )
    assert disease_term_binding["input_fields"]["ontology_term_type"]["value"] == (
        "DOTerm"
    )
    assert disease_term_binding["input_fields"]["accepted_prefixes"]["source"] == (
        "literal"
    )
    assert disease_term_binding["input_fields"]["accepted_prefixes"]["value"] == [
        "DOID"
    ]
    assert disease_term_binding["expected_result_fields"] == {
        "curie": "disease_annotation_object.curie",
        "label": "disease_annotation_object.name",
    }
    evidence_code_binding = next(
        binding
        for binding in validator_bindings["under_development"]
        if binding["binding_id"] == "disease_evidence_code_lookup"
    )
    assert evidence_code_binding["input_fields"]["curie"] == {
        "source": "payload",
        "path": "evidence_code_curies[0]",
        "required": False,
    }
    assert evidence_code_binding["input_fields"]["ontology_term_type"]["source"] == (
        "literal"
    )
    assert evidence_code_binding["input_fields"]["ontology_term_type"]["value"] == (
        "ECOTerm"
    )
    assert evidence_code_binding["input_fields"]["accepted_prefixes"]["source"] == (
        "literal"
    )
    assert evidence_code_binding["input_fields"]["accepted_prefixes"]["value"] == [
        "ECO"
    ]
    assert evidence_code_binding["expected_result_fields"] == {
        "curie": "evidence_code_curies[0]",
    }


def test_disease_pack_records_db_projection_and_representative_rows():
    object_metadata = _disease_object_definition().metadata
    db_projection = object_metadata["db_projection"]

    assert db_projection["base_table"] == "public.diseaseannotation"
    assert db_projection["concrete_tables"] == [
        "public.genediseaseannotation",
        "public.allelediseaseannotation",
        "public.agmdiseaseannotation",
    ]
    assert "public.experimentalcondition" in db_projection["condition_tables"]

    representative_rows = db_projection["representative_rows"]
    assert representative_rows == [
        {
            "row_id": 209127250,
            "subtype": "GeneDiseaseAnnotation",
            "subject_primary_external_id": "SGD:S000004578",
            "disease_curie": "DOID:0050730",
            "relation_name": "is_implicated_in",
            "data_provider": "SGD",
        },
        {
            "row_id": 209127267,
            "subtype": "AlleleDiseaseAnnotation",
            "subject_primary_external_id": "MGI:3689328",
            "disease_curie": "DOID:0111789",
            "relation_name": "is_implicated_in",
            "data_provider": "MGI",
        },
        {
            "row_id": 209127402,
            "subtype": "AGMDiseaseAnnotation",
            "subject_primary_external_id": "MGI:8308849",
            "disease_curie": "DOID:0111441",
            "relation_name": "is_model_of",
            "data_provider": "MGI",
        },
    ]
    assert db_projection["representative_condition_row"] == {
        "diseaseannotation_id": 209127194,
        "condition_relation_type": "has_condition",
        "condition_class_curie": "ZECO:0000111",
        "condition_id_curie": "ZECO:0000238",
        "condition_chemical_curie": "CHEBI:6909",
    }

    curation_db_ref = object_metadata["provider_refs"]["alliance_curation_db"]
    inspected_table_names = {
        table_name.removeprefix("public.")
        for table_name in curation_db_ref["inspected_tables"]
    }
    assert set(curation_db_ref["row_counts"]) == inspected_table_names
    assert (
        curation_db_ref["row_counts"]["conditionrelation_experimentalcondition"]
        == 23532
    )


def test_disease_pack_declares_validatable_disease_and_condition_fields():
    disease_object = _disease_object_definition()
    fields_by_path = {field.field_path: field for field in disease_object.fields}
    raw_validator_bindings = _disease_pack().metadata.metadata["validator_bindings"]
    validator_bindings = {
        binding["binding_id"]: binding
        for binding in [
            *raw_validator_bindings["active"],
            *raw_validator_bindings["under_development"],
        ]
    }
    referenced_validator_binding_ids = {
        field.metadata["validator_binding_id"]
        for field in disease_object.fields
        if field.metadata.get("validatable") is True
    }
    assert referenced_validator_binding_ids <= set(validator_bindings)

    required_fields = {
        field.field_path for field in disease_object.fields if field.required
    }
    assert required_fields == {
        "mention",
        "disease_annotation_object",
        "disease_annotation_object.name",
        "role",
        "confidence",
        "evidence_record_ids",
        "evidence_records",
    }
    assert fields_by_path["role"].field_type is DomainPackFieldType.ENUM
    assert fields_by_path["role"].enum_ref == "DiseaseAssertionRole"
    assert fields_by_path["confidence"].enum_ref == "DiseaseAssertionConfidence"

    disease_curie = fields_by_path["disease_annotation_object.curie"]
    assert disease_curie.metadata["validatable"] is True
    assert (
        disease_curie.metadata["validator_binding_id"] == "disease_ontology_term_lookup"
    )
    assert disease_curie.metadata["validator_state"] == "active"

    condition_fields = {
        "condition_relations",
        "condition_relations[0].conditions[0].condition_class.curie",
        "condition_relations[0].conditions[0].condition_chemical.curie",
        "condition_relations[0].conditions[0].condition_taxon.curie",
    }
    assert condition_fields.issubset(fields_by_path)
    for field_path in condition_fields:
        field = fields_by_path[field_path]
        assert field.definition_state.value == "in_development"
        assert field.metadata["validatable"] is True
        assert field.metadata["validator_state"] == "under_development"
        binding = validator_bindings[field.metadata["validator_binding_id"]]
        assert binding["state_explanation"]
    condition_relation_type = fields_by_path[
        "condition_relations[0].condition_relation_type.name"
    ]
    assert condition_relation_type.metadata["validator_state"] == "active"
    assert (
        condition_relation_type.metadata["validator_binding_id"]
        == "disease_condition_relation_lookup"
    )
    composite_binding = validator_bindings["experimental_condition_validation"]
    assert composite_binding["validator_agent"] == {
        "package_id": "agr.alliance",
        "agent_id": "experimental_condition_validation",
    }
    assert composite_binding["input_fields"]["evidence_quote"] == {
        "source": "evidence_record",
        "path": "verified_quote",
        "required": False,
        "context_only": True,
    }
    assert composite_binding["expected_result_fields"] == {
        "condition_id": "ExperimentalCondition.condition_id",
        "normalized_components": "ExperimentalCondition.components",
    }

    blocked_fields = {
        "disease_annotation_subject",
        "single_reference",
        "evidence_code_curies[0]",
    }
    for field_path in blocked_fields:
        field = fields_by_path[field_path]
        assert field.definition_state.value == "in_development"
        assert field.metadata["validator_state"] == "under_development"
        assert field.metadata["definition_state_category"] == "blocked"
        binding = validator_bindings[field.metadata["validator_binding_id"]]
        assert binding["state_explanation"]

    for field_path in ("data_provider", "data_provider.abbreviation"):
        field = fields_by_path[field_path]
        assert field.definition_state.value == "in_development"
        assert field.metadata["validator_state"] == "active"
        assert field.metadata["definition_state_category"] == "under_development"
        binding = validator_bindings[field.metadata["validator_binding_id"]]
        assert binding["validator_agent"]["agent_id"] == "data_provider_validation"


def test_disease_pack_linkml_class_slot_attribute_and_range_refs_exist(tmp_path: Path):
    schema_cache_dir, _env_values = _cache_schema(tmp_path)
    index = _load_linkml_index(schema_cache_dir)
    metadata = _disease_pack().metadata

    provider_refs = tuple(_iter_linkml_provider_refs(metadata))
    assert provider_refs

    for provider_ref in provider_refs:
        assert provider_ref["commit"] == ALLIANCE_LINKML_COMMIT
        assert provider_ref["schema_ref"] == "alliance.linkml"

        class_name = provider_ref.get("class")
        if class_name is not None:
            assert (
                class_name in index["classes"]
            ), f"LinkML class {class_name} is missing from pinned schema"
            actual_file, class_definition = index["classes"][class_name]
            if "slot" not in provider_ref:
                _assert_source_file_matches(
                    provider_ref=provider_ref,
                    actual_file=actual_file,
                    ref_kind="class",
                    ref_name=class_name,
                )

            attribute_name = provider_ref.get("attribute")
            if attribute_name is not None:
                attributes = class_definition.get("attributes") or {}
                assert attribute_name in attributes
                if "range" in provider_ref:
                    assert attributes[attribute_name]["range"] == provider_ref["range"]

        slot_name = provider_ref.get("slot")
        if slot_name is not None:
            assert (
                slot_name in index["slots"]
            ), f"LinkML slot {slot_name} is missing from pinned schema"
            actual_file, _definition = index["slots"][slot_name]
            _assert_source_file_matches(
                provider_ref=provider_ref,
                actual_file=actual_file,
                ref_kind="slot",
                ref_name=slot_name,
            )

        _assert_range_exists(index, provider_ref)


def test_tool_verified_disease_fixture_converts_to_pending_envelope():
    raw_fixture = _load_raw_disease_fixture()
    converted_envelope = tool_verified_disease_output_to_pending_envelope(raw_fixture)

    fixture_ref = load_alliance_domain_pack_registry().get_fixture_pack_ref(
        DISEASE_DOMAIN_PACK_ID,
        DISEASE_FIXTURE_PACK_ID,
    )
    assert fixture_ref is not None
    fixture_pack = load_domain_fixture_pack(DISEASE_PACK_DIR / fixture_ref.path)
    expected_envelope = fixture_pack.fixtures[0].envelope

    assert converted_envelope.model_dump(mode="json", exclude_none=True) == (
        expected_envelope.model_dump(mode="json", exclude_none=True)
    )
    assert validate_pending_disease_envelope(converted_envelope) == ()
    assert converted_envelope.domain_pack_id == DISEASE_DOMAIN_PACK_ID
    assert converted_envelope.schema_ref.schema_id == DISEASE_LINKML_SCHEMA_ID
    assert converted_envelope.objects[0].pending_ref_id == "disease-assertion-1"
    assert converted_envelope.objects[0].status is CuratableObjectStatus.PENDING
    assert converted_envelope.objects[0].metadata[OBJECT_ROLE_METADATA_KEY] == (
        "curatable_unit"
    )

    payload = converted_envelope.objects[0].payload
    assert payload["disease_annotation_object"] == {
        "curie": "DOID:0050434",
        "name": "Andersen-Tawil syndrome",
    }
    assert payload["data_provider"] == {"abbreviation": "ZFIN"}
    assert field_path_exists(payload, "evidence_records[0].verified_quote")
    assert payload["disease_relation_name"] == "is_model_of"


def test_converted_disease_envelope_omits_legacy_semantic_stores():
    raw_fixture = _load_raw_disease_fixture()
    converted_envelope = tool_verified_disease_output_to_pending_envelope(raw_fixture)

    observed_keys = set(
        _iter_mapping_keys(converted_envelope.model_dump(mode="python"))
    )
    assert FORBIDDEN_LEGACY_COLLECTIONS.isdisjoint(observed_keys)


def test_pending_disease_validator_rejects_legacy_semantic_store():
    raw_fixture = _load_raw_disease_fixture()
    converted_envelope = tool_verified_disease_output_to_pending_envelope(raw_fixture)
    legacy_envelope = converted_envelope.model_copy(
        update={"metadata": {**converted_envelope.metadata, "diseases": []}},
    )

    findings = validate_pending_disease_envelope(legacy_envelope)
    assert [finding.code for finding in findings] == [
        "alliance.disease.legacy_semantic_store_present"
    ]


def test_tool_verified_disease_fixture_rejects_malformed_required_data():
    raw_fixture = _load_raw_disease_fixture()

    missing_assertions = copy.deepcopy(raw_fixture)
    missing_assertions.pop("disease_assertions")
    with pytest.raises(ValidationError, match="disease_assertions"):
        tool_verified_disease_output_to_pending_envelope(missing_assertions)

    blank_note = copy.deepcopy(raw_fixture)
    blank_note["normalization_notes"].append("  ")
    with pytest.raises(ValidationError, match="normalization_notes"):
        tool_verified_disease_output_to_pending_envelope(blank_note)

    unknown_evidence = copy.deepcopy(raw_fixture)
    unknown_evidence["disease_assertions"][0]["evidence_record_ids"].append("missing")
    with pytest.raises(ValidationError, match="unknown evidence_record_ids"):
        tool_verified_disease_output_to_pending_envelope(unknown_evidence)

    missing_subject_type = copy.deepcopy(raw_fixture)
    missing_subject_type["disease_assertions"][0]["subject"].pop("subject_type")
    with pytest.raises(ValidationError, match="subject_type"):
        tool_verified_disease_output_to_pending_envelope(missing_subject_type)

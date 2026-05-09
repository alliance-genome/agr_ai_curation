"""Contract tests for the Alliance chemical-condition domain pack."""

from __future__ import annotations

import copy
import importlib
import sys
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from src.schemas.domain_envelope import CuratableObjectStatus
from src.schemas.domain_pack_metadata import DomainPackFieldType


REPO_ROOT = Path(__file__).resolve().parents[5]
ALLIANCE_PYTHON_SRC = REPO_ROOT / "packages" / "alliance" / "python" / "src"
if str(ALLIANCE_PYTHON_SRC) not in sys.path:
    sys.path.insert(0, str(ALLIANCE_PYTHON_SRC))

from agr_ai_curation_alliance.domain_packs import (  # noqa: E402
    ALLIANCE_LINKML_COMMIT,
    ALLIANCE_LINKML_PROVIDER_KEY,
    load_alliance_domain_pack_registry,
)
from agr_ai_curation_alliance.domain_packs.chemical_condition import (  # noqa: E402
    CHEMICAL_CONDITION_DOMAIN_PACK_ID,
    CHEMICAL_CONDITION_DOMAIN_PACK_VERSION,
    CHEMICAL_CONDITION_EXPORT_CONTEXT_FIELDS,
    CHEMICAL_CONDITION_MODEL_ID,
    CHEMICAL_CONDITION_OBJECT_TYPE,
    CHEMICAL_CONDITION_VALIDATOR_STATES,
    CHEMICAL_TERM_OBJECT_TYPE,
    EVIDENCE_QUOTE_OBJECT_TYPE,
    REFERENCE_OBJECT_TYPE,
    build_pending_chemical_condition_envelope_from_tool_verified_output,
    get_chemical_condition_domain_pack_metadata_path,
    validate_pending_chemical_condition_envelope,
)

from .test_alliance_domain_pack_scaffold import (  # noqa: E402
    _assert_range_exists,
    _assert_source_file_matches,
    _cache_schema,
    _iter_linkml_provider_refs,
    _load_linkml_index,
)


RAW_CHEMICAL_FIXTURE_PATH = (
    REPO_ROOT
    / "backend"
    / "tests"
    / "fixtures"
    / "domain_packs"
    / "chemical_condition"
    / "tool_verified_chemical_output.yaml"
)
EXPECTED_CHEMICAL_ENVELOPE_PATH = (
    REPO_ROOT
    / "backend"
    / "tests"
    / "fixtures"
    / "domain_packs"
    / "chemical_condition"
    / "tool_verified_pending_envelope.yaml"
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


def _chemical_condition_pack():
    registry = load_alliance_domain_pack_registry()
    pack = registry.get_pack(CHEMICAL_CONDITION_DOMAIN_PACK_ID)
    assert pack is not None
    return pack


def _load_raw_fixture() -> dict[str, Any]:
    return yaml.safe_load(RAW_CHEMICAL_FIXTURE_PATH.read_text(encoding="utf-8"))


def _iter_mapping_keys(value: Any):
    if isinstance(value, Mapping):
        yield from value.keys()
        for child in value.values():
            yield from _iter_mapping_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_mapping_keys(child)


def test_chemical_condition_pack_declares_roles_and_validator_bindings():
    pack = _chemical_condition_pack()
    metadata = pack.metadata

    assert pack.metadata_path == get_chemical_condition_domain_pack_metadata_path()
    assert metadata.pack_id == CHEMICAL_CONDITION_DOMAIN_PACK_ID
    assert metadata.version == CHEMICAL_CONDITION_DOMAIN_PACK_VERSION

    roles_by_object_type = {
        object_definition.object_type: object_definition.metadata["object_role"]
        for object_definition in metadata.object_definitions
    }
    assert roles_by_object_type == {
        CHEMICAL_CONDITION_OBJECT_TYPE: "curatable_unit",
        CHEMICAL_TERM_OBJECT_TYPE: "validated_reference",
        REFERENCE_OBJECT_TYPE: "validated_reference",
        EVIDENCE_QUOTE_OBJECT_TYPE: "metadata_only",
    }

    condition = next(
        item
        for item in metadata.object_definitions
        if item.object_type == CHEMICAL_CONDITION_OBJECT_TYPE
    )
    assert condition.model_ref == CHEMICAL_CONDITION_MODEL_ID
    assert condition.schema_ref.name == "ExperimentalCondition"
    assert condition.metadata["export_behavior"]["status"] == "blocked"
    assert condition.metadata["export_behavior"]["exportable"] is False
    assert condition.metadata["export_behavior"]["required_export_context_fields"] == list(
        CHEMICAL_CONDITION_EXPORT_CONTEXT_FIELDS
    )

    object_ref_fields = {
        field.field_path: field.object_type_ref
        for field in condition.fields
        if field.field_type is DomainPackFieldType.OBJECT_REF
    }
    assert object_ref_fields == {
        "chemical": CHEMICAL_TERM_OBJECT_TYPE,
        "source_reference": REFERENCE_OBJECT_TYPE,
        "evidence_quote": EVIDENCE_QUOTE_OBJECT_TYPE,
    }

    validators = metadata.metadata["validators"]
    assert tuple(validators) == CHEMICAL_CONDITION_VALIDATOR_STATES
    assert all(validators[state] for state in CHEMICAL_CONDITION_VALIDATOR_STATES)

    validator_bindings = metadata.metadata["validator_bindings"]
    assert tuple(validator_bindings) == CHEMICAL_CONDITION_VALIDATOR_STATES
    active_binding = validator_bindings["active"][0]
    assert active_binding["binding_id"] == "chemical_condition.pending_envelope_validator"
    module_name, _, function_name = active_binding["validator"].rpartition(".")
    assert callable(getattr(importlib.import_module(module_name), function_name))
    assert validator_bindings["planned"][0]["tool_name"] == "chebi_api_call"
    assert validator_bindings["blocked"][0]["blocked_by"] == "ALL-425"


def test_chemical_condition_pack_records_grounding_and_blocks_exports():
    metadata = _chemical_condition_pack().metadata
    provider_refs = metadata.metadata["provider_refs"]

    linkml_ref = provider_refs[ALLIANCE_LINKML_PROVIDER_KEY]
    assert linkml_ref["commit"] == ALLIANCE_LINKML_COMMIT
    assert linkml_ref["root_schema"] == "model/schema/allianceModel.yaml"

    db_ref = provider_refs["alliance_curation_db"]
    tables = {table["table"]: table for table in db_ref["tables"]}
    assert set(tables) >= {
        "public.experimentalcondition",
        "public.conditionrelation",
        "public.conditionrelation_experimentalcondition",
        "public.phenotypeannotation_conditionrelation",
        "public.diseaseannotation_conditionrelation",
        "public.geneexpressionannotation_conditionrelation",
    }
    assert tables["public.experimentalcondition"]["verified_constraints"] == [
        "experimentalcondition_conditionchemical_id_fk references public.ontologyterm(id)",
        "experimentalcondition_conditionclass_id_fk references public.ontologyterm(id)",
        "experimentalcondition_conditionid_id_fk references public.ontologyterm(id)",
    ]
    assert db_ref["representative_rows"] == [
        {
            "experimentalcondition_id": 200016096,
            "unique_id": "ZECO:0000111|CHEBI:9168|3 pM|chemical:sirolimus",
            "condition_class_curie": "ZECO:0000111",
            "condition_class_name": "chemical treatment",
            "condition_chemical_curie": "CHEBI:9168",
            "condition_chemical_name": "sirolimus",
            "condition_quantity": "3 pM",
        }
    ]

    write_behavior = db_ref["write_behavior"]
    assert write_behavior["status"] == "blocked"
    assert "insert public.experimentalcondition" in write_behavior["blocked_operations"]
    assert (
        "Resolve source papers to durable public.reference.id values."
        in write_behavior["required_before_write"]
    )


def test_chemical_condition_linkml_class_slot_and_range_refs_exist(tmp_path: Path):
    schema_cache_dir, _env_values = _cache_schema(tmp_path)
    index = _load_linkml_index(schema_cache_dir)
    metadata = _chemical_condition_pack().metadata

    provider_refs = tuple(_iter_linkml_provider_refs(metadata))
    assert provider_refs

    for provider_ref in provider_refs:
        assert provider_ref["commit"] == ALLIANCE_LINKML_COMMIT
        assert provider_ref["schema_ref"] == "alliance.linkml"

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


def test_tool_verified_chemical_fixture_converts_to_pending_envelope():
    raw_fixture = _load_raw_fixture()
    envelope = build_pending_chemical_condition_envelope_from_tool_verified_output(
        raw_fixture
    )

    assert envelope.domain_pack_id == CHEMICAL_CONDITION_DOMAIN_PACK_ID
    assert {obj.status for obj in envelope.objects} == {CuratableObjectStatus.PENDING}

    counts = Counter(obj.object_type for obj in envelope.objects)
    assert counts == {
        REFERENCE_OBJECT_TYPE: 1,
        CHEMICAL_TERM_OBJECT_TYPE: 1,
        EVIDENCE_QUOTE_OBJECT_TYPE: 2,
        CHEMICAL_CONDITION_OBJECT_TYPE: 1,
    }

    condition = next(
        obj for obj in envelope.objects if obj.object_type == CHEMICAL_CONDITION_OBJECT_TYPE
    )
    assert condition.payload["condition_class"] == {
        "curie": "ZECO:0000111",
        "name": "chemical treatment",
    }
    assert condition.payload["condition_chemical"] == {
        "curie": "CHEBI:9168",
        "name": "sirolimus",
    }
    assert condition.metadata["export_behavior"]["status"] == "blocked"
    assert condition.object_refs[0].object_type == CHEMICAL_TERM_OBJECT_TYPE

    findings = validate_pending_chemical_condition_envelope(envelope)
    assert [finding.code for finding in findings] == [
        "alliance.chemical_condition.export_context_missing"
    ]
    assert findings[0].severity.value == "blocker"
    assert findings[0].details["missing_export_context_fields"] == [
        "host_annotation_type",
        "host_annotation_id",
        "source_reference.reference_id",
    ]

    expected = yaml.safe_load(
        EXPECTED_CHEMICAL_ENVELOPE_PATH.read_text(encoding="utf-8")
    )
    assert envelope.model_dump(
        mode="json",
        exclude_defaults=True,
        exclude_none=True,
    ) == expected["envelope"]


def test_converted_chemical_condition_envelope_omits_legacy_semantic_stores():
    raw_fixture = _load_raw_fixture()
    envelope = build_pending_chemical_condition_envelope_from_tool_verified_output(
        raw_fixture
    )

    observed_keys = set(_iter_mapping_keys(envelope.model_dump(mode="python")))
    assert FORBIDDEN_LEGACY_COLLECTIONS.isdisjoint(observed_keys)


def test_chemical_condition_export_context_can_be_completed_without_fake_success():
    raw_fixture = _load_raw_fixture()
    raw_fixture["reference"]["reference_id"] = 12345
    raw_fixture["chemical_conditions"][0]["host_annotation_type"] = "PhenotypeAnnotation"
    raw_fixture["chemical_conditions"][0]["host_annotation_id"] = "200000001"

    envelope = build_pending_chemical_condition_envelope_from_tool_verified_output(
        raw_fixture
    )

    assert [
        finding.code
        for finding in validate_pending_chemical_condition_envelope(envelope)
    ] == []
    condition = next(
        obj for obj in envelope.objects if obj.object_type == CHEMICAL_CONDITION_OBJECT_TYPE
    )
    assert condition.metadata["export_behavior"]["status"] == "blocked"
    chemical_reference = next(
        obj for obj in envelope.objects if obj.object_type == CHEMICAL_TERM_OBJECT_TYPE
    )
    assert chemical_reference.metadata["validation_state"] == "pending_chebi_lookup"


def test_tool_verified_chemical_fixture_rejects_malformed_required_data():
    raw_fixture = _load_raw_fixture()

    missing_conditions = copy.deepcopy(raw_fixture)
    missing_conditions.pop("chemical_conditions")
    with pytest.raises(ValidationError, match="chemical_conditions"):
        build_pending_chemical_condition_envelope_from_tool_verified_output(
            missing_conditions
        )

    missing_evidence_link = copy.deepcopy(raw_fixture)
    missing_evidence_link["chemical_conditions"][0]["evidence_record_ids"] = [
        "unknown-evidence-record"
    ]
    with pytest.raises(ValidationError, match="unknown evidence_record_ids"):
        build_pending_chemical_condition_envelope_from_tool_verified_output(
            missing_evidence_link
        )

    invalid_chebi = copy.deepcopy(raw_fixture)
    invalid_chebi["chemical_conditions"][0]["normalized_id"] = "ZECO:0000111"
    envelope = build_pending_chemical_condition_envelope_from_tool_verified_output(
        invalid_chebi
    )
    assert [
        finding.code
        for finding in validate_pending_chemical_condition_envelope(envelope)
        if finding.code == "alliance.chemical_condition.invalid_chebi_curie"
    ] == ["alliance.chemical_condition.invalid_chebi_curie"]

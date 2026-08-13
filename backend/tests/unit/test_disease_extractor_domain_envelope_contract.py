"""Disease extractor domain-envelope migration coverage."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from src.lib.config import agent_loader, agent_sources, schema_discovery
from src.lib.curation_workspace.extraction_results import (
    build_extraction_envelope_candidate_with_evidence,
)
from src.schemas.models import LEGACY_SEMANTIC_LIST_FIELDS


REPO_ROOT = Path(__file__).resolve().parents[3]
REPO_PACKAGES_DIR = REPO_ROOT / "packages"
ALLIANCE_PYTHON_SRC = REPO_ROOT / "packages" / "alliance" / "python" / "src"
if str(ALLIANCE_PYTHON_SRC) not in sys.path:
    sys.path.insert(0, str(ALLIANCE_PYTHON_SRC))

from agr_ai_curation_alliance.domain_packs.disease import (  # noqa: E402
    DISEASE_DOMAIN_PACK_ID,
    DISEASE_MODEL_ID,
    DISEASE_OBJECT_TYPE,
    disease_extraction_output_to_pending_envelope,
    validate_pending_disease_envelope,
)


FIXTURE_PATH = (
    REPO_ROOT
    / "backend"
    / "tests"
    / "fixtures"
    / "domain_packs"
    / "disease"
    / "disease_extractor_domain_envelope_output.yaml"
)


@pytest.fixture(autouse=True)
def _reset_config_caches(monkeypatch):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))
    agent_loader.reset_cache()
    schema_discovery.reset_cache()
    yield
    agent_loader.reset_cache()
    schema_discovery.reset_cache()


def _disease_extractor_source():
    return next(
        source
        for source in agent_sources.resolve_agent_config_sources(REPO_PACKAGES_DIR)
        if source.folder_name == "disease_extractor"
    )


def _disease_extractor_schema():
    schema_discovery.discover_agent_schemas(force_reload=True)
    schema_cls = schema_discovery.get_schema_for_agent("disease_extractor")
    assert schema_cls is not None
    return schema_cls


def _validate_disease_extractor_payload(payload: dict[str, object]):
    return _disease_extractor_schema().model_validate(payload)


def _valid_disease_extractor_payload() -> dict[str, object]:
    return yaml.safe_load(FIXTURE_PATH.read_text(encoding="utf-8"))["output"]


def _set_payload_field(payload: dict[str, object], field_path: str, value: object) -> None:
    current = payload["curatable_objects"][0]["payload"]
    for part in field_path.split(".")[:-1]:
        current = current[part]
    current[field_path.split(".")[-1]] = value


def _valid_pending_disease_envelope():
    return disease_extraction_output_to_pending_envelope(
        copy.deepcopy(_valid_disease_extractor_payload()),
        envelope_id="disease-extractor-env-1",
        document_id="disease-paper-0001",
    )


def test_disease_extractor_schema_accepts_pending_disease_annotation_output():
    envelope = _validate_disease_extractor_payload(
        _valid_disease_extractor_payload()
    )

    obj = envelope.curatable_objects[0]
    assert obj.object_type == DISEASE_OBJECT_TYPE
    assert obj.object_role == "curatable_unit"
    assert obj.model_ref == DISEASE_MODEL_ID
    assert obj.definition_state.value == "in_development"
    assert obj.payload["disease_annotation_object"] == {
        "curie": "DOID:0050434",
        "name": "Andersen-Tawil syndrome",
    }
    assert obj.payload["data_provider"] == {"abbreviation": "ZFIN"}
    assert obj.evidence_record_ids == [
        "ats-model-evidence-1",
        "ats-cohort-evidence-1",
    ]
    assert envelope.metadata.raw_mentions[0].mention == "Andersen-Tawil syndrome"
    assert envelope.metadata.evidence_records[0].evidence_record_id == (
        "ats-model-evidence-1"
    )


def test_disease_extractor_schema_accepts_label_backed_pending_disease_candidates():
    payload = _valid_disease_extractor_payload()
    del payload["curatable_objects"][0]["payload"]["disease_annotation_object"]["curie"]

    envelope = _validate_disease_extractor_payload(payload)

    obj = envelope.curatable_objects[0]
    assert obj.payload["disease_annotation_object"] == {
        "name": "Andersen-Tawil syndrome",
    }


def test_disease_extractor_schema_canonicalizes_pending_scaffold_fields():
    payload = _valid_disease_extractor_payload()
    obj = payload["curatable_objects"][0]
    del obj["definition_state"]
    del obj["schema_ref"]["definition_state"]
    del obj["evidence_record_ids"]
    del obj["metadata_refs"]
    del obj["metadata"]["assertion_kind"]
    del obj["metadata"]["write_behavior"]
    payload["metadata"]["raw_mentions"] = []

    envelope = _validate_disease_extractor_payload(payload)

    normalized_obj = envelope.curatable_objects[0]
    assert normalized_obj.schema_ref.definition_state.value == "in_development"
    assert normalized_obj.definition_state.value == "in_development"
    assert normalized_obj.evidence_record_ids == [
        "ats-model-evidence-1",
        "ats-cohort-evidence-1",
    ]
    assert normalized_obj.metadata["assertion_kind"] == "pending_disease_assertion"
    assert normalized_obj.metadata["write_behavior"]["status"] == "blocked"
    assert [
        metadata_ref.model_dump(mode="json", exclude_none=True)
        for metadata_ref in normalized_obj.metadata_refs
    ] == [
        {"metadata_path": "raw_mentions[0]", "role": "source_mention"},
        {"metadata_path": "evidence_records[0]", "role": "supporting_evidence"},
        {"metadata_path": "evidence_records[1]", "role": "supporting_evidence"},
    ]
    assert envelope.metadata.raw_mentions[0].mention == "Andersen-Tawil syndrome"
    assert envelope.metadata.raw_mentions[0].entity_type == "disease"
    assert envelope.metadata.raw_mentions[0].evidence_record_ids == [
        "ats-model-evidence-1",
        "ats-cohort-evidence-1",
    ]


@pytest.mark.parametrize("legacy_field", sorted(LEGACY_SEMANTIC_LIST_FIELDS))
def test_disease_extractor_schema_rejects_top_level_legacy_semantic_lists(
    legacy_field: str,
):
    payload = _valid_disease_extractor_payload()
    payload[legacy_field] = []

    with pytest.raises(ValidationError) as exc_info:
        _disease_extractor_schema().model_validate(payload)

    assert "curatable_objects[]" in str(exc_info.value)
    assert legacy_field in str(exc_info.value)


def test_disease_extractor_schema_rejects_legacy_flat_disease_payload_fields():
    payload = _valid_disease_extractor_payload()
    payload["curatable_objects"][0]["payload"]["normalized_id"] = "DOID:0050434"
    payload["curatable_objects"][0]["payload"]["normalized_label"] = (
        "Andersen-Tawil syndrome"
    )

    with pytest.raises(ValidationError) as exc_info:
        _disease_extractor_schema().model_validate(payload)

    message = str(exc_info.value)
    assert "legacy flat disease helper fields" in message
    assert "disease_annotation_object.name with optional curie" in message


@pytest.mark.parametrize(
    "field_path",
    [
        "mention",
        "disease_annotation_object.name",
        "data_provider.abbreviation",
    ],
)
def test_disease_extractor_schema_rejects_blank_required_payload_values(
    field_path: str,
):
    payload = _valid_disease_extractor_payload()
    _set_payload_field(payload, field_path, "  ")

    with pytest.raises(ValidationError) as exc_info:
        _disease_extractor_schema().model_validate(payload)

    message = str(exc_info.value)
    assert "missing required non-empty fields" in message
    assert field_path in message


@pytest.mark.parametrize(
    ("field_path", "value"),
    [
        ("role", "not_a_role"),
        ("confidence", "certain"),
    ],
)
def test_disease_extractor_schema_rejects_unsupported_payload_enum_values(
    field_path: str,
    value: str,
):
    payload = _valid_disease_extractor_payload()
    _set_payload_field(payload, field_path, value)

    with pytest.raises(ValidationError) as exc_info:
        _disease_extractor_schema().model_validate(payload)

    message = str(exc_info.value)
    assert f"payload.{field_path} must be one of" in message


def test_disease_extractor_schema_rejects_incomplete_payload_evidence_snapshot():
    payload = _valid_disease_extractor_payload()
    payload["curatable_objects"][0]["payload"]["evidence_records"][0][
        "verified_quote"
    ] = " "

    with pytest.raises(ValidationError) as exc_info:
        _disease_extractor_schema().model_validate(payload)

    message = str(exc_info.value)
    assert "payload.evidence_records[0] must include non-empty verified_quote" in message


def test_disease_extractor_schema_requires_metadata_evidence_alignment():
    payload = _valid_disease_extractor_payload()
    payload["metadata"]["evidence_records"][0]["verified_quote"] = "changed quote"

    with pytest.raises(ValidationError) as exc_info:
        _disease_extractor_schema().model_validate(payload)

    assert "must match metadata.evidence_records[]" in str(exc_info.value)


@pytest.mark.parametrize(
    ("location", "field_name", "value"),
    (
        ("object", "repair_hints", ["legacy repair hint"]),
        ("metadata", "repair_notes", ["legacy repair note"]),
        ("top_level", "repair_mode", True),
    ),
)
def test_disease_extractor_schema_rejects_repair_surfaces(
    location: str,
    field_name: str,
    value: object,
):
    payload = _valid_disease_extractor_payload()
    if location == "object":
        payload["curatable_objects"][0][field_name] = value
    elif location == "metadata":
        payload["metadata"][field_name] = value
    else:
        payload[field_name] = value

    with pytest.raises(ValidationError):
        _disease_extractor_schema().model_validate(payload)


def test_disease_extractor_prompt_agent_and_group_rules_name_domain_contract():
    source = _disease_extractor_source()
    prompt_content = str(
        yaml.safe_load(source.prompt_yaml.read_text(encoding="utf-8"))["content"]
    )
    agent_data = yaml.safe_load(source.agent_yaml.read_text(encoding="utf-8"))

    assert agent_data["tools"] == [
        "search_document",
        "read_chunk",
        "read_section",
        "read_subsection",
        "record_evidence",
        "list_recorded_evidence",
        "get_recorded_evidence",
        "attach_evidence_to_object",
        "detach_evidence_from_object",
        "discard_recorded_evidence",
        "update_recorded_evidence_metadata",
        "get_agent_contract",
        "agr_species_context_lookup",
        "search_domain_field_terms",
        "inspect_ontology_term",
        "resolve_domain_field_term",
        "stage_disease_observation",
        "patch_disease_observation",
        "discard_disease_observation",
        "list_staged_disease_observations",
        "find_staged_disease_observations",
        "finalize_disease_extraction",
    ]
    assert "DiseaseAnnotation curatable_objects[]" in agent_data["supervisor_routing"][
        "description"
    ]
    # Builder-pattern contract (Phase 2 disease migration): the agent stages
    # evidence-backed observations with the builder tools; the backend builds the
    # DiseaseExtractionResultEnvelope and materializes the concrete subtype.
    assert "Do not hand-author `curatable_objects[]`" in prompt_content
    assert "DiseaseExtractionResultEnvelope" in prompt_content
    assert (
        "GeneDiseaseAnnotation / AlleleDiseaseAnnotation / AGMDiseaseAnnotation"
        in prompt_content
    )
    assert "stage_disease_observation" in prompt_content
    assert "finalize_disease_extraction" in prompt_content
    assert "subject SELECTS which concrete subtype is written" in prompt_content
    assert "`is_model_of`" in prompt_content
    assert (
        "Active validator bindings declared by the disease domain pack own final "
        "disease ontology" in prompt_content
    )
    assert "agr_species_context_lookup" in prompt_content
    # Validators own ontology identity; the extractor does not perform broad
    # extraction-time disease ontology lookup.
    assert "active validator bindings own final disease ontology" in prompt_content
    assert "agr_curation_query" not in prompt_content
    assert "repair_mode" not in prompt_content
    assert "repair_notes" not in prompt_content
    assert "repair_hints" not in prompt_content

    for group_rule_file in source.group_rule_files:
        content = str(
            yaml.safe_load(group_rule_file.read_text(encoding="utf-8"))["content"]
        )
        assert "DiseaseAnnotation" in content
        assert "PendingDiseaseAssertionPayload" in content
        assert "disease_annotation_object.name" in content
        assert "agr_curation_query" not in content


def test_disease_extractor_payload_persists_curatable_objects_only_for_new_runs():
    payload = _valid_disease_extractor_payload()
    candidate, evidence_metadata = build_extraction_envelope_candidate_with_evidence(
        json.dumps(payload),
        agent_key="disease_extractor",
        adapter_key="disease",
        conversation_summary="Extract disease assertions.",
    )

    assert candidate is not None
    assert candidate.payload_json["curatable_objects"][0]["object_type"] == (
        DISEASE_OBJECT_TYPE
    )
    assert LEGACY_SEMANTIC_LIST_FIELDS.isdisjoint(candidate.payload_json)
    assert evidence_metadata["evidence_count"] == 2
    assert evidence_metadata["evidence_records"][0]["evidence_record_id"] == (
        "ats-model-evidence-1"
    )


def test_disease_extractor_fixture_converts_to_pending_domain_envelope():
    converted = _valid_pending_disease_envelope()

    assert converted.domain_pack_id == DISEASE_DOMAIN_PACK_ID
    assert converted.extracted_objects[0].object_type == DISEASE_OBJECT_TYPE
    assert converted.extracted_objects[0].status.value == "pending"
    assert converted.extracted_objects[0].payload["disease_annotation_object"]["curie"] == (
        "DOID:0050434"
    )
    assert converted.metadata["semantic_source"] == "domain_envelope.extracted_objects"
    assert converted.metadata["legacy_semantic_lists"] == []
    assert validate_pending_disease_envelope(converted) == ()


@pytest.mark.parametrize(
    "field_path",
    [
        "mention",
        "disease_annotation_object.name",
        "data_provider.abbreviation",
    ],
)
def test_pending_disease_validator_rejects_blank_required_payload_values(
    field_path: str,
):
    converted = _valid_pending_disease_envelope()
    payload = converted.extracted_objects[0].payload
    current = payload
    for part in field_path.split(".")[:-1]:
        current = current[part]
    current[field_path.split(".")[-1]] = " "

    findings = validate_pending_disease_envelope(converted)

    assert [finding.code for finding in findings] == [
        "alliance.disease.required_payload_fields_missing"
    ]
    assert findings[0].details["missing_fields"] == [field_path]


@pytest.mark.parametrize(
    ("field_path", "value"),
    [
        ("role", "not_a_role"),
        ("confidence", "certain"),
    ],
)
def test_pending_disease_validator_rejects_unsupported_payload_enum_values(
    field_path: str,
    value: str,
):
    converted = _valid_pending_disease_envelope()
    converted.extracted_objects[0].payload[field_path] = value

    findings = validate_pending_disease_envelope(converted)

    assert [finding.code for finding in findings] == [
        "alliance.disease.payload_enum_value_invalid"
    ]
    assert findings[0].details["field_path"] == field_path
    assert findings[0].details["observed_value"] == value


def test_pending_disease_validator_rejects_incomplete_evidence_snapshot():
    converted = _valid_pending_disease_envelope()
    converted.extracted_objects[0].payload["evidence_records"][0]["chunk_id"] = ""

    findings = validate_pending_disease_envelope(converted)

    assert [finding.code for finding in findings] == [
        "alliance.disease.evidence_records_incomplete"
    ]
    assert findings[0].details["invalid_records"] == [
        {
            "record_index": 0,
            "evidence_record_id": "ats-model-evidence-1",
            "missing_or_invalid_fields": ["chunk_id"],
        }
    ]

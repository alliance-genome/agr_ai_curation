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


def _valid_disease_extractor_payload() -> dict[str, object]:
    return yaml.safe_load(FIXTURE_PATH.read_text(encoding="utf-8"))["output"]


def test_disease_extractor_schema_accepts_pending_disease_annotation_output():
    envelope = _disease_extractor_schema().model_validate(
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
    assert obj.evidence_record_ids == [
        "ats-model-evidence-1",
        "ats-cohort-evidence-1",
    ]
    assert envelope.metadata.raw_mentions[0].mention == "Andersen-Tawil syndrome"
    assert envelope.metadata.evidence_records[0].evidence_record_id == (
        "ats-model-evidence-1"
    )


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
    assert "disease_annotation_object.curie/name" in message


def test_disease_extractor_schema_requires_metadata_evidence_alignment():
    payload = _valid_disease_extractor_payload()
    payload["metadata"]["evidence_records"][0]["verified_quote"] = "changed quote"

    with pytest.raises(ValidationError) as exc_info:
        _disease_extractor_schema().model_validate(payload)

    assert "must match metadata.evidence_records[]" in str(exc_info.value)


def test_disease_extractor_schema_requires_bounded_repair_field_refs():
    payload = _valid_disease_extractor_payload()
    payload["repair_mode"] = True
    payload["metadata"]["repair_notes"] = [
        "Repaired only the requested disease ontology curie field."
    ]

    with pytest.raises(ValidationError) as exc_info:
        _disease_extractor_schema().model_validate(payload)

    assert "field_refs must identify repaired field paths" in str(exc_info.value)

    payload["curatable_objects"][0]["field_refs"] = [
        {
            "object_ref": {
                "pending_ref_id": "disease-assertion-1",
                "object_type": DISEASE_OBJECT_TYPE,
            },
            "field_path": "disease_annotation_object.curie",
        }
    ]
    repaired = _disease_extractor_schema().model_validate(payload)

    assert repaired.repair_mode is True
    assert repaired.curatable_objects[0].field_refs[0].field_path.endswith("curie")


def test_disease_extractor_prompt_agent_and_group_rules_name_domain_contract():
    source = _disease_extractor_source()
    prompt_content = str(
        yaml.safe_load(source.prompt_yaml.read_text(encoding="utf-8"))["content"]
    )
    agent_data = yaml.safe_load(source.agent_yaml.read_text(encoding="utf-8"))

    assert "record_evidence" in agent_data["tools"]
    assert "DiseaseAnnotation curatable_objects[]" in agent_data["supervisor_routing"][
        "description"
    ]
    assert "`object_type`: `DiseaseAnnotation`" in prompt_content
    assert "`object_role`: `curatable_unit`" in prompt_content
    assert "`model_ref`: `PendingDiseaseAssertionPayload`" in prompt_content
    assert "`disease_annotation_object.curie`" in prompt_content
    assert "Do not use legacy flat payload fields" in prompt_content
    assert "In repair mode" in prompt_content
    assert "metadata.repair_notes[]" in prompt_content

    for group_rule_file in source.group_rule_files:
        content = str(
            yaml.safe_load(group_rule_file.read_text(encoding="utf-8"))["content"]
        )
        assert "DiseaseAnnotation" in content
        assert "PendingDiseaseAssertionPayload" in content
        assert "disease_annotation_object.curie/name" in content


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
    payload = copy.deepcopy(_valid_disease_extractor_payload())
    converted = disease_extraction_output_to_pending_envelope(
        payload,
        envelope_id="disease-extractor-env-1",
        document_id="ZFIN-paper-disease-0001",
    )

    assert converted.domain_pack_id == DISEASE_DOMAIN_PACK_ID
    assert converted.objects[0].object_type == DISEASE_OBJECT_TYPE
    assert converted.objects[0].status.value == "pending"
    assert converted.objects[0].payload["disease_annotation_object"]["curie"] == (
        "DOID:0050434"
    )
    assert converted.metadata["semantic_source"] == "domain_envelope.objects"
    assert converted.metadata["legacy_semantic_lists"] == []
    assert validate_pending_disease_envelope(converted) == ()

"""Chemical extractor domain-envelope migration coverage."""

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

from agr_ai_curation_alliance.domain_packs.chemical_condition import (  # noqa: E402
    CHEMICAL_CONDITION_DOMAIN_PACK_ID,
    CHEMICAL_CONDITION_MODEL_ID,
    CHEMICAL_CONDITION_OBJECT_TYPE,
    CHEMICAL_TERM_OBJECT_TYPE,
    EVIDENCE_QUOTE_OBJECT_TYPE,
    REFERENCE_OBJECT_TYPE,
    build_pending_chemical_condition_envelope_from_tool_verified_output,
    validate_pending_chemical_condition_envelope,
)


@pytest.fixture(autouse=True)
def _reset_config_caches(monkeypatch):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))
    agent_loader.reset_cache()
    schema_discovery.reset_cache()
    yield
    agent_loader.reset_cache()
    schema_discovery.reset_cache()


def _chemical_extractor_source():
    return next(
        source
        for source in agent_sources.resolve_agent_config_sources(REPO_PACKAGES_DIR)
        if source.folder_name == "chemical_extractor"
    )


def _chemical_extractor_schema():
    schema_discovery.discover_agent_schemas(force_reload=True)
    schema_cls = schema_discovery.get_schema_for_agent("chemical_extractor")
    assert schema_cls is not None
    return schema_cls


def _validate_chemical_extractor_payload(payload: dict[str, object]):
    return _chemical_extractor_schema().model_validate(payload)


def _schema_ref(schema_id: str, name: str, source_file: str) -> dict[str, object]:
    commit = "1b11d0888f19eba4ca72022200bb7d96b30d4a52"
    return {
        "schema_id": schema_id,
        "provider": "alliance_linkml",
        "name": name,
        "version": commit,
        "uri": (
            "https://github.com/alliance-genome/agr_curation_schema/blob/"
            f"{commit}/{source_file}"
        ),
    }


def _valid_chemical_extractor_payload() -> dict[str, object]:
    condition_ref = "chemical-condition-1"
    chemical_ref = "chemical-reference-1"
    reference_ref = "source-reference-1"
    evidence_ref = "evidence-quote-1"
    evidence_record_id = "rapamycin-treatment-evidence-1"
    verified_quote = (
        "Rapamycin treatment at 100 nM restored autophagy in mutant larvae "
        "after 24 hours."
    )

    return {
        "summary": "Retained one rapamycin chemical treatment condition.",
        "curatable_objects": [
            {
                "object_type": REFERENCE_OBJECT_TYPE,
                "object_role": "validated_reference",
                "pending_ref_id": reference_ref,
                "model_ref": "ReferencePayload",
                "schema_ref": _schema_ref(
                    "alliance.linkml.Reference",
                    "Reference",
                    "model/schema/reference.yaml",
                ),
                "definition_state": "in_development",
                "payload": {
                    "title": "Rapamycin chemical evidence fixture paper",
                    "filename": "test_tool_verified_chemical_paper.pdf",
                },
            },
            {
                "object_type": CHEMICAL_TERM_OBJECT_TYPE,
                "object_role": "validated_reference",
                "pending_ref_id": chemical_ref,
                "model_ref": "ChemicalTermPayload",
                "schema_ref": _schema_ref(
                    "alliance.linkml.ChemicalTerm",
                    "ChemicalTerm",
                    "model/schema/ontologyTerm.yaml",
                ),
                "definition_state": "in_development",
                "payload": {
                    "curie": "CHEBI:9168",
                    "name": "sirolimus",
                    "source_mentions": ["rapamycin"],
                },
            },
            {
                "object_type": EVIDENCE_QUOTE_OBJECT_TYPE,
                "object_role": "metadata_only",
                "pending_ref_id": evidence_ref,
                "model_ref": "EvidenceQuotePayload",
                "definition_state": "in_development",
                "payload": {
                    "evidence_record_id": evidence_record_id,
                    "entity": "rapamycin",
                    "verified_quote": verified_quote,
                    "page": 4,
                    "section": "Results",
                    "subsection": "Drug response",
                    "chunk_id": "chunk-chemical-treatment",
                    "figure_reference": "Figure 2C",
                },
            },
            {
                "object_type": CHEMICAL_CONDITION_OBJECT_TYPE,
                "object_role": "curatable_unit",
                "pending_ref_id": condition_ref,
                "model_ref": CHEMICAL_CONDITION_MODEL_ID,
                "schema_ref": {
                    **_schema_ref(
                        "alliance.linkml.ExperimentalCondition",
                        "ExperimentalCondition",
                        "model/schema/phenotypeAndDiseaseAnnotation.yaml",
                    ),
                    "definition_state": "in_development",
                },
                "definition_state": "in_development",
                "definition_notes": [
                    "Pending only; export is blocked until host annotation context and reference materialization are supplied."
                ],
                "payload": {
                    "condition_relation_type": {"name": "has_condition"},
                    "condition_class": {
                        "curie": "ZECO:0000111",
                        "name": "chemical treatment",
                    },
                    "condition_chemical": {
                        "curie": "CHEBI:9168",
                        "name": "sirolimus",
                    },
                    "source_chemical_mention": "rapamycin",
                    "source_mentions": ["rapamycin"],
                    "role": "treatment",
                    "confidence": "high",
                    "evidence_record_ids": [evidence_record_id],
                    "condition_quantity": "100 nM",
                    "condition_free_text": (
                        "Rapamycin treatment at 100 nM after 24 hours"
                    ),
                    "condition_summary": "chemical:sirolimus",
                    "timing": "after 24 hours",
                },
                "object_refs": [
                    {"pending_ref_id": chemical_ref, "object_type": CHEMICAL_TERM_OBJECT_TYPE},
                    {"pending_ref_id": reference_ref, "object_type": REFERENCE_OBJECT_TYPE},
                    {"pending_ref_id": evidence_ref, "object_type": EVIDENCE_QUOTE_OBJECT_TYPE},
                ],
                "evidence_record_ids": [evidence_record_id],
                "metadata_refs": [
                    {"metadata_path": "raw_mentions[0]", "role": "source_mention"},
                    {"metadata_path": "evidence_records[0]", "role": "verified_evidence"},
                ],
                "metadata": {
                    "object_role": "curatable_unit",
                    "condition_kind": "chemical_condition",
                    "semantic_source": "domain_envelope.objects",
                    "validator_binding_ids": [
                        "chemical_condition.pending_envelope_validator",
                        "chemical_condition.chebi_curie_format",
                    ],
                    "export_behavior": {
                        "status": "blocked",
                        "exportable": False,
                        "submit": False,
                        "reason": (
                            "Chemical condition export requires a host annotation, "
                            "materialized reference, and downstream submission adapter."
                        ),
                    },
                },
            },
        ],
        "metadata": {
            "raw_mentions": [
                {
                    "mention": "rapamycin",
                    "entity_type": "chemical",
                    "evidence_record_ids": [evidence_record_id],
                },
                {"mention": "DMSO", "entity_type": "chemical"},
            ],
            "evidence_records": [
                {
                    "evidence_record_id": evidence_record_id,
                    "entity": "rapamycin",
                    "verified_quote": verified_quote,
                    "page": 4,
                    "section": "Results",
                    "subsection": "Drug response",
                    "chunk_id": "chunk-chemical-treatment",
                    "figure_reference": "Figure 2C",
                }
            ],
            "normalization_notes": [
                "Resolved rapamycin to CHEBI:9168, the Alliance ChemicalTerm row named sirolimus."
            ],
            "exclusions": [
                {
                    "mention": "DMSO",
                    "reason_code": "vehicle_control_only",
                    "details": "Vehicle control only; no chemical-specific biological result.",
                }
            ],
            "ambiguities": [
                {
                    "mention": "Dex",
                    "why_ambiguous": (
                        "The abbreviation did not resolve to one compound from the provided context."
                    ),
                    "recommended_followup": "Curator should inspect the methods table.",
                }
            ],
            "notes": ["Chemical condition export remains blocked."],
            "provenance": {"semantic_source": "curatable_objects"},
        },
        "run_summary": {
            "candidate_count": 3,
            "kept_count": 1,
            "excluded_count": 1,
            "ambiguous_count": 1,
            "warnings": ["One ambiguous chemical abbreviation preserved in metadata."],
        },
    }


def test_chemical_extractor_schema_accepts_chemical_condition_objects():
    envelope = _validate_chemical_extractor_payload(
        _valid_chemical_extractor_payload()
    )

    assert [obj.object_type for obj in envelope.curatable_objects] == [
        REFERENCE_OBJECT_TYPE,
        CHEMICAL_TERM_OBJECT_TYPE,
        EVIDENCE_QUOTE_OBJECT_TYPE,
        CHEMICAL_CONDITION_OBJECT_TYPE,
    ]
    condition = envelope.curatable_objects[-1]
    assert condition.object_role == "curatable_unit"
    assert condition.payload.condition_chemical.curie == "CHEBI:9168"
    assert condition.metadata["export_behavior"]["status"] == "blocked"
    assert envelope.metadata.raw_mentions[0].mention == "rapamycin"
    assert envelope.metadata.ambiguities[0].mention == "Dex"


def test_chemical_extractor_schema_accepts_label_backed_pending_ontology_candidates():
    payload = _valid_chemical_extractor_payload()
    del payload["curatable_objects"][1]["payload"]["curie"]
    condition_payload = payload["curatable_objects"][-1]["payload"]
    del condition_payload["condition_class"]["curie"]
    del condition_payload["condition_chemical"]["curie"]

    envelope = _validate_chemical_extractor_payload(payload)

    chemical_term = envelope.curatable_objects[1]
    condition = envelope.curatable_objects[-1]
    assert chemical_term.payload.curie is None
    assert chemical_term.payload.name == "sirolimus"
    assert condition.payload.condition_class.curie is None
    assert condition.payload.condition_class.name == "chemical treatment"
    assert condition.payload.condition_chemical.curie is None
    assert condition.payload.condition_chemical.name == "sirolimus"


def test_chemical_extractor_schema_canonicalizes_runtime_scaffold_objects():
    payload = _valid_chemical_extractor_payload()
    reference = copy.deepcopy(payload["curatable_objects"][0])
    condition = copy.deepcopy(payload["curatable_objects"][-1])
    evidence = copy.deepcopy(payload["curatable_objects"][2])
    reference["schema_ref"]["definition_state"] = "in_development"
    condition["object_refs"] = []
    condition["metadata"] = {}
    evidence["pending_ref_id"] = None
    payload["metadata"]["evidence_records"][0]["chunk_id"] = "chunk-with-typo"
    payload["curatable_objects"] = [condition, reference, evidence]

    envelope = _validate_chemical_extractor_payload(payload)

    condition_object = next(
        obj
        for obj in envelope.curatable_objects
        if obj.object_type == CHEMICAL_CONDITION_OBJECT_TYPE
    )
    ref_types = {ref.object_type for ref in condition_object.object_refs}
    assert ref_types == {
        CHEMICAL_TERM_OBJECT_TYPE,
        REFERENCE_OBJECT_TYPE,
        EVIDENCE_QUOTE_OBJECT_TYPE,
    }
    assert condition_object.metadata["export_behavior"]["status"] == "blocked"
    reference_object = next(
        obj
        for obj in envelope.curatable_objects
        if obj.object_type == REFERENCE_OBJECT_TYPE
    )
    assert reference_object.schema_ref.definition_state == "stable"
    assert envelope.metadata.evidence_records[0].chunk_id == (
        evidence["payload"]["chunk_id"]
    )
    assert all(obj.pending_ref_id for obj in envelope.curatable_objects)


def test_chemical_extractor_schema_rejects_ambiguous_label_backed_chemical_refs():
    payload = _valid_chemical_extractor_payload()
    ambiguous_chemical_ref = "chemical-reference-ambiguous"
    ambiguous_chemical = copy.deepcopy(payload["curatable_objects"][1])
    ambiguous_chemical["pending_ref_id"] = ambiguous_chemical_ref
    ambiguous_chemical["payload"]["curie"] = "CHEBI:12345"
    payload["curatable_objects"].insert(2, ambiguous_chemical)
    condition = payload["curatable_objects"][-1]
    del condition["payload"]["condition_chemical"]["curie"]
    condition["object_refs"].insert(
        1,
        {
            "pending_ref_id": ambiguous_chemical_ref,
            "object_type": CHEMICAL_TERM_OBJECT_TYPE,
        },
    )

    with pytest.raises(ValidationError, match="multiple ChemicalTerm objects"):
        _chemical_extractor_schema().model_validate(payload)


@pytest.mark.parametrize("legacy_field", sorted(LEGACY_SEMANTIC_LIST_FIELDS))
def test_chemical_extractor_schema_rejects_top_level_legacy_lists(legacy_field):
    payload = _valid_chemical_extractor_payload()
    payload[legacy_field] = []

    with pytest.raises(ValidationError, match=legacy_field):
        _chemical_extractor_schema().model_validate(payload)


def test_chemical_extractor_schema_rejects_invalid_chebi_curie():
    payload = _valid_chemical_extractor_payload()
    payload["curatable_objects"][1]["payload"]["curie"] = "ZECO:0000111"

    with pytest.raises(ValidationError, match="CHEBI"):
        _chemical_extractor_schema().model_validate(payload)


def test_chemical_extractor_schema_rejects_evidence_ids_missing_from_metadata():
    payload = _valid_chemical_extractor_payload()
    payload["metadata"]["evidence_records"] = []

    with pytest.raises(ValidationError, match="metadata.evidence_records"):
        _chemical_extractor_schema().model_validate(payload)


def test_chemical_extractor_schema_requires_export_blocker_metadata():
    payload = _valid_chemical_extractor_payload()
    payload["curatable_objects"][-1]["metadata"]["export_behavior"]["status"] = "ready"

    with pytest.raises(ValidationError, match="export_behavior"):
        _chemical_extractor_schema().model_validate(payload)


def test_chemical_extractor_schema_rejects_malformed_export_behavior_metadata():
    payload = _valid_chemical_extractor_payload()
    payload["curatable_objects"][-1]["metadata"]["export_behavior"] = "blocked"

    with pytest.raises(ValidationError, match="export_behavior must be a mapping"):
        _chemical_extractor_schema().model_validate(payload)


def test_chemical_extractor_schema_requires_condition_source_mentions():
    payload = _valid_chemical_extractor_payload()
    del payload["curatable_objects"][-1]["payload"]["source_mentions"]

    with pytest.raises(ValidationError, match="source_mentions"):
        _chemical_extractor_schema().model_validate(payload)


def test_chemical_extractor_schema_requires_condition_role():
    payload = _valid_chemical_extractor_payload()
    del payload["curatable_objects"][-1]["payload"]["role"]

    with pytest.raises(ValidationError, match="role"):
        _chemical_extractor_schema().model_validate(payload)


@pytest.mark.parametrize(
    ("location", "field_name", "value"),
    (
        ("object", "repair_hints", ["legacy repair hint"]),
        ("metadata", "repair_notes", ["legacy repair note"]),
        ("top_level", "repair_mode", True),
    ),
)
def test_chemical_extractor_schema_rejects_repair_surfaces(
    location: str,
    field_name: str,
    value: object,
):
    payload = _valid_chemical_extractor_payload()
    if location == "object":
        payload["curatable_objects"][-1][field_name] = value
    elif location == "metadata":
        payload["metadata"][field_name] = value
    else:
        payload[field_name] = value

    with pytest.raises(ValidationError):
        _chemical_extractor_schema().model_validate(payload)


def test_chemical_extractor_prompt_agent_and_group_rules_name_domain_contract():
    source = _chemical_extractor_source()
    prompt_content = str(
        yaml.safe_load(source.prompt_yaml.read_text(encoding="utf-8"))["content"]
    )
    agent_data = yaml.safe_load(source.agent_yaml.read_text(encoding="utf-8"))

    assert "record_evidence" in agent_data["tools"]
    assert "agr_species_context_lookup" in agent_data["tools"]
    assert "agr_curation_query" not in agent_data["tools"]
    assert "ChemicalCondition" in agent_data["description"]
    assert "curatable_objects[]" in agent_data["supervisor_routing"]["description"]
    assert '"object_type": "ChemicalCondition"' in prompt_content
    assert "ChemicalConditionPayload" in prompt_content
    assert "ChemicalTermPayload" in prompt_content
    assert "ReferencePayload" in prompt_content
    assert "EvidenceQuotePayload" in prompt_content
    assert '`definition_state: "in_development"`' in prompt_content
    assert 'metadata.export_behavior.status: "blocked"' in prompt_content
    assert "Active validator bindings own final ChEBI" in prompt_content
    assert "Do not perform extraction-time ChEBI" in prompt_content
    assert "agr_species_context_lookup" in prompt_content
    assert "agr_curation_query" not in prompt_content
    assert "repair_mode" not in prompt_content
    assert "repair_notes" not in prompt_content
    assert "repair_hints" not in prompt_content
    assert '"normalized_id"' not in prompt_content
    assert '"object_type": "ChemicalAssertion"' not in prompt_content

    for group_rule_file in source.group_rule_files:
        content = str(yaml.safe_load(group_rule_file.read_text(encoding="utf-8"))["content"])
        assert "ChemicalCondition" in content
        assert "paper-supplied exact-CURIE hints" in content
        assert "ChemicalConditionPayload" in content
        assert "ChemicalTermPayload" in content
        assert "metadata.export_behavior.status: blocked" in content


def test_chemical_extractor_payload_persists_as_curatable_objects_only():
    payload = _valid_chemical_extractor_payload()
    candidate, evidence_metadata = build_extraction_envelope_candidate_with_evidence(
        json.dumps(payload),
        agent_key="chemical_extractor",
        adapter_key="chemical",
        conversation_summary="Extract chemical treatment conditions.",
    )

    assert candidate is not None
    assert candidate.payload_json["curatable_objects"][-1]["object_type"] == (
        CHEMICAL_CONDITION_OBJECT_TYPE
    )
    assert LEGACY_SEMANTIC_LIST_FIELDS.isdisjoint(candidate.payload_json)
    assert evidence_metadata["evidence_count"] == 1
    assert evidence_metadata["evidence_records"][0]["evidence_record_id"] == (
        "rapamycin-treatment-evidence-1"
    )


def test_chemical_condition_fixture_converts_to_pending_domain_envelope():
    raw_fixture_path = (
        REPO_ROOT
        / "backend"
        / "tests"
        / "fixtures"
        / "domain_packs"
        / "chemical_condition"
        / "tool_verified_chemical_output.yaml"
    )
    raw_fixture = yaml.safe_load(raw_fixture_path.read_text(encoding="utf-8"))

    converted = build_pending_chemical_condition_envelope_from_tool_verified_output(
        raw_fixture
    )

    assert converted.domain_pack_id == CHEMICAL_CONDITION_DOMAIN_PACK_ID
    assert converted.objects[-1].object_type == CHEMICAL_CONDITION_OBJECT_TYPE
    assert converted.objects[-1].payload["condition_chemical"]["curie"] == "CHEBI:9168"
    assert LEGACY_SEMANTIC_LIST_FIELDS.isdisjoint(converted.metadata)
    assert all(
        LEGACY_SEMANTIC_LIST_FIELDS.isdisjoint(obj.payload)
        for obj in converted.objects
    )
    assert [
        finding.code
        for finding in validate_pending_chemical_condition_envelope(converted)
    ] == ["alliance.chemical_condition.export_context_missing"]


def test_chemical_extractor_schema_rejects_missing_raw_mentions_for_retained_objects():
    payload = copy.deepcopy(_valid_chemical_extractor_payload())
    payload["metadata"]["raw_mentions"] = []

    with pytest.raises(ValidationError, match="metadata.raw_mentions"):
        _chemical_extractor_schema().model_validate(payload)

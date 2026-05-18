"""Phenotype extractor domain-envelope migration coverage."""

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
from src.lib.prompts import assembly
from src.lib.domain_packs.loader import load_domain_fixture_pack
from src.schemas.models import LEGACY_SEMANTIC_LIST_FIELDS


REPO_ROOT = Path(__file__).resolve().parents[3]
REPO_PACKAGES_DIR = REPO_ROOT / "packages"
ALLIANCE_PYTHON_SRC = REPO_ROOT / "packages" / "alliance" / "python" / "src"
if str(ALLIANCE_PYTHON_SRC) not in sys.path:
    sys.path.insert(0, str(ALLIANCE_PYTHON_SRC))

from agr_ai_curation_alliance.domain_packs import load_alliance_domain_pack_registry  # noqa: E402
from agr_ai_curation_alliance.domain_packs.phenotype import (  # noqa: E402
    PHENOTYPE_DOMAIN_PACK_ID,
    PHENOTYPE_FIXTURE_PACK_ID,
    PHENOTYPE_OBJECT_TYPE,
    validate_pending_phenotype_envelope,
)


LINKML_COMMIT = "1b11d0888f19eba4ca72022200bb7d96b30d4a52"


@pytest.fixture(autouse=True)
def _reset_config_caches(monkeypatch):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))
    agent_loader.reset_cache()
    schema_discovery.reset_cache()
    yield
    agent_loader.reset_cache()
    schema_discovery.reset_cache()


def _phenotype_extractor_source():
    return next(
        source
        for source in agent_sources.resolve_agent_config_sources(REPO_PACKAGES_DIR)
        if source.folder_name == "phenotype_extractor"
    )


def _phenotype_extractor_schema():
    schema_discovery.discover_agent_schemas(force_reload=True)
    schema_cls = schema_discovery.get_schema_for_agent("phenotype_extractor")
    assert schema_cls is not None
    return schema_cls


def _validate_phenotype_extractor_payload(payload: dict[str, object]):
    return _phenotype_extractor_schema().model_validate(payload)


def _schema_ref(schema_id: str, name: str, source_file: str) -> dict[str, str]:
    return {
        "schema_id": schema_id,
        "provider": "alliance_linkml",
        "name": name,
        "version": LINKML_COMMIT,
        "uri": (
            "https://github.com/alliance-genome/agr_curation_schema/blob/"
            f"{LINKML_COMMIT}/{source_file}"
        ),
    }


def _valid_phenotype_payload() -> dict[str, object]:
    evidence_record = {
        "evidence_record_id": "reduced-brood-size-evidence-1",
        "entity": "reduced brood size",
        "verified_quote": (
            "daf-2(e1370) adults produced 40% fewer progeny than wild type."
        ),
        "page": 5,
        "section": "Results",
        "subsection": "Fertility defects",
        "chunk_id": "chunk-phenotype-count",
        "figure_reference": "Figure 5A",
    }
    subject_payload = {
        "resolution_state": "pending_entity_resolution",
        "subject_identifier": "WB:WBGene00000912",
        "subject_label": "daf-2(e1370)",
        "subject_type": "gene",
        "taxon": "NCBITaxon:6239",
    }
    return {
        "summary": "Retained one verified pending phenotype assertion.",
        "curatable_objects": [
            {
                "object_type": "Reference",
                "object_role": "validated_reference",
                "pending_ref_id": "paper-reference-1",
                "model_ref": "ReferencePayload",
                "schema_ref": _schema_ref(
                    "alliance.linkml.Reference",
                    "Reference",
                    "model/schema/reference.yaml",
                ),
                "definition_state": "in_development",
                "payload": {
                    "title": "Brood size phenotype evidence fixture paper",
                    "filename": "test_tool_verified_phenotype_paper.pdf",
                },
            },
            {
                "object_type": "PhenotypeSubject",
                "object_role": "validated_reference",
                "pending_ref_id": "phenotype-subject-1",
                "model_ref": "PhenotypeSubjectPayload",
                "schema_ref": _schema_ref(
                    "alliance.linkml.BiologicalEntity",
                    "BiologicalEntity",
                    "model/schema/core.yaml",
                ),
                "definition_state": "in_development",
                "payload": subject_payload,
                "metadata": {
                    "validation_state": "pending_entity_resolution",
                    "validator_binding_id": "phenotype_subject_entity_validator",
                },
            },
            {
                "object_type": "PhenotypeTerm",
                "object_role": "validated_reference",
                "pending_ref_id": "phenotype-term-1",
                "model_ref": "PhenotypeTermPayload",
                "schema_ref": _schema_ref(
                    "alliance.linkml.PhenotypeTerm",
                    "PhenotypeTerm",
                    "model/schema/ontologyTerm.yaml",
                ),
                "definition_state": "in_development",
                "payload": {
                    "resolution_state": "pending_ontology_resolution",
                    "curie": "WBPhenotype:0000886",
                    "label": "reduced brood size",
                    "source_mentions": ["reduced brood size"],
                    "ontology_lookup_hint": {
                        "data_provider": "WB",
                        "taxon_id": "NCBITaxon:6239",
                        "evidence_record_id": "reduced-brood-size-evidence-1",
                    },
                    "export_state": "blocked_pending_ontology_resolution",
                    "write_blocked_reason": "phenotype term CURIE unresolved",
                },
                "evidence_record_ids": ["reduced-brood-size-evidence-1"],
                "metadata": {
                    "validation_state": "pending_ontology_resolution",
                    "validator_binding_id": "phenotype_term_ontology_validator",
                    "export_state": "blocked_pending_ontology_resolution",
                    "write_blocked_reason": "phenotype term CURIE unresolved",
                },
            },
            {
                "object_type": "EvidenceQuote",
                "object_role": "metadata_only",
                "pending_ref_id": "evidence-quote-1",
                "model_ref": "EvidenceQuotePayload",
                "definition_state": "in_development",
                "payload": evidence_record,
            },
            {
                "object_type": PHENOTYPE_OBJECT_TYPE,
                "object_role": "curatable_unit",
                "pending_ref_id": "phenotype-annotation-1",
                "model_ref": "PhenotypeAnnotationPayload",
                "schema_ref": _schema_ref(
                    "alliance.linkml.PhenotypeAnnotation",
                    "PhenotypeAnnotation",
                    "model/schema/phenotypeAndDiseaseAnnotation.yaml",
                ),
                "definition_state": "in_development",
                "definition_notes": [
                    "Pending only; export is blocked until subject, reference, ontology, and write targets are resolved."
                ],
                "payload": {
                    "annotation_kind": "phenotype_assertion",
                    "phenotype_annotation_object": "reduced brood size",
                    "phenotype_annotation_subject": subject_payload,
                    "phenotype_terms": [
                        {
                            "resolution_state": "pending_ontology_resolution",
                            "curie": "WBPhenotype:0000886",
                            "label": "reduced brood size",
                            "source_mentions": ["reduced brood size"],
                            "ontology_lookup_hint": {
                                "data_provider": "WB",
                                "taxon_id": "NCBITaxon:6239",
                                "evidence_record_id": "reduced-brood-size-evidence-1",
                            },
                            "export_state": "blocked_pending_ontology_resolution",
                            "write_blocked_reason": "phenotype term CURIE unresolved",
                        }
                    ],
                    "single_reference": {
                        "title": "Brood size phenotype evidence fixture paper",
                        "filename": "test_tool_verified_phenotype_paper.pdf",
                    },
                    "evidence_quote": {
                        "evidence_record_id": "reduced-brood-size-evidence-1"
                    },
                    "evidence_record_ids": ["reduced-brood-size-evidence-1"],
                    "source_mentions": ["reduced brood size"],
                    "negated": False,
                    "related_notes": [
                        {
                            "note_type": "phenotype_context",
                            "free_text": "Adult fertility phenotype compared with wild type.",
                        }
                    ],
                },
                "object_refs": [
                    {"pending_ref_id": "phenotype-subject-1", "object_type": "PhenotypeSubject"},
                    {"pending_ref_id": "phenotype-term-1", "object_type": "PhenotypeTerm"},
                    {"pending_ref_id": "paper-reference-1", "object_type": "Reference"},
                    {"pending_ref_id": "evidence-quote-1", "object_type": "EvidenceQuote"},
                ],
                "evidence_record_ids": ["reduced-brood-size-evidence-1"],
                "metadata_refs": [
                    {"metadata_path": "raw_mentions[0]", "role": "source_mention"},
                    {"metadata_path": "evidence_records[0]", "role": "verified_evidence"},
                    {"metadata_path": "exclusions[0]", "role": "excluded_prior_work"},
                    {"metadata_path": "ambiguities[0]", "role": "unresolved_context"},
                ],
                "metadata": {
                    "validation_state": "pending_entity_resolution",
                    "export_behavior": {"status": "blocked"},
                    "write_behavior": {"status": "blocked"},
                },
            },
        ],
        "metadata": {
            "raw_mentions": [
                {
                    "mention": "reduced brood size",
                    "entity_type": "phenotype",
                    "evidence_record_ids": ["reduced-brood-size-evidence-1"],
                }
            ],
            "evidence_records": [evidence_record],
            "normalization_notes": [
                "Phenotype term is grounded; subject subtype remains pending."
            ],
            "exclusions": [
                {
                    "mention": "developmental defects",
                    "reason_code": "previously_reported",
                    "details": "Prior-work background only.",
                }
            ],
            "ambiguities": [
                {
                    "mention": "daf-2(e1370)",
                    "why_ambiguous": "Subject subtype must be resolved before export.",
                    "recommended_followup": "Resolve Gene, Allele, or AGM target.",
                    "evidence_record_ids": ["reduced-brood-size-evidence-1"],
                }
            ],
            "notes": ["Export/write behavior is blocked."],
            "provenance": {"semantic_source": "curatable_objects"},
        },
        "run_summary": {
            "candidate_count": 2,
            "kept_count": 1,
            "excluded_count": 1,
            "ambiguous_count": 1,
            "warnings": ["Subject subtype remains pending."],
        },
    }


def test_phenotype_extractor_schema_accepts_domain_pack_objects_and_metadata():
    envelope = _validate_phenotype_extractor_payload(_valid_phenotype_payload())

    annotation = envelope.curatable_objects[-1]
    assert annotation.object_type == PHENOTYPE_OBJECT_TYPE
    assert annotation.model_ref == "PhenotypeAnnotationPayload"
    assert annotation.payload.phenotype_terms[0].curie == "WBPhenotype:0000886"
    assert annotation.evidence_record_ids == ["reduced-brood-size-evidence-1"]
    assert envelope.metadata.exclusions[0].reason_code == "previously_reported"


def test_phenotype_extractor_schema_accepts_pending_term_without_curie():
    payload = _valid_phenotype_payload()
    term = payload["curatable_objects"][2]
    term["payload"]["curie"] = None
    annotation_term = payload["curatable_objects"][-1]["payload"]["phenotype_terms"][0]
    annotation_term["curie"] = None

    envelope = _validate_phenotype_extractor_payload(payload)

    phenotype_term = envelope.curatable_objects[2]
    assert phenotype_term.payload.curie is None
    assert phenotype_term.payload.label == "reduced brood size"
    assert phenotype_term.payload.resolution_state == "pending_ontology_resolution"
    assert phenotype_term.payload.export_state == (
        "blocked_pending_ontology_resolution"
    )


def test_phenotype_extractor_schema_requires_taxon_for_resolved_subjects():
    payload = copy.deepcopy(_valid_phenotype_payload())
    subject_payload = payload["curatable_objects"][1]["payload"]
    subject_payload["resolution_state"] = "resolved"
    subject_payload.pop("taxon", None)
    annotation_subject = payload["curatable_objects"][-1]["payload"][
        "phenotype_annotation_subject"
    ]
    annotation_subject["resolution_state"] = "resolved"
    annotation_subject.pop("taxon", None)

    with pytest.raises(ValidationError, match="taxon"):
        _phenotype_extractor_schema().model_validate(payload)


@pytest.mark.parametrize("legacy_field", sorted(LEGACY_SEMANTIC_LIST_FIELDS))
def test_phenotype_extractor_schema_rejects_top_level_legacy_semantic_lists(legacy_field):
    payload = _valid_phenotype_payload()
    payload[legacy_field] = []

    with pytest.raises(ValidationError, match=legacy_field):
        _phenotype_extractor_schema().model_validate(payload)


def test_phenotype_extractor_schema_rejects_evidence_ids_missing_from_metadata():
    payload = _valid_phenotype_payload()
    payload["metadata"]["evidence_records"] = []

    with pytest.raises(ValidationError) as exc_info:
        _phenotype_extractor_schema().model_validate(payload)

    assert "metadata.evidence_records[]" in str(exc_info.value)
    assert "reduced-brood-size-evidence-1" in str(exc_info.value)


def test_phenotype_extractor_schema_rejects_dangling_object_refs():
    payload = copy.deepcopy(_valid_phenotype_payload())
    payload["curatable_objects"][-1]["object_refs"][0]["pending_ref_id"] = (
        "missing-subject-ref"
    )

    with pytest.raises(ValidationError) as exc_info:
        _phenotype_extractor_schema().model_validate(payload)

    assert "object_refs references unknown objects" in str(exc_info.value)
    assert "missing-subject-ref" in str(exc_info.value)


@pytest.mark.parametrize(
    ("location", "field_name", "value"),
    (
        ("object", "repair_hints", ["legacy repair hint"]),
        ("metadata", "repair_notes", ["legacy repair note"]),
        ("top_level", "repair_mode", True),
    ),
)
def test_phenotype_extractor_schema_rejects_repair_surfaces(
    location: str,
    field_name: str,
    value: object,
):
    payload = copy.deepcopy(_valid_phenotype_payload())
    if location == "object":
        payload["curatable_objects"][-1][field_name] = value
    elif location == "metadata":
        payload["metadata"][field_name] = value
    else:
        payload[field_name] = value

    with pytest.raises(ValidationError):
        _phenotype_extractor_schema().model_validate(payload)


def test_phenotype_extractor_prompt_agent_and_group_rules_name_domain_contract():
    source = _phenotype_extractor_source()
    prompt_content = str(
        yaml.safe_load(source.prompt_yaml.read_text(encoding="utf-8"))["content"]
    )
    generated_content = assembly.build_agent_core_prompt("phenotype_extractor").render()
    agent_data = yaml.safe_load(source.agent_yaml.read_text(encoding="utf-8"))

    assert agent_data["tools"] == [
        "search_document",
        "read_section",
        "read_subsection",
        "record_evidence",
        "get_agent_contract",
        "agr_curation_query",
    ]
    assert "curatable_objects[]" in agent_data["supervisor_routing"]["description"]
    assert "locked generated runtime contract owns deterministic tool inventory" in prompt_content
    assert "PhenotypeAnnotation(PhenotypeAnnotationPayload role=curatable_unit" in generated_content
    assert "Schema/provider refs: alliance_linkml." in generated_content
    assert "PhenotypeSubject" in generated_content
    assert "PhenotypeTerm" in generated_content
    assert "taxon" in generated_content
    assert "PhenotypeSubject.taxon->phenotype_subject_entity_validator" in generated_content
    assert "Active validator bindings own validator result fields" in generated_content
    assert "repair_mode" not in prompt_content
    assert "repair_notes" not in prompt_content
    assert "repair_hints" not in prompt_content
    assert "normalized_id" not in prompt_content
    assert "candidate_terms" not in prompt_content

    for group_rule_file in source.group_rule_files:
        content = str(yaml.safe_load(group_rule_file.read_text(encoding="utf-8"))["content"])
        assert "PhenotypeAnnotation" not in content
        assert "PhenotypeSubject" not in content
        assert "PhenotypeTerm" not in content
        assert "metadata.ambiguities[]" not in content


def test_phenotype_payload_persists_as_curatable_objects_only_for_new_runs():
    payload = _valid_phenotype_payload()
    candidate, evidence_metadata = build_extraction_envelope_candidate_with_evidence(
        json.dumps(payload),
        agent_key="phenotype_extractor",
        adapter_key="phenotype",
        conversation_summary="Extract pending phenotype assertions.",
    )

    assert candidate is not None
    assert candidate.payload_json["curatable_objects"][-1]["object_type"] == (
        PHENOTYPE_OBJECT_TYPE
    )
    assert LEGACY_SEMANTIC_LIST_FIELDS.isdisjoint(candidate.payload_json)
    assert evidence_metadata["evidence_count"] == 1
    assert evidence_metadata["evidence_records"][0]["evidence_record_id"] == (
        "reduced-brood-size-evidence-1"
    )


def test_phenotype_domain_pack_loads_tool_verified_pending_fixture():
    registry = load_alliance_domain_pack_registry()
    pack = registry.get_pack(PHENOTYPE_DOMAIN_PACK_ID)
    assert pack is not None
    fixture_ref = registry.get_fixture_pack_ref(
        PHENOTYPE_DOMAIN_PACK_ID,
        PHENOTYPE_FIXTURE_PACK_ID,
    )
    assert fixture_ref is not None

    fixture_pack = load_domain_fixture_pack(pack.pack_path / fixture_ref.path)
    envelope = fixture_pack.fixtures[0].envelope

    assert validate_pending_phenotype_envelope(envelope) == ()
    assert envelope.metadata["semantic_source"] == "domain_envelope.objects"
    assert LEGACY_SEMANTIC_LIST_FIELDS.isdisjoint(envelope.metadata)
    assert envelope.metadata["raw_mentions"][0]["mention"] == "reduced brood size"
    assert envelope.metadata["exclusions"][0]["reason_code"] == "previously_reported"
    assert envelope.metadata["ambiguities"][0]["mention"] == "daf-2(e1370)"
    annotation = next(obj for obj in envelope.objects if obj.object_type == PHENOTYPE_OBJECT_TYPE)
    assert annotation.object_role == "curatable_unit"
    assert annotation.payload["phenotype_terms"][0]["curie"] == "WBPhenotype:0000886"
    assert annotation.metadata["write_behavior"]["status"] == "blocked"


def test_phenotype_domain_pack_validator_rejects_legacy_semantic_keys():
    registry = load_alliance_domain_pack_registry()
    pack = registry.get_pack(PHENOTYPE_DOMAIN_PACK_ID)
    assert pack is not None
    fixture_ref = registry.get_fixture_pack_ref(
        PHENOTYPE_DOMAIN_PACK_ID,
        PHENOTYPE_FIXTURE_PACK_ID,
    )
    assert fixture_ref is not None
    fixture_pack = load_domain_fixture_pack(pack.pack_path / fixture_ref.path)
    envelope = fixture_pack.fixtures[0].envelope
    envelope_with_legacy_key = envelope.model_copy(
        update={"metadata": {**envelope.metadata, "phenotypes": []}}
    )

    findings = validate_pending_phenotype_envelope(envelope_with_legacy_key)

    assert [finding.code for finding in findings] == [
        "alliance.phenotype.legacy_semantic_store_present"
    ]

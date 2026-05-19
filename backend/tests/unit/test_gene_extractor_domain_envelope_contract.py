"""Gene extractor domain-envelope migration coverage."""

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
from src.lib.packages.tool_registry import load_tool_registry
from src.schemas.models import LEGACY_SEMANTIC_LIST_FIELDS


REPO_ROOT = Path(__file__).resolve().parents[3]
REPO_PACKAGES_DIR = REPO_ROOT / "packages"
ALLIANCE_PYTHON_SRC = REPO_ROOT / "packages" / "alliance" / "python" / "src"
if str(ALLIANCE_PYTHON_SRC) not in sys.path:
    sys.path.insert(0, str(ALLIANCE_PYTHON_SRC))

from agr_ai_curation_alliance.domain_packs.gene import (  # noqa: E402
    GENE_DOMAIN_PACK_ID,
    GENE_MENTION_EVIDENCE_MODEL_ID,
    GENE_MENTION_EVIDENCE_OBJECT_TYPE,
    tool_verified_gene_output_to_pending_envelope,
)


@pytest.fixture(autouse=True)
def _reset_config_caches(monkeypatch):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))
    agent_loader.reset_cache()
    schema_discovery.reset_cache()
    yield
    agent_loader.reset_cache()
    schema_discovery.reset_cache()


def _gene_extractor_source():
    return next(
        source
        for source in agent_sources.resolve_agent_config_sources(REPO_PACKAGES_DIR)
        if source.folder_name == "gene_extractor"
    )


def _gene_extractor_schema():
    schema_discovery.discover_agent_schemas(force_reload=True)
    schema_cls = schema_discovery.get_schema_for_agent("gene_extractor")
    assert schema_cls is not None
    return schema_cls


def _validate_gene_extractor_payload(payload: dict[str, object]):
    return _gene_extractor_schema().model_validate(payload)


def _valid_gene_extractor_payload() -> dict[str, object]:
    return {
        "summary": "Retained one verified daf-16 gene mention.",
        "curatable_objects": [
            {
                "object_type": GENE_MENTION_EVIDENCE_OBJECT_TYPE,
                "object_role": "validated_reference",
                "pending_ref_id": "gene-mention-evidence-1",
                "model_ref": GENE_MENTION_EVIDENCE_MODEL_ID,
                "schema_ref": {
                    "schema_id": "alliance.linkml.Gene",
                    "provider": "alliance_linkml",
                    "name": "Gene",
                    "version": "1b11d0888f19eba4ca72022200bb7d96b30d4a52",
                    "uri": (
                        "https://github.com/alliance-genome/agr_curation_schema/blob/"
                        "1b11d0888f19eba4ca72022200bb7d96b30d4a52/model/schema/gene.yaml"
                    ),
                },
                "definition_state": "in_development",
                "definition_notes": [
                    "Envelope-only validated reference evidence; this object does not create or mutate Alliance Gene rows."
                ],
                "payload": {
                    "mention": "daf-16",
                    "species": "Caenorhabditis elegans",
                    "taxon_hint": "NCBITaxon:6239",
                    "data_provider_hint": "WB",
                    "proposed_primary_external_id": "WB:WBGene00000912",
                    "proposed_gene_symbol": "daf-16",
                    "proposed_taxon": "NCBITaxon:6239",
                    "identity_resolution_notes": [
                        "Paper context supports C. elegans/WormBase provider context."
                    ],
                    "confidence": "high",
                    "evidence_record_id": "ev-daf16-1",
                    "verified_quote": "DAF-16 translocated to nuclei after heat shock.",
                    "page": 4,
                    "section": "Results",
                    "subsection": "Stress response assay",
                    "chunk_id": "chunk-daf16-1",
                    "figure_reference": "Figure 2A",
                },
                "evidence_record_ids": ["ev-daf16-1"],
                "metadata_refs": [
                    {"metadata_path": "raw_mentions[0]", "role": "source_mention"},
                    {"metadata_path": "evidence_records[0]", "role": "supporting_evidence"},
                ],
            }
        ],
        "metadata": {
            "raw_mentions": [
                {
                    "mention": "daf-16",
                    "entity_type": "gene",
                    "evidence_record_ids": ["ev-daf16-1"],
                }
            ],
            "evidence_records": [
                {
                    "evidence_record_id": "ev-daf16-1",
                    "entity": "daf-16",
                    "verified_quote": "DAF-16 translocated to nuclei after heat shock.",
                    "page": 4,
                    "section": "Results",
                    "subsection": "Stress response assay",
                    "chunk_id": "chunk-daf16-1",
                    "figure_reference": "Figure 2A",
                }
            ],
            "normalization_notes": [
                "Proposed daf-16 identity from paper-backed species context for validator confirmation."
            ],
            "exclusions": [
                {
                    "mention": "FOXO family",
                    "reason_code": "gene_family_not_individual",
                    "evidence_record_ids": [],
                    "details": "Family-level term, not an individual gene.",
                }
            ],
            "ambiguities": [
                {
                    "mention": "daf",
                    "why_ambiguous": "Mention did not resolve to a single gene.",
                    "recommended_followup": "Inspect the source table.",
                    "evidence_record_ids": [],
                }
            ],
            "notes": ["One retained validated-reference evidence object."],
            "provenance": {"source_agent": "gene_extractor"},
        },
        "run_summary": {
            "candidate_count": 3,
            "kept_count": 1,
            "excluded_count": 1,
            "ambiguous_count": 1,
            "warnings": ["One ambiguous mention preserved as metadata."],
        },
    }


def test_gene_extractor_schema_accepts_gene_mention_evidence_domain_envelope():
    envelope = _validate_gene_extractor_payload(_valid_gene_extractor_payload())

    obj = envelope.curatable_objects[0]
    assert obj.object_type == GENE_MENTION_EVIDENCE_OBJECT_TYPE
    assert obj.object_role == "validated_reference"
    assert obj.model_ref == GENE_MENTION_EVIDENCE_MODEL_ID
    assert obj.payload.proposed_primary_external_id == "WB:WBGene00000912"
    assert obj.payload.taxon_hint == "NCBITaxon:6239"
    assert not hasattr(obj.payload, "primary_external_id")
    assert obj.payload.evidence_record_id == "ev-daf16-1"
    assert obj.evidence_record_ids == ["ev-daf16-1"]
    assert envelope.metadata.raw_mentions[0].mention == "daf-16"
    assert envelope.metadata.ambiguities[0].mention == "daf"


@pytest.mark.parametrize("required_field", ("schema_ref", "definition_notes"))
def test_gene_extractor_schema_requires_object_contract_fields(required_field):
    payload = _valid_gene_extractor_payload()
    del payload["curatable_objects"][0][required_field]

    with pytest.raises(ValidationError, match=required_field):
        _gene_extractor_schema().model_validate(payload)


@pytest.mark.parametrize("legacy_field", sorted(LEGACY_SEMANTIC_LIST_FIELDS))
def test_gene_extractor_schema_rejects_top_level_legacy_semantic_lists(legacy_field):
    payload = _valid_gene_extractor_payload()
    payload[legacy_field] = []

    with pytest.raises(ValidationError, match=legacy_field):
        _gene_extractor_schema().model_validate(payload)


def test_gene_extractor_schema_rejects_legacy_gene_assertion_payload():
    payload = _valid_gene_extractor_payload()
    payload["curatable_objects"][0]["object_type"] = "GeneAssertion"
    payload["curatable_objects"][0]["object_role"] = "curatable_unit"
    payload["curatable_objects"][0]["payload"] = {
        "mention": "daf-16",
        "normalized_symbol": "daf-16",
        "normalized_id": "WB:WBGene00000912",
        "confidence": "high",
    }

    with pytest.raises(ValidationError) as exc_info:
        _gene_extractor_schema().model_validate(payload)

    message = str(exc_info.value)
    assert GENE_MENTION_EVIDENCE_OBJECT_TYPE in message
    assert "normalized_symbol" in message or "proposed_primary_external_id" in message


def test_gene_extractor_schema_requires_payload_metadata_evidence_alignment():
    payload = _valid_gene_extractor_payload()
    payload["metadata"]["evidence_records"][0]["verified_quote"] = "different quote"

    with pytest.raises(ValidationError, match="payload.verified_quote"):
        _gene_extractor_schema().model_validate(payload)


@pytest.mark.parametrize(
    ("location", "field_name", "value"),
    (
        ("object", "repair_hints", ["legacy repair hint"]),
        ("metadata", "repair_notes", ["legacy repair note"]),
        ("top_level", "repair_mode", True),
    ),
)
def test_gene_extractor_schema_rejects_repair_surfaces(
    location: str,
    field_name: str,
    value: object,
):
    payload = _valid_gene_extractor_payload()
    if location == "object":
        payload["curatable_objects"][0][field_name] = value
    elif location == "metadata":
        payload["metadata"][field_name] = value
    else:
        payload[field_name] = value

    with pytest.raises(ValidationError):
        _gene_extractor_schema().model_validate(payload)


def test_gene_extractor_prompt_agent_and_group_rules_name_domain_envelope_contract():
    source = _gene_extractor_source()
    prompt_content = str(
        yaml.safe_load(source.prompt_yaml.read_text(encoding="utf-8"))["content"]
    )
    agent_data = yaml.safe_load(source.agent_yaml.read_text(encoding="utf-8"))

    assert "gene_mention_evidence" in agent_data["description"]
    assert "curatable_objects[]" in agent_data["supervisor_routing"]["description"]
    assert '"object_type": "gene_mention_evidence"' in prompt_content
    assert "`object_role`: `validated_reference`" in prompt_content
    assert "`model_ref`: `GeneMentionEvidencePayload`" in prompt_content
    assert "`payload.proposed_primary_external_id`" in prompt_content
    assert "agr_species_context_lookup" in prompt_content
    assert "agr_species_context_lookup" in agent_data["tools"]
    assert "agr_curation_query" not in agent_data["tools"]
    assert "Active validator bindings own final Alliance Gene identity decisions" in prompt_content
    assert "repair_mode" not in prompt_content
    assert "repair_hints" not in prompt_content
    assert "repair_notes" not in prompt_content
    assert '"object_type": "GeneAssertion"' not in prompt_content
    assert '"normalized_symbol"' not in prompt_content
    assert '"normalized_id"' not in prompt_content
    example_object_count = prompt_content.count('"object_type": "gene_mention_evidence"')
    assert example_object_count == 3
    assert prompt_content.count('"schema_ref": {') == example_object_count
    assert prompt_content.count('"definition_notes": [') == example_object_count

    for group_rule_file in source.group_rule_files:
        content = str(yaml.safe_load(group_rule_file.read_text(encoding="utf-8"))["content"])
        assert "gene_mention_evidence" in content
        assert "GeneMentionEvidencePayload" in content
        assert "payload.proposed_primary_external_id" in content


def test_gene_extractor_species_context_tool_is_registered_without_broad_lookup():
    source = _gene_extractor_source()
    agent_data = yaml.safe_load(source.agent_yaml.read_text(encoding="utf-8"))
    registry = load_tool_registry(REPO_PACKAGES_DIR)
    species_tool = registry.get("agr_species_context_lookup")

    assert species_tool is not None
    assert species_tool.import_path == (
        "agr_ai_curation_alliance.tools.agr_curation:agr_species_context_lookup"
    )
    assert "agr_species_context_lookup" in agent_data["tools"]
    assert "agr_curation_query" not in agent_data["tools"]


def test_gene_extractor_payload_persists_as_curatable_objects_only_for_new_runs():
    payload = _valid_gene_extractor_payload()
    candidate, evidence_metadata = build_extraction_envelope_candidate_with_evidence(
        json.dumps(payload),
        agent_key="gene_extractor",
        adapter_key="gene",
        conversation_summary="Extract gene mention evidence.",
    )

    assert candidate is not None
    assert candidate.payload_json["curatable_objects"][0]["object_type"] == (
        GENE_MENTION_EVIDENCE_OBJECT_TYPE
    )
    assert LEGACY_SEMANTIC_LIST_FIELDS.isdisjoint(candidate.payload_json)
    assert evidence_metadata["evidence_count"] == 1
    assert evidence_metadata["evidence_records"][0]["evidence_record_id"] == "ev-daf16-1"


def test_gene_domain_pack_fixture_converts_to_pending_gene_mention_envelope():
    raw_fixture_path = (
        REPO_ROOT
        / "backend"
        / "tests"
        / "fixtures"
        / "domain_packs"
        / "gene"
        / "tool_verified_gene_output.yaml"
    )
    raw_fixture = yaml.safe_load(raw_fixture_path.read_text(encoding="utf-8"))

    converted = tool_verified_gene_output_to_pending_envelope(raw_fixture)

    assert converted.domain_pack_id == GENE_DOMAIN_PACK_ID
    assert converted.objects[0].object_type == GENE_MENTION_EVIDENCE_OBJECT_TYPE
    assert converted.objects[0].payload["primary_external_id"] == "WB:WBGene00000912"
    assert LEGACY_SEMANTIC_LIST_FIELDS.isdisjoint(converted.metadata)
    assert all(LEGACY_SEMANTIC_LIST_FIELDS.isdisjoint(obj.payload) for obj in converted.objects)


def test_gene_extractor_schema_rejects_missing_raw_mentions_for_retained_objects():
    payload = copy.deepcopy(_valid_gene_extractor_payload())
    payload["metadata"]["raw_mentions"] = []

    with pytest.raises(ValidationError, match="metadata.raw_mentions"):
        _gene_extractor_schema().model_validate(payload)

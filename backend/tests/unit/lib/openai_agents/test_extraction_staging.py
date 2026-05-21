"""Tests for run-scoped extraction builder staging."""

from __future__ import annotations

import importlib.util
import asyncio
from pathlib import Path

import pytest

from src.lib.openai_agents.extraction_staging import (
    activate_extraction_staging,
    clear_extraction_staging,
    current_extraction_staging_state,
    finalize_allele_extraction_payload,
    finalized_ack_from_state,
    finalized_envelope_from_state,
    record_document_retrieval_call,
    register_verified_evidence_record,
    stage_allele_paper_evidence_payload,
)
from src.schemas.domain_pack_metadata import DomainPackExtractionBuilder


REPO_ROOT = Path(__file__).resolve().parents[5]


def _load_allele_output_type():
    schema_path = REPO_ROOT / "packages/alliance/agents/allele_extractor/schema.py"
    spec = importlib.util.spec_from_file_location(
        "allele_builder_test_schema",
        schema_path,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.AlleleExtractionResultEnvelope


AlleleExtractionResultEnvelope = _load_allele_output_type()


def _builder() -> DomainPackExtractionBuilder:
    return DomainPackExtractionBuilder.model_validate(
        {
            "enabled": True,
            "stage_tool": "stage_allele_paper_evidence",
            "finalize_tool": "finalize_allele_extraction",
            "retained_unit": "allele_paper_evidence",
            "model_final_ack_schema": "ExtractionToolFinalizationAck",
            "curation_output_schema": "AlleleExtractionResultEnvelope",
            "fields": {
                "mention_text": {
                    "json_type": "string",
                    "required": True,
                    "maps_to": "AlleleMention.mention.text",
                },
                "evidence_record_ids": {
                    "json_type": "array",
                    "required": True,
                    "collection": True,
                    "min_items": 1,
                    "maps_to": "EvidenceQuote.evidence_record_id",
                },
                "associated_gene_symbol": {
                    "json_type": "string",
                    "maps_to": "AlleleMention.associated_gene.symbol",
                },
                "taxon_curie": {
                    "json_type": "string",
                    "maps_to": "AlleleMention.taxon.curie",
                },
            },
            "finalize_fields": {
                "summary": {"json_type": "string", "required": True},
                "candidate_count": {"json_type": "integer", "required": True},
                "kept_count": {"json_type": "integer", "required": True},
                "excluded_count": {"json_type": "integer", "required": True},
                "ambiguous_count": {"json_type": "integer", "required": True},
            },
            "object_graph": {
                "required_objects": [
                    "AllelePaperEvidenceAssociation",
                    "Reference",
                    "AlleleMention",
                    "EvidenceQuote",
                ],
                "validator_target": {
                    "object_type": "AlleleMention",
                    "field_path": "mention.text",
                },
                "objects": [
                    {
                        "object_type": "AllelePaperEvidenceAssociation",
                        "model_ref": "AllelePaperEvidenceAssociationPayload",
                        "object_role": "curatable_unit",
                        "pending_ref_template": "association_{staged_id}",
                    },
                    {
                        "object_type": "Reference",
                        "model_ref": "ReferencePayload",
                        "object_role": "validated_reference",
                        "pending_ref_template": "reference_{staged_id}",
                    },
                    {
                        "object_type": "AlleleMention",
                        "model_ref": "AlleleMentionPayload",
                        "object_role": "metadata_only",
                        "pending_ref_template": "mention_{staged_id}",
                    },
                    {
                        "object_type": "EvidenceQuote",
                        "model_ref": "EvidenceQuotePayload",
                        "object_role": "metadata_only",
                        "pending_ref_template": "evidence_quote_{evidence_record_id}",
                    },
                ],
            },
            "allowed_exclusion_reason_codes": [
                "previously_reported",
                "strain_not_allele",
            ],
        }
    )


@pytest.fixture
def staging_state():
    token = activate_extraction_staging(
        agent_id="ask_allele_extractor_specialist",
        specialist_name="Allele Extraction",
        domain_pack_id="agr.alliance.allele",
        domain_pack_version="0.1.0",
        builder=_builder(),
        curation_output_type=AlleleExtractionResultEnvelope,
    )
    try:
        yield current_extraction_staging_state(required=True)
    finally:
        clear_extraction_staging(token)


def _evidence_record(
    evidence_record_id: str = "ev-daf2-m41",
    *,
    quote: str = "Sequencing identified a G-to-A substitution in daf-2(m41).",
) -> dict[str, object]:
    return {
        "evidence_record_id": evidence_record_id,
        "entity": "daf-2(m41)",
        "verified_quote": quote,
        "page": 2,
        "section": "Results",
        "chunk_id": "chunk-results-1",
    }


def test_staging_context_clears_without_leaking_state():
    token = activate_extraction_staging(
        agent_id="ask_allele_extractor_specialist",
        specialist_name="Allele Extraction",
        domain_pack_id="agr.alliance.allele",
        domain_pack_version="0.1.0",
        builder=_builder(),
        curation_output_type=AlleleExtractionResultEnvelope,
    )

    assert current_extraction_staging_state(required=True).agent_id == (
        "ask_allele_extractor_specialist"
    )
    clear_extraction_staging(token)
    assert current_extraction_staging_state() is None


@pytest.mark.asyncio
async def test_staging_context_is_task_local():
    async def _run_with_agent(agent_id: str) -> str:
        token = activate_extraction_staging(
            agent_id=agent_id,
            specialist_name="Allele Extraction",
            domain_pack_id="agr.alliance.allele",
            domain_pack_version="0.1.0",
            builder=_builder(),
            curation_output_type=AlleleExtractionResultEnvelope,
        )
        try:
            await asyncio.sleep(0)
            return current_extraction_staging_state(required=True).agent_id
        finally:
            clear_extraction_staging(token)

    assert await asyncio.gather(
        _run_with_agent("agent-a"),
        _run_with_agent("agent-b"),
    ) == ["agent-a", "agent-b"]
    assert current_extraction_staging_state() is None


def test_stage_and_finalize_builds_valid_allele_envelope(staging_state):
    record_document_retrieval_call("search_document", {"query": "daf-2 m41"})
    register_verified_evidence_record(_evidence_record())
    register_verified_evidence_record(
        _evidence_record(
            "ev-daf2-phenotype",
            quote="daf-2(m41) animals showed a temperature-sensitive phenotype.",
        )
    )

    staged = stage_allele_paper_evidence_payload(
        {
            "mention_text": "daf-2(m41)",
            "evidence_record_ids": ["ev-daf2-m41", "ev-daf2-phenotype"],
            "verified_quotes": [
                "Sequencing identified a G-to-A substitution in daf-2(m41).",
                "daf-2(m41) animals showed a temperature-sensitive phenotype.",
            ],
            "page": 2,
            "section": "Results",
            "chunk_id": "chunk-results-1",
            "associated_gene_symbol": "daf-2",
            "taxon_curie": "NCBITaxon:6239",
            "reference": {"title": "A daf-2 allele paper"},
        }
    )

    assert staged["status"] == "staged"
    duplicate = stage_allele_paper_evidence_payload(
        {
            "mention_text": "daf-2(m41)",
            "evidence_record_ids": ["ev-daf2-phenotype", "ev-daf2-m41"],
        }
    )
    assert duplicate["idempotent"] is True

    finalized = finalize_allele_extraction_payload(
        {
            "summary": "Retained one allele finding.",
            "candidate_count": 1,
            "kept_count": 1,
            "excluded_count": 0,
            "ambiguous_count": 0,
        }
    )

    assert finalized["status"] == "finalized"
    ack = finalized_ack_from_state()
    assert ack == {
        "status": "complete",
        "finalized_run_id": staging_state.run_id,
        "summary": "Retained one allele finding.",
        "staged_count": 1,
        "finalized_count": 1,
    }
    assert "curatable_objects" not in ack

    envelope = finalized_envelope_from_state()
    assert envelope is not None
    object_types = [item["object_type"] for item in envelope["curatable_objects"]]
    assert "Allele" not in object_types
    assert object_types.count("Reference") == 1
    assert object_types.count("AlleleMention") == 1
    assert object_types.count("EvidenceQuote") == 2
    assert object_types.count("AllelePaperEvidenceAssociation") == 1

    association = next(
        item
        for item in envelope["curatable_objects"]
        if item["object_type"] == "AllelePaperEvidenceAssociation"
    )
    assert association["payload"]["evidence_record_ids"] == [
        "ev-daf2-m41",
        "ev-daf2-phenotype",
    ]
    assert association["evidence_record_ids"] == [
        "ev-daf2-m41",
        "ev-daf2-phenotype",
    ]
    assert association["metadata"]["write_behavior"]["status"] == "blocked"
    assert {ref["object_type"] for ref in association["object_refs"]} == {
        "Reference",
        "AlleleMention",
        "EvidenceQuote",
    }
    assert staging_state.validator_target_count == 1


def test_stage_requires_verified_evidence_and_single_mention(staging_state):
    missing = stage_allele_paper_evidence_payload(
        {
            "mention_text": "daf-2(m41)",
            "evidence_record_ids": ["unknown-evidence"],
        }
    )
    assert missing["status"] == "needs_repair"
    assert "evidence_record_ids" in missing["invalid_fields"]

    register_verified_evidence_record(_evidence_record())
    joined_mentions = stage_allele_paper_evidence_payload(
        {
            "mention_text": "daf-2(m41), daf-16(mu86)",
            "evidence_record_ids": ["ev-daf2-m41"],
        }
    )
    assert joined_mentions["status"] == "needs_repair"
    assert "mention_text" in joined_mentions["invalid_fields"]

    invented_identity = stage_allele_paper_evidence_payload(
        {
            "mention_text": "daf-2(m41)",
            "evidence_record_ids": ["ev-daf2-m41"],
            "allele_identifier": "WB:WBVar00000001",
        }
    )
    assert invented_identity["status"] == "needs_repair"
    assert "allele_identifier" in invented_identity["invalid_fields"]


def test_finalize_rejects_count_mismatches_and_bad_exclusion_codes(staging_state):
    record_document_retrieval_call("read_section", {"section": "Results"})
    register_verified_evidence_record(_evidence_record())
    assert stage_allele_paper_evidence_payload(
        {
            "mention_text": "daf-2(m41)",
            "evidence_record_ids": ["ev-daf2-m41"],
        }
    )["status"] == "staged"

    finalized = finalize_allele_extraction_payload(
        {
            "summary": "Bad counts.",
            "candidate_count": 1,
            "kept_count": 0,
            "excluded_count": 1,
            "ambiguous_count": 0,
            "exclusions": [
                {"mention": "N2", "reason_code": "not_a_reason"},
            ],
        }
    )

    assert finalized["status"] == "needs_repair"
    assert any("kept_count" in item for item in finalized["invalid_fields"])
    assert any("unsupported exclusion" in item for item in finalized["invalid_fields"])


def test_empty_finalization_succeeds_after_document_coverage(staging_state):
    record_document_retrieval_call("read_section", {"section": "Results"})

    finalized = finalize_allele_extraction_payload(
        {
            "summary": "No curatable allele findings retained.",
            "candidate_count": 1,
            "kept_count": 0,
            "excluded_count": 1,
            "ambiguous_count": 0,
            "exclusions": [
                {
                    "mention": "N2",
                    "reason_code": "strain_not_allele",
                    "details": "Background strain only.",
                }
            ],
        }
    )

    assert finalized["status"] == "finalized"
    envelope = finalized_envelope_from_state()
    assert envelope is not None
    assert envelope["curatable_objects"] == []
    assert staging_state.zero_validator_jobs_status == "empty_finalized_output"

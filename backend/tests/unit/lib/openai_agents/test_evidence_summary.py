"""Unit tests for evidence registry canonicalization helpers."""

from src.lib.openai_agents.evidence_summary import (
    build_record_evidence_summary_record,
    canonicalize_structured_result_payload,
    structured_result_missing_evidence_record_refs,
    structured_result_requires_evidence,
)
from src.lib.openai_agents.models import AlleleExtractionResultEnvelope


def _record(
    *,
    entity: str,
    quote: str = "Shared sentence from the paper.",
    chunk_id: str = "chunk-1",
    page: int = 3,
    section: str = "Results",
) -> dict[str, object]:
    return {
        "entity": entity,
        "verified_quote": quote,
        "page": page,
        "section": section,
        "chunk_id": chunk_id,
    }


def test_canonicalize_keeps_distinct_live_records_when_entities_share_same_locator():
    payload = {
        "items": [
            {
                "label": "Actin 5C",
                "entity_type": "gene",
                "source_mentions": ["Actin 5C"],
                "evidence_record_ids": ["evidence-live-a"],
            },
            {
                "label": "Actin 87E",
                "entity_type": "gene",
                "source_mentions": ["Actin 87E"],
                "evidence_record_ids": ["evidence-live-b"],
            },
        ],
        "evidence_records": [],
        "run_summary": {"kept_count": 2},
    }

    canonical = canonicalize_structured_result_payload(
        payload,
        preferred_evidence_records=[
            {**_record(entity="Actin 5C"), "evidence_record_id": "evidence-live-a"},
            {**_record(entity="Actin 87E"), "evidence_record_id": "evidence-live-b"},
        ],
    )

    assert canonical["evidence_records"] == [
        {**_record(entity="Actin 5C"), "evidence_record_id": "evidence-live-a"},
        {**_record(entity="Actin 87E"), "evidence_record_id": "evidence-live-b"},
    ]


def test_missing_evidence_record_refs_when_kept_count_positive_and_items_missing():
    assert structured_result_missing_evidence_record_refs(
        {
            "summary": "Retained a finding but dropped items.",
            "evidence_records": [
                {
                    **_record(entity="Actin 5C"),
                    "evidence_record_id": "evidence-live-a",
                }
            ],
            "run_summary": {"kept_count": 1},
        }
    ) is True


def test_schema_defined_retained_collection_satisfies_evidence_guard_without_items():
    payload = {
        "summary": "Retained one focal allele with verified evidence.",
        "alleles": [
            {
                "mention": "Actin 5C",
                "normalized_symbol": "Act5C",
                "normalized_id": "FB:FBal0000001",
                "associated_gene": "Act5C",
                "confidence": "high",
                "evidence_record_ids": ["evidence-live-a"],
            }
        ],
        "items": [],
        "evidence_records": [
            {**_record(entity="Actin 5C"), "evidence_record_id": "evidence-live-a"}
        ],
        "run_summary": {"kept_count": 1},
    }

    assert structured_result_requires_evidence(
        payload,
        expected_output_type=AlleleExtractionResultEnvelope,
    ) is True
    assert structured_result_missing_evidence_record_refs(
        payload,
        expected_output_type=AlleleExtractionResultEnvelope,
    ) is False


def test_schema_defined_auxiliary_lists_do_not_satisfy_retained_evidence_guard():
    payload = {
        "summary": "Kept count drifted positive but only raw mentions survived.",
        "raw_mentions": [
            {
                "mention": "Actin 5C",
                "entity_type": "allele",
                "evidence_record_ids": ["evidence-live-a"],
            }
        ],
        "items": [],
        "evidence_records": [
            {**_record(entity="Actin 5C"), "evidence_record_id": "evidence-live-a"}
        ],
        "run_summary": {"kept_count": 1},
    }

    assert structured_result_missing_evidence_record_refs(
        payload,
        expected_output_type=AlleleExtractionResultEnvelope,
    ) is True


def test_record_evidence_summary_uses_resolved_output_chunk_id():
    resolved_chunk_id = "1b3651f8-7745-51a0-80f3-b3eafb70a558"

    record = build_record_evidence_summary_record(
        tool_name="record_evidence",
        tool_input={
            "entity": "Trp53 fl/fl ;Wwox fl/fl",
            "chunk_id": "stale-input-id",
            "claimed_quote": "The strains were bred in the source passage.",
        },
        tool_output={
            "status": "verified",
            "chunk_id": resolved_chunk_id,
            "verified_quote": "The strains were bred in the source passage.",
            "page": 11,
            "section": "Source Passage",
        },
    )

    assert record is not None
    assert record["chunk_id"] == resolved_chunk_id


def test_record_evidence_summary_ignores_quote_mismatch_results():
    record = build_record_evidence_summary_record(
        tool_name="record_evidence",
        tool_input={
            "entity": "CD8a-/-",
            "chunk_id": "b247a1a2-a6fa-2176-46ff-b814431e61c8",
            "claimed_quote": "CD8a-/- (Strain NO. S-KO-01440) mice were purchased from Cyagen.",
        },
        tool_output={
            "status": "quote_mismatch",
            "needs_retry": True,
            "closest_quote": "CD4-/- (Strain NO. S-KO-01417) mice were purchased from Cyagen.",
            "page": 22,
            "section": "Methods",
            "mismatch_reasons": [
                "allele_or_entity_identifier_mismatch",
                "strain_or_stock_identifier_mismatch",
            ],
        },
    )

    assert record is None

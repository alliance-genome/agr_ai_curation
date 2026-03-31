"""Unit tests for evidence registry canonicalization helpers."""

from src.lib.openai_agents.evidence_summary import (
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

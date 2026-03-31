"""Unit tests for evidence registry canonicalization helpers."""

from src.lib.openai_agents.evidence_summary import (
    canonicalize_structured_result_payload,
    structured_result_missing_evidence_record_refs,
)


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

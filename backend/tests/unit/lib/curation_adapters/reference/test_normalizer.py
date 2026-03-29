"""Unit tests for the reference adapter candidate normalizer."""

from __future__ import annotations

from src.lib.curation_adapters.reference import (
    REFERENCE_ADAPTER_KEY,
    REFERENCE_PAYLOAD_BUILDER_KEY,
    REFERENCE_VALIDATION_PLAN_KEY,
    ReferenceCandidateNormalizer,
)
from src.lib.curation_workspace.pipeline import CandidateNormalizationContext
from src.schemas.curation_prep import CurationPrepCandidate


def _candidate() -> CurationPrepCandidate:
    return CurationPrepCandidate.model_validate(
        {
            "adapter_key": REFERENCE_ADAPTER_KEY,
            "profile_key": None,
            "payload": {
                "citation": {
                    "title": "  Adapter-owned editor packs stay outside the shared editor  ",
                    "authors": ["Ada Lovelace", " Grace Hopper "],
                    "journal": "  Boundary Systems Journal  ",
                    "publication_year": "2024",
                },
                "identifiers": {
                    "doi": " doi:10.4242/Example-1 ",
                },
            },
            "evidence_records": [
                {
                    "evidence_record_id": "evidence-title",
                    "source": "extracted",
                    "extraction_result_id": "prep-extract-reference",
                    "field_paths": ["citation.title"],
                    "anchor": {
                        "anchor_kind": "snippet",
                        "locator_quality": "exact_quote",
                        "supports_decision": "supports",
                        "snippet_text": "Adapter-owned editor packs stay outside the shared editor",
                        "viewer_search_text": (
                            "Adapter-owned editor packs stay outside the shared editor"
                        ),
                        "page_number": 2,
                        "section_title": "Results",
                        "chunk_ids": ["chunk-reference-title"],
                    },
                    "notes": ["The manuscript title is directly quoted."],
                }
            ],
            "conversation_context_summary": "Conversation narrowed to one reference citation.",
        }
    )


def test_reference_candidate_normalizer_builds_adapter_owned_payload_and_layout():
    candidate = _candidate()
    normalized = ReferenceCandidateNormalizer().normalize(
        candidate.payload,
        prep_candidate=candidate,
        context=CandidateNormalizationContext(
            document_id="document-1",
            adapter_key=REFERENCE_ADAPTER_KEY,
            profile_key=None,
            prep_extraction_result_id="prep-result-1",
            candidate_index=0,
            flow_run_id="flow-1",
        ),
    )

    assert normalized.normalized_payload == {
        "citation": {
            "title": "Adapter-owned editor packs stay outside the shared editor",
            "authors": ["Ada Lovelace", "Grace Hopper"],
            "journal": "Boundary Systems Journal",
            "publication_year": 2024,
            "reference_type": "journal_article",
        },
        "identifiers": {
            "doi": "10.4242/example-1",
            "pmid": None,
        },
    }
    assert normalized.display_label == "Adapter-owned editor packs stay outside the shared editor"
    assert normalized.secondary_label == "DOI 10.4242/example-1"
    assert normalized.metadata["reference_adapter"]["payload_builder"] == REFERENCE_PAYLOAD_BUILDER_KEY

    assert [field.field_key for field in normalized.draft_fields] == [
        "citation.title",
        "citation.authors",
        "citation.journal",
        "citation.publication_year",
        "citation.reference_type",
        "identifiers.doi",
        "identifiers.pmid",
    ]
    assert normalized.draft_fields[0].group_key == "citation_details"
    assert normalized.draft_fields[0].group_label == "Citation details"
    assert normalized.draft_fields[1].value == ["Ada Lovelace", "Grace Hopper"]
    assert normalized.draft_fields[1].field_type == "json"
    assert normalized.draft_fields[1].metadata["widget"] == "reference_author_list"
    assert normalized.draft_fields[1].metadata["validation"]["plan_key"] == REFERENCE_VALIDATION_PLAN_KEY
    assert normalized.draft_fields[4].value == "journal_article"
    assert normalized.draft_fields[4].required is True
    assert normalized.draft_fields[4].metadata["default_applied"] is True
    assert normalized.draft_fields[5].value == "10.4242/example-1"

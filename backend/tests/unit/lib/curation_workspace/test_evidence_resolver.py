"""Unit tests for deterministic evidence-anchor pass-through resolution."""

from __future__ import annotations

from src.lib.curation_workspace.evidence_resolver import DeterministicEvidenceAnchorResolver
from src.lib.curation_workspace.pipeline import EvidenceResolutionContext, NormalizedCandidate
from src.schemas.curation_prep import CurationPrepCandidate
from src.schemas.curation_workspace import EvidenceAnchor, EvidenceAnchorKind, EvidenceLocatorQuality


def _make_candidate(anchor_payload: dict, *, field_path: str = "gene_symbol") -> CurationPrepCandidate:
    return CurationPrepCandidate.model_validate(
        {
            "adapter_key": "gene",
            "profile_key": "pilot",
            "payload": {
                "gene_symbol": "tinman",
                "anatomy_label": "embryonic heart",
                "is_negative": False,
            },
            "evidence_records": [
                {
                    "evidence_record_id": "evidence-1",
                    "source": "extracted",
                    "extraction_result_id": "extract-1",
                    "field_paths": [field_path],
                    "anchor": anchor_payload,
                    "notes": [],
                }
            ],
            "conversation_context_summary": "Conversation summary.",
        }
    )


def _make_anchor_payload(**overrides: object) -> dict:
    payload = {
        "anchor_kind": "snippet",
        "locator_quality": "exact_quote",
        "supports_decision": "supports",
        "snippet_text": "Verified quote from the paper.",
        "sentence_text": "Verified quote from the paper.",
        "normalized_text": None,
        "viewer_search_text": "Verified quote from the paper.",
        "pdfx_markdown_offset_start": None,
        "pdfx_markdown_offset_end": None,
        "page_number": 4,
        "page_label": None,
        "section_title": "Results",
        "subsection_title": "Expression analysis",
        "figure_reference": "Figure 2A",
        "table_reference": None,
        "chunk_ids": ["chunk-1"],
    }
    payload.update(overrides)
    return payload


def _make_context() -> EvidenceResolutionContext:
    return EvidenceResolutionContext(
        document_id="document-1",
        adapter_key="gene",
        profile_key="pilot",
        prep_extraction_result_id="prep-result-1",
        candidate_index=0,
    )


def _make_normalized_candidate(candidate: CurationPrepCandidate) -> NormalizedCandidate:
    return NormalizedCandidate(
        prep_candidate=candidate,
        normalized_payload=dict(candidate.payload),
        draft_fields=[],
    )


def test_resolver_preserves_tool_verified_anchor_payload():
    candidate = _make_candidate(_make_anchor_payload())
    resolver = DeterministicEvidenceAnchorResolver()

    result = resolver.resolve(
        candidate,
        normalized_candidate=_make_normalized_candidate(candidate),
        context=_make_context(),
    )

    assert len(result) == 1
    resolved_record = result[0]
    anchor = EvidenceAnchor.model_validate(resolved_record.anchor)

    assert resolved_record.field_keys == ["gene_symbol"]
    assert resolved_record.field_group_keys == []
    assert resolved_record.is_primary is True
    assert resolved_record.warnings == []
    assert anchor.anchor_kind is EvidenceAnchorKind.SNIPPET
    assert anchor.locator_quality is EvidenceLocatorQuality.EXACT_QUOTE
    assert anchor.snippet_text == "Verified quote from the paper."
    assert anchor.page_number == 4
    assert anchor.section_title == "Results"
    assert anchor.subsection_title == "Expression analysis"
    assert anchor.figure_reference == "Figure 2A"
    assert anchor.table_reference is None
    assert anchor.chunk_ids == ["chunk-1"]


def test_resolver_moves_table_literal_into_table_reference():
    candidate = _make_candidate(
        _make_anchor_payload(
            figure_reference="Table 3",
            table_reference=None,
        ),
        field_path="anatomy_label",
    )
    resolver = DeterministicEvidenceAnchorResolver()

    result = resolver.resolve(
        candidate,
        normalized_candidate=_make_normalized_candidate(candidate),
        context=_make_context(),
    )

    anchor = EvidenceAnchor.model_validate(result[0].anchor)

    assert result[0].field_keys == ["anatomy_label"]
    assert anchor.figure_reference is None
    assert anchor.table_reference == "Table 3"

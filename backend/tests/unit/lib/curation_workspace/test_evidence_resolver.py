"""Unit tests for deterministic evidence-anchor resolution."""

from __future__ import annotations

from src.lib.curation_workspace.evidence_resolver import (
    DeterministicEvidenceAnchorResolver,
    _build_canonical_markdown_from_elements,
)
from src.lib.curation_workspace.pipeline import EvidenceResolutionContext, NormalizedCandidate
from src.lib.pipeline.pdfx_parser import markdown_to_pipeline_elements
from src.schemas.curation_prep import CurationPrepCandidate
from src.schemas.curation_workspace import EvidenceAnchor, EvidenceAnchorKind, EvidenceLocatorQuality


def _make_candidate(anchor_payload: dict, *, field_path: str = "gene_symbol") -> CurationPrepCandidate:
    return CurationPrepCandidate.model_validate(
        {
            "adapter_key": "gene",
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
        prep_extraction_result_id="prep-result-1",
        candidate_index=0,
    )


def _make_normalized_candidate(candidate: CurationPrepCandidate) -> NormalizedCandidate:
    return NormalizedCandidate(
        prep_candidate=candidate,
        normalized_payload=dict(candidate.payload),
        draft_fields=[],
    )


def _resolve_anchor(
    candidate: CurationPrepCandidate,
    *,
    chunks: list[dict],
    processed_elements: list[dict] | None = None,
    resolve_against_document: bool = True,
) -> tuple[EvidenceAnchor, list[str], list[str]]:
    resolver = DeterministicEvidenceAnchorResolver(
        user_id_resolver=lambda _prep_result_id: "user-1",
        chunk_loader=lambda _document_id, _user_id: chunks,
        processed_element_loader=lambda _document_id, _user_id: processed_elements or [],
        resolve_against_document=resolve_against_document,
    )
    result = resolver.resolve(
        candidate,
        normalized_candidate=_make_normalized_candidate(candidate),
        context=_make_context(),
    )
    resolved_record = result[0]
    return (
        EvidenceAnchor.model_validate(resolved_record.anchor),
        resolved_record.warnings,
        resolved_record.field_keys,
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


def test_resolver_document_lookup_enriches_quote_from_matching_chunk():
    processed_elements = markdown_to_pipeline_elements(
        """# Results

Verified quote from the paper.
"""
    )
    candidate = _make_candidate(
        _make_anchor_payload(
            page_number=None,
            section_title=None,
            subsection_title=None,
            figure_reference=None,
            chunk_ids=[],
        )
    )

    anchor, warnings, field_keys = _resolve_anchor(
        candidate,
        chunks=[
            {
                "id": "chunk-1",
                "chunk_index": 0,
                "content": "Introductory text. Verified quote from the paper. Closing text.",
                "page_number": 3,
                "section_title": "Results",
                "subsection": "Association",
                "metadata": {},
            }
        ],
        processed_elements=processed_elements,
    )

    assert field_keys == ["gene_symbol"]
    assert anchor.locator_quality is EvidenceLocatorQuality.EXACT_QUOTE
    assert anchor.anchor_kind is EvidenceAnchorKind.SNIPPET
    assert anchor.page_number == 3
    assert anchor.section_title == "Results"
    assert anchor.subsection_title == "Association"
    assert anchor.chunk_ids == ["chunk-1"]
    assert anchor.pdfx_markdown_offset_start is not None
    assert anchor.pdfx_markdown_offset_end is not None
    canonical_markdown = _build_canonical_markdown_from_elements(processed_elements)
    assert (
        canonical_markdown[
            anchor.pdfx_markdown_offset_start:anchor.pdfx_markdown_offset_end
        ]
        == "Verified quote from the paper."
    )
    assert warnings == []


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


def test_resolver_offsets_remain_stable_across_canonical_markdown_round_trip():
    schema_enforced_markdown = """# Title

## Metadata

Study: Example study

## Results

Verified quote from the paper.

## References

- Example reference
"""
    processed_elements = markdown_to_pipeline_elements(schema_enforced_markdown)
    candidate = _make_candidate(
        _make_anchor_payload(
            snippet_text="Verified quote from the paper.",
            sentence_text="Verified quote from the paper.",
            viewer_search_text="Verified quote from the paper.",
            page_number=1,
            section_title="Results",
            subsection_title=None,
            chunk_ids=[],
        )
    )

    anchor, warnings, _field_keys = _resolve_anchor(
        candidate,
        chunks=[
            {
                "id": "chunk-1",
                "chunk_index": 0,
                "content": "Verified quote from the paper.",
                "page_number": 1,
                "section_title": "Results",
                "metadata": {},
            }
        ],
        processed_elements=processed_elements,
    )

    canonical_markdown = _build_canonical_markdown_from_elements(processed_elements)
    round_trip_markdown = _build_canonical_markdown_from_elements(
        markdown_to_pipeline_elements(canonical_markdown)
    )

    assert warnings == []
    assert canonical_markdown == round_trip_markdown
    assert anchor.pdfx_markdown_offset_start is not None
    assert anchor.pdfx_markdown_offset_end is not None
    assert (
        canonical_markdown[
            anchor.pdfx_markdown_offset_start:anchor.pdfx_markdown_offset_end
        ]
        == "Verified quote from the paper."
    )
    assert (
        round_trip_markdown[
            anchor.pdfx_markdown_offset_start:anchor.pdfx_markdown_offset_end
        ]
        == "Verified quote from the paper."
    )

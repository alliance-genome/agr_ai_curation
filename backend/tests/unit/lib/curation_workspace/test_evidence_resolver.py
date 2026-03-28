"""Unit tests for deterministic evidence-anchor resolution."""

from __future__ import annotations

from src.lib.curation_workspace.evidence_resolver import DeterministicEvidenceAnchorResolver
from src.lib.curation_workspace.pipeline import EvidenceResolutionContext, NormalizedCandidate
from src.schemas.curation_prep import CurationPrepCandidate
from src.schemas.curation_workspace import EvidenceAnchor, EvidenceAnchorKind, EvidenceLocatorQuality


def _make_candidate(anchor_payload: dict, *, field_path: str = "entity.name") -> CurationPrepCandidate:
    return CurationPrepCandidate.model_validate(
        {
            "adapter_key": "generic",
            "profile_key": "default",
            "payload": {"entity": {"name": "Entity Alpha"}},
            "evidence_records": [
                {
                    "evidence_record_id": "evidence-1",
                    "source": "extracted",
                    "extraction_result_id": "extract-1",
                    "field_paths": [field_path],
                    "anchor": anchor_payload,
                    "notes": ["Supports the extracted field."],
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
        "snippet_text": "Exact quote from PDFX markdown.",
        "sentence_text": "Exact quote from PDFX markdown.",
        "normalized_text": None,
        "viewer_search_text": None,
        "pdfx_markdown_offset_start": None,
        "pdfx_markdown_offset_end": None,
        "page_number": None,
        "page_label": None,
        "section_title": None,
        "subsection_title": None,
        "figure_reference": None,
        "table_reference": None,
        "chunk_ids": [],
    }
    payload.update(overrides)
    return payload


def _make_context() -> EvidenceResolutionContext:
    return EvidenceResolutionContext(
        document_id="document-1",
        adapter_key="generic",
        profile_key="default",
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
) -> tuple[EvidenceAnchor, list[str], list[str]]:
    resolver = DeterministicEvidenceAnchorResolver(
        user_id_resolver=lambda _prep_result_id: "user-1",
        chunk_loader=lambda _document_id, _user_id: chunks,
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


def test_resolver_assigns_exact_quote_when_raw_snippet_matches_chunk_text():
    candidate = _make_candidate(
        _make_anchor_payload(
            snippet_text="Exact quote from PDFX markdown.",
            sentence_text="Exact quote from PDFX markdown.",
            page_number=3,
            section_title="Results",
            subsection_title="Association",
        )
    )
    anchor, warnings, field_keys = _resolve_anchor(
        candidate,
        chunks=[
            {
                "id": "chunk-1",
                "chunk_index": 0,
                "content": "Introductory text. Exact quote from PDFX markdown. Closing text.",
                "page_number": 3,
                "section_title": "Results",
                "subsection": "Association",
                "metadata": {},
            }
        ],
    )

    assert field_keys == ["entity.name"]
    assert anchor.locator_quality is EvidenceLocatorQuality.EXACT_QUOTE
    assert anchor.anchor_kind is EvidenceAnchorKind.SNIPPET
    assert anchor.viewer_search_text == "Exact quote from PDFX markdown."
    assert anchor.page_number == 3
    assert anchor.section_title == "Results"
    assert anchor.subsection_title == "Association"
    assert anchor.chunk_ids == ["chunk-1"]
    assert anchor.pdfx_markdown_offset_start is not None
    assert anchor.pdfx_markdown_offset_end is not None
    assert warnings == []


def test_resolver_assigns_normalized_quote_for_canonical_cross_chunk_match():
    candidate = _make_candidate(
        _make_anchor_payload(
            snippet_text='Repeated   quote\nwith “smart” punctuation and enough extra words to cross the chunk boundary cleanly.',
            sentence_text='Repeated   quote\nwith “smart” punctuation and enough extra words to cross the chunk boundary cleanly.',
            section_title="Results",
            page_number=4,
        )
    )
    anchor, warnings, _field_keys = _resolve_anchor(
        candidate,
        chunks=[
            {
                "id": "chunk-1",
                "chunk_index": 0,
                "content": 'Repeated quote with "smart"',
                "page_number": 4,
                "section_title": "Results",
                "subsection": "Quantification",
                "metadata": {},
            },
            {
                "id": "chunk-2",
                "chunk_index": 1,
                "content": "punctuation and enough extra words to cross the chunk boundary cleanly.",
                "page_number": 5,
                "section_title": "Results",
                "subsection": "Quantification",
                "metadata": {},
            },
        ],
    )

    assert anchor.locator_quality is EvidenceLocatorQuality.NORMALIZED_QUOTE
    assert anchor.viewer_search_text == (
        'Repeated quote with "smart" punctuation and enough extra words to cross the chunk boundary cleanly.'
    )
    assert anchor.normalized_text == anchor.viewer_search_text
    assert anchor.page_number == 4
    assert anchor.section_title == "Results"
    assert anchor.chunk_ids == ["chunk-1", "chunk-2"]
    assert anchor.pdfx_markdown_offset_start is not None
    assert anchor.pdfx_markdown_offset_end is not None
    assert warnings == []


def test_resolver_falls_back_to_section_only_when_quote_is_empty_but_section_matches():
    candidate = _make_candidate(
        _make_anchor_payload(
            anchor_kind="section",
            locator_quality="section_only",
            snippet_text=None,
            sentence_text=None,
            viewer_search_text="Methods",
            section_title="Methods",
        )
    )
    anchor, warnings, _field_keys = _resolve_anchor(
        candidate,
        chunks=[
            {
                "id": "chunk-methods",
                "chunk_index": 0,
                "content": "Methods section text.",
                "page_number": 2,
                "section_title": "Methods",
                "subsection": "Assay",
                "metadata": {},
            }
        ],
    )

    assert anchor.locator_quality is EvidenceLocatorQuality.SECTION_ONLY
    assert anchor.anchor_kind is EvidenceAnchorKind.SECTION
    assert anchor.viewer_search_text is None
    assert anchor.page_number == 2
    assert anchor.section_title == "Methods"
    assert anchor.chunk_ids == ["chunk-methods"]
    assert warnings == []

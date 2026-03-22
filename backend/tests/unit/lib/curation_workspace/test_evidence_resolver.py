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
            "extracted_fields": [
                {
                    "field_path": field_path,
                    "value_type": "string",
                    "string_value": "Entity Alpha",
                    "number_value": None,
                    "boolean_value": None,
                    "json_value": None,
                }
            ],
            "evidence_references": [
                {
                    "field_path": field_path,
                    "evidence_record_id": "evidence-1",
                    "extraction_result_id": "extract-1",
                    "anchor": anchor_payload,
                    "rationale": "Supports the extracted field.",
                }
            ],
            "conversation_context_summary": "Conversation summary.",
            "confidence": 0.9,
            "unresolved_ambiguities": [],
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
        normalized_payload=candidate.to_extracted_fields_dict(),
        draft_fields=[],
    )


def _resolve_anchor(
    candidate: CurationPrepCandidate,
    *,
    chunks: list[dict],
) -> tuple[EvidenceAnchor, list[str]]:
    resolver = DeterministicEvidenceAnchorResolver(
        user_id_resolver=lambda _prep_result_id: "user-1",
        chunk_loader=lambda _document_id, _user_id: chunks,
    )
    result = resolver.resolve(
        candidate,
        normalized_candidate=_make_normalized_candidate(candidate),
        context=_make_context(),
    )
    return EvidenceAnchor.model_validate(result[0].anchor), result[0].warnings


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
    anchor, warnings = _resolve_anchor(
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
    anchor, warnings = _resolve_anchor(
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
    anchor, warnings = _resolve_anchor(
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


def test_resolver_does_not_treat_section_viewer_search_text_as_quote_input():
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
    anchor, _warnings = _resolve_anchor(
        candidate,
        chunks=[
            {
                "id": "chunk-methods",
                "chunk_index": 0,
                "content": "Methods section text.",
                "page_number": 2,
                "section_title": "Methods",
                "metadata": {},
            }
        ],
    )

    assert anchor.locator_quality is EvidenceLocatorQuality.SECTION_ONLY
    assert anchor.anchor_kind is EvidenceAnchorKind.SECTION
    assert anchor.viewer_search_text is None
    assert anchor.chunk_ids == ["chunk-methods"]


def test_resolver_falls_back_to_page_only_when_no_chunks_are_available():
    candidate = _make_candidate(
        _make_anchor_payload(
            anchor_kind="page",
            locator_quality="page_only",
            snippet_text="Missing quote",
            sentence_text="Missing quote",
            page_number=9,
        )
    )
    anchor, warnings = _resolve_anchor(candidate, chunks=[])

    assert anchor.locator_quality is EvidenceLocatorQuality.PAGE_ONLY
    assert anchor.anchor_kind is EvidenceAnchorKind.PAGE
    assert anchor.viewer_search_text is None
    assert anchor.page_number == 9
    assert anchor.section_title is None
    assert warnings == []


def test_resolver_falls_back_to_document_only_for_intentional_document_scoped_anchor():
    candidate = _make_candidate(
        _make_anchor_payload(
            anchor_kind="document",
            locator_quality="document_only",
            snippet_text=None,
            sentence_text=None,
            page_number=7,
        )
    )
    anchor, warnings = _resolve_anchor(candidate, chunks=[])

    assert anchor.locator_quality is EvidenceLocatorQuality.DOCUMENT_ONLY
    assert anchor.anchor_kind is EvidenceAnchorKind.DOCUMENT
    assert anchor.viewer_search_text is None
    assert anchor.page_number is None
    assert anchor.section_title is None
    assert warnings == []


def test_resolver_marks_anchor_unresolved_when_no_durable_locator_can_be_produced():
    candidate = _make_candidate(
        _make_anchor_payload(
            snippet_text="Quote that does not exist in markdown",
            sentence_text="Quote that does not exist in markdown",
        )
    )
    anchor, warnings = _resolve_anchor(
        candidate,
        chunks=[
            {
                "id": "chunk-1",
                "chunk_index": 0,
                "content": "Completely unrelated content.",
                "page_number": 1,
                "section_title": "Background",
                "metadata": {},
            }
        ],
    )

    assert anchor.locator_quality is EvidenceLocatorQuality.UNRESOLVED
    assert anchor.viewer_search_text is None
    assert anchor.page_number is None
    assert anchor.section_title is None
    assert anchor.chunk_ids == []
    assert warnings == []


def test_resolver_prefers_page_biased_quote_match_when_multiple_spans_exist():
    candidate = _make_candidate(
        _make_anchor_payload(
            snippet_text="Shared quote.",
            sentence_text="Shared quote.",
            page_number=5,
            section_title="Results",
        )
    )
    anchor, warnings = _resolve_anchor(
        candidate,
        chunks=[
            {
                "id": "chunk-1",
                "chunk_index": 0,
                "content": "Shared quote.",
                "page_number": 2,
                "section_title": "Background",
                "metadata": {},
            },
            {
                "id": "chunk-2",
                "chunk_index": 1,
                "content": "Shared quote.",
                "page_number": 5,
                "section_title": "Results",
                "metadata": {},
            },
        ],
    )

    assert anchor.locator_quality is EvidenceLocatorQuality.EXACT_QUOTE
    assert anchor.page_number == 5
    assert anchor.chunk_ids == ["chunk-2"]
    assert warnings == [
        "Multiple PDFX quote matches found; selected the closest page/section-biased span."
    ]

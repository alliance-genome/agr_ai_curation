"""Unit tests for deterministic evidence-quality scoring."""

from __future__ import annotations

from dataclasses import dataclass

from src.lib.curation_workspace.evidence_quality import (
    enrich_evidence_anchor,
    evidence_anchor_payload_with_quality,
    summarize_evidence_anchors,
    summarize_evidence_records,
)
from src.schemas.curation_workspace import (
    EvidenceAnchor,
    EvidenceLocatorQuality,
)


@dataclass(frozen=True)
class _Record:
    anchor: dict
    warnings: list[str]


def _anchor(**overrides: object) -> EvidenceAnchor:
    payload = {
        "anchor_kind": "snippet",
        "locator_quality": "exact_quote",
        "supports_decision": "supports",
        "snippet_text": "APOE was linked to the phenotype.",
        "sentence_text": "APOE was linked to the phenotype.",
        "normalized_text": None,
        "viewer_search_text": "APOE was linked to the phenotype.",
        "viewer_highlightable": False,
        "pdfx_markdown_offset_start": 10,
        "pdfx_markdown_offset_end": 43,
        "page_number": 3,
        "page_label": None,
        "section_title": "Results",
        "subsection_title": "Association",
        "figure_reference": None,
        "table_reference": None,
        "chunk_ids": ["chunk-1"],
    }
    payload.update(overrides)
    return EvidenceAnchor.model_validate(payload)


def test_enrich_evidence_anchor_sets_viewer_highlightable_for_quote_anchors():
    anchor = enrich_evidence_anchor(_anchor())

    assert anchor.locator_quality is EvidenceLocatorQuality.EXACT_QUOTE
    assert anchor.viewer_highlightable is True


def test_enrich_evidence_anchor_clears_viewer_highlightable_for_non_quote_anchors():
    anchor = enrich_evidence_anchor(
        _anchor(
            anchor_kind="section",
            locator_quality="section_only",
            snippet_text=None,
            sentence_text=None,
            viewer_search_text="Results",
            viewer_highlightable=True,
            pdfx_markdown_offset_start=None,
            pdfx_markdown_offset_end=None,
            page_number=3,
            chunk_ids=["chunk-1"],
        )
    )

    assert anchor.locator_quality is EvidenceLocatorQuality.SECTION_ONLY
    assert anchor.viewer_highlightable is False


def test_evidence_anchor_payload_with_quality_serializes_viewer_highlightable():
    payload = evidence_anchor_payload_with_quality(_anchor().model_dump(mode="json"))

    assert payload["viewer_highlightable"] is True


def test_summarize_evidence_anchors_aggregates_counts_without_degradation():
    summary = summarize_evidence_anchors(
        [
            _anchor(locator_quality="exact_quote"),
            _anchor(
                locator_quality="normalized_quote",
                normalized_text="apoe was linked to the phenotype.",
            ),
            _anchor(
                anchor_kind="section",
                locator_quality="section_only",
                snippet_text=None,
                sentence_text=None,
                viewer_search_text=None,
                pdfx_markdown_offset_start=None,
                pdfx_markdown_offset_end=None,
                chunk_ids=["chunk-2"],
            ),
        ]
    )

    assert summary.total_anchor_count == 3
    assert summary.resolved_anchor_count == 3
    assert summary.viewer_highlightable_anchor_count == 2
    assert summary.quality_counts.exact_quote == 1
    assert summary.quality_counts.normalized_quote == 1
    assert summary.quality_counts.section_only == 1
    assert summary.degraded is False
    assert summary.warnings == []


def test_summarize_evidence_anchors_warns_when_no_anchor_is_highlightable():
    summary = summarize_evidence_anchors(
        [
            _anchor(
                anchor_kind="section",
                locator_quality="section_only",
                snippet_text=None,
                sentence_text=None,
                viewer_search_text=None,
                pdfx_markdown_offset_start=None,
                pdfx_markdown_offset_end=None,
            )
        ]
    )

    assert summary.viewer_highlightable_anchor_count == 0
    assert summary.degraded is False
    assert summary.warnings == [
        "No evidence anchors can be highlighted in the PDF viewer text layer."
    ]


def test_summarize_evidence_records_marks_document_level_and_unresolved_evidence_as_degraded():
    summary = summarize_evidence_records(
        [
            _Record(
                anchor=_anchor(
                    anchor_kind="page",
                    locator_quality="page_only",
                    snippet_text=None,
                    sentence_text=None,
                    viewer_search_text=None,
                    pdfx_markdown_offset_start=None,
                    pdfx_markdown_offset_end=None,
                    chunk_ids=[],
                ).model_dump(mode="json"),
                warnings=[],
            ),
            _Record(
                anchor=_anchor(
                    anchor_kind="document",
                    locator_quality="document_only",
                    snippet_text=None,
                    sentence_text=None,
                    viewer_search_text=None,
                    pdfx_markdown_offset_start=None,
                    pdfx_markdown_offset_end=None,
                    page_number=None,
                    section_title=None,
                    chunk_ids=[],
                ).model_dump(mode="json"),
                warnings=[],
            ),
            _Record(
                anchor=_anchor(
                    anchor_kind="document",
                    locator_quality="unresolved",
                    snippet_text=None,
                    sentence_text=None,
                    viewer_search_text=None,
                    pdfx_markdown_offset_start=None,
                    pdfx_markdown_offset_end=None,
                    page_number=None,
                    section_title=None,
                    chunk_ids=[],
                ).model_dump(mode="json"),
                warnings=["Evidence resolution could not load PDFX chunks for this document."],
            ),
        ]
    )

    assert summary is not None
    assert summary.total_anchor_count == 3
    assert summary.resolved_anchor_count == 1
    assert summary.viewer_highlightable_anchor_count == 0
    assert summary.quality_counts.page_only == 1
    assert summary.quality_counts.document_only == 1
    assert summary.quality_counts.unresolved == 1
    assert summary.degraded is True
    assert summary.warnings == [
        "1 evidence anchor could not be localized to PDF text or metadata.",
        "1 evidence anchor resolved only at the document level.",
        "1 evidence anchor resolved only to a page-level location.",
        "No evidence anchors can be highlighted in the PDF viewer text layer.",
        "Evidence resolution could not load PDFX chunks for this document.",
    ]


def test_summarize_evidence_anchors_treats_majority_page_level_locations_as_degraded():
    summary = summarize_evidence_anchors(
        [
            _anchor(
                anchor_kind="page",
                locator_quality="page_only",
                snippet_text=None,
                sentence_text=None,
                viewer_search_text=None,
                pdfx_markdown_offset_start=None,
                pdfx_markdown_offset_end=None,
                chunk_ids=[],
            ),
            _anchor(
                anchor_kind="page",
                locator_quality="page_only",
                snippet_text=None,
                sentence_text=None,
                viewer_search_text=None,
                pdfx_markdown_offset_start=None,
                pdfx_markdown_offset_end=None,
                chunk_ids=[],
            ),
            _anchor(locator_quality="exact_quote"),
        ]
    )

    assert summary.quality_counts.page_only == 2
    assert summary.degraded is True

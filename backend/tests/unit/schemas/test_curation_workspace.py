"""Unit tests for curation workspace contract schemas."""

import pytest
from pydantic import ValidationError

from src.schemas.curation_workspace import (
    EvidenceAnchor,
    FieldValidationResult,
    SubmissionPayload,
)


class TestEvidenceAnchor:
    """Validation coverage for evidence anchor contracts."""

    def test_sentence_anchor_accepts_text_first_locator_metadata(self):
        """Sentence anchors should keep PDFX and viewer metadata aligned."""
        anchor = EvidenceAnchor(
            anchor_kind="sentence",
            locator_quality="exact_quote",
            supports_decision="supports",
            document_id="doc-1",
            chunk_id="chunk-42",
            doc_item_ids=[" item-1 ", "item-1", "item-2"],
            page_number=3,
            section_title=" Results ",
            section_path=[" Results ", "Expression"],
            figure_reference="Fig. 2",
            snippet_text="Expression was observed in the hindgut.",
            sentence_text="Expression was observed in the hindgut in wild-type embryos.",
            normalized_text="expression was observed in the hindgut in wild type embryos",
            viewer_search_text="Expression was observed in the hindgut in wild-type embryos.",
            pdfx_markdown_start_offset=120,
            pdfx_markdown_end_offset=176,
        )

        assert anchor.chunk_id == "chunk-42"
        assert anchor.doc_item_ids == ["item-1", "item-2"]
        assert anchor.section_title == "Results"
        assert anchor.section_path == ["Results", "Expression"]

    def test_anchor_forbids_bbox_fields(self):
        """The shared anchor contract must not allow bounding-box inputs."""
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            EvidenceAnchor(
                anchor_kind="snippet",
                locator_quality="normalized_quote",
                supports_decision="supports",
                document_id="doc-1",
                snippet_text="Expression was observed in the hindgut.",
                bbox={"left": 1, "top": 2, "right": 3, "bottom": 4},
            )

    def test_anchor_requires_a_non_document_locator(self):
        """Non-document anchors should reject payloads with no locator detail."""
        with pytest.raises(
            ValidationError, match="requires its matching locator field"
        ):
            EvidenceAnchor(
                anchor_kind="snippet",
                locator_quality="unresolved",
                supports_decision="context_only",
                document_id="doc-1",
            )

    def test_page_anchor_requires_page_number(self):
        """Page anchors should enforce their primary locator field."""
        with pytest.raises(
            ValidationError, match="requires its matching locator field"
        ):
            EvidenceAnchor(
                anchor_kind="page",
                locator_quality="page_only",
                supports_decision="supports",
                document_id="doc-1",
            )

    def test_quote_locator_quality_requires_quote_text_or_offsets(self):
        """Quote-based quality states need text or offsets, not page-only context."""
        with pytest.raises(
            ValidationError,
            match="quote-based locator_quality requires quote text or PDFX markdown offsets",
        ):
            EvidenceAnchor(
                anchor_kind="page",
                locator_quality="exact_quote",
                supports_decision="supports",
                document_id="doc-1",
                page_number=5,
            )

    def test_anchor_requires_paired_markdown_offsets(self):
        """Offset metadata should be provided as a complete PDFX span."""
        with pytest.raises(
            ValidationError,
            match="pdfx_markdown_start_offset and pdfx_markdown_end_offset must be provided together",
        ):
            EvidenceAnchor(
                anchor_kind="snippet",
                locator_quality="normalized_quote",
                supports_decision="supports",
                document_id="doc-1",
                snippet_text="Expression was observed in the hindgut.",
                pdfx_markdown_start_offset=20,
            )

    def test_section_locator_quality_requires_section_context(self):
        """Section-only quality should always carry section metadata."""
        with pytest.raises(
            ValidationError,
            match="section_only locator_quality requires section_title or section_path",
        ):
            EvidenceAnchor(
                anchor_kind="page",
                locator_quality="section_only",
                supports_decision="supports",
                document_id="doc-1",
                page_number=3,
            )

    def test_document_anchor_allows_document_level_fallback(self):
        """Document-only anchors should be valid without page or quote metadata."""
        anchor = EvidenceAnchor(
            anchor_kind="document",
            locator_quality="document_only",
            supports_decision="context_only",
            document_id="doc-1",
        )

        assert anchor.anchor_kind == "document"
        assert anchor.document_id == "doc-1"


class TestFieldValidationResult:
    """Validation coverage for field validation contracts."""

    def test_validated_status_requires_resolver(self):
        """Completed validation statuses should record the resolver name."""
        with pytest.raises(ValidationError, match="resolver is required"):
            FieldValidationResult(status="validated")

    def test_overridden_status_allows_missing_resolver(self):
        """Curator overrides can be recorded without re-running a resolver."""
        result = FieldValidationResult(
            status="overridden",
            warnings=[" curator override ", "curator override", "manual evidence linked"],
        )

        assert result.resolver is None
        assert result.warnings == ["curator override", "manual evidence linked"]

    def test_ambiguous_status_requires_candidate_matches(self):
        """Ambiguous validation should surface selectable resolver candidates."""
        with pytest.raises(ValidationError, match="must include candidate_matches"):
            FieldValidationResult(status="ambiguous", resolver="alliance_gene_lookup")

    def test_ambiguous_status_accepts_candidate_matches_and_warning(self):
        """Ambiguous validation can carry resolver candidates and normalized warnings."""
        result = FieldValidationResult(
            status="ambiguous",
            resolver="alliance_gene_lookup",
            candidate_matches=[
                {
                    "matched_value": "pax6a",
                    "candidate_id": "ZFIN:ZDB-GENE-990415-72",
                    "display_label": "pax6a",
                    "confidence": 0.81,
                }
            ],
            warnings=[" curator review needed ", "curator review needed", "synonym collision"],
        )

        assert len(result.candidate_matches) == 1
        assert result.warnings == ["curator review needed", "synonym collision"]

    def test_not_found_status_accepts_resolver_without_candidates(self):
        """Missing matches do not need candidate lists when the resolver is explicit."""
        result = FieldValidationResult(
            status="not_found",
            resolver="alliance_gene_lookup",
        )

        assert result.resolver == "alliance_gene_lookup"
        assert result.candidate_matches == []


class TestSubmissionPayload:
    """Validation coverage for submission payload contracts."""

    def test_preview_payload_allows_empty_adapter_payload(self):
        """Preview mode can carry an adapter shell before the payload is finalized."""
        payload = SubmissionPayload(
            mode="preview",
            target_system="alliance_curation_api",
            domain_adapter={
                "domain": "disease",
                "adapter_name": "alliance_disease",
                "target_schema": "alliance_linkml_json",
                "payload": {},
            },
        )

        assert payload.mode == "preview"
        assert payload.domain_adapter.payload == {}

    def test_export_payload_requires_adapter_payload(self):
        """Export mode should fail fast when no adapter payload was built."""
        with pytest.raises(
            ValidationError,
            match="must be populated when mode is 'export' or 'direct_submit'",
        ):
            SubmissionPayload(
                mode="export",
                target_system="file_export_upload",
                domain_adapter={
                    "domain": "disease",
                    "adapter_name": "bioc_export",
                    "target_schema": "bioc_json",
                    "payload": {},
                },
            )

    def test_export_payload_accepts_file_export_target(self):
        """Export mode should support explicit file-based handoff targets."""
        payload = SubmissionPayload(
            mode="export",
            target_system="file_export_upload",
            domain_adapter={
                "domain": "disease",
                "adapter_name": "bioc_export",
                "target_schema": "bioc_json",
                "payload": {
                    "file_name": "session-1.bioc.json",
                },
            },
        )

        assert payload.target_system == "file_export_upload"
        assert payload.domain_adapter.adapter_name == "bioc_export"

    def test_direct_submit_accepts_ingest_bulk_target(self):
        """Direct submit contracts should support ingest-style bulk targets."""
        payload = SubmissionPayload(
            mode="direct_submit",
            target_system="ingest_bulk_submission",
            domain_adapter={
                "domain": "disease",
                "adapter_name": "ingest_bulk_disease",
                "target_schema": "bioc_json",
                "payload": {
                    "file_name": "session-1.bioc.json",
                },
            },
        )

        assert payload.target_system == "ingest_bulk_submission"
        assert payload.domain_adapter.target_schema == "bioc_json"

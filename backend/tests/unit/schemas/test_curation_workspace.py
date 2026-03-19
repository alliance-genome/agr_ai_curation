"""Unit tests for curation workspace contract schemas."""

import pytest
from pydantic import ValidationError

from src.schemas.curation_workspace import (
    EvidenceAnchor,
    FieldValidationResult,
    SubmissionPayload,
)


def make_bbox() -> dict:
    """Build a valid PDF bounding box payload."""
    return {
        "left": 10.0,
        "top": 30.0,
        "right": 25.0,
        "bottom": 5.0,
        "coord_origin": "BOTTOMLEFT",
    }


class TestEvidenceAnchor:
    """Validation coverage for evidence anchor contracts."""

    def test_chunk_anchor_accepts_matching_locator_fields(self):
        """Chunk anchors should validate when a chunk locator is present."""
        anchor = EvidenceAnchor(
            anchor_kind="chunk",
            locator_quality="exact",
            supports_decision="supports",
            document_id="doc-1",
            chunk_id="chunk-42",
            snippet="Expression was observed in the hindgut.",
            doc_item_ids=[" item-1 ", "item-1", "item-2"],
        )

        assert anchor.chunk_id == "chunk-42"
        assert anchor.doc_item_ids == ["item-1", "item-2"]

    def test_anchor_requires_a_locator_field(self):
        """Anchors without any locator details should be rejected."""
        with pytest.raises(ValidationError, match="requires at least one locator field"):
            EvidenceAnchor(
                anchor_kind="snippet",
                locator_quality="unknown",
                supports_decision="context_only",
            )

    def test_bbox_anchor_requires_page_number(self):
        """Bounding-box anchors must include a page number."""
        with pytest.raises(ValidationError, match="page_number is required when bbox is provided"):
            EvidenceAnchor(
                anchor_kind="bbox",
                locator_quality="exact",
                supports_decision="supports",
                bbox=make_bbox(),
            )

    def test_sentence_anchor_requires_sentence_text(self):
        """Sentence anchors should enforce their primary locator field."""
        with pytest.raises(ValidationError, match="requires its matching locator field"):
            EvidenceAnchor(
                anchor_kind="sentence",
                locator_quality="approximate",
                supports_decision="supports",
                snippet="A nearby snippet is present, but no sentence text was saved.",
            )


class TestFieldValidationResult:
    """Validation coverage for field validation contracts."""

    def test_pending_status_does_not_require_resolver(self):
        """Pending validation results can exist before a resolver runs."""
        result = FieldValidationResult(status="pending")

        assert result.resolver is None
        assert result.candidate_matches == []

    def test_non_pending_status_requires_resolver(self):
        """Completed validation states should record the resolver name."""
        with pytest.raises(ValidationError, match="resolver is required once validation has run"):
            FieldValidationResult(status="valid")

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


class TestSubmissionPayload:
    """Validation coverage for submission payload contracts."""

    def test_preview_payload_allows_empty_adapter_payload(self):
        """Preview mode can carry an adapter shell before the payload is finalized."""
        payload = SubmissionPayload(
            mode="preview",
            target_system="alliance_curation_api",
            domain_adapter={
                "domain": "gene_expression",
                "adapter_name": "alliance_gene_expression",
                "payload": {},
            },
        )

        assert payload.mode == "preview"
        assert payload.domain_adapter.payload == {}

    def test_submit_payload_requires_adapter_payload(self):
        """Submit mode should fail fast when no adapter payload was built."""
        with pytest.raises(ValidationError, match="must be populated when mode='submit'"):
            SubmissionPayload(
                mode="submit",
                target_system="abc_api",
                domain_adapter={
                    "domain": "gene_expression",
                    "adapter_name": "abc_gene_expression",
                    "payload": {},
                },
            )

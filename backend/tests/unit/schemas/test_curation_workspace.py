"""Unit tests for curation workspace contract schemas."""

import pytest
from pydantic import ValidationError

from src.schemas.curation_workspace import (
    EvidenceAnchor,
    EvidenceAnchorKind,
    EvidenceLocatorQuality,
    EvidenceSupportsDecision,
    FieldValidationResult,
    FieldValidationStatus,
    SubmissionMode,
    SubmissionPayloadContract,
    SubmissionTargetSystem,
)


def make_anchor_payload() -> dict:
    """Build a representative evidence anchor payload."""

    return {
        "anchor_kind": EvidenceAnchorKind.SNIPPET,
        "locator_quality": EvidenceLocatorQuality.EXACT_QUOTE,
        "supports_decision": EvidenceSupportsDecision.SUPPORTS,
        "snippet_text": "Disease association was observed in treated animals.",
        "sentence_text": "Disease association was observed in treated animals.",
        "normalized_text": "disease association was observed in treated animals",
        "viewer_search_text": "Disease association was observed in treated animals",
        "pdfx_markdown_offset_start": 120,
        "pdfx_markdown_offset_end": 177,
        "page_number": 3,
        "page_label": "3",
        "section_title": "Results",
        "subsection_title": "Disease association",
        "figure_reference": "Fig. 2",
        "chunk_ids": ["chunk-1", "chunk-2"],
    }


def test_evidence_anchor_accepts_full_text_first_contract():
    """Evidence anchors accept the expected text-first contract fields."""

    anchor = EvidenceAnchor(**make_anchor_payload())

    assert anchor.anchor_kind is EvidenceAnchorKind.SNIPPET
    assert anchor.locator_quality is EvidenceLocatorQuality.EXACT_QUOTE
    assert anchor.supports_decision is EvidenceSupportsDecision.SUPPORTS
    assert anchor.page_number == 3
    assert anchor.section_title == "Results"
    assert anchor.chunk_ids == ["chunk-1", "chunk-2"]


def test_evidence_anchor_schema_excludes_bbox_fields():
    """Bounding boxes are not part of the evidence anchor contract."""

    schema = EvidenceAnchor.model_json_schema()
    assert "bbox" not in schema["properties"]

    with pytest.raises(ValidationError):
        EvidenceAnchor(
            **make_anchor_payload(),
            bbox={"left": 1, "top": 2, "right": 3, "bottom": 4},
        )


def test_evidence_anchor_rejects_incomplete_or_reversed_offsets():
    """Markdown offsets must be complete and monotonic."""

    with pytest.raises(ValidationError):
        EvidenceAnchor(
            **{
                **make_anchor_payload(),
                "pdfx_markdown_offset_start": 120,
                "pdfx_markdown_offset_end": None,
            }
        )

    with pytest.raises(ValidationError):
        EvidenceAnchor(
            **{
                **make_anchor_payload(),
                "pdfx_markdown_offset_start": 200,
                "pdfx_markdown_offset_end": 150,
            }
        )


def test_field_validation_result_supports_required_statuses():
    """Field validation results expose the plan-defined statuses."""

    result = FieldValidationResult(
        status=FieldValidationStatus.AMBIGUOUS,
        resolver="agr_db",
        candidate_matches=[
            {
                "label": "APOE",
                "identifier": "HGNC:613",
                "matched_value": "apoE",
                "score": 0.82,
            }
        ],
        warnings=["Matched against a synonym"],
    )

    assert result.status is FieldValidationStatus.AMBIGUOUS
    assert result.resolver == "agr_db"
    assert result.candidate_matches[0].identifier == "HGNC:613"
    assert result.warnings == ["Matched against a synonym"]


def test_submission_payload_requires_a_payload_variant():
    """Submission contracts require structured JSON or serialized text payloads."""

    with pytest.raises(ValidationError):
        SubmissionPayloadContract(
            mode=SubmissionMode.PREVIEW,
            target_system=SubmissionTargetSystem.ALLIANCE_CURATION_API,
            adapter_key="disease",
        )

    payload = SubmissionPayloadContract(
        mode=SubmissionMode.EXPORT,
        target_system=SubmissionTargetSystem.FILE_EXPORT,
        adapter_key="disease",
        candidate_ids=["candidate-1"],
        payload_text="<collection></collection>",
        content_type="application/xml",
        filename="disease-export.xml",
    )

    assert payload.mode is SubmissionMode.EXPORT
    assert payload.target_system is SubmissionTargetSystem.FILE_EXPORT
    assert payload.filename == "disease-export.xml"


def test_submission_target_system_rejects_direct_database_target():
    """Raw direct database writes are not valid submission targets."""

    with pytest.raises(ValidationError):
        SubmissionPayloadContract(
            mode=SubmissionMode.DIRECT_SUBMIT,
            target_system="direct_database",
            adapter_key="disease",
            payload_json={},
        )

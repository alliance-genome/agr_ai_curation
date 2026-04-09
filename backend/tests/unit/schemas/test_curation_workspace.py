"""Unit tests for curation workspace contract schemas."""

import pytest
from pydantic import ValidationError

from src.schemas.curation_workspace import (
    CurationCandidateStatus,
    CurationEntityTagDbValidationStatus,
    CurationEntityTagSource,
    CurationSessionListRequest,
    CurationSessionSortField,
    CurationSessionStatus,
    CurationSortDirection,
    CurationSubmissionStatus,
    CurationWorkspaceResponse,
    EvidenceAnchor,
    EvidenceAnchorKind,
    EvidenceLocatorQuality,
    EvidenceSupportsDecision,
    FieldValidationResult,
    FieldValidationStatus,
    SubmissionMode,
    SubmissionPayloadContract,
)


def make_anchor_payload() -> dict:
    """Build a representative evidence anchor payload."""

    return {
        "anchor_kind": EvidenceAnchorKind.SNIPPET,
        "locator_quality": EvidenceLocatorQuality.EXACT_QUOTE,
        "supports_decision": EvidenceSupportsDecision.SUPPORTS,
        "snippet_text": "Observed response was recorded in treated samples.",
        "sentence_text": "Observed response was recorded in treated samples.",
        "normalized_text": "observed response was recorded in treated samples",
        "viewer_search_text": "Observed response was recorded in treated samples",
        "page_number": 3,
        "page_label": "3",
        "section_title": "Results",
        "subsection_title": "Observed response",
        "figure_reference": "Fig. 2",
        "chunk_ids": ["chunk-1", "chunk-2"],
    }


def make_workspace_response_payload() -> dict:
    """Build a representative workspace payload spanning the new substrate contracts."""

    return {
        "workspace": {
            "session": {
                "session_id": "session-1",
                "status": CurationSessionStatus.IN_PROGRESS,
                "adapter": {
                    "adapter_key": "disease",
                    "display_label": "Disease",
                    "color_token": "teal",
                    "metadata": {},
                },
                "document": {
                    "document_id": "document-1",
                    "title": "Shared workspace contract paper",
                    "pmid": "123456",
                    "citation_label": "PMID:123456",
                    "pdf_url": "/api/documents/document-1/pdf",
                    "viewer_url": "/documents/document-1/viewer",
                },
                "flow_run_id": "flow-run-1",
                "progress": {
                    "total_candidates": 2,
                    "reviewed_candidates": 1,
                    "pending_candidates": 1,
                    "accepted_candidates": 1,
                    "rejected_candidates": 0,
                    "manual_candidates": 0,
                },
                "validation": {
                    "state": "completed",
                    "counts": {
                        "validated": 4,
                        "ambiguous": 1,
                        "not_found": 0,
                        "invalid_format": 0,
                        "conflict": 0,
                        "skipped": 0,
                        "overridden": 0,
                    },
                    "last_validated_at": "2026-03-20T22:10:00Z",
                    "stale_field_keys": [],
                    "warnings": [],
                },
                "evidence": {
                    "total_anchor_count": 3,
                    "resolved_anchor_count": 3,
                    "viewer_highlightable_anchor_count": 2,
                    "quality_counts": {
                        "exact_quote": 2,
                        "normalized_quote": 1,
                        "section_only": 0,
                        "page_only": 0,
                        "document_only": 0,
                        "unresolved": 0,
                    },
                    "degraded": False,
                    "warnings": [],
                },
                "current_candidate_id": "candidate-1",
                "assigned_curator": {
                    "actor_id": "user-1",
                    "display_name": "Curator One",
                },
                "created_by": {
                    "actor_id": "user-1",
                    "display_name": "Curator One",
                },
                "prepared_at": "2026-03-20T22:00:00Z",
                "last_worked_at": "2026-03-20T22:15:00Z",
                "notes": "Ready for review",
                "warnings": [],
                "tags": ["priority"],
                "session_version": 2,
                "extraction_results": [
                    {
                        "extraction_result_id": "extract-1",
                        "document_id": "document-1",
                        "adapter_key": "disease",
                        "agent_key": "curation_prep",
                        "source_kind": "chat",
                        "candidate_count": 2,
                        "payload_json": {"ok": True},
                        "created_at": "2026-03-20T21:55:00Z",
                        "metadata": {},
                    }
                ],
                "latest_submission": {
                    "submission_id": "submission-1",
                    "session_id": "session-1",
                    "adapter_key": "disease",
                    "mode": SubmissionMode.PREVIEW,
                    "target_key": "review_export_bundle",
                    "status": CurationSubmissionStatus.PREVIEW_READY,
                    "readiness": [
                        {
                            "candidate_id": "candidate-1",
                            "ready": True,
                            "blocking_reasons": [],
                            "warnings": [],
                        }
                    ],
                    "payload": {
                        "mode": SubmissionMode.PREVIEW,
                        "target_key": "review_export_bundle",
                        "adapter_key": "disease",
                        "candidate_ids": ["candidate-1"],
                        "payload_json": {"ok": True},
                        "warnings": [],
                    },
                    "requested_at": "2026-03-20T22:18:00Z",
                    "validation_errors": [],
                    "warnings": [],
                },
            },
            "entity_tags": [
                {
                    "tag_id": "candidate-1",
                    "entity_name": "APOE",
                    "entity_type": "ATP:0000005",
                    "species": "",
                    "topic": "",
                    "db_status": CurationEntityTagDbValidationStatus.VALIDATED,
                    "db_entity_id": "HGNC:613",
                    "source": CurationEntityTagSource.AI,
                    "decision": CurationCandidateStatus.ACCEPTED,
                    "evidence": {
                        "sentence_text": "APOE was linked to the phenotype.",
                        "page_number": 3,
                        "section_title": "Results",
                        "chunk_ids": ["chunk-1"],
                    },
                    "notes": None,
                }
            ],
            "candidates": [
                {
                    "candidate_id": "candidate-1",
                    "session_id": "session-1",
                    "source": "extracted",
                    "status": CurationCandidateStatus.ACCEPTED,
                    "order": 0,
                    "adapter_key": "disease",
                    "display_label": "APOE association",
                    "draft": {
                        "draft_id": "draft-1",
                        "candidate_id": "candidate-1",
                        "adapter_key": "disease",
                        "version": 3,
                        "fields": [
                            {
                                "field_key": "gene_symbol",
                                "label": "Gene symbol",
                                "value": "APOE",
                                "seed_value": "APOE",
                                "order": 0,
                                "required": True,
                                "read_only": False,
                                "dirty": False,
                                "stale_validation": False,
                                "evidence_anchor_ids": ["anchor-1"],
                                "validation_result": {
                                    "status": FieldValidationStatus.VALIDATED,
                                    "resolver": "agr_db",
                                    "candidate_matches": [],
                                    "warnings": [],
                                },
                                "metadata": {},
                            }
                        ],
                        "created_at": "2026-03-20T22:01:00Z",
                        "updated_at": "2026-03-20T22:12:00Z",
                        "metadata": {},
                    },
                    "evidence_anchors": [
                        {
                            "anchor_id": "anchor-1",
                            "candidate_id": "candidate-1",
                            "source": "extracted",
                            "field_keys": ["gene_symbol"],
                            "field_group_keys": ["primary"],
                            "is_primary": True,
                            "anchor": {
                                "anchor_kind": EvidenceAnchorKind.SNIPPET,
                                "locator_quality": EvidenceLocatorQuality.EXACT_QUOTE,
                                "supports_decision": EvidenceSupportsDecision.SUPPORTS,
                                "snippet_text": "APOE was linked to the phenotype.",
                                "chunk_ids": ["chunk-1"],
                            },
                            "created_at": "2026-03-20T22:02:00Z",
                            "updated_at": "2026-03-20T22:02:00Z",
                            "warnings": [],
                        }
                    ],
                    "validation": {
                        "state": "completed",
                        "counts": {
                            "validated": 1,
                            "ambiguous": 0,
                            "not_found": 0,
                            "invalid_format": 0,
                            "conflict": 0,
                            "skipped": 0,
                            "overridden": 0,
                        },
                        "stale_field_keys": [],
                        "warnings": [],
                    },
                    "evidence_summary": {
                        "total_anchor_count": 1,
                        "resolved_anchor_count": 1,
                        "viewer_highlightable_anchor_count": 1,
                        "quality_counts": {
                            "exact_quote": 1,
                            "normalized_quote": 0,
                            "section_only": 0,
                            "page_only": 0,
                            "document_only": 0,
                            "unresolved": 0,
                        },
                        "degraded": False,
                        "warnings": [],
                    },
                    "created_at": "2026-03-20T22:01:00Z",
                    "updated_at": "2026-03-20T22:12:00Z",
                    "metadata": {},
                }
            ],
            "active_candidate_id": "candidate-1",
            "queue_context": {
                "filters": {
                    "statuses": [CurationSessionStatus.IN_PROGRESS],
                    "search": "APOE",
                },
                "sort_by": CurationSessionSortField.PREPARED_AT,
                "sort_direction": CurationSortDirection.DESC,
                "position": 1,
                "total_sessions": 3,
                "next_session_id": "session-2",
            },
            "action_log": [
                {
                    "action_id": "action-1",
                    "session_id": "session-1",
                    "candidate_id": "candidate-1",
                    "action_type": "candidate_accepted",
                    "actor_type": "user",
                    "actor": {
                        "actor_id": "user-1",
                        "display_name": "Curator One",
                    },
                    "occurred_at": "2026-03-20T22:12:00Z",
                    "previous_candidate_status": CurationCandidateStatus.PENDING,
                    "new_candidate_status": CurationCandidateStatus.ACCEPTED,
                    "changed_field_keys": [],
                    "evidence_anchor_ids": ["anchor-1"],
                    "metadata": {},
                }
            ],
            "submission_history": [],
        }
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


def test_evidence_anchor_accepts_minimal_required_fields_and_defaults():
    """Evidence anchors allow omission of all optional locator metadata."""

    anchor = EvidenceAnchor(
        anchor_kind=EvidenceAnchorKind.SNIPPET,
        locator_quality=EvidenceLocatorQuality.UNRESOLVED,
        supports_decision=EvidenceSupportsDecision.NEUTRAL,
    )

    assert anchor.snippet_text is None
    assert anchor.sentence_text is None
    assert anchor.viewer_search_text is None
    assert anchor.chunk_ids == []


def test_field_validation_result_supports_required_statuses():
    """Field validation results expose the plan-defined statuses."""

    result = FieldValidationResult(
        status=FieldValidationStatus.AMBIGUOUS,
        resolver="reference_resolver",
        candidate_matches=[
            {
                "label": "Candidate Alpha",
                "identifier": "CURIE:1234",
                "matched_value": "candidate alpha",
                "score": 0.82,
            }
        ],
        warnings=["Matched against an alternate label"],
    )

    assert result.status is FieldValidationStatus.AMBIGUOUS
    assert result.resolver == "reference_resolver"
    assert result.candidate_matches[0].identifier == "CURIE:1234"
    assert result.warnings == ["Matched against an alternate label"]


def test_submission_payload_requires_a_payload_variant():
    """Submission contracts require structured JSON or serialized text payloads."""

    with pytest.raises(ValidationError):
        SubmissionPayloadContract(
            mode=SubmissionMode.PREVIEW,
            target_key="partner_preview",
            adapter_key="workspace_adapter",
        )

    payload = SubmissionPayloadContract(
        mode=SubmissionMode.EXPORT,
        target_key="review_export_bundle",
        adapter_key="workspace_adapter",
        candidate_ids=["candidate-1"],
        payload_text="<collection></collection>",
        content_type="application/xml",
        filename="curation-export.xml",
    )

    assert payload.mode is SubmissionMode.EXPORT
    assert payload.target_key == "review_export_bundle"
    assert payload.filename == "curation-export.xml"


def test_submission_payload_allows_dual_payload_representations():
    """Submission contracts may carry both JSON and text payload variants."""

    payload = SubmissionPayloadContract(
        mode=SubmissionMode.PREVIEW,
        target_key="partner_preview",
        adapter_key="workspace_adapter",
        payload_json={"preview": True},
        payload_text='{"preview": true}',
    )

    assert payload.payload_json == {"preview": True}
    assert payload.payload_text == '{"preview": true}'


def test_submission_payload_accepts_adapter_owned_target_keys():
    """Shared submission contracts allow adapter-owned integration keys."""

    payload = SubmissionPayloadContract(
        mode=SubmissionMode.DIRECT_SUBMIT,
        target_key="partner_submission_api",
        adapter_key="workspace_adapter",
        payload_json={"records": 1},
    )

    assert payload.target_key == "partner_submission_api"


def test_submission_target_key_rejects_blank_values():
    """Blank submission target keys are not valid shared substrate values."""

    with pytest.raises(ValidationError):
        SubmissionPayloadContract(
            mode=SubmissionMode.DIRECT_SUBMIT,
            target_key="   ",
            adapter_key="workspace_adapter",
            payload_json={},
        )


def test_curation_session_status_exposes_required_lifecycle_values():
    """Review-session lifecycle enums expose the expected stable contract values."""

    assert [status.value for status in CurationSessionStatus] == [
        "new",
        "in_progress",
        "paused",
        "ready_for_submission",
        "submitted",
        "rejected",
    ]


def test_workspace_response_accepts_representative_workspace_contract():
    """Workspace response models compose session, draft, evidence, and submission shells."""

    workspace = CurationWorkspaceResponse(**make_workspace_response_payload())

    assert workspace.workspace.session.status is CurationSessionStatus.IN_PROGRESS
    assert workspace.workspace.candidates[0].status is CurationCandidateStatus.ACCEPTED
    assert workspace.workspace.session.latest_submission is not None
    assert (
        workspace.workspace.session.latest_submission.status
        is CurationSubmissionStatus.PREVIEW_READY
    )
    assert workspace.workspace.entity_tags[0].entity_name == "APOE"
    assert (
        workspace.workspace.entity_tags[0].db_status
        is CurationEntityTagDbValidationStatus.VALIDATED
    )
    assert workspace.workspace.candidates[0].draft.fields[0].field_key == "gene_symbol"
    assert (
        workspace.workspace.candidates[0].evidence_anchors[0].anchor.locator_quality
        is EvidenceLocatorQuality.EXACT_QUOTE
    )
    assert workspace.workspace.queue_context.next_session_id == "session-2"


def test_session_list_request_defaults_match_inventory_contract():
    """Inventory list requests default to prepared-at descending pagination."""

    request = CurationSessionListRequest()

    assert request.sort_by is CurationSessionSortField.PREPARED_AT
    assert request.sort_direction is CurationSortDirection.DESC
    assert request.page == 1
    assert request.page_size == 25

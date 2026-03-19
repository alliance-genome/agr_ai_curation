"""Unit tests for curation workspace contract schemas."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.schemas.curation_workspace import (
    CURATION_WORKSPACE_SCHEMA_VERSION,
    CurationActionActorKind,
    CurationActionLogEntry,
    CurationActionType,
    CurationCandidate,
    CurationCandidateDecision,
    CurationCandidateSummary,
    CurationCandidateStatus,
    CurationDocumentSummary,
    CurationDomain,
    CurationDraft,
    CurationDraftField,
    CurationDraftFieldInputKind,
    CurationDraftSection,
    CurationDraftValueSource,
    CurationEvidenceRequest,
    CurationEvidenceResponse,
    CurationEvidenceSummary,
    CurationExtractionPersistenceRequest,
    CurationExtractionPersistenceResponse,
    CurationExtractionResultSummary,
    CurationInventoryResponse,
    CurationReviewProgress,
    CurationSavedView,
    CurationSavedViewScope,
    CurationSavedViewState,
    CurationSessionDetail,
    CurationSessionListFilters,
    CurationSessionNavigation,
    CurationSessionOrigin,
    CurationSessionSortBy,
    CurationSessionSourceKind,
    CurationSessionStatsResponse,
    CurationSessionStatus,
    CurationSortOrder,
    CurationSubmissionRequest,
    CurationSubmissionResponse,
    CurationSubmissionStatus,
    CurationSubmissionSummary,
    CurationValidationRequest,
    CurationValidationResponse,
    CurationValidationSnapshotSummary,
    CurationValidationSummary,
    CurationWorkspaceResponse,
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


NOW = datetime(2026, 3, 19, tzinfo=timezone.utc)


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


def make_field(field_key: str = "disease.name") -> CurationDraftField:
    """Build a minimal valid draft field."""
    return CurationDraftField(
        field_key=field_key,
        label="Disease",
        input_kind=CurationDraftFieldInputKind.TEXT,
        value="Alzheimer disease",
        ai_value="Alzheimer disease",
        dirty=False,
        value_source=CurationDraftValueSource.AI_SEED,
        options=[],
        evidence_anchor_ids=[],
        validation_stale=False,
    )


def make_section(section_key: str = "core_annotation") -> CurationDraftSection:
    """Build a minimal valid draft section."""
    return CurationDraftSection(
        section_key=section_key,
        label="CORE ANNOTATION",
        fields=[make_field()],
        collapsed=False,
    )


def make_draft(**overrides) -> CurationDraft:
    """Build a minimal valid candidate draft."""
    payload = {
        "draft_id": uuid4(),
        "candidate_id": uuid4(),
        "sections": [make_section()],
        "is_dirty": False,
        "dirty_field_keys": [],
        "validation_stale": False,
    }
    payload.update(overrides)
    return CurationDraft(**payload)


def make_session_detail(**overrides) -> CurationSessionDetail:
    """Build a minimal valid session detail payload."""
    payload = {
        "session_id": uuid4(),
        "status": CurationSessionStatus.IN_PROGRESS,
        "domain": CurationDomain.DISEASE,
        "document": CurationDocumentSummary(
            document_id=uuid4(),
            pmid="12345678",
            title="A paper about APOE and disease",
            journal="GENETICS",
            published_at=NOW,
        ),
        "origin": CurationSessionOrigin(
            source_kind=CurationSessionSourceKind.FLOW,
            flow_run_id="flow-run-1",
            trace_id="trace-1",
            label="Nightly disease prep",
        ),
        "candidate_count": 1,
        "reviewed_candidate_count": 0,
        "review_progress": CurationReviewProgress(
            total_candidates=1,
            pending_candidates=1,
            editing_candidates=0,
            reviewed_candidates=0,
            accepted_candidates=0,
            modified_candidates=0,
            rejected_candidates=0,
        ),
        "evidence_summary": CurationEvidenceSummary(
            total_count=2,
            resolved_count=1,
            unresolved_count=1,
        ),
        "validation_summary": CurationValidationSummary(
            total_count=1,
            validated_count=1,
            warning_count=0,
            error_count=0,
            stale_count=0,
            unvalidated_count=0,
        ),
        "prepared_at": NOW,
        "last_worked_at": NOW,
        "created_at": NOW,
        "updated_at": NOW,
        "active_candidate_id": None,
        "notes": None,
        "hydration": None,
        "latest_extraction": None,
    }
    payload.update(overrides)
    return CurationSessionDetail(**payload)


def make_candidate(session_id: str | None = None, **overrides) -> CurationCandidate:
    """Build a minimal valid candidate payload."""
    draft = make_draft()
    payload = {
        "candidate_id": draft.candidate_id,
        "session_id": session_id or uuid4(),
        "queue_position": 1,
        "display_label": "AD - APOE",
        "status": CurationCandidateStatus.PENDING,
        "decision": CurationCandidateDecision.PENDING,
        "has_curator_edits": False,
        "unresolved_ambiguity_count": 0,
        "evidence_summary": CurationEvidenceSummary(
            total_count=2,
            resolved_count=1,
            unresolved_count=1,
        ),
        "validation_summary": CurationValidationSummary(
            total_count=1,
            validated_count=1,
            warning_count=0,
            error_count=0,
            stale_count=0,
            unvalidated_count=0,
        ),
        "draft": draft,
        "evidence_anchor_ids": [],
        "validation_snapshot_ids": [],
        "unresolved_ambiguities": [],
    }
    payload.update(overrides)
    return CurationCandidate(**payload)


def make_saved_view(**overrides) -> CurationSavedView:
    """Build a minimal valid saved inventory view."""
    payload = {
        "saved_view_id": uuid4(),
        "scope": CurationSavedViewScope.INVENTORY,
        "name": "In-progress disease sessions",
        "description": "Default disease triage queue",
        "is_default": True,
        "shared": False,
        "session_id": None,
        "created_by": None,
        "created_at": NOW,
        "updated_at": NOW,
        "state": CurationSavedViewState(
            filters=CurationSessionListFilters(
                statuses=[CurationSessionStatus.IN_PROGRESS],
                domains=[CurationDomain.DISEASE],
                sort_by=CurationSessionSortBy.PREPARED_AT,
                sort_order=CurationSortOrder.DESC,
            )
        ),
    }
    payload.update(overrides)
    return CurationSavedView(**payload)


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


def test_submission_payload_allows_dual_payload_representations():
    """Submission contracts may carry both JSON and text payload variants."""
    payload = SubmissionPayloadContract(
        mode=SubmissionMode.PREVIEW,
        target_system=SubmissionTargetSystem.FILE_EXPORT,
        adapter_key="disease",
        payload_json={"preview": True},
        payload_text='{"preview": true}',
    )

    assert payload.payload_json == {"preview": True}
    assert payload.payload_text == '{"preview": true}'


def test_submission_target_system_rejects_direct_database_target():
    """Raw direct database writes are not valid submission targets."""
    with pytest.raises(ValidationError):
        SubmissionPayloadContract(
            mode=SubmissionMode.DIRECT_SUBMIT,
            target_system="direct_database",
            adapter_key="disease",
            payload_json={},
        )


def test_review_progress_requires_balanced_counts():
    """reviewed_candidates must match the decision buckets."""
    with pytest.raises(ValidationError) as exc_info:
        CurationReviewProgress(
            total_candidates=3,
            pending_candidates=0,
            editing_candidates=1,
            reviewed_candidates=1,
            accepted_candidates=1,
            modified_candidates=1,
            rejected_candidates=0,
        )

    assert "reviewed_candidates" in str(exc_info.value)


def test_draft_rejects_duplicate_section_keys():
    """Draft section keys should be unique."""
    with pytest.raises(ValidationError) as exc_info:
        make_draft(sections=[make_section("core"), make_section("core")])

    assert "section keys" in str(exc_info.value)


def test_draft_rejects_unknown_dirty_field_keys():
    """dirty_field_keys should only reference real fields."""
    with pytest.raises(ValidationError) as exc_info:
        make_draft(is_dirty=True, dirty_field_keys=["missing.field"])

    assert "dirty_field_keys" in str(exc_info.value)


def test_session_list_filters_reject_inverted_date_ranges():
    """Inventory filters should reject inverted prepared dates."""
    with pytest.raises(ValidationError) as exc_info:
        CurationSessionListFilters(
            sort_by=CurationSessionSortBy.PREPARED_AT,
            sort_order=CurationSortOrder.DESC,
            prepared_from=datetime(2026, 3, 20, tzinfo=timezone.utc),
            prepared_to=datetime(2026, 3, 19, tzinfo=timezone.utc),
        )

    assert "prepared_from" in str(exc_info.value)


def test_workspace_response_accepts_hydrated_payload():
    """A fully hydrated workspace envelope should validate cleanly."""
    session = make_session_detail()
    candidate = make_candidate(session.session_id, summary="Seeded disease annotation")
    action = CurationActionLogEntry(
        action_id=uuid4(),
        session_id=session.session_id,
        candidate_id=candidate.candidate_id,
        action_type=CurationActionType.SESSION_CREATED,
        actor_kind=CurationActionActorKind.SYSTEM,
        metadata={"source": "bootstrap"},
        created_at=NOW,
    )

    response = CurationWorkspaceResponse(
        schema_version=CURATION_WORKSPACE_SCHEMA_VERSION,
        session=session,
        candidates=[candidate],
        action_log=[action],
        navigation=CurationSessionNavigation(
            previous_session_id=None,
            next_session_id=uuid4(),
            queue_position=1,
            total_sessions=3,
        ),
        saved_views=[make_saved_view()],
    )

    assert response.schema_version == CURATION_WORKSPACE_SCHEMA_VERSION
    assert response.session.document.pmid == "12345678"
    assert response.candidates[0].display_label == "AD - APOE"
    assert response.action_log[0].action_type == CurationActionType.SESSION_CREATED


def test_session_stats_accept_ready_for_submission_bucket():
    """Inventory stats should expose the ready_for_submission status bucket."""
    response = CurationSessionStatsResponse(
        total_sessions=3,
        new_sessions=1,
        in_progress_sessions=1,
        ready_for_submission_sessions=1,
        submitted_sessions=0,
        paused_sessions=0,
        rejected_sessions=0,
    )

    assert response.ready_for_submission_sessions == 1


def test_saved_view_rejects_workspace_state_for_inventory_scope():
    """Inventory saved views should not carry workspace hydration state."""
    with pytest.raises(ValidationError) as exc_info:
        make_saved_view(
            state=CurationSavedViewState(
                hydration={
                    "selected_candidate_id": str(uuid4()),
                    "panel_layout": {"left": 0.3, "right": 0.7},
                }
            )
        )

    assert "workspace hydration state" in str(exc_info.value)


def test_saved_view_requires_session_for_workspace_scope():
    """Workspace saved views require a concrete session binding."""
    with pytest.raises(ValidationError) as exc_info:
        make_saved_view(
            scope=CurationSavedViewScope.WORKSPACE,
            session_id=None,
            state=CurationSavedViewState(selected_candidate_id=uuid4()),
        )

    assert "require session_id" in str(exc_info.value)


def test_validation_request_rejects_unknown_field_keys():
    """Validation requests should reject field keys absent from the provided draft."""
    with pytest.raises(ValidationError) as exc_info:
        CurationValidationRequest(
            session_id=uuid4(),
            candidate_id=uuid4(),
            draft=make_draft(),
            field_keys=["missing.field"],
            force_refresh=True,
        )

    assert "unknown draft fields" in str(exc_info.value)


def test_workspace_endpoint_envelopes_reference_canonical_all_93_types():
    """Workspace request and response envelopes should use merged sibling contract types."""
    session = make_session_detail(status=CurationSessionStatus.READY_FOR_SUBMISSION)
    snapshot = CurationValidationSnapshotSummary(
        validation_snapshot_id=uuid4(),
        session_id=session.session_id,
        candidate_id=uuid4(),
        summary=CurationValidationSummary(
            total_count=1,
            validated_count=1,
            warning_count=0,
            error_count=0,
            stale_count=0,
            unvalidated_count=0,
        ),
        created_at=NOW,
        created_by=None,
        stale=False,
    )
    candidate = make_candidate(
        session.session_id,
        latest_validation_snapshot=snapshot,
    )

    inventory = CurationInventoryResponse(
        sessions=[session],
        total=1,
        page=1,
        page_size=20,
        applied_filters=CurationSessionListFilters(
            statuses=[CurationSessionStatus.READY_FOR_SUBMISSION],
            sort_by=CurationSessionSortBy.PREPARED_AT,
            sort_order=CurationSortOrder.DESC,
        ),
        stats=CurationSessionStatsResponse(
            total_sessions=1,
            new_sessions=0,
            in_progress_sessions=0,
            ready_for_submission_sessions=1,
            submitted_sessions=0,
            paused_sessions=0,
            rejected_sessions=0,
        ),
        saved_views=[make_saved_view()],
    )
    evidence = CurationEvidenceResponse(
        session_id=session.session_id,
        candidate_id=candidate.candidate_id,
        summary=CurationEvidenceSummary(
            total_count=1,
            resolved_count=1,
            unresolved_count=0,
        ),
        evidence_anchors=[EvidenceAnchor(**make_anchor_payload())],
    )
    validation = CurationValidationResponse(
        session_id=session.session_id,
        candidate_id=candidate.candidate_id,
        snapshot=snapshot,
        results=[
            FieldValidationResult(
                status=FieldValidationStatus.VALIDATED,
                resolver="agr_db",
            )
        ],
    )
    submission_request = CurationSubmissionRequest(
        session_id=session.session_id,
        candidate_ids=[candidate.candidate_id],
        submission_payload=SubmissionPayloadContract(
            mode=SubmissionMode.PREVIEW,
            target_system=SubmissionTargetSystem.ALLIANCE_CURATION_API,
            adapter_key="disease",
            candidate_ids=[str(candidate.candidate_id)],
            payload_json={"preview": True},
        ),
    )
    submission_response = CurationSubmissionResponse(
        session=session,
        submitted_candidate_ids=[candidate.candidate_id],
        submission_summary=CurationSubmissionSummary(
            submission_id=uuid4(),
            status=CurationSubmissionStatus.PENDING,
        ),
    )
    extraction_request = CurationExtractionPersistenceRequest(
        document_id=session.document.document_id,
        domain=session.domain,
        source_kind=CurationSessionSourceKind.FLOW,
        extraction_payload={"raw_payload_id": "raw-1"},
        flow_run_id="flow-run-1",
    )
    extraction_response = CurationExtractionPersistenceResponse(
        extraction_result=CurationExtractionResultSummary(
            extraction_result_id=uuid4(),
            document_id=session.document.document_id,
            domain=session.domain,
            source_kind=CurationSessionSourceKind.FLOW,
            flow_run_id="flow-run-1",
            created_at=NOW,
        ),
        seeded_candidates=[
            CurationCandidateSummary(
                candidate_id=candidate.candidate_id,
                session_id=session.session_id,
                queue_position=1,
                display_label=candidate.display_label,
                status=candidate.status,
                decision=candidate.decision,
                has_curator_edits=False,
                unresolved_ambiguity_count=0,
                evidence_summary=CurationEvidenceSummary(
                    total_count=1,
                    resolved_count=1,
                    unresolved_count=0,
                ),
                validation_summary=CurationValidationSummary(
                    total_count=1,
                    validated_count=1,
                    warning_count=0,
                    error_count=0,
                    stale_count=0,
                    unvalidated_count=0,
                ),
            )
        ],
    )

    assert inventory.stats.ready_for_submission_sessions == 1
    assert evidence.evidence_anchors[0].anchor_kind is EvidenceAnchorKind.SNIPPET
    assert validation.results[0].status is FieldValidationStatus.VALIDATED
    assert submission_request.submission_payload.mode is SubmissionMode.PREVIEW
    assert submission_response.submission_summary.status is CurationSubmissionStatus.PENDING
    assert extraction_request.extraction_payload["raw_payload_id"] == "raw-1"
    assert extraction_response.seeded_candidates[0].candidate_id == candidate.candidate_id


def test_evidence_request_requires_a_resolution_bucket():
    """Evidence request must include resolved or unresolved anchors."""
    with pytest.raises(ValidationError) as exc_info:
        CurationEvidenceRequest(
            session_id=uuid4(),
            candidate_id=uuid4(),
            include_resolved=False,
            include_unresolved=False,
        )

    assert "resolved or unresolved anchors" in str(exc_info.value)

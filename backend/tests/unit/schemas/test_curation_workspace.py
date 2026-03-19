"""Unit tests for curation workspace schema contracts."""

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
    CurationCandidateStatus,
    CurationDocumentSummary,
    CurationDomain,
    CurationDraft,
    CurationDraftField,
    CurationDraftFieldInputKind,
    CurationDraftSection,
    CurationDraftValueSource,
    CurationEvidenceSummary,
    CurationReviewProgress,
    CurationSessionDetail,
    CurationSessionListFilters,
    CurationSessionNavigation,
    CurationSessionOrigin,
    CurationSessionSortBy,
    CurationSessionSourceKind,
    CurationSessionStatus,
    CurationSortOrder,
    CurationValidationSummary,
    CurationWorkspaceResponse,
)


NOW = datetime(2026, 3, 19, tzinfo=timezone.utc)


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


def make_candidate(session_id, **overrides) -> CurationCandidate:
    """Build a minimal valid candidate payload."""
    draft = make_draft()
    payload = {
        "candidate_id": draft.candidate_id,
        "session_id": session_id,
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
    )

    assert response.schema_version == CURATION_WORKSPACE_SCHEMA_VERSION
    assert response.session.document.pmid == "12345678"
    assert response.candidates[0].display_label == "AD - APOE"
    assert response.action_log[0].action_type == CurationActionType.SESSION_CREATED

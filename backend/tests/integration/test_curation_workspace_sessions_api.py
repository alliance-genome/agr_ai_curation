"""Integration tests for curation workspace review-session endpoints."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import sys
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from fastapi import Security
from fastapi.testclient import TestClient

from conftest import MOCK_USERS


def _hash(char: str) -> str:
    return char * 64


def _seed_submission_record(
    test_db,
    *,
    session_id: str,
    adapter_key: str,
    candidate_id: str,
    status,
    mode="direct_submit",
    target_key: str = "review_export_bundle",
) -> str:
    from src.lib.curation_workspace import session_service
    from src.lib.curation_workspace.models import CurationSubmissionRecord
    from src.schemas.curation_workspace import SubmissionMode, SubmissionPayloadContract

    normalized_mode = SubmissionMode(mode)

    requested_at = datetime(2026, 3, 5, 15, 10, tzinfo=timezone.utc)
    record = CurationSubmissionRecord(
        id=uuid4(),
        session_id=UUID(session_id),
        adapter_key=adapter_key,
        mode=normalized_mode,
        target_key=target_key,
        status=status,
        readiness=[
            {
                "candidate_id": candidate_id,
                "ready": True,
                "blocking_reasons": [],
                "warnings": [],
            }
        ],
        payload=session_service._serialize_submission_payload_contract(
            SubmissionPayloadContract(
                mode=normalized_mode,
                target_key=target_key,
                adapter_key=adapter_key,
                candidate_ids=[candidate_id],
                payload_json={"candidate_count": 1},
                payload_text='{"candidate_count": 1}',
                content_type="application/json",
                filename="submission.json",
            )
        ),
        response_message="Initial submission failed.",
        requested_at=requested_at,
        completed_at=requested_at,
    )
    test_db.add(record)
    test_db.commit()
    return str(record.id)


@pytest.fixture
def client(test_db, get_auth_mock, monkeypatch):
    """Create isolated app client with auth and database overrides."""
    monkeypatch.setenv("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY", "test-key"))
    monkeypatch.setenv("GROQ_API_KEY", os.getenv("GROQ_API_KEY", "test-key"))
    monkeypatch.setenv("LLM_PROVIDER_STRICT_MODE", "false")

    get_auth_mock.set_user("curator1")

    modules_to_clear = [
        name
        for name in list(sys.modules.keys())
        if name == "main" or name.startswith("src.")
    ]
    for module_name in modules_to_clear:
        del sys.modules[module_name]

    with patch("src.api.auth.get_auth_dependency") as mock_get_auth_dep:
        mock_get_auth_dep.return_value = Security(get_auth_mock.get_user)

        from main import app
        from src.models.sql.database import get_db

        def override_get_db():
            yield test_db

        app.dependency_overrides[get_db] = override_get_db
        try:
            test_client = TestClient(app)
            test_client.current_user_auth_sub = MOCK_USERS["curator1"]["sub"]
            test_client.other_user_auth_sub = MOCK_USERS["curator2"]["sub"]
            yield test_client
        finally:
            app.dependency_overrides.clear()


@pytest.fixture
def seeded_review_sessions(client: TestClient, test_db):
    """Seed curation workspace records used by the session endpoint tests."""
    from src.lib.curation_workspace.models import (
        CurationActionLogEntry as SessionActionLogModel,
        CurationCandidate,
        CurationDraft,
        CurationEvidenceRecord,
        CurationExtractionResultRecord,
        CurationReviewSession,
        CurationSavedView,
        CurationSubmissionRecord,
        CurationValidationSnapshot,
    )
    from src.models.sql.database import Base
    from src.models.sql.pdf_document import PDFDocument
    from src.models.sql.user import User
    from src.schemas.curation_workspace import (
        CurationCandidateSource,
        CurationCandidateStatus,
        CurationEvidenceSource,
        CurationExtractionSourceKind,
        CurationSessionStatus,
        CurationSubmissionStatus,
        CurationValidationScope,
        CurationValidationSnapshotState,
        SubmissionMode,
    )

    now = datetime.now(timezone.utc)
    creator_auth_sub = "test_cw_creator"
    current_user_auth_sub = client.current_user_auth_sub
    other_user_auth_sub = client.other_user_auth_sub

    Base.metadata.create_all(
        bind=test_db.get_bind(),
        tables=[
            User.__table__,
            PDFDocument.__table__,
            CurationReviewSession.__table__,
            CurationExtractionResultRecord.__table__,
            CurationCandidate.__table__,
            CurationEvidenceRecord.__table__,
            CurationDraft.__table__,
            CurationValidationSnapshot.__table__,
            CurationSubmissionRecord.__table__,
            SessionActionLogModel.__table__,
            CurationSavedView.__table__,
        ],
    )

    users = [
        User(
            auth_sub=current_user_auth_sub,
            email="curator1@alliancegenome.org",
            display_name="Curator One",
            is_active=True,
        ),
        User(
            auth_sub=other_user_auth_sub,
            email="curator2@alliancegenome.org",
            display_name="Curator Two",
            is_active=True,
        ),
        User(
            auth_sub=creator_auth_sub,
            email="creator@alliancegenome.org",
            display_name="Creator User",
            is_active=True,
        ),
    ]
    test_db.add_all(users)

    document_alpha_id = uuid4()
    document_beta_id = uuid4()
    document_gamma_id = uuid4()

    documents = [
        PDFDocument(
            id=document_alpha_id,
            filename="test_cw_alpha.pdf",
            title="Alpha 100% curation paper",
            file_path=f"{document_alpha_id}/alpha.pdf",
            file_hash=_hash("a"),
            file_size=4096,
            page_count=3,
        ),
        PDFDocument(
            id=document_beta_id,
            filename="test_cw_beta.pdf",
            title="Beta_gene paper",
            file_path=f"{document_beta_id}/beta.pdf",
            file_hash=_hash("b"),
            file_size=4096,
            page_count=4,
        ),
        PDFDocument(
            id=document_gamma_id,
            filename="test_cw_gamma.pdf",
            title="BetaXgene submission paper",
            file_path=f"{document_gamma_id}/gamma.pdf",
            file_hash=_hash("c"),
            file_size=4096,
            page_count=5,
        ),
    ]
    test_db.add_all(documents)
    test_db.commit()

    session_alpha_id = uuid4()
    session_beta_id = uuid4()
    session_gamma_id = uuid4()
    candidate_alpha_id = uuid4()
    candidate_beta_id = uuid4()
    candidate_gamma_id = uuid4()
    extraction_alpha_id = uuid4()
    extraction_beta_id = uuid4()
    extraction_gamma_id = uuid4()

    extraction_results = [
        CurationExtractionResultRecord(
            id=extraction_alpha_id,
            document_id=document_alpha_id,
            adapter_key="disease",
            profile_key="primary",
            domain_key="disease",
            agent_key="curation_prep",
            source_kind=CurationExtractionSourceKind.FLOW,
            flow_run_id="flow-alpha",
            candidate_count=1,
            payload_json={"source": "alpha"},
            extraction_metadata={"batch": "alpha"},
            created_at=datetime(2026, 3, 1, 9, 30, tzinfo=timezone.utc),
        ),
        CurationExtractionResultRecord(
            id=extraction_beta_id,
            document_id=document_beta_id,
            adapter_key="gene",
            profile_key="secondary",
            domain_key="gene",
            agent_key="curation_prep",
            source_kind=CurationExtractionSourceKind.FLOW,
            flow_run_id="flow-alpha",
            candidate_count=1,
            payload_json={"source": "beta"},
            extraction_metadata={"batch": "beta"},
            created_at=datetime(2026, 3, 5, 11, 30, tzinfo=timezone.utc),
        ),
        CurationExtractionResultRecord(
            id=extraction_gamma_id,
            document_id=document_gamma_id,
            adapter_key="disease",
            profile_key="primary",
            domain_key="disease",
            agent_key="curation_prep",
            source_kind=CurationExtractionSourceKind.FLOW,
            flow_run_id="flow-beta",
            candidate_count=1,
            payload_json={"source": "gamma"},
            extraction_metadata={"batch": "gamma"},
            created_at=datetime(2026, 3, 10, 8, 30, tzinfo=timezone.utc),
        ),
    ]
    test_db.add_all(extraction_results)

    sessions = [
        CurationReviewSession(
            id=session_alpha_id,
            status=CurationSessionStatus.NEW,
            adapter_key="disease",
            profile_key="primary",
            document_id=document_alpha_id,
            flow_run_id="flow-alpha",
            current_candidate_id=candidate_alpha_id,
            assigned_curator_id=current_user_auth_sub,
            created_by_id=creator_auth_sub,
            session_version=1,
            notes="Alpha notes",
            tags=["priority"],
            total_candidates=1,
            reviewed_candidates=0,
            pending_candidates=1,
            accepted_candidates=0,
            rejected_candidates=0,
            manual_candidates=0,
            warnings=[],
            prepared_at=datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc),
            last_worked_at=datetime(2026, 3, 1, 11, 0, tzinfo=timezone.utc),
        ),
        CurationReviewSession(
            id=session_beta_id,
            status=CurationSessionStatus.IN_PROGRESS,
            adapter_key="gene",
            profile_key="secondary",
            document_id=document_beta_id,
            flow_run_id="flow-alpha",
            current_candidate_id=candidate_beta_id,
            assigned_curator_id=other_user_auth_sub,
            created_by_id=creator_auth_sub,
            session_version=3,
            notes="Beta notes",
            tags=["triage"],
            total_candidates=1,
            reviewed_candidates=1,
            pending_candidates=0,
            accepted_candidates=1,
            rejected_candidates=0,
            manual_candidates=0,
            warnings=[],
            prepared_at=datetime(2026, 3, 5, 12, 0, tzinfo=timezone.utc),
            last_worked_at=datetime(2026, 3, 5, 15, 0, tzinfo=timezone.utc),
        ),
        CurationReviewSession(
            id=session_gamma_id,
            status=CurationSessionStatus.SUBMITTED,
            adapter_key="disease",
            profile_key="primary",
            document_id=document_gamma_id,
            flow_run_id="flow-beta",
            current_candidate_id=candidate_gamma_id,
            assigned_curator_id=current_user_auth_sub,
            created_by_id=creator_auth_sub,
            session_version=2,
            notes="Gamma notes",
            tags=["complete"],
            total_candidates=1,
            reviewed_candidates=1,
            pending_candidates=0,
            accepted_candidates=1,
            rejected_candidates=0,
            manual_candidates=0,
            warnings=[],
            prepared_at=datetime(2026, 3, 10, 9, 0, tzinfo=timezone.utc),
            last_worked_at=datetime(2026, 3, 11, 9, 0, tzinfo=timezone.utc),
            submitted_at=now - timedelta(days=2),
        ),
    ]
    test_db.add_all(sessions)
    test_db.commit()

    candidates = [
        CurationCandidate(
            id=candidate_alpha_id,
            session_id=session_alpha_id,
            source=CurationCandidateSource.EXTRACTED,
            status=CurationCandidateStatus.PENDING,
            order=0,
            adapter_key="disease",
            profile_key="primary",
            display_label="Alpha disease candidate",
            secondary_label="ALPHA",
            confidence=0.82,
            extraction_result_id=extraction_alpha_id,
            candidate_metadata={"ticket": "ALL-105"},
            created_at=datetime(2026, 3, 1, 10, 5, tzinfo=timezone.utc),
            updated_at=datetime(2026, 3, 1, 10, 5, tzinfo=timezone.utc),
        ),
        CurationCandidate(
            id=candidate_beta_id,
            session_id=session_beta_id,
            source=CurationCandidateSource.EXTRACTED,
            status=CurationCandidateStatus.ACCEPTED,
            order=0,
            adapter_key="gene",
            profile_key="secondary",
            display_label="Beta gene candidate",
            secondary_label="BETA",
            confidence=0.91,
            extraction_result_id=extraction_beta_id,
            candidate_metadata={"ticket": "ALL-105"},
            created_at=datetime(2026, 3, 5, 12, 5, tzinfo=timezone.utc),
            updated_at=datetime(2026, 3, 5, 14, 5, tzinfo=timezone.utc),
            last_reviewed_at=datetime(2026, 3, 5, 14, 0, tzinfo=timezone.utc),
        ),
        CurationCandidate(
            id=candidate_gamma_id,
            session_id=session_gamma_id,
            source=CurationCandidateSource.EXTRACTED,
            status=CurationCandidateStatus.ACCEPTED,
            order=0,
            adapter_key="disease",
            profile_key="primary",
            display_label="Gamma disease candidate",
            secondary_label="GAMMA",
            confidence=0.88,
            extraction_result_id=extraction_gamma_id,
            candidate_metadata={"ticket": "ALL-105"},
            created_at=datetime(2026, 3, 10, 9, 5, tzinfo=timezone.utc),
            updated_at=datetime(2026, 3, 10, 10, 5, tzinfo=timezone.utc),
            last_reviewed_at=datetime(2026, 3, 10, 10, 0, tzinfo=timezone.utc),
        ),
    ]
    test_db.add_all(candidates)

    drafts = [
        CurationDraft(
            candidate_id=candidate_alpha_id,
            adapter_key="disease",
            version=1,
            title="Alpha draft",
            summary="Alpha summary",
            fields=[
                {
                    "field_key": "disease_term",
                    "label": "Disease term",
                    "value": "Alpha disease",
                    "seed_value": "Alpha disease",
                    "order": 0,
                    "required": True,
                    "read_only": False,
                    "dirty": False,
                    "stale_validation": False,
                    "evidence_anchor_ids": [],
                    "metadata": {},
                }
            ],
            notes="Alpha draft notes",
            created_at=datetime(2026, 3, 1, 10, 6, tzinfo=timezone.utc),
            updated_at=datetime(2026, 3, 1, 10, 7, tzinfo=timezone.utc),
            draft_metadata={},
        ),
        CurationDraft(
            candidate_id=candidate_beta_id,
            adapter_key="gene",
            version=2,
            title="Beta draft",
            summary="Beta summary",
            fields=[
                {
                    "field_key": "gene_symbol",
                    "label": "Gene symbol",
                    "value": "BETA1",
                    "seed_value": "BETA1",
                    "order": 0,
                    "required": True,
                    "read_only": False,
                    "dirty": False,
                    "stale_validation": False,
                    "evidence_anchor_ids": [],
                    "metadata": {},
                }
            ],
            notes="Beta draft notes",
            created_at=datetime(2026, 3, 5, 12, 6, tzinfo=timezone.utc),
            updated_at=datetime(2026, 3, 5, 14, 6, tzinfo=timezone.utc),
            last_saved_at=datetime(2026, 3, 5, 14, 10, tzinfo=timezone.utc),
            draft_metadata={"source": "fixture"},
        ),
        CurationDraft(
            candidate_id=candidate_gamma_id,
            adapter_key="disease",
            version=2,
            title="Gamma draft",
            summary="Gamma summary",
            fields=[
                {
                    "field_key": "disease_term",
                    "label": "Disease term",
                    "value": "Gamma disease",
                    "seed_value": "Gamma disease",
                    "order": 0,
                    "required": True,
                    "read_only": False,
                    "dirty": False,
                    "stale_validation": False,
                    "evidence_anchor_ids": [],
                    "metadata": {},
                }
            ],
            notes="Gamma draft notes",
            created_at=datetime(2026, 3, 10, 9, 6, tzinfo=timezone.utc),
            updated_at=datetime(2026, 3, 10, 10, 6, tzinfo=timezone.utc),
            last_saved_at=datetime(2026, 3, 10, 10, 10, tzinfo=timezone.utc),
            draft_metadata={},
        ),
    ]
    test_db.add_all(drafts)

    evidence_records = [
        CurationEvidenceRecord(
            candidate_id=candidate_alpha_id,
            source=CurationEvidenceSource.EXTRACTED,
            field_keys=["disease_term"],
            field_group_keys=["primary"],
            is_primary=True,
            anchor={
                "anchor_kind": "snippet",
                "locator_quality": "exact_quote",
                "supports_decision": "supports",
            },
            warnings=[],
        ),
        CurationEvidenceRecord(
            candidate_id=candidate_beta_id,
            source=CurationEvidenceSource.EXTRACTED,
            field_keys=["gene_symbol"],
            field_group_keys=["primary"],
            is_primary=True,
            anchor={
                "anchor_kind": "sentence",
                "locator_quality": "normalized_quote",
                "supports_decision": "supports",
            },
            warnings=[],
        ),
        CurationEvidenceRecord(
            candidate_id=candidate_gamma_id,
            source=CurationEvidenceSource.EXTRACTED,
            field_keys=["disease_term"],
            field_group_keys=["primary"],
            is_primary=True,
            anchor={
                "anchor_kind": "page",
                "locator_quality": "page_only",
                "supports_decision": "supports",
            },
            warnings=[],
        ),
    ]
    test_db.add_all(evidence_records)

    validation_snapshots = [
        CurationValidationSnapshot(
            scope=CurationValidationScope.SESSION,
            session_id=session_alpha_id,
            adapter_key="disease",
            state=CurationValidationSnapshotState.PENDING,
            field_results={},
            summary={
                "state": "pending",
                "counts": {
                    "validated": 0,
                    "ambiguous": 0,
                    "not_found": 0,
                    "invalid_format": 0,
                    "conflict": 0,
                    "skipped": 0,
                    "overridden": 0,
                },
                "warnings": [],
                "stale_field_keys": [],
            },
            warnings=[],
            requested_at=datetime(2026, 3, 1, 10, 30, tzinfo=timezone.utc),
        ),
        CurationValidationSnapshot(
            scope=CurationValidationScope.SESSION,
            session_id=session_beta_id,
            adapter_key="gene",
            state=CurationValidationSnapshotState.COMPLETED,
            field_results={},
            summary={
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
                "warnings": [],
                "stale_field_keys": [],
                "last_validated_at": "2026-03-05T14:30:00Z",
            },
            warnings=[],
            requested_at=datetime(2026, 3, 5, 14, 0, tzinfo=timezone.utc),
            completed_at=datetime(2026, 3, 5, 14, 30, tzinfo=timezone.utc),
        ),
        CurationValidationSnapshot(
            scope=CurationValidationScope.SESSION,
            session_id=session_gamma_id,
            adapter_key="disease",
            state=CurationValidationSnapshotState.COMPLETED,
            field_results={},
            summary={
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
                "warnings": [],
                "stale_field_keys": [],
                "last_validated_at": "2026-03-10T10:30:00Z",
            },
            warnings=[],
            requested_at=datetime(2026, 3, 10, 10, 0, tzinfo=timezone.utc),
            completed_at=datetime(2026, 3, 10, 10, 30, tzinfo=timezone.utc),
        ),
    ]
    test_db.add_all(validation_snapshots)

    submissions = [
        CurationSubmissionRecord(
            session_id=session_beta_id,
            adapter_key="gene",
            mode=SubmissionMode.PREVIEW,
            target_key="review_export_bundle",
            status=CurationSubmissionStatus.PREVIEW_READY,
            readiness=[
                {
                    "candidate_id": str(candidate_beta_id),
                    "ready": True,
                    "blocking_reasons": [],
                    "warnings": [],
                }
            ],
            payload={"ok": True},
            requested_at=datetime(2026, 3, 5, 15, 5, tzinfo=timezone.utc),
            completed_at=datetime(2026, 3, 5, 15, 6, tzinfo=timezone.utc),
        )
    ]
    test_db.add_all(submissions)
    test_db.commit()

    yield {
        "session_alpha_id": str(session_alpha_id),
        "session_beta_id": str(session_beta_id),
        "session_gamma_id": str(session_gamma_id),
        "candidate_alpha_id": str(candidate_alpha_id),
        "candidate_beta_id": str(candidate_beta_id),
        "candidate_gamma_id": str(candidate_gamma_id),
        "draft_alpha_id": str(drafts[0].id),
        "draft_beta_id": str(drafts[1].id),
        "draft_gamma_id": str(drafts[2].id),
        "current_user_auth_sub": current_user_auth_sub,
        "other_user_auth_sub": other_user_auth_sub,
    }

    test_db.query(SessionActionLogModel).filter(
        SessionActionLogModel.session_id.in_([session_alpha_id, session_beta_id, session_gamma_id])
    ).delete(synchronize_session=False)
    test_db.query(CurationSubmissionRecord).filter(
        CurationSubmissionRecord.session_id.in_([session_alpha_id, session_beta_id, session_gamma_id])
    ).delete(synchronize_session=False)
    test_db.query(CurationValidationSnapshot).filter(
        CurationValidationSnapshot.session_id.in_([session_alpha_id, session_beta_id, session_gamma_id])
    ).delete(synchronize_session=False)
    test_db.query(CurationEvidenceRecord).filter(
        CurationEvidenceRecord.candidate_id.in_([candidate_alpha_id, candidate_beta_id, candidate_gamma_id])
    ).delete(synchronize_session=False)
    test_db.query(CurationDraft).filter(
        CurationDraft.candidate_id.in_([candidate_alpha_id, candidate_beta_id, candidate_gamma_id])
    ).delete(synchronize_session=False)
    test_db.query(CurationCandidate).filter(
        CurationCandidate.id.in_([candidate_alpha_id, candidate_beta_id, candidate_gamma_id])
    ).delete(synchronize_session=False)
    test_db.query(CurationExtractionResultRecord).filter(
        CurationExtractionResultRecord.id.in_([extraction_alpha_id, extraction_beta_id, extraction_gamma_id])
    ).delete(synchronize_session=False)
    test_db.query(CurationReviewSession).filter(
        CurationReviewSession.id.in_([session_alpha_id, session_beta_id, session_gamma_id])
    ).delete(synchronize_session=False)
    test_db.commit()


def test_list_review_sessions_supports_filters_sorting_and_pagination(
    client: TestClient,
    seeded_review_sessions,
):
    response = client.get(
        "/api/curation-workspace/sessions",
        params={
            "domain_key": "disease",
            "curator_id": seeded_review_sessions["current_user_auth_sub"],
            "prepared_from": "2026-03-01T00:00:00Z",
            "prepared_to": "2026-03-15T00:00:00Z",
            "sort_by": "prepared_at",
            "sort_direction": "asc",
            "page": 1,
            "page_size": 1,
            "group_by_flow_run": "true",
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    assert [session["session_id"] for session in payload["sessions"]] == [
        seeded_review_sessions["session_alpha_id"]
    ]
    assert payload["page_info"] == {
        "page": 1,
        "page_size": 1,
        "total_items": 2,
        "total_pages": 2,
        "has_next_page": True,
        "has_previous_page": False,
    }
    assert {group["flow_run_id"] for group in payload["flow_run_groups"]} == {
        "flow-alpha",
        "flow-beta",
    }

    filtered_response = client.get(
        "/api/curation-workspace/sessions",
        params={
            "status": "in_progress",
            "adapter_key": "gene",
            "flow_run_id": "flow-alpha",
            "domain_key": "gene",
            "curator_id": seeded_review_sessions["other_user_auth_sub"],
            "sort_by": "last_worked_at",
            "sort_direction": "desc",
        },
    )
    assert filtered_response.status_code == 200, filtered_response.text
    filtered_payload = filtered_response.json()
    assert [session["session_id"] for session in filtered_payload["sessions"]] == [
        seeded_review_sessions["session_beta_id"]
    ]


def test_get_review_flow_runs_returns_filtered_group_summaries(
    client: TestClient,
    seeded_review_sessions,
):
    response = client.get(
        "/api/curation-workspace/flow-runs",
        params={
            "status": "submitted",
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["applied_filters"]["statuses"] == ["submitted"]
    assert payload["flow_runs"] == [
        {
            "flow_run_id": "flow-beta",
            "display_label": "flow-beta",
            "session_count": 1,
            "reviewed_count": 1,
            "pending_count": 0,
            "submitted_count": 1,
            "last_activity_at": "2026-03-11T09:00:00Z",
        }
    ]


def test_get_review_flow_run_sessions_returns_paginated_group_members(
    client: TestClient,
    seeded_review_sessions,
):
    response = client.get(
        "/api/curation-workspace/flow-runs/flow-alpha/sessions",
        params={
            "page": 1,
            "page_size": 1,
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["flow_run"] == {
        "flow_run_id": "flow-alpha",
        "display_label": "flow-alpha",
        "session_count": 2,
        "reviewed_count": 1,
        "pending_count": 1,
        "submitted_count": 0,
        "last_activity_at": "2026-03-05T15:00:00Z",
    }
    assert [session["session_id"] for session in payload["sessions"]] == [
        seeded_review_sessions["session_beta_id"]
    ]
    assert payload["page_info"] == {
        "page": 1,
        "page_size": 1,
        "total_items": 2,
        "total_pages": 2,
        "has_next_page": True,
        "has_previous_page": False,
    }


def test_get_review_session_returns_detail_payload(
    client: TestClient,
    seeded_review_sessions,
):
    response = client.get(
        f"/api/curation-workspace/sessions/{seeded_review_sessions['session_beta_id']}"
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["session_id"] == seeded_review_sessions["session_beta_id"]
    assert payload["document"]["title"] == "Beta_gene paper"
    assert payload["assigned_curator"]["actor_id"] == seeded_review_sessions["other_user_auth_sub"]
    assert payload["extraction_results"][0]["domain_key"] == "gene"
    assert payload["latest_submission"]["status"] == "preview_ready"
    assert payload["latest_submission"]["payload"]["payload_json"] == {"ok": True}


def test_get_review_session_include_workspace_returns_hydrated_workspace_payload(
    client: TestClient,
    seeded_review_sessions,
):
    response = client.get(
        f"/api/curation-workspace/sessions/{seeded_review_sessions['session_beta_id']}",
        params={"include_workspace": "true"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["workspace"]["session"]["session_id"] == seeded_review_sessions["session_beta_id"]
    assert payload["workspace"]["session"]["document"]["title"] == "Beta_gene paper"
    assert payload["workspace"]["active_candidate_id"] is not None
    assert payload["workspace"]["queue_context"] is None
    assert payload["workspace"]["saved_view_context"] is None
    assert payload["workspace"]["action_log"] == []
    assert payload["workspace"]["submission_history"][0]["status"] == "preview_ready"

    candidate = payload["workspace"]["candidates"][0]
    assert candidate["candidate_id"] == payload["workspace"]["active_candidate_id"]
    assert candidate["display_label"] == "Beta gene candidate"
    assert candidate["draft"]["title"] == "Beta draft"
    assert candidate["draft"]["fields"][0]["field_key"] == "gene_symbol"
    assert candidate["evidence_anchors"][0]["anchor"]["locator_quality"] == "normalized_quote"
    assert candidate["evidence_summary"]["quality_counts"]["normalized_quote"] == 1


def test_list_review_sessions_search_escapes_like_wildcards(
    client: TestClient,
    seeded_review_sessions,
):
    percent_response = client.get(
        "/api/curation-workspace/sessions",
        params={"search": "100%"},
    )
    assert percent_response.status_code == 200, percent_response.text
    assert [session["session_id"] for session in percent_response.json()["sessions"]] == [
        seeded_review_sessions["session_alpha_id"]
    ]

    underscore_response = client.get(
        "/api/curation-workspace/sessions",
        params={"search": "Beta_gene"},
    )
    assert underscore_response.status_code == 200, underscore_response.text
    assert [session["session_id"] for session in underscore_response.json()["sessions"]] == [
        seeded_review_sessions["session_beta_id"]
    ]


def test_list_review_sessions_supports_adapter_sorting(
    client: TestClient,
    seeded_review_sessions,
):
    ascending_response = client.get(
        "/api/curation-workspace/sessions",
        params={
            "sort_by": "adapter",
            "sort_direction": "asc",
        },
    )
    assert ascending_response.status_code == 200, ascending_response.text
    assert [session["session_id"] for session in ascending_response.json()["sessions"]] == [
        seeded_review_sessions["session_gamma_id"],
        seeded_review_sessions["session_alpha_id"],
        seeded_review_sessions["session_beta_id"],
    ]

    descending_response = client.get(
        "/api/curation-workspace/sessions",
        params={
            "sort_by": "adapter",
            "sort_direction": "desc",
        },
    )
    assert descending_response.status_code == 200, descending_response.text
    assert [session["session_id"] for session in descending_response.json()["sessions"]] == [
        seeded_review_sessions["session_beta_id"],
        seeded_review_sessions["session_gamma_id"],
        seeded_review_sessions["session_alpha_id"],
    ]


def test_patch_review_session_updates_status_and_notes(
    client: TestClient,
    seeded_review_sessions,
    test_db,
):
    from src.lib.curation_workspace.models import CurationReviewSession
    from src.schemas.curation_workspace import CurationSessionStatus

    response = client.patch(
        f"/api/curation-workspace/sessions/{seeded_review_sessions['session_alpha_id']}",
        json={
            "session_id": seeded_review_sessions["session_alpha_id"],
            "status": "paused",
            "notes": "Paused for curator follow-up",
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["session"]["status"] == "paused"
    assert payload["session"]["notes"] == "Paused for curator follow-up"
    assert payload["session"]["session_version"] == 2
    assert payload["session"]["paused_at"] is not None
    assert payload["action_log_entry"]["action_type"] == "session_status_updated"
    assert payload["action_log_entry"]["new_session_status"] == "paused"

    refreshed = test_db.get(
        CurationReviewSession,
        UUID(seeded_review_sessions["session_alpha_id"]),
    )
    assert refreshed is not None
    assert refreshed.status == CurationSessionStatus.PAUSED
    assert refreshed.notes == "Paused for curator follow-up"


def test_post_candidate_decision_updates_status_and_writes_action_log(
    client: TestClient,
    seeded_review_sessions,
    test_db,
):
    from src.lib.curation_workspace.models import (
        CurationActionLogEntry as SessionActionLogModel,
        CurationCandidate,
    )
    from src.schemas.curation_workspace import CurationCandidateStatus

    response = client.post(
        f"/api/curation-workspace/candidates/{seeded_review_sessions['candidate_alpha_id']}/decision",
        json={
            "session_id": seeded_review_sessions["session_alpha_id"],
            "candidate_id": seeded_review_sessions["candidate_alpha_id"],
            "action": "accept",
            "advance_queue": True,
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["candidate"]["status"] == "accepted"
    assert payload["session"]["status"] == "in_progress"
    assert payload["next_candidate_id"] is None
    assert payload["action_log_entry"]["action_type"] == "candidate_accepted"
    assert payload["action_log_entry"]["previous_candidate_status"] == "pending"
    assert payload["action_log_entry"]["new_candidate_status"] == "accepted"

    refreshed_candidate = test_db.get(
        CurationCandidate,
        UUID(seeded_review_sessions["candidate_alpha_id"]),
    )
    assert refreshed_candidate is not None
    assert refreshed_candidate.status == CurationCandidateStatus.ACCEPTED

    action_logs = (
        test_db.query(SessionActionLogModel)
        .filter(SessionActionLogModel.session_id == UUID(seeded_review_sessions["session_alpha_id"]))
        .order_by(SessionActionLogModel.occurred_at.asc())
        .all()
    )

    assert len(action_logs) == 1
    assert action_logs[0].action_type.value == "candidate_accepted"


def test_get_review_session_stats_returns_aggregate_counts(
    client: TestClient,
    seeded_review_sessions,
):
    response = client.get("/api/curation-workspace/sessions/stats")
    assert response.status_code == 200, response.text
    payload = response.json()
    stats = payload["stats"]

    assert stats["total_sessions"] == 3
    assert stats["domain_count"] == 2
    assert stats["new_sessions"] == 1
    assert stats["in_progress_sessions"] == 1
    assert stats["ready_for_submission_sessions"] == 0
    assert stats["paused_sessions"] == 0
    assert stats["submitted_sessions"] == 1
    assert stats["rejected_sessions"] == 0
    assert stats["assigned_to_current_user"] == 2
    assert stats["assigned_to_others"] == 1
    assert stats["submitted_last_7_days"] == 1


def test_get_next_review_session_returns_queue_navigation_context(
    client: TestClient,
    seeded_review_sessions,
):
    response = client.get(
        "/api/curation-workspace/sessions/next",
        params={
            "current_session_id": seeded_review_sessions["session_alpha_id"],
            "sort_by": "prepared_at",
            "sort_direction": "asc",
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["session"]["session_id"] == seeded_review_sessions["session_beta_id"]
    assert payload["queue_context"] == {
        "filters": {
            "statuses": [],
            "adapter_keys": [],
            "profile_keys": [],
            "domain_keys": [],
            "curator_ids": [],
            "tags": [],
            "flow_run_id": None,
            "origin_session_id": None,
            "document_id": None,
            "search": None,
            "prepared_between": None,
            "last_worked_between": None,
            "saved_view_id": None,
        },
        "sort_by": "prepared_at",
        "sort_direction": "asc",
        "position": 2,
        "total_sessions": 3,
        "previous_session_id": seeded_review_sessions["session_alpha_id"],
        "next_session_id": seeded_review_sessions["session_gamma_id"],
    }


def test_patch_review_candidate_draft_revalidates_changed_fields(
    client: TestClient,
    seeded_review_sessions,
):
    response = client.patch(
        (
            "/api/curation-workspace/sessions/"
            f"{seeded_review_sessions['session_beta_id']}/candidates/"
            f"{seeded_review_sessions['candidate_beta_id']}/draft"
        ),
        json={
            "session_id": seeded_review_sessions["session_beta_id"],
            "candidate_id": seeded_review_sessions["candidate_beta_id"],
            "draft_id": seeded_review_sessions["draft_beta_id"],
            "expected_version": 2,
            "field_changes": [
                {
                    "field_key": "gene_symbol",
                    "value": "BETA2",
                }
            ],
            "autosave": True,
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["draft"]["version"] == 3
    assert payload["draft"]["fields"][0]["value"] == "BETA2"
    assert payload["draft"]["fields"][0]["dirty"] is True
    assert payload["draft"]["fields"][0]["stale_validation"] is False
    assert payload["draft"]["fields"][0]["validation_result"]["status"] == "overridden"
    assert payload["validation_snapshot"]["scope"] == "candidate"
    assert payload["validation_snapshot"]["field_results"]["gene_symbol"]["status"] == "overridden"
    assert payload["candidate"]["validation"]["counts"]["overridden"] == 1
    assert payload["action_log_entry"]["action_type"] == "candidate_updated"


def test_post_candidate_validation_returns_completed_snapshot(
    client: TestClient,
    seeded_review_sessions,
):
    response = client.post(
        (
            "/api/curation-workspace/candidates/"
            f"{seeded_review_sessions['candidate_alpha_id']}/validate"
        ),
        json={
            "session_id": seeded_review_sessions["session_alpha_id"],
            "candidate_id": seeded_review_sessions["candidate_alpha_id"],
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["candidate"]["candidate_id"] == seeded_review_sessions["candidate_alpha_id"]
    assert payload["validation_snapshot"]["scope"] == "candidate"
    assert payload["validation_snapshot"]["state"] == "completed"
    assert payload["validation_snapshot"]["field_results"]["disease_term"]["status"] == "skipped"
    assert payload["candidate"]["draft"]["fields"][0]["validation_result"]["status"] == "skipped"
    assert payload["candidate"]["draft"]["fields"][0]["stale_validation"] is False


def test_post_candidate_validation_only_updates_requested_field_keys(
    client: TestClient,
    test_db,
    seeded_review_sessions,
):
    from src.lib.curation_workspace.models import CurationDraft

    draft = (
        test_db.query(CurationDraft)
        .filter(CurationDraft.id == UUID(seeded_review_sessions["draft_beta_id"]))
        .one()
    )
    draft.fields = [
        {
            "field_key": "field_a",
            "label": "Field A",
            "value": "Alpha",
            "seed_value": "Alpha",
            "order": 0,
            "required": True,
            "read_only": False,
            "dirty": False,
            "stale_validation": True,
            "evidence_anchor_ids": [],
            "metadata": {},
        },
        {
            "field_key": "field_b",
            "label": "Field B",
            "value": "Beta",
            "seed_value": "Beta",
            "order": 1,
            "required": False,
            "read_only": False,
            "dirty": False,
            "stale_validation": True,
            "evidence_anchor_ids": [],
            "validation_result": {
                "status": "ambiguous",
                "resolver": "fixture",
                "candidate_matches": [],
                "warnings": ["Needs follow-up"],
            },
            "metadata": {},
        },
    ]
    test_db.add(draft)
    test_db.commit()

    response = client.post(
        (
            "/api/curation-workspace/candidates/"
            f"{seeded_review_sessions['candidate_beta_id']}/validate"
        ),
        json={
            "session_id": seeded_review_sessions["session_beta_id"],
            "candidate_id": seeded_review_sessions["candidate_beta_id"],
            "field_keys": ["field_a"],
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    fields_by_key = {
        field["field_key"]: field
        for field in payload["candidate"]["draft"]["fields"]
    }
    assert fields_by_key["field_a"]["stale_validation"] is False
    assert fields_by_key["field_a"]["validation_result"]["status"] == "skipped"
    assert fields_by_key["field_b"]["stale_validation"] is True
    assert fields_by_key["field_b"]["validation_result"]["status"] == "ambiguous"
    assert payload["validation_snapshot"]["field_results"]["field_b"]["status"] == "ambiguous"
    assert payload["validation_snapshot"]["summary"]["stale_field_keys"] == ["field_b"]
    assert payload["candidate"]["validation"]["counts"]["skipped"] == 1
    assert payload["candidate"]["validation"]["counts"]["ambiguous"] == 1


def test_post_session_validation_returns_session_and_candidate_snapshots(
    client: TestClient,
    seeded_review_sessions,
):
    response = client.post(
        (
            "/api/curation-workspace/sessions/"
            f"{seeded_review_sessions['session_alpha_id']}/validate-all"
        ),
        json={
            "session_id": seeded_review_sessions["session_alpha_id"],
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["session"]["session_id"] == seeded_review_sessions["session_alpha_id"]
    assert payload["session_validation"]["scope"] == "session"
    assert payload["session_validation"]["state"] == "completed"
    assert payload["session_validation"]["summary"]["counts"]["skipped"] == 1
    assert len(payload["candidate_validations"]) == 1
    assert payload["candidate_validations"][0]["candidate_id"] == seeded_review_sessions["candidate_alpha_id"]
    assert payload["candidate_validations"][0]["field_results"]["disease_term"]["status"] == "skipped"


def test_post_submission_retry_creates_new_submission_record(
    client: TestClient,
    seeded_review_sessions,
    test_db,
    monkeypatch,
):
    from src.lib.curation_workspace import session_service
    from src.lib.curation_workspace.models import CurationSubmissionRecord
    from src.lib.curation_workspace.submission_adapters import NoOpSubmissionAdapter
    from src.schemas.curation_workspace import (
        CurationActionType,
        CurationSubmissionStatus,
        SubmissionMode,
    )

    original_submission_id = _seed_submission_record(
        test_db,
        session_id=seeded_review_sessions["session_beta_id"],
        adapter_key="gene",
        candidate_id=seeded_review_sessions["candidate_beta_id"],
        status=CurationSubmissionStatus.FAILED,
    )
    monkeypatch.setattr(
        session_service,
        "_resolve_submission_transport_adapter",
        lambda _target_key: NoOpSubmissionAdapter(target_key="review_export_bundle"),
    )

    response = client.post(
        (
            "/api/curation-workspace/sessions/"
            f"{seeded_review_sessions['session_beta_id']}/submissions/{original_submission_id}/retry"
        ),
        json={
            "submission_id": original_submission_id,
            "reason": "Retry after transient submission transport failure.",
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["submission"]["submission_id"] != original_submission_id
    assert payload["submission"]["status"] == "accepted"
    assert payload["submission"]["target_key"] == "review_export_bundle"
    assert payload["submission"]["payload"]["candidate_ids"] == [
        seeded_review_sessions["candidate_beta_id"]
    ]
    assert payload["action_log_entry"]["action_type"] == "submission_retried"
    assert payload["action_log_entry"]["metadata"]["original_submission_id"] == original_submission_id

    original_record = test_db.get(CurationSubmissionRecord, UUID(original_submission_id))
    assert original_record is not None
    assert original_record.status == CurationSubmissionStatus.FAILED
    assert original_record.response_message == "Initial submission failed."

    retried_record = test_db.get(
        CurationSubmissionRecord,
        UUID(payload["submission"]["submission_id"]),
    )
    assert retried_record is not None
    assert retried_record.status == CurationSubmissionStatus.ACCEPTED
    assert retried_record.adapter_key == "gene"

    direct_submit_rows = (
        test_db.query(CurationSubmissionRecord)
        .filter(CurationSubmissionRecord.session_id == UUID(seeded_review_sessions["session_beta_id"]))
        .filter(CurationSubmissionRecord.mode == SubmissionMode.DIRECT_SUBMIT)
        .order_by(CurationSubmissionRecord.requested_at.asc())
        .all()
    )
    assert len(direct_submit_rows) == 2
    assert str(direct_submit_rows[0].id) == original_submission_id
    assert direct_submit_rows[0].status == CurationSubmissionStatus.FAILED
    assert direct_submit_rows[1].status == CurationSubmissionStatus.ACCEPTED

    action_log_rows = (
        test_db.query(session_service.SessionActionLogModel)
        .filter(
            session_service.SessionActionLogModel.session_id
            == UUID(seeded_review_sessions["session_beta_id"])
        )
        .filter(
            session_service.SessionActionLogModel.action_type
            == CurationActionType.SUBMISSION_RETRIED
        )
        .all()
    )
    assert len(action_log_rows) == 1


def test_get_submission_history_returns_single_submission_record(
    client: TestClient,
    seeded_review_sessions,
    test_db,
):
    from src.lib.curation_workspace.models import CurationSubmissionRecord
    from src.schemas.curation_workspace import CurationSubmissionStatus

    submission_id = _seed_submission_record(
        test_db,
        session_id=seeded_review_sessions["session_beta_id"],
        adapter_key="gene",
        candidate_id=seeded_review_sessions["candidate_beta_id"],
        status=CurationSubmissionStatus.FAILED,
    )

    response = client.get(
        (
            "/api/curation-workspace/sessions/"
            f"{seeded_review_sessions['session_beta_id']}/submissions/{submission_id}"
        )
    )

    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["submission"]["submission_id"] == submission_id
    assert payload["submission"]["session_id"] == seeded_review_sessions["session_beta_id"]
    assert payload["submission"]["status"] == "failed"
    assert payload["submission"]["payload"]["candidate_ids"] == [
        seeded_review_sessions["candidate_beta_id"]
    ]
    assert payload["submission"]["response_message"] == "Initial submission failed."

    persisted_submission = test_db.get(CurationSubmissionRecord, UUID(submission_id))
    assert persisted_submission is not None

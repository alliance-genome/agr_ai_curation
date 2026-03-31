"""End-to-end submission workflow coverage for the curation workspace substrate."""

from __future__ import annotations

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
            yield test_client
        finally:
            app.dependency_overrides.clear()


@pytest.fixture
def submission_e2e_context(client: TestClient, test_db):
    """Seed the document and cleanup all curation workspace records after the e2e run."""
    from src.lib.curation_workspace.models import (
        CurationActionLogEntry as SessionActionLogModel,
        CurationCandidate,
        CurationDraft,
        CurationEvidenceRecord,
        CurationExtractionResultRecord,
        CurationReviewSession,
        CurationSubmissionRecord,
        CurationValidationSnapshot,
    )
    from src.models.sql.database import Base
    from src.models.sql.pdf_document import PDFDocument
    from src.models.sql.user import User

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
        ],
    )

    current_user_auth_sub = client.current_user_auth_sub
    test_db.add(
        User(
            auth_sub=current_user_auth_sub,
            email="curator1@alliancegenome.org",
            display_name="Curator One",
            is_active=True,
        )
    )

    document_id = uuid4()
    test_db.add(
        PDFDocument(
            id=document_id,
            filename="test_submission_e2e.pdf",
            title="Submission E2E Paper",
            file_path=f"{document_id}/submission-e2e.pdf",
            file_hash=_hash("e"),
            file_size=4096,
            page_count=4,
        )
    )
    test_db.commit()

    yield {
        "document_id": str(document_id),
        "current_user_auth_sub": current_user_auth_sub,
    }

    session_ids = [
        row[0]
        for row in (
            test_db.query(CurationReviewSession.id)
            .filter(CurationReviewSession.document_id == document_id)
            .all()
        )
    ]
    candidate_ids = [
        row[0]
        for row in (
            test_db.query(CurationCandidate.id)
            .filter(CurationCandidate.session_id.in_(session_ids))
            .all()
        )
    ] if session_ids else []

    if session_ids:
        test_db.query(SessionActionLogModel).filter(
            SessionActionLogModel.session_id.in_(session_ids)
        ).delete(synchronize_session=False)
        test_db.query(CurationSubmissionRecord).filter(
            CurationSubmissionRecord.session_id.in_(session_ids)
        ).delete(synchronize_session=False)
        test_db.query(CurationValidationSnapshot).filter(
            CurationValidationSnapshot.session_id.in_(session_ids)
        ).delete(synchronize_session=False)

    if candidate_ids:
        test_db.query(CurationEvidenceRecord).filter(
            CurationEvidenceRecord.candidate_id.in_(candidate_ids)
        ).delete(synchronize_session=False)
        test_db.query(CurationDraft).filter(
            CurationDraft.candidate_id.in_(candidate_ids)
        ).delete(synchronize_session=False)
        test_db.query(CurationCandidate).filter(
            CurationCandidate.id.in_(candidate_ids)
        ).delete(synchronize_session=False)

    if session_ids:
        test_db.query(CurationReviewSession).filter(
            CurationReviewSession.id.in_(session_ids)
        ).delete(synchronize_session=False)

    test_db.query(CurationExtractionResultRecord).filter(
        CurationExtractionResultRecord.document_id == document_id
    ).delete(synchronize_session=False)
    test_db.query(PDFDocument).filter(PDFDocument.id == document_id).delete(synchronize_session=False)
    test_db.query(User).filter(User.auth_sub == current_user_auth_sub).delete(synchronize_session=False)
    test_db.commit()


def _reference_prep_output_payload() -> dict[str, object]:
    return {
        "candidates": [
            {
                "adapter_key": "reference_adapter",
                "payload": {
                    "citation": {
                        "title": "Adapter-owned reference scaffold in practice",
                        "authors": ["Ada Lovelace", "Grace Hopper"],
                        "journal": "Journal of Adapter Boundaries",
                        "publication_year": "2025",
                    },
                    "identifiers": {
                        "doi": "10.1000/reference-1",
                    },
                },
                "evidence_records": [
                    {
                        "evidence_record_id": "reference-evidence-title",
                        "source": "extracted",
                        "extraction_result_id": "prep-extract-reference",
                        "field_paths": ["citation.title"],
                        "anchor": {
                            "anchor_kind": "snippet",
                            "locator_quality": "exact_quote",
                            "supports_decision": "supports",
                            "snippet_text": "Adapter-owned reference scaffold in practice",
                            "sentence_text": "Adapter-owned reference scaffold in practice",
                            "normalized_text": None,
                            "viewer_search_text": "Adapter-owned reference scaffold in practice",
                            "pdfx_markdown_offset_start": 12,
                            "pdfx_markdown_offset_end": 60,
                            "page_number": 2,
                            "page_label": None,
                            "section_title": "Results",
                            "subsection_title": None,
                            "figure_reference": None,
                            "table_reference": None,
                            "chunk_ids": ["chunk-reference-title"],
                        },
                        "notes": ["The title is quoted directly from the manuscript."],
                    },
                    {
                        "evidence_record_id": "reference-evidence-doi",
                        "source": "extracted",
                        "extraction_result_id": "prep-extract-reference",
                        "field_paths": ["identifiers.doi"],
                        "anchor": {
                            "anchor_kind": "snippet",
                            "locator_quality": "exact_quote",
                            "supports_decision": "supports",
                            "snippet_text": "10.1000/reference-1",
                            "sentence_text": "10.1000/reference-1",
                            "normalized_text": None,
                            "viewer_search_text": "10.1000/reference-1",
                            "pdfx_markdown_offset_start": 200,
                            "pdfx_markdown_offset_end": 223,
                            "page_number": 4,
                            "page_label": None,
                            "section_title": "References",
                            "subsection_title": None,
                            "figure_reference": None,
                            "table_reference": None,
                            "chunk_ids": ["chunk-reference-doi"],
                        },
                        "notes": ["The DOI is present in the reference block."],
                    },
                ],
                "conversation_context_summary": (
                    "Conversation narrowed the workspace to a single supporting reference."
                ),
            }
        ],
        "run_metadata": {
            "model_name": "gpt-5.4-nano",
            "token_usage": {
                "input_tokens": 90,
                "output_tokens": 35,
                "total_tokens": 125,
            },
            "processing_notes": [
                "Reference candidate normalized by the adapter scaffold.",
            ],
            "warnings": [],
        },
    }


@pytest.mark.asyncio
async def test_deterministic_prep_bootstrap_preserves_tool_verified_evidence_anchors(
    submission_e2e_context,
    test_db,
):
    from src.lib.curation_workspace.bootstrap_service import bootstrap_document_session
    from src.lib.curation_workspace.curation_prep_service import (
        CurationPrepPersistenceContext,
        run_curation_prep,
    )
    from src.lib.curation_workspace.session_service import get_session_workspace
    from src.schemas.curation_prep import CurationPrepScopeConfirmation
    from src.schemas.curation_workspace import (
        CurationDocumentBootstrapRequest,
        CurationExtractionResultRecord,
        CurationExtractionSourceKind,
    )

    extraction_result = CurationExtractionResultRecord.model_validate(
        {
            "extraction_result_id": "extract-observation-1",
            "document_id": submission_e2e_context["document_id"],
            "adapter_key": "gene",
            "agent_key": "gene_extractor",
            "source_kind": CurationExtractionSourceKind.CHAT,
            "origin_session_id": "chat-session-1",
            "trace_id": "trace-observation-1",
            "flow_run_id": None,
            "user_id": submission_e2e_context["current_user_auth_sub"],
            "candidate_count": 1,
            "conversation_summary": "Conversation focused on evidence-backed extraction findings.",
            "payload_json": {
                "items": [
                    {
                        "gene_symbol": "alpha-1",
                        "entity_type": "gene",
                        "normalized_id": "FB:FBgn0000008",
                        "source_mentions": ["Alpha mention"],
                        "evidence": [
                            {
                                "entity": "alpha-1",
                                "verified_quote": "alpha-1 was supported by a verified observation.",
                                "page": 6,
                                "section": "Results",
                                "subsection": "Observation set",
                                "chunk_id": "chunk-alpha-1",
                                "figure_reference": "Figure 3B",
                            }
                        ],
                    }
                ],
                "evidence_records": [
                    {
                        "entity": "alpha-1",
                        "verified_quote": "alpha-1 was supported by a verified observation.",
                        "page": 6,
                        "section": "Results",
                        "subsection": "Observation set",
                        "chunk_id": "chunk-alpha-1",
                        "figure_reference": "Figure 3B",
                    }
                ],
                "run_summary": {"candidate_count": 1},
            },
            "created_at": "2026-03-28T12:00:00Z",
            "metadata": {},
        }
    )

    prep_output = await run_curation_prep(
        [extraction_result],
        scope_confirmation=CurationPrepScopeConfirmation(
            confirmed=True,
            adapter_keys=["gene"],
            notes=["Confirmed from chat session bootstrap test."],
        ),
        db=test_db,
        persistence_context=CurationPrepPersistenceContext(
            origin_session_id="chat-session-1",
            user_id=submission_e2e_context["current_user_auth_sub"],
            source_kind=CurationExtractionSourceKind.CHAT,
        ),
    )

    assert len(prep_output.candidates) == 1

    bootstrap_response = await bootstrap_document_session(
        submission_e2e_context["document_id"],
        CurationDocumentBootstrapRequest(origin_session_id="chat-session-1"),
        current_user_id=submission_e2e_context["current_user_auth_sub"],
        db=test_db,
    )

    assert bootstrap_response.created is True
    assert bootstrap_response.session.adapter.adapter_key == "gene"
    assert bootstrap_response.session.progress.total_candidates == 1

    workspace = get_session_workspace(test_db, bootstrap_response.session.session_id)
    candidate = workspace.workspace.candidates[0]
    assert candidate.adapter_key == "gene"
    label_field = next(
        field for field in candidate.draft.fields if field.field_key == "gene_symbol"
    )
    assert label_field.value == "alpha-1"
    assert candidate.evidence_anchors[0].field_keys == [
        "gene_symbol",
        "entity_type",
        "normalized_id",
        "source_mentions.0",
    ]
    assert candidate.evidence_anchors[0].anchor.snippet_text == (
        "alpha-1 was supported by a verified observation."
    )
    assert candidate.evidence_anchors[0].anchor.page_number == 6
    assert candidate.evidence_anchors[0].anchor.section_title == "Results"
    assert candidate.evidence_anchors[0].anchor.subsection_title == "Observation set"
    assert candidate.evidence_anchors[0].anchor.figure_reference == "Figure 3B"
    assert candidate.evidence_anchors[0].anchor.table_reference is None


def test_submission_workflow_e2e_with_retry_and_history(
    client: TestClient,
    submission_e2e_context,
    test_db,
    monkeypatch,
):
    from src.lib.curation_workspace import session_service
    from src.lib.curation_workspace.extraction_results import persist_extraction_result
    from src.lib.curation_workspace.models import CurationReviewSession, CurationSubmissionRecord
    from src.lib.curation_workspace.submission_adapters import NoOpSubmissionAdapter
    from src.schemas.curation_prep import CurationPrepAgentOutput
    from src.schemas.curation_workspace import (
        CurationActionType,
        CurationExtractionPersistenceRequest,
        CurationExtractionSourceKind,
        CurationSessionStatus,
        CurationSubmissionStatus,
    )

    prep_output = CurationPrepAgentOutput.model_validate(_reference_prep_output_payload())
    prep_output_payload = prep_output.model_dump(mode="json")
    persist_extraction_result(
        CurationExtractionPersistenceRequest(
            document_id=submission_e2e_context["document_id"],
            adapter_key="reference_adapter",
            agent_key="curation_prep",
            source_kind=CurationExtractionSourceKind.CHAT,
            origin_session_id="chat-session-submission-e2e",
            trace_id="trace-submission-e2e",
            flow_run_id="flow-submission-e2e",
            user_id=submission_e2e_context["current_user_auth_sub"],
            candidate_count=len(prep_output.candidates),
            conversation_summary="Prepare one reference candidate for submission e2e coverage.",
            payload_json={
                "candidates": prep_output_payload["candidates"],
                "run_metadata": {
                    "model_name": "placeholder",
                    "token_usage": {
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "total_tokens": 0,
                    },
                    "processing_notes": [],
                    "warnings": [],
                },
            },
            metadata={
                "final_run_metadata": prep_output_payload["run_metadata"],
            },
        ),
        db=test_db,
    )

    bootstrap_response = client.post(
        (
            "/api/curation-workspace/documents/"
            f"{submission_e2e_context['document_id']}/bootstrap"
        ),
        json={},
    )
    assert bootstrap_response.status_code == 200, bootstrap_response.text
    bootstrap_payload = bootstrap_response.json()
    assert bootstrap_payload["created"] is True
    session_id = bootstrap_payload["session"]["session_id"]
    assert bootstrap_payload["session"]["adapter"]["adapter_key"] == "reference_adapter"
    assert bootstrap_payload["session"]["progress"]["total_candidates"] == 1

    workspace_response = client.get(
        f"/api/curation-workspace/sessions/{session_id}",
        params={"include_workspace": "true"},
    )
    assert workspace_response.status_code == 200, workspace_response.text
    workspace_payload = workspace_response.json()["workspace"]
    assert workspace_payload["session"]["session_id"] == session_id
    assert workspace_payload["submission_history"] == []
    assert len(workspace_payload["candidates"]) == 1

    candidate = workspace_payload["candidates"][0]
    candidate_id = candidate["candidate_id"]
    draft = candidate["draft"]
    string_field = next(
        field
        for field in draft["fields"]
        if isinstance(field["value"], str) and field["value"]
    )
    edited_value = f"{string_field['value']} (reviewed)"

    draft_response = client.patch(
        (
            "/api/curation-workspace/sessions/"
            f"{session_id}/candidates/{candidate_id}/draft"
        ),
        json={
            "session_id": session_id,
            "candidate_id": candidate_id,
            "draft_id": draft["draft_id"],
            "expected_version": draft["version"],
            "field_changes": [
                {
                    "field_key": string_field["field_key"],
                    "value": edited_value,
                }
            ],
            "autosave": True,
        },
    )
    assert draft_response.status_code == 200, draft_response.text
    draft_payload = draft_response.json()
    assert any(
        field["field_key"] == string_field["field_key"] and field["value"] == edited_value
        for field in draft_payload["draft"]["fields"]
    )
    assert draft_payload["action_log_entry"]["action_type"] == "candidate_updated"

    decision_response = client.post(
        f"/api/curation-workspace/candidates/{candidate_id}/decision",
        json={
            "session_id": session_id,
            "candidate_id": candidate_id,
            "action": "accept",
            "advance_queue": True,
        },
    )
    assert decision_response.status_code == 200, decision_response.text
    decision_payload = decision_response.json()
    assert decision_payload["candidate"]["status"] == "accepted"

    validation_response = client.post(
        f"/api/curation-workspace/sessions/{session_id}/validate-all",
        json={"session_id": session_id},
    )
    assert validation_response.status_code == 200, validation_response.text
    validation_payload = validation_response.json()
    assert validation_payload["session"]["session_id"] == session_id
    assert len(validation_payload["candidate_validations"]) == 1
    assert validation_payload["candidate_validations"][0]["candidate_id"] == candidate_id

    preview_response = client.post(
        f"/api/curation-workspace/sessions/{session_id}/submission-preview",
        json={
            "session_id": session_id,
            "mode": "export",
            "target_key": "review_export_bundle",
            "include_payload": True,
        },
    )
    assert preview_response.status_code == 200, preview_response.text
    preview_payload = preview_response.json()
    assert preview_payload["submission"]["status"] == "export_ready"
    assert preview_payload["submission"]["payload"]["candidate_ids"] == [candidate_id]
    assert preview_payload["submission"]["payload"]["payload_json"]["candidate_count"] == 1

    monkeypatch.setattr(
        session_service,
        "_resolve_submission_transport_adapter",
        lambda _target_key: NoOpSubmissionAdapter(
            target_key="review_export_bundle",
            response_status=CurationSubmissionStatus.FAILED,
        ),
    )
    failed_submit_response = client.post(
        f"/api/curation-workspace/sessions/{session_id}/submit",
        json={
            "session_id": session_id,
            "target_key": "review_export_bundle",
        },
    )
    assert failed_submit_response.status_code == 200, failed_submit_response.text
    failed_submit_payload = failed_submit_response.json()
    failed_submission_id = failed_submit_payload["submission"]["submission_id"]
    assert failed_submit_payload["submission"]["status"] == "failed"
    assert failed_submit_payload["action_log_entry"]["action_type"] == "submission_executed"

    session_row = test_db.get(CurationReviewSession, UUID(session_id))
    assert session_row is not None
    assert session_row.status != CurationSessionStatus.SUBMITTED

    persisted_failed_submission = test_db.get(
        CurationSubmissionRecord,
        UUID(failed_submission_id),
    )
    assert persisted_failed_submission is not None
    assert persisted_failed_submission.status == CurationSubmissionStatus.FAILED

    monkeypatch.setattr(
        session_service,
        "_resolve_submission_transport_adapter",
        lambda _target_key: NoOpSubmissionAdapter(target_key="review_export_bundle"),
    )
    retry_response = client.post(
        (
            "/api/curation-workspace/sessions/"
            f"{session_id}/submissions/{failed_submission_id}/retry"
        ),
        json={
            "submission_id": failed_submission_id,
            "reason": "Retry after downstream transport recovered.",
        },
    )
    assert retry_response.status_code == 200, retry_response.text
    retry_payload = retry_response.json()
    retried_submission_id = retry_payload["submission"]["submission_id"]
    assert retried_submission_id != failed_submission_id
    assert retry_payload["submission"]["status"] == "accepted"
    assert retry_payload["action_log_entry"]["action_type"] == "submission_retried"
    assert retry_payload["action_log_entry"]["metadata"]["original_submission_id"] == failed_submission_id

    history_response = client.get(
        (
            "/api/curation-workspace/sessions/"
            f"{session_id}/submissions/{retried_submission_id}"
        )
    )
    assert history_response.status_code == 200, history_response.text
    history_payload = history_response.json()
    assert history_payload["submission"]["submission_id"] == retried_submission_id
    assert history_payload["submission"]["status"] == "accepted"
    assert history_payload["submission"]["external_reference"] == "noop:review_export_bundle:1"

    final_workspace_response = client.get(
        f"/api/curation-workspace/sessions/{session_id}",
        params={"include_workspace": "true"},
    )
    assert final_workspace_response.status_code == 200, final_workspace_response.text
    final_workspace_payload = final_workspace_response.json()["workspace"]
    assert [entry["status"] for entry in final_workspace_payload["submission_history"]] == [
        "failed",
        "accepted",
    ]

    final_session_row = test_db.get(CurationReviewSession, UUID(session_id))
    assert final_session_row is not None
    assert final_session_row.status == CurationSessionStatus.SUBMITTED

    action_types = [
        row.action_type.value
        for row in (
            test_db.query(session_service.SessionActionLogModel)
            .filter(session_service.SessionActionLogModel.session_id == UUID(session_id))
            .order_by(session_service.SessionActionLogModel.occurred_at.asc())
            .all()
        )
    ]
    assert CurationActionType.SUBMISSION_EXECUTED.value in action_types
    assert CurationActionType.SUBMISSION_RETRIED.value in action_types

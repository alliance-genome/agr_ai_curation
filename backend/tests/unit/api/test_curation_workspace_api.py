"""Unit tests for curation workspace prep endpoints."""

import pytest
from uuid import uuid4

from src.api import curation_workspace as module
from src.schemas.curation_prep import (
    CurationPrepChatPreviewResponse,
    CurationPrepChatRunRequest,
    CurationPrepChatRunResponse,
)
from src.schemas.curation_workspace import (
    CurationDocumentBootstrapAvailabilityResponse,
    CurationDocumentBootstrapRequest,
    CurationEvidenceRecomputeRequest,
    CurationEvidenceResolveRequest,
    CurationFlowRunListRequest,
    CurationFlowRunSessionsRequest,
    CurationManualCandidateCreateRequest,
    CurationManualEvidenceCreateRequest,
    CurationSavedViewCreateRequest,
    CurationSessionCreateRequest,
    EvidenceAnchor,
    EvidenceAnchorKind,
    EvidenceLocatorQuality,
    EvidenceSupportsDecision,
)


def _anchor() -> EvidenceAnchor:
    return EvidenceAnchor(
        anchor_kind=EvidenceAnchorKind.SNIPPET,
        locator_quality=EvidenceLocatorQuality.EXACT_QUOTE,
        supports_decision=EvidenceSupportsDecision.SUPPORTS,
        snippet_text="Example quote.",
        sentence_text="Example quote.",
        normalized_text=None,
        viewer_search_text=None,
        pdfx_markdown_offset_start=None,
        pdfx_markdown_offset_end=None,
        page_number=2,
        page_label=None,
        section_title="Results",
        subsection_title=None,
        figure_reference=None,
        table_reference=None,
        chunk_ids=[],
    )


@pytest.mark.asyncio
async def test_get_chat_prep_preview_returns_service_payload(monkeypatch):
    monkeypatch.setattr(module, "set_global_user_from_cognito", lambda _db, _user: None)
    monkeypatch.setattr(
        module,
        "build_chat_curation_prep_preview",
        lambda **_kwargs: CurationPrepChatPreviewResponse(
            ready=True,
            summary_text="You discussed 2 candidate annotations. Prepare all for curation review?",
            candidate_count=2,
            extraction_result_count=1,
            conversation_message_count=4,
            adapter_keys=["reference_adapter"],
            profile_keys=["primary"],
            domain_keys=["disease"],
            blocking_reasons=[],
        ),
    )

    response = await module.get_chat_prep_preview(
        session_id="session-1",
        user={"sub": "user-1"},
        db=object(),
    )

    assert response.ready is True
    assert response.adapter_keys == ["reference_adapter"]
    assert response.candidate_count == 2


@pytest.mark.asyncio
async def test_post_review_session_delegates_to_manual_create_service(monkeypatch):
    monkeypatch.setattr(module, "set_global_user_from_cognito", lambda _db, _user: None)
    expected = object()
    captured: dict[str, object] = {}

    def _create_manual_session(request, *, current_user_id, actor_claims, db):
        captured["request"] = request
        captured["current_user_id"] = current_user_id
        captured["actor_claims"] = actor_claims
        captured["db"] = db
        return expected

    monkeypatch.setattr(module, "create_manual_session", _create_manual_session)

    request = CurationSessionCreateRequest(
        document_id="document-1",
        adapter_key="reference_adapter",
        profile_key="primary",
        curator_id="curator-2",
        notes="Manual queue seed.",
        tags=["triage"],
    )
    db = object()
    user = {"sub": "user-1", "email": "user-1@example.org"}

    response = await module.post_review_session(
        request,
        user=user,
        db=db,
    )

    assert response is expected
    assert captured == {
        "request": request,
        "current_user_id": "user-1",
        "actor_claims": user,
        "db": db,
    }


@pytest.mark.asyncio
async def test_post_review_session_propagates_missing_document_error(monkeypatch):
    monkeypatch.setattr(module, "set_global_user_from_cognito", lambda _db, _user: None)

    def _raise_not_found(*_args, **_kwargs):
        raise module.HTTPException(status_code=404, detail="Document document-404 not found")

    monkeypatch.setattr(module, "create_manual_session", _raise_not_found)

    with pytest.raises(module.HTTPException) as exc:
        await module.post_review_session(
            CurationSessionCreateRequest(
                document_id="document-404",
                adapter_key="reference_adapter",
            ),
            user={"sub": "user-1"},
            db=object(),
        )

    assert exc.value.status_code == 404
    assert exc.value.detail == "Document document-404 not found"


@pytest.mark.asyncio
async def test_get_saved_views_delegates_to_service(monkeypatch):
    monkeypatch.setattr(module, "set_global_user_from_cognito", lambda _db, _user: None)
    expected = object()
    captured: dict[str, object] = {}
    db = object()

    def _list_saved_views(db, *, current_user_id):
        captured["db"] = db
        captured["current_user_id"] = current_user_id
        return expected

    monkeypatch.setattr(module, "list_saved_view_records", _list_saved_views)

    response = await module.get_saved_views(
        user={"sub": "user-1"},
        db=db,
    )

    assert response is expected
    assert captured == {
        "db": db,
        "current_user_id": "user-1",
    }


@pytest.mark.asyncio
async def test_post_saved_view_delegates_to_service(monkeypatch):
    monkeypatch.setattr(module, "set_global_user_from_cognito", lambda _db, _user: None)
    expected = object()
    captured: dict[str, object] = {}
    db = object()

    def _create_saved_view(db, request, *, current_user_id):
        captured["db"] = db
        captured["request"] = request
        captured["current_user_id"] = current_user_id
        return expected

    monkeypatch.setattr(module, "create_saved_view_record", _create_saved_view)

    request = CurationSavedViewCreateRequest(
        name="My pending sessions",
        filters={},
        sort_by="prepared_at",
        sort_direction="desc",
    )

    response = await module.post_saved_view(
        request,
        user={"sub": "user-1"},
        db=db,
    )

    assert response is expected
    assert captured["db"] is db
    assert captured["request"] == request
    assert captured["current_user_id"] == "user-1"


@pytest.mark.asyncio
async def test_delete_saved_view_delegates_to_service(monkeypatch):
    monkeypatch.setattr(module, "set_global_user_from_cognito", lambda _db, _user: None)
    expected = object()
    captured: dict[str, object] = {}
    db = object()

    def _delete_saved_view(db, view_id, *, current_user_id):
        captured["db"] = db
        captured["view_id"] = view_id
        captured["current_user_id"] = current_user_id
        return expected

    monkeypatch.setattr(module, "delete_saved_view_record", _delete_saved_view)

    view_id = uuid4()
    response = await module.delete_saved_view(
        view_id,
        user={"sub": "user-1"},
        db=db,
    )

    assert response is expected
    assert captured["db"] is db
    assert captured["view_id"] == view_id
    assert captured["current_user_id"] == "user-1"


@pytest.mark.asyncio
async def test_get_saved_views_requires_user_id(monkeypatch):
    monkeypatch.setattr(module, "set_global_user_from_cognito", lambda _db, _user: None)

    with pytest.raises(module.HTTPException) as exc:
        await module.get_saved_views(
            user={"email": "user@example.org"},
            db=object(),
        )

    assert exc.value.status_code == 401
    assert exc.value.detail == "User identifier not found in token"


@pytest.mark.asyncio
async def test_post_document_bootstrap_delegates_to_bootstrap_service(monkeypatch):
    monkeypatch.setattr(module, "set_global_user_from_cognito", lambda _db, _user: None)
    expected = object()
    captured: dict[str, object] = {}

    async def _bootstrap_document_session(document_id, request, *, current_user_id, db):
        captured["document_id"] = document_id
        captured["request"] = request
        captured["current_user_id"] = current_user_id
        captured["db"] = db
        return expected

    monkeypatch.setattr(module, "bootstrap_document_session", _bootstrap_document_session)

    request = CurationDocumentBootstrapRequest(
        adapter_key="reference_adapter",
        profile_key="primary",
        domain_key="entity",
        flow_run_id="flow-7",
        origin_session_id="chat-session-7",
        curator_id="curator-2",
    )
    db = object()

    response = await module.post_document_bootstrap(
        "document-1",
        request,
        user={"sub": "user-1"},
        db=db,
    )

    assert response is expected
    assert captured == {
        "document_id": "document-1",
        "request": request,
        "current_user_id": "user-1",
        "db": db,
    }


@pytest.mark.asyncio
async def test_post_document_bootstrap_propagates_no_extraction_results_error(monkeypatch):
    monkeypatch.setattr(module, "set_global_user_from_cognito", lambda _db, _user: None)

    async def _raise_not_found(*_args, **_kwargs):
        raise module.HTTPException(
            status_code=404,
            detail=(
                "No persisted curation prep extraction results were found for "
                "document document-1"
            ),
        )

    monkeypatch.setattr(module, "bootstrap_document_session", _raise_not_found)

    with pytest.raises(module.HTTPException) as exc:
        await module.post_document_bootstrap(
            "document-1",
            CurationDocumentBootstrapRequest(),
            user={"sub": "user-1"},
            db=object(),
        )

    assert exc.value.status_code == 404
    assert exc.value.detail == (
        "No persisted curation prep extraction results were found for document document-1"
    )


@pytest.mark.asyncio
async def test_get_document_bootstrap_status_delegates_to_service(monkeypatch):
    monkeypatch.setattr(module, "set_global_user_from_cognito", lambda _db, _user: None)
    expected = CurationDocumentBootstrapAvailabilityResponse(eligible=True)
    captured: dict[str, object] = {}

    def _get_document_bootstrap_availability(document_id, request, *, db):
        captured["document_id"] = document_id
        captured["request"] = request
        captured["db"] = db
        return expected

    monkeypatch.setattr(
        module,
        "get_document_bootstrap_availability",
        _get_document_bootstrap_availability,
    )

    db = object()
    response = await module.get_document_bootstrap_status(
        "document-1",
        CurationDocumentBootstrapRequest(
            adapter_key="reference_adapter",
            flow_run_id="flow-7",
        ),
        user={"sub": "user-1"},
        db=db,
    )

    assert response == expected
    assert captured == {
        "document_id": "document-1",
        "request": CurationDocumentBootstrapRequest(
            adapter_key="reference_adapter",
            flow_run_id="flow-7",
        ),
        "db": db,
    }


@pytest.mark.asyncio
async def test_post_evidence_recompute_delegates_to_service(monkeypatch):
    monkeypatch.setattr(module, "set_global_user_from_cognito", lambda _db, _user: None)
    expected = object()
    captured: dict[str, object] = {}

    def _recompute(request, *, current_user_id, actor_claims, db):
        captured["request"] = request
        captured["current_user_id"] = current_user_id
        captured["actor_claims"] = actor_claims
        captured["db"] = db
        return expected

    monkeypatch.setattr(module, "recompute_evidence", _recompute)

    request = CurationEvidenceRecomputeRequest(
        session_id="session-1",
        candidate_ids=["candidate-1"],
        force=True,
    )
    db = object()
    user = {"sub": "user-1", "email": "user-1@example.org"}

    response = await module.post_evidence_recompute(
        request,
        user=user,
        db=db,
    )

    assert response is expected
    assert captured == {
        "request": request,
        "current_user_id": "user-1",
        "actor_claims": user,
        "db": db,
    }


@pytest.mark.asyncio
async def test_post_manual_candidate_delegates_to_service(monkeypatch):
    monkeypatch.setattr(module, "set_global_user_from_cognito", lambda _db, _user: None)
    expected = object()
    captured: dict[str, object] = {}

    def _create_manual_candidate(db, session_id, request, *, actor_claims):
        captured["db"] = db
        captured["session_id"] = session_id
        captured["request"] = request
        captured["actor_claims"] = actor_claims
        return expected

    monkeypatch.setattr(module, "create_manual_candidate", _create_manual_candidate)

    session_id = uuid4()
    request = CurationManualCandidateCreateRequest(
        session_id=str(session_id),
        adapter_key="reference_adapter",
        profile_key="primary",
        source="manual",
        display_label="Manual candidate",
        draft={
            "draft_id": "draft-temp-1",
            "candidate_id": "candidate-temp-1",
            "adapter_key": "reference_adapter",
            "version": 1,
            "title": "Manual candidate",
            "fields": [
                {
                    "field_key": "field_a",
                    "label": "Field A",
                    "value": "value alpha",
                    "seed_value": "value alpha",
                    "field_type": "string",
                    "group_key": "group_one",
                    "group_label": "Group One",
                    "order": 0,
                    "required": True,
                    "read_only": False,
                    "dirty": False,
                    "stale_validation": False,
                    "evidence_anchor_ids": [],
                    "metadata": {},
                }
            ],
            "created_at": "2026-03-21T10:00:00Z",
            "updated_at": "2026-03-21T10:00:00Z",
            "metadata": {},
        },
        evidence_anchors=[],
    )
    db = object()
    user = {"sub": "user-1", "email": "user-1@example.org"}

    response = await module.post_manual_candidate(
        session_id,
        request,
        user=user,
        db=db,
    )

    assert response is expected
    assert captured == {
        "db": db,
        "session_id": session_id,
        "request": request,
        "actor_claims": user,
    }


@pytest.mark.asyncio
async def test_post_manual_evidence_delegates_to_service(monkeypatch):
    monkeypatch.setattr(module, "set_global_user_from_cognito", lambda _db, _user: None)
    expected = object()
    captured: dict[str, object] = {}

    def _create_manual_evidence(request, *, actor_claims, db):
        captured["request"] = request
        captured["actor_claims"] = actor_claims
        captured["db"] = db
        return expected

    monkeypatch.setattr(module, "create_manual_evidence", _create_manual_evidence)

    request = CurationManualEvidenceCreateRequest(
        session_id="session-1",
        candidate_id="candidate-1",
        field_keys=["gene.symbol"],
        anchor=_anchor(),
        is_primary=True,
    )
    db = object()
    user = {"sub": "user-1", "email": "user-1@example.org"}

    response = await module.post_manual_evidence(
        request,
        user=user,
        db=db,
    )

    assert response is expected
    assert captured == {
        "request": request,
        "actor_claims": user,
        "db": db,
    }


@pytest.mark.asyncio
async def test_get_review_flow_runs_delegates_to_service(monkeypatch):
    monkeypatch.setattr(module, "set_global_user_from_cognito", lambda _db, _user: None)
    expected = object()
    captured: dict[str, object] = {}

    def _list_flow_runs(db, request):
        captured["db"] = db
        captured["request"] = request
        return expected

    monkeypatch.setattr(module, "list_flow_runs", _list_flow_runs)

    request = CurationFlowRunListRequest()
    db = object()

    response = await module.get_review_flow_runs(
        request=request,
        user={"sub": "user-1"},
        db=db,
    )

    assert response is expected
    assert captured == {
        "db": db,
        "request": request,
    }


@pytest.mark.asyncio
async def test_get_review_flow_run_sessions_delegates_to_service(monkeypatch):
    monkeypatch.setattr(module, "set_global_user_from_cognito", lambda _db, _user: None)
    expected = object()
    captured: dict[str, object] = {}

    def _list_flow_run_sessions(db, request):
        captured["db"] = db
        captured["request"] = request
        return expected

    monkeypatch.setattr(module, "list_flow_run_sessions", _list_flow_run_sessions)

    request = CurationFlowRunSessionsRequest(flow_run_id="flow-alpha", page=2, page_size=10)
    db = object()

    response = await module.get_review_flow_run_sessions(
        request=request,
        user={"sub": "user-1"},
        db=db,
    )

    assert response is expected
    assert captured == {
        "db": db,
        "request": request,
    }


@pytest.mark.asyncio
async def test_post_evidence_resolve_delegates_to_service(monkeypatch):
    monkeypatch.setattr(module, "set_global_user_from_cognito", lambda _db, _user: None)
    expected = object()
    captured: dict[str, object] = {}

    def _resolve_evidence(request, *, current_user_id, db):
        captured["request"] = request
        captured["current_user_id"] = current_user_id
        captured["db"] = db
        return expected

    monkeypatch.setattr(module, "resolve_evidence", _resolve_evidence)

    request = CurationEvidenceResolveRequest(
        session_id="session-1",
        candidate_id="candidate-1",
        field_key="gene.symbol",
        anchor=_anchor(),
        replace_existing=True,
    )
    db = object()
    user = {"sub": "user-1", "email": "user-1@example.org"}

    response = await module.post_evidence_resolve(
        request,
        user=user,
        db=db,
    )

    assert response is expected
    assert captured == {
        "request": request,
        "current_user_id": "user-1",
        "db": db,
    }


@pytest.mark.asyncio
async def test_trigger_chat_prep_maps_value_error_to_http_400(monkeypatch):
    monkeypatch.setattr(module, "set_global_user_from_cognito", lambda _db, _user: None)

    async def _raise_value_error(*_args, **_kwargs):
        raise ValueError("No candidate annotations are available from this chat yet.")

    monkeypatch.setattr(module, "run_chat_curation_prep", _raise_value_error)

    with pytest.raises(module.HTTPException) as exc:
        await module.trigger_chat_prep(
            CurationPrepChatRunRequest(session_id="session-1"),
            user={"sub": "user-1"},
            db=object(),
        )

    assert exc.value.status_code == 400
    assert "No candidate annotations" in exc.value.detail


@pytest.mark.asyncio
async def test_trigger_chat_prep_returns_service_payload(monkeypatch):
    monkeypatch.setattr(module, "set_global_user_from_cognito", lambda _db, _user: None)

    async def _run_chat_prep(*_args, **_kwargs):
        return CurationPrepChatRunResponse(
            summary_text="Prepared 2 candidate annotations for curation review.",
            candidate_count=2,
            warnings=["Review evidence alignment before downstream normalization."],
            processing_notes=["Prepared from chat extraction context."],
            adapter_keys=["reference_adapter"],
            profile_keys=["primary"],
            domain_keys=["disease"],
        )

    monkeypatch.setattr(module, "run_chat_curation_prep", _run_chat_prep)

    response = await module.trigger_chat_prep(
        CurationPrepChatRunRequest(session_id="session-1"),
        user={"sub": "user-1"},
        db=object(),
    )

    assert response.candidate_count == 2
    assert response.summary_text == "Prepared 2 candidate annotations for curation review."

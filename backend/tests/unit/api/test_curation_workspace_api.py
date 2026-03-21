"""Unit tests for curation workspace prep endpoints."""

import pytest

from src.api import curation_workspace as module
from src.schemas.curation_prep import (
    CurationPrepChatPreviewResponse,
    CurationPrepChatRunRequest,
    CurationPrepChatRunResponse,
)
from src.schemas.curation_workspace import (
    CurationDocumentBootstrapRequest,
    CurationSessionCreateRequest,
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

"""Unit tests for curation workspace prep endpoints."""

import pytest

from src.api import curation_workspace as module
from src.schemas.curation_prep import (
    CurationPrepChatPreviewResponse,
    CurationPrepChatRunRequest,
    CurationPrepChatRunResponse,
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

"""Tests for Phase 4 Agent Workshop tool-idea endpoints."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
import uuid

import pytest
from fastapi import HTTPException


def _tool_idea_record(**overrides):
    payload = {
        "id": uuid.UUID("11111111-1111-1111-1111-111111111111"),
        "user_id": 7,
        "project_id": uuid.UUID("22222222-2222-2222-2222-222222222222"),
        "title": "Need a new GO cross-reference tool",
        "description": "Fetch GO xrefs from external API and return normalized rows.",
        "opus_conversation": [
            {"role": "user", "content": "Need API enrichment"},
            {"role": "assistant", "content": "What inputs and outputs do you need?"},
        ],
        "status": "submitted",
        "developer_notes": None,
        "resulting_tool_key": None,
        "created_at": datetime(2026, 2, 23, tzinfo=UTC),
        "updated_at": datetime(2026, 2, 23, tzinfo=UTC),
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def test_create_tool_idea_endpoint_submits_request(monkeypatch):
    import src.api.agent_studio as api_module

    observed = {}
    project_id = uuid.UUID("22222222-2222-2222-2222-222222222222")

    monkeypatch.setattr(
        api_module,
        "set_global_user_from_cognito",
        lambda _db, _user: SimpleNamespace(id=7, auth_sub="auth-sub"),
    )
    monkeypatch.setattr(
        api_module,
        "get_primary_project_id_for_user",
        lambda _db, _user_id: project_id,
    )

    def _fake_create_tool_idea_request(**kwargs):
        observed.update(kwargs)
        return _tool_idea_record()

    monkeypatch.setattr(api_module, "create_tool_idea_request", _fake_create_tool_idea_request)

    db = SimpleNamespace(
        commit=lambda: None,
        refresh=lambda _obj: None,
        rollback=lambda: None,
    )

    response = asyncio.run(
        api_module.create_tool_idea_endpoint(
            request=api_module.ToolIdeaCreateRequest(
                title="Need a new GO cross-reference tool",
                description="Fetch GO xrefs from external API and return normalized rows.",
                opus_conversation=[
                    api_module.ToolIdeaConversationEntry(
                        role="user",
                        content="Need API enrichment",
                    )
                ],
            ),
            user={"sub": "auth-sub"},
            db=db,
        )
    )

    assert observed["user_id"] == 7
    assert observed["project_id"] == project_id
    assert observed["title"] == "Need a new GO cross-reference tool"
    assert observed["opus_conversation"] == [{"role": "user", "content": "Need API enrichment", "timestamp": None}]
    assert response.status == "submitted"


def test_create_tool_idea_endpoint_returns_400_on_validation_error(monkeypatch):
    import src.api.agent_studio as api_module

    rollback_called = {"value": False}

    monkeypatch.setattr(
        api_module,
        "set_global_user_from_cognito",
        lambda _db, _user: SimpleNamespace(id=7, auth_sub="auth-sub"),
    )
    monkeypatch.setattr(
        api_module,
        "get_primary_project_id_for_user",
        lambda _db, _user_id: uuid.UUID("22222222-2222-2222-2222-222222222222"),
    )
    monkeypatch.setattr(
        api_module,
        "create_tool_idea_request",
        lambda **_kwargs: (_ for _ in ()).throw(ValueError("description is required")),
    )

    db = SimpleNamespace(
        commit=lambda: None,
        refresh=lambda _obj: None,
        rollback=lambda: rollback_called.__setitem__("value", True),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            api_module.create_tool_idea_endpoint(
                request=api_module.ToolIdeaCreateRequest(
                    title="Need a tool",
                    description="Valid payload but service fails validation",
                ),
                user={"sub": "auth-sub"},
                db=db,
            )
        )

    assert rollback_called["value"] is True
    assert exc_info.value.status_code == 400
    assert "description is required" in str(exc_info.value.detail)


def test_list_tool_ideas_endpoint_returns_current_user_requests(monkeypatch):
    import src.api.agent_studio as api_module

    observed = {}

    monkeypatch.setattr(
        api_module,
        "set_global_user_from_cognito",
        lambda _db, _user: SimpleNamespace(id=7, auth_sub="auth-sub"),
    )

    def _fake_list_tool_idea_requests_for_user(_db, user_id):
        observed["user_id"] = user_id
        return [_tool_idea_record()]

    monkeypatch.setattr(api_module, "list_tool_idea_requests_for_user", _fake_list_tool_idea_requests_for_user)

    response = asyncio.run(
        api_module.list_tool_ideas_endpoint(
            user={"sub": "auth-sub"},
            db=SimpleNamespace(),
        )
    )

    assert observed["user_id"] == 7
    assert response.total == 1
    assert response.tool_ideas[0].title == "Need a new GO cross-reference tool"

"""Tests for Phase 3 Agent Workshop sharing/clone endpoints."""

import asyncio
from datetime import UTC, datetime
import logging
from types import SimpleNamespace
import uuid

import pytest
from fastapi import HTTPException


def _custom_agent_payload() -> dict:
    return {
        "id": "11111111-1111-1111-1111-111111111111",
        "agent_id": "ca_11111111-1111-1111-1111-111111111111",
        "user_id": 1,
        "template_source": "gene",
        "name": "My Agent",
        "description": "Desc",
        "custom_prompt": "Prompt",
        "group_prompt_overrides": {},
        "icon": "🔧",
        "include_group_rules": True,
        "model_id": "gpt-4o",
        "model_temperature": 0.1,
        "model_reasoning": None,
        "tool_ids": ["agr_curation_query"],
        "output_schema_key": None,
        "visibility": "private",
        "project_id": None,
        "parent_prompt_hash": None,
        "current_parent_prompt_hash": None,
        "parent_prompt_stale": False,
        "parent_exists": True,
        "is_active": True,
        "created_at": datetime(2026, 2, 23, tzinfo=UTC),
        "updated_at": datetime(2026, 2, 23, tzinfo=UTC),
    }


def test_clone_agent_endpoint_clones_visible_agent(monkeypatch):
    import src.api.agent_studio as api_module

    observed = {}

    monkeypatch.setattr(
        api_module,
        "set_global_user_from_cognito",
        lambda _db, _user: SimpleNamespace(id=1, auth_sub="auth-sub"),
    )

    def _fake_clone_visible_agent_for_user(**kwargs):
        observed.update(kwargs)
        return SimpleNamespace()

    monkeypatch.setattr(api_module, "clone_visible_agent_for_user", _fake_clone_visible_agent_for_user)
    monkeypatch.setattr(api_module, "custom_agent_to_dict", lambda _agent: _custom_agent_payload())

    db = SimpleNamespace(
        commit=lambda: None,
        refresh=lambda _obj: None,
        rollback=lambda: None,
    )

    response = asyncio.run(
        api_module.clone_agent_endpoint(
            agent_id="ca_source",
            request=api_module.CloneAgentRequest(name="Gene Copy"),
            user={"sub": "auth-sub"},
            db=db,
        )
    )

    assert observed["user_id"] == 1
    assert observed["source_agent_key"] == "ca_source"
    assert observed["name"] == "Gene Copy"
    assert response["agent_id"] == "ca_11111111-1111-1111-1111-111111111111"


def test_clone_agent_endpoint_returns_404_with_sanitized_missing_agent_detail(monkeypatch, caplog):
    import src.api.agent_studio as api_module

    rollback_called = {"value": False}
    caplog.set_level(logging.WARNING, logger=api_module.logger.name)

    monkeypatch.setattr(
        api_module,
        "set_global_user_from_cognito",
        lambda _db, _user: SimpleNamespace(id=1, auth_sub="auth-sub"),
    )
    monkeypatch.setattr(
        api_module,
        "clone_visible_agent_for_user",
        lambda **_kwargs: (_ for _ in ()).throw(api_module.CustomAgentNotFoundError("Agent 'ca_source' not found")),
    )

    db = SimpleNamespace(
        commit=lambda: None,
        refresh=lambda _obj: None,
        rollback=lambda: rollback_called.__setitem__("value", True),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            api_module.clone_agent_endpoint(
                agent_id="ca_source",
                request=api_module.CloneAgentRequest(name="Gene Copy"),
                user={"sub": "auth-sub"},
                db=db,
            )
        )

    assert rollback_called["value"] is True
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Agent not found"
    assert "ca_source" not in str(exc_info.value.detail)
    assert "ca_source" in caplog.text


def test_clone_agent_endpoint_returns_403_on_access_error(monkeypatch, caplog):
    import src.api.agent_studio as api_module

    rollback_called = {"value": False}
    caplog.set_level(logging.WARNING, logger=api_module.logger.name)

    monkeypatch.setattr(
        api_module,
        "set_global_user_from_cognito",
        lambda _db, _user: SimpleNamespace(id=1, auth_sub="auth-sub"),
    )
    monkeypatch.setattr(
        api_module,
        "clone_visible_agent_for_user",
        lambda **_kwargs: (_ for _ in ()).throw(api_module.CustomAgentAccessError("forbidden")),
    )

    db = SimpleNamespace(
        commit=lambda: None,
        refresh=lambda _obj: None,
        rollback=lambda: rollback_called.__setitem__("value", True),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            api_module.clone_agent_endpoint(
                agent_id="ca_source",
                request=api_module.CloneAgentRequest(name="Gene Copy"),
                user={"sub": "auth-sub"},
                db=db,
            )
        )

    assert rollback_called["value"] is True
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Access denied to agent"
    assert "forbidden" not in str(exc_info.value.detail)
    assert "forbidden" in caplog.text


def test_clone_agent_endpoint_returns_409_for_duplicate_name(monkeypatch, caplog):
    import src.api.agent_studio as api_module

    rollback_called = {"value": False}
    caplog.set_level(logging.WARNING, logger=api_module.logger.name)

    monkeypatch.setattr(
        api_module,
        "set_global_user_from_cognito",
        lambda _db, _user: SimpleNamespace(id=1, auth_sub="auth-sub"),
    )
    monkeypatch.setattr(
        api_module,
        "clone_visible_agent_for_user",
        lambda **_kwargs: (_ for _ in ()).throw(ValueError("custom agent already exists")),
    )

    db = SimpleNamespace(
        commit=lambda: None,
        refresh=lambda _obj: None,
        rollback=lambda: rollback_called.__setitem__("value", True),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            api_module.clone_agent_endpoint(
                agent_id="ca_source",
                request=api_module.CloneAgentRequest(name="Gene Copy"),
                user={"sub": "auth-sub"},
                db=db,
            )
        )

    assert rollback_called["value"] is True
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "A custom agent with this name already exists"
    assert "custom agent already exists" in caplog.text


def test_share_agent_endpoint_updates_visibility(monkeypatch):
    import src.api.agent_studio as api_module

    custom_agent_uuid = uuid.uuid4()
    fake_agent = SimpleNamespace(id=custom_agent_uuid)
    observed = {}

    monkeypatch.setattr(
        api_module,
        "set_global_user_from_cognito",
        lambda _db, _user: SimpleNamespace(id=1, auth_sub="auth-sub"),
    )
    monkeypatch.setattr(api_module, "parse_custom_agent_id", lambda _agent_id: custom_agent_uuid)
    monkeypatch.setattr(
        api_module,
        "get_custom_agent_for_user",
        lambda _db, _uuid, _uid: fake_agent,
    )

    def _fake_set_custom_agent_visibility(**kwargs):
        observed.update(kwargs)
        return fake_agent

    monkeypatch.setattr(api_module, "set_custom_agent_visibility", _fake_set_custom_agent_visibility)
    monkeypatch.setattr(api_module, "custom_agent_to_dict", lambda _agent: _custom_agent_payload())

    db = SimpleNamespace(
        commit=lambda: None,
        refresh=lambda _obj: None,
        rollback=lambda: None,
    )

    response = asyncio.run(
        api_module.share_agent_endpoint(
            agent_id="ca_11111111-1111-1111-1111-111111111111",
            request=api_module.ShareAgentRequest(visibility="project"),
            user={"sub": "auth-sub"},
            db=db,
        )
    )

    assert observed["user_id"] == 1
    assert observed["custom_agent"] == fake_agent
    assert observed["visibility"] == "project"
    assert response["agent_id"] == "ca_11111111-1111-1111-1111-111111111111"


def test_share_agent_endpoint_returns_403_on_access_error(monkeypatch, caplog):
    import src.api.agent_studio as api_module

    custom_agent_uuid = uuid.uuid4()
    rollback_called = {"value": False}
    caplog.set_level(logging.WARNING, logger=api_module.logger.name)

    monkeypatch.setattr(
        api_module,
        "set_global_user_from_cognito",
        lambda _db, _user: SimpleNamespace(id=1, auth_sub="auth-sub"),
    )
    monkeypatch.setattr(api_module, "parse_custom_agent_id", lambda _agent_id: custom_agent_uuid)
    monkeypatch.setattr(
        api_module,
        "get_custom_agent_for_user",
        lambda _db, _uuid, _uid: (_ for _ in ()).throw(api_module.CustomAgentAccessError("not allowed")),
    )

    db = SimpleNamespace(
        commit=lambda: None,
        refresh=lambda _obj: None,
        rollback=lambda: rollback_called.__setitem__("value", True),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            api_module.share_agent_endpoint(
                agent_id="ca_11111111-1111-1111-1111-111111111111",
                request=api_module.ShareAgentRequest(visibility="project"),
                user={"sub": "auth-sub"},
                db=db,
            )
        )

    assert rollback_called["value"] is True
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Access denied to custom agent"
    assert "not allowed" not in str(exc_info.value.detail)
    assert "not allowed" in caplog.text


def test_share_agent_endpoint_rejects_non_custom_agent_id():
    import src.api.agent_studio as api_module

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            api_module.share_agent_endpoint(
                agent_id="gene",
                request=api_module.ShareAgentRequest(visibility="project"),
            )
        )

    assert exc_info.value.status_code == 400
    assert "Only custom agents can be shared" in str(exc_info.value.detail)

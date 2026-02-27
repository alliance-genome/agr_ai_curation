"""Unit tests for admin prompt management API helpers/endpoints."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
import uuid
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

import src.api.admin.prompts as prompts_api
from sqlalchemy.exc import IntegrityError


class _QueryStub:
    def __init__(self, *, first_result=None, all_result=None, count_result=0):
        self.first_result = first_result
        self.all_result = all_result if all_result is not None else []
        self.count_result = count_result

    def filter(self, *_args, **_kwargs):
        return self

    def order_by(self, *_args, **_kwargs):
        if self.all_result and hasattr(self.all_result[0], "version"):
            self.all_result = sorted(self.all_result, key=lambda item: item.version, reverse=True)
        return self

    def offset(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def count(self):
        return self.count_result

    def first(self):
        return self.first_result

    def all(self):
        return self.all_result


def _prompt(*, version=1, is_active=True, group_id=None):
    return prompts_api.PromptTemplate(
        id=uuid.uuid4(),
        agent_name="gene",
        prompt_type="system",
        group_id=group_id,
        content=f"prompt-v{version}",
        version=version,
        is_active=is_active,
        created_at=datetime.now(timezone.utc),
        created_by="admin@example.org",
    )


def test_parse_admin_emails_normalizes(monkeypatch):
    monkeypatch.setattr(
        prompts_api.os,
        "getenv",
        lambda key, default="": "Admin@Example.org, test@example.org , , SECOND@example.org",
    )
    emails = prompts_api._parse_admin_emails()
    assert emails == {"admin@example.org", "test@example.org", "second@example.org"}


def test_require_admin_allows_in_dev_mode_when_unconfigured(monkeypatch):
    monkeypatch.setattr(prompts_api, "get_admin_emails", lambda: set())
    monkeypatch.setattr(prompts_api.os, "getenv", lambda key, default="": "true" if key == "DEV_MODE" else default)

    user = {"email": "anyone@example.org"}
    result = asyncio.run(prompts_api.require_admin(user))
    assert result == user


def test_require_admin_denies_when_unconfigured_outside_dev(monkeypatch):
    monkeypatch.setattr(prompts_api, "get_admin_emails", lambda: set())
    monkeypatch.setattr(prompts_api.os, "getenv", lambda key, default="": "false" if key == "DEV_MODE" else default)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(prompts_api.require_admin({"email": "anyone@example.org"}))
    assert exc.value.status_code == 403


def test_require_admin_denies_non_admin(monkeypatch):
    monkeypatch.setattr(prompts_api, "get_admin_emails", lambda: {"admin@example.org"})

    with pytest.raises(HTTPException) as exc:
        asyncio.run(prompts_api.require_admin({"email": "user@example.org"}))
    assert exc.value.status_code == 403


def test_require_admin_allows_admin(monkeypatch):
    monkeypatch.setattr(prompts_api, "get_admin_emails", lambda: {"admin@example.org"})
    user = {"email": "Admin@Example.org"}
    result = asyncio.run(prompts_api.require_admin(user))
    assert result == user


def test_list_prompts_returns_paginated_response():
    items = [_prompt(version=1), _prompt(version=2)]
    db = MagicMock()
    db.query.return_value = _QueryStub(all_result=items, count_result=2)

    result = asyncio.run(
        prompts_api.list_prompts(
            group_id="base",
            active_only=True,
            page=1,
            page_size=50,
            db=db,
            _admin={"email": "admin@example.org"},
        )
    )

    assert result.total == 2
    assert result.page == 1
    assert len(result.prompts) == 2
    assert result.prompts[0].version == 2
    assert result.prompts[1].version == 1


def test_get_prompt_404_when_missing():
    db = MagicMock()
    db.query.return_value = _QueryStub(first_result=None)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(prompts_api.get_prompt(uuid.uuid4(), db=db, _admin={"email": "admin@example.org"}))
    assert exc.value.status_code == 404


def test_get_prompt_success():
    prompt = _prompt(version=3)
    db = MagicMock()
    db.query.return_value = _QueryStub(first_result=prompt)

    result = asyncio.run(prompts_api.get_prompt(prompt.id, db=db, _admin={"email": "admin@example.org"}))
    assert result.id == prompt.id
    assert result.version == 3


def test_create_prompt_success_without_activation(monkeypatch):
    db = MagicMock()
    db.query.return_value = _QueryStub(first_result=None)
    db.refresh.side_effect = lambda obj: setattr(obj, "id", uuid.uuid4())
    refresh_calls = {"count": 0}
    monkeypatch.setattr(prompts_api.prompt_cache, "refresh", lambda _db: refresh_calls.__setitem__("count", refresh_calls["count"] + 1))

    request = prompts_api.CreatePromptRequest(
        agent_name="gene",
        prompt_type="system",
        group_id=None,
        content="new content",
        activate=False,
    )
    result = asyncio.run(prompts_api.create_prompt(request, db=db, admin={"email": "admin@example.org"}))

    assert result.version == 1
    assert result.is_active is False
    assert "Created version 1" in result.message
    assert refresh_calls["count"] == 0
    assert db.commit.called


def test_create_prompt_success_with_activation(monkeypatch):
    existing = _prompt(version=2, is_active=True)
    db = MagicMock()
    db.query.return_value = _QueryStub(first_result=existing)
    db.refresh.side_effect = lambda obj: setattr(obj, "id", uuid.uuid4())
    refresh_calls = {"count": 0}
    monkeypatch.setattr(prompts_api.prompt_cache, "refresh", lambda _db: refresh_calls.__setitem__("count", refresh_calls["count"] + 1))

    request = prompts_api.CreatePromptRequest(
        agent_name="gene",
        prompt_type="system",
        group_id=None,
        content="new content",
        activate=True,
    )
    result = asyncio.run(prompts_api.create_prompt(request, db=db, admin={"email": "admin@example.org"}))

    assert existing.is_active is False
    assert result.version == 3
    assert result.is_active is True
    assert "activated" in result.message
    assert refresh_calls["count"] == 1


def test_create_prompt_returns_400_on_check_violation(monkeypatch):
    class _FakeCheckViolation(Exception):
        pass

    monkeypatch.setattr(prompts_api, "CheckViolation", _FakeCheckViolation)
    monkeypatch.setattr(prompts_api, "get_valid_group_ids", lambda: ["FB", "WB"])

    db = MagicMock()
    db.query.return_value = _QueryStub(first_result=None)
    db.commit.side_effect = IntegrityError("stmt", {}, _FakeCheckViolation("bad group"))

    request = prompts_api.CreatePromptRequest(
        agent_name="gene",
        prompt_type="group_rules",
        group_id="BAD",
        content="rules",
        activate=False,
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(prompts_api.create_prompt(request, db=db, admin={"email": "admin@example.org"}))
    assert exc.value.status_code == 400
    assert "Invalid group_id" in exc.value.detail
    assert db.rollback.called


def test_create_prompt_returns_409_after_retries(monkeypatch):
    class _FakeCheckViolation(Exception):
        pass

    class _FakeUniqueViolation(Exception):
        pass

    monkeypatch.setattr(prompts_api, "CheckViolation", _FakeCheckViolation)
    monkeypatch.setattr(prompts_api, "UniqueViolation", _FakeUniqueViolation)

    db = MagicMock()
    db.query.return_value = _QueryStub(first_result=None)
    db.commit.side_effect = [
        IntegrityError("stmt", {}, _FakeUniqueViolation("collision1")),
        IntegrityError("stmt", {}, _FakeUniqueViolation("collision2")),
        IntegrityError("stmt", {}, _FakeUniqueViolation("collision3")),
    ]

    request = prompts_api.CreatePromptRequest(
        agent_name="gene",
        prompt_type="system",
        group_id=None,
        content="new content",
        activate=False,
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(prompts_api.create_prompt(request, db=db, admin={"email": "admin@example.org"}))
    assert exc.value.status_code == 409
    assert db.rollback.call_count == 3


def test_create_prompt_returns_500_on_non_unique_integrity(monkeypatch):
    class _FakeCheckViolation(Exception):
        pass

    class _FakeUniqueViolation(Exception):
        pass

    monkeypatch.setattr(prompts_api, "CheckViolation", _FakeCheckViolation)
    monkeypatch.setattr(prompts_api, "UniqueViolation", _FakeUniqueViolation)

    db = MagicMock()
    db.query.return_value = _QueryStub(first_result=None)
    db.commit.side_effect = IntegrityError("stmt", {}, Exception("fk violation"))

    request = prompts_api.CreatePromptRequest(
        agent_name="gene",
        prompt_type="system",
        group_id=None,
        content="new content",
        activate=False,
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(prompts_api.create_prompt(request, db=db, admin={"email": "admin@example.org"}))
    assert exc.value.status_code == 500
    assert "database integrity error" in exc.value.detail
    assert db.rollback.call_count == 1


def test_activate_prompt_404_when_missing():
    db = MagicMock()
    db.query.return_value = _QueryStub(first_result=None)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(prompts_api.activate_prompt(uuid.uuid4(), db=db, admin={"email": "admin@example.org"}))
    assert exc.value.status_code == 404


def test_activate_prompt_400_when_already_active():
    prompt = _prompt(version=2, is_active=True)
    db = MagicMock()
    db.query.return_value = _QueryStub(first_result=prompt)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(prompts_api.activate_prompt(prompt.id, db=db, admin={"email": "admin@example.org"}))
    assert exc.value.status_code == 400


def test_activate_prompt_success(monkeypatch):
    target = _prompt(version=2, is_active=False)
    current_active = _prompt(version=1, is_active=True)

    db = MagicMock()
    db.query.side_effect = [_QueryStub(first_result=target), _QueryStub(first_result=current_active)]
    refresh_calls = {"count": 0}
    monkeypatch.setattr(prompts_api.prompt_cache, "refresh", lambda _db: refresh_calls.__setitem__("count", refresh_calls["count"] + 1))

    result = asyncio.run(prompts_api.activate_prompt(target.id, db=db, admin={"email": "admin@example.org"}))

    assert current_active.is_active is False
    assert target.is_active is True
    assert result.previous_active_version == 1
    assert refresh_calls["count"] == 1
    assert db.commit.call_count == 1


def test_refresh_cache_endpoint_returns_status(monkeypatch):
    calls = {"refresh": 0}
    monkeypatch.setattr(prompts_api.prompt_cache, "refresh", lambda _db: calls.__setitem__("refresh", calls["refresh"] + 1))
    monkeypatch.setattr(
        prompts_api.prompt_cache,
        "get_cache_info",
        lambda: {"initialized": True, "loaded_at": "now", "active_prompts": 5, "total_versions": 8},
    )

    result = asyncio.run(prompts_api.refresh_cache(db=object(), admin={"email": "admin@example.org"}))

    assert result.status == "refreshed"
    assert result.active_prompts == 5
    assert calls["refresh"] == 1


def test_get_cache_status_returns_cache_info(monkeypatch):
    monkeypatch.setattr(
        prompts_api.prompt_cache,
        "get_cache_info",
        lambda: {"initialized": False, "loaded_at": None, "active_prompts": 0, "total_versions": 0},
    )

    result = asyncio.run(prompts_api.get_cache_status(_admin={"email": "admin@example.org"}))
    assert result.initialized is False
    assert result.active_prompts == 0


def test_get_prompt_history_404_when_missing():
    db = MagicMock()
    db.query.return_value = _QueryStub(all_result=[])

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            prompts_api.get_prompt_history(
                agent_name="gene",
                prompt_type="system",
                group_id="base",
                db=db,
                _admin={"email": "admin@example.org"},
            )
        )
    assert exc.value.status_code == 404


def test_get_prompt_history_success():
    prompts = [_prompt(version=3, is_active=True), _prompt(version=2, is_active=False)]
    db = MagicMock()
    db.query.return_value = _QueryStub(all_result=prompts)

    result = asyncio.run(
        prompts_api.get_prompt_history(
            agent_name="gene",
            prompt_type="system",
            group_id="base",
            db=db,
            _admin={"email": "admin@example.org"},
        )
    )
    assert result.total_versions == 2
    assert result.active_version == 3


def test_get_prompt_by_version_404_when_missing():
    db = MagicMock()
    db.query.return_value = _QueryStub(first_result=None)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            prompts_api.get_prompt_by_version(
                agent_name="gene",
                version=99,
                prompt_type="system",
                group_id="base",
                db=db,
                _admin={"email": "admin@example.org"},
            )
        )
    assert exc.value.status_code == 404


def test_get_prompt_by_version_success():
    prompt = _prompt(version=4)
    db = MagicMock()
    db.query.return_value = _QueryStub(first_result=prompt)

    result = asyncio.run(
        prompts_api.get_prompt_by_version(
            agent_name="gene",
            version=4,
            prompt_type="system",
            group_id="base",
            db=db,
            _admin={"email": "admin@example.org"},
        )
    )
    assert result.version == 4

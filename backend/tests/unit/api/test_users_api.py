"""Unit tests for users API helpers."""

from unittest.mock import AsyncMock

from fastapi import HTTPException

from src.api import users


async def test_get_current_user_info_returns_db_user_dict(monkeypatch):
    class _DbUser:
        def to_dict(self):
            return {"user_id": "user-1", "email": "user@example.org"}

    calls = {}

    async def _fake_set_global_user_from_cognito(principal):
        calls["principal"] = principal
        return _DbUser()

    def _fake_get_groups_from_provider_groups(provider_groups):
        calls["provider_groups"] = provider_groups
        return []

    monkeypatch.setattr(users, "provision_user_async", _fake_set_global_user_from_cognito)
    monkeypatch.setattr(
        users,
        "get_groups_from_provider_groups",
        _fake_get_groups_from_provider_groups,
    )

    user_payload = {"sub": "user-1", "email": "user@example.org"}
    result = await users.get_current_user_info(user=user_payload)

    assert result == {
        "user_id": "user-1",
        "email": "user@example.org",
        "provider_groups": [],
        "active_groups": [],
    }
    assert calls["principal"].subject == user_payload["sub"]
    assert calls["provider_groups"] == []


async def test_get_current_user_info_maps_legacy_claim_with_provider_api(monkeypatch):
    class _DbUser:
        def to_dict(self):
            return {"user_id": "user-1"}

    captured = {}
    monkeypatch.setattr(
        users,
        "provision_user_async",
        AsyncMock(return_value=_DbUser()),
    )

    def _map_provider_groups(provider_groups):
        captured["provider_groups"] = provider_groups
        return ["curator-group"]

    monkeypatch.setattr(
        users,
        "get_groups_from_provider_groups",
        _map_provider_groups,
    )

    result = await users.get_current_user_info(
        user={"sub": "user-1", "cognito:groups": ["flybase-curators"]},
    )

    assert result["provider_groups"] == ["flybase-curators"]
    assert result["active_groups"] == ["curator-group"]
    assert captured["provider_groups"] == ["flybase-curators"]


async def test_get_current_user_info_raises_401_when_not_authenticated():
    try:
        await users.get_current_user_info(user=None)
        raise AssertionError("Expected HTTPException")
    except HTTPException as exc:
        assert exc.status_code == 401
        assert exc.detail == "Not authenticated"

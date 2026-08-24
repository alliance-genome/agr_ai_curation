"""Unit tests for users API helpers."""

from fastapi import HTTPException

from src.api import users


async def test_get_current_user_info_returns_db_user_dict(monkeypatch):
    class _DbUser:
        def to_dict(self):
            return {"user_id": "user-1", "email": "user@example.org"}

    calls = {}

    def _fake_set_global_user_from_cognito(db, user):
        calls["args"] = (db, user)
        return _DbUser()

    def _fake_get_groups_from_provider_groups(provider_groups):
        calls["provider_groups"] = provider_groups
        return []

    monkeypatch.setattr(users, "set_global_user_from_cognito", _fake_set_global_user_from_cognito)
    monkeypatch.setattr(
        users,
        "get_groups_from_provider_groups",
        _fake_get_groups_from_provider_groups,
    )

    db = object()
    user_payload = {"sub": "user-1", "email": "user@example.org"}
    result = await users.get_current_user_info(user=user_payload, db=db)

    assert result == {
        "user_id": "user-1",
        "email": "user@example.org",
        "provider_groups": [],
        "active_groups": [],
    }
    assert calls["args"][0] is db
    assert calls["args"][1] == user_payload
    assert calls["provider_groups"] == []


async def test_get_current_user_info_maps_legacy_claim_with_provider_api(monkeypatch):
    class _DbUser:
        def to_dict(self):
            return {"user_id": "user-1"}

    captured = {}
    monkeypatch.setattr(
        users,
        "set_global_user_from_cognito",
        lambda _db, _user: _DbUser(),
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
        db=object(),
    )

    assert result["provider_groups"] == ["flybase-curators"]
    assert result["active_groups"] == ["curator-group"]
    assert captured["provider_groups"] == ["flybase-curators"]


async def test_get_current_user_info_raises_401_when_not_authenticated():
    try:
        await users.get_current_user_info(user=None, db=object())
        raise AssertionError("Expected HTTPException")
    except HTTPException as exc:
        assert exc.status_code == 401
        assert exc.detail == "Not authenticated"

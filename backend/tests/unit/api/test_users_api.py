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

    monkeypatch.setattr(users, "set_global_user_from_cognito", _fake_set_global_user_from_cognito)

    db = object()
    user_payload = {"sub": "user-1", "email": "user@example.org"}
    result = await users.get_current_user_info(user=user_payload, db=db)

    assert result == {"user_id": "user-1", "email": "user@example.org"}
    assert calls["args"][0] is db
    assert calls["args"][1] == user_payload


async def test_get_current_user_info_raises_401_when_not_authenticated():
    try:
        await users.get_current_user_info(user=None, db=object())
        raise AssertionError("Expected HTTPException")
    except HTTPException as exc:
        assert exc.status_code == 401
        assert exc.detail == "Not authenticated"

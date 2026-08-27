"""Thin authenticated API behavior for chat route preferences."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.api import users
from src.schemas.chat_route_preferences import ChatRoutePreferenceUpdate
from src.services.chat_route_preference_service import (
    ChatRoutePreferenceState,
    ChatRouteTarget,
    ChatRouteTargetUnavailableError,
)


@pytest.fixture
def authenticated_user(monkeypatch):
    user = {"sub": "auth-user", "cognito:groups": ["group-alpha-raw"]}
    monkeypatch.setattr(
        users,
        "set_global_user_from_cognito",
        lambda db, claims: SimpleNamespace(id=37),
    )
    monkeypatch.setattr(
        users,
        "get_groups_from_provider_groups",
        lambda groups: ["group-alpha"],
    )
    return user


async def test_read_derives_user_and_groups_from_auth_context(
    monkeypatch, authenticated_user
):
    captured = {}

    def fake_read(db, **kwargs):
        captured.update(kwargs)
        return ChatRoutePreferenceState("automatic", None, None, True, None)

    monkeypatch.setattr(users, "get_chat_route_preference", fake_read)
    result = await users.read_chat_route_preference(
        user=authenticated_user,
        db=object(),
    )

    assert result.mode.value == "automatic"
    assert result.status == "available"
    assert captured == {"user_id": 37, "active_group_ids": ["group-alpha"]}


async def test_replace_returns_authorized_target(monkeypatch, authenticated_user):
    target = ChatRouteTarget(
        id="gene_validation",
        kind="agent",
        display_name="Gene Validation",
        description="Validates genes",
        category="Validation",
        available=True,
    )
    monkeypatch.setattr(
        users,
        "update_chat_route_preference",
        lambda db, **kwargs: ChatRoutePreferenceState(
            "agent", "gene_validation", None, True, target
        ),
    )

    result = await users.replace_chat_route_preference(
        request=ChatRoutePreferenceUpdate(mode="agent", agent_id="gene_validation"),
        user=authenticated_user,
        db=object(),
    )

    assert result.agent_id == "gene_validation"
    assert result.flow_id is None
    assert result.target is not None and result.target.available is True


async def test_replace_hides_missing_or_unauthorized_target(
    monkeypatch, authenticated_user
):
    def unavailable(db, **kwargs):
        raise ChatRouteTargetUnavailableError

    monkeypatch.setattr(users, "update_chat_route_preference", unavailable)

    with pytest.raises(HTTPException) as exc_info:
        await users.replace_chat_route_preference(
            request=ChatRoutePreferenceUpdate(mode="agent", agent_id="restricted"),
            user=authenticated_user,
            db=object(),
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Chat route target is unavailable"


async def test_clear_restores_automatic_default(monkeypatch, authenticated_user):
    captured = {}
    monkeypatch.setattr(
        users,
        "clear_chat_route_preference",
        lambda db, **kwargs: captured.update(kwargs),
    )

    result = await users.delete_chat_route_preference(
        user=authenticated_user,
        db=object(),
    )

    assert captured == {"user_id": 37}
    assert result.mode.value == "automatic"
    assert result.target is None


async def test_picker_returns_only_service_authorized_summaries(
    monkeypatch, authenticated_user
):
    flow_id = uuid4()
    monkeypatch.setattr(
        users,
        "list_chat_route_picker_targets",
        lambda db, **kwargs: [
            ChatRouteTarget(
                id=str(flow_id),
                kind="flow",
                display_name="RGD GO and Disease Paper Review",
                description=None,
                category=None,
                available=True,
            )
        ],
    )

    result = await users.read_chat_route_targets(
        user=authenticated_user,
        db=object(),
    )

    assert [target.id for target in result.targets] == [str(flow_id)]
    assert result.targets[0].kind == "flow"
    assert result.targets[0].display_name == "RGD GO and Disease Paper Review"
    assert result.targets[0].id != "RGD GO and Disease Paper Review"

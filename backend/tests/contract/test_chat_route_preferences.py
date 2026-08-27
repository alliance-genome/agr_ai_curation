"""HTTP contract for authenticated chat route preference endpoints."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from src.services.chat_route_preference_service import ChatRoutePreferenceState


PREFERENCE_PATH = "/api/users/me/chat-route-preference"
TARGETS_PATH = "/api/users/me/chat-route-targets"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("AUTH_PROVIDER", "dev")
    monkeypatch.delenv("DEV_MODE", raising=False)
    monkeypatch.setenv("EMBEDDING_MODEL", "text-embedding-3-small")
    monkeypatch.setenv("EMBEDDING_TOKEN_PREFLIGHT_ENABLED", "true")
    monkeypatch.setenv("EMBEDDING_MODEL_TOKEN_LIMIT", "8191")
    monkeypatch.setenv("EMBEDDING_TOKEN_SAFETY_MARGIN", "500")
    monkeypatch.setenv("CONTENT_PREVIEW_CHARS", "1600")

    from fastapi.testclient import TestClient

    from main import app
    from src.api import auth as auth_module

    auth_module.reset_auth_provider_cache()
    app.dependency_overrides.clear()
    yield TestClient(app)
    app.dependency_overrides.clear()
    auth_module.reset_auth_provider_cache()


def _override_user(*, authenticated: bool) -> None:
    from main import app
    from src.api.auth import auth, get_db

    if authenticated:
        app.dependency_overrides[auth.get_user] = lambda: {
            "sub": "preference-user",
            "cognito:groups": ["developers"],
        }
    else:
        def unauthorized():
            raise HTTPException(status_code=401, detail="Not authenticated")

        app.dependency_overrides[auth.get_user] = unauthorized
    app.dependency_overrides[get_db] = lambda: object()


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", PREFERENCE_PATH),
        ("put", PREFERENCE_PATH),
        ("delete", PREFERENCE_PATH),
        ("get", TARGETS_PATH),
    ],
)
def test_chat_route_endpoints_require_authentication(client, method, path):
    _override_user(authenticated=False)
    response = client.request(method, path, json={"mode": "automatic"})

    assert response.status_code == 401


def test_read_returns_automatic_default_when_no_row_exists(client):
    _override_user(authenticated=True)
    with (
        patch(
            "src.api.users.set_global_user_from_cognito",
            return_value=SimpleNamespace(id=9),
        ),
        patch(
            "src.api.users.get_chat_route_preference",
            return_value=ChatRoutePreferenceState(
                "automatic", None, None, True, None
            ),
        ),
    ):
        response = client.get(PREFERENCE_PATH)

    assert response.status_code == 200
    assert response.json() == {
        "mode": "automatic",
        "agent_id": None,
        "flow_id": None,
        "status": "available",
        "target": None,
    }


def test_put_rejects_mixed_targets_before_service_dispatch(client):
    _override_user(authenticated=True)
    response = client.put(
        PREFERENCE_PATH,
        json={
            "mode": "agent",
            "agent_id": "gene_validation",
            "flow_id": "14dcb0d7-8ef6-4114-9df8-2b3675eb5d9e",
        },
    )

    assert response.status_code == 422

def test_put_rejects_caller_supplied_identity_or_group_claims(client):
    _override_user(authenticated=True)
    response = client.put(
        PREFERENCE_PATH,
        json={"mode": "automatic", "user_id": 99, "groups": ["group-alpha"]},
    )

    assert response.status_code == 422

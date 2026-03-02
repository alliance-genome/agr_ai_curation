"""Contract tests for GET /api/users/me."""

from datetime import datetime
from unittest.mock import patch

import pytest
from fastapi import HTTPException


USERS_ME_PATH = "/api/users/me"


@pytest.fixture
def client(monkeypatch):
    """Create test client configured for deterministic auth behavior."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("AUTH_PROVIDER", "dev")
    monkeypatch.delenv("DEV_MODE", raising=False)
    monkeypatch.setenv("EMBEDDING_MODEL", "text-embedding-3-small")
    monkeypatch.setenv("EMBEDDING_TOKEN_PREFLIGHT_ENABLED", "true")
    monkeypatch.setenv("EMBEDDING_MODEL_TOKEN_LIMIT", "8191")
    monkeypatch.setenv("EMBEDDING_TOKEN_SAFETY_MARGIN", "500")
    monkeypatch.setenv("CONTENT_PREVIEW_CHARS", "1600")

    from fastapi.testclient import TestClient
    import os
    import sys

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

    # Ensure each test gets a fresh app import (prevents auth override leakage).
    modules_to_clear = []
    for module_name in list(sys.modules.keys()):
        if module_name == "main" or module_name.startswith("src."):
            modules_to_clear.append(module_name)
    for module_name in modules_to_clear:
        del sys.modules[module_name]

    from main import app
    from src.api import auth as auth_module

    auth_module._provider = None
    auth_module._provider_error = None
    app.dependency_overrides.clear()

    yield TestClient(app)

    app.dependency_overrides.clear()
    auth_module._provider = None
    auth_module._provider_error = None


def _override_authenticated_user():
    from main import app
    from src.api.auth import auth

    app.dependency_overrides[auth.get_user] = lambda: {
        "sub": "test-user-sub-123",
        "uid": "test-user-sub-123",
        "email": "curator@alliancegenome.org",
        "name": "Test Curator",
        "cognito:groups": ["developers"],
    }


def _override_db():
    from main import app
    from src.api.auth import get_db

    app.dependency_overrides[get_db] = lambda: object()


def _override_unauthenticated_user():
    from main import app
    from src.api.auth import auth

    def _raise_401():
        raise HTTPException(status_code=401, detail="Not authenticated")

    app.dependency_overrides[auth.get_user] = _raise_401


class _FakeUserModel:
    def __init__(self, payload):
        self._payload = payload

    def to_dict(self):
        return self._payload


class TestUsersMeEndpoint:
    """Current API contract tests for /api/users/me."""

    def test_users_me_endpoint_exists(self, client):
        _override_unauthenticated_user()
        try:
            response = client.get(USERS_ME_PATH)
            assert response.status_code != 404
        finally:
            from main import app

            app.dependency_overrides.clear()

    def test_users_me_requires_authentication(self, client):
        _override_unauthenticated_user()
        try:
            response = client.get(USERS_ME_PATH)
            assert response.status_code == 401
            assert "detail" in response.json()
        finally:
            from main import app

            app.dependency_overrides.clear()

    def test_users_me_invalid_authorization_header_still_unauthorized(self, client):
        _override_unauthenticated_user()
        try:
            response = client.get(
                USERS_ME_PATH,
                headers={"Authorization": "Bearer invalid_malformed_token"},
            )
            assert response.status_code == 401
        finally:
            from main import app

            app.dependency_overrides.clear()

    def test_users_me_success_response_schema(self, client):
        _override_authenticated_user()
        _override_db()

        now = datetime.utcnow().isoformat()
        payload = {
            "id": 123,
            "auth_sub": "test-user-sub-123",
            "email": "curator@alliancegenome.org",
            "display_name": "Test Curator",
            "created_at": now,
            "last_login": now,
            "is_active": True,
        }

        try:
            with patch(
                "src.api.users.set_global_user_from_cognito",
                return_value=_FakeUserModel(payload),
            ):
                response = client.get(USERS_ME_PATH)

            assert response.status_code == 200
            data = response.json()
            assert data["id"] == 123
            assert data["auth_sub"] == "test-user-sub-123"
            assert data["email"] == "curator@alliancegenome.org"
            assert data["is_active"] is True
            assert "created_at" in data
            assert "last_login" in data
        finally:
            from main import app

            app.dependency_overrides.clear()

    def test_users_me_allows_null_email(self, client):
        _override_authenticated_user()
        _override_db()

        payload = {
            "id": 789,
            "auth_sub": "00s1service2account3def",
            "email": None,
            "display_name": None,
            "created_at": datetime.utcnow().isoformat(),
            "last_login": None,
            "is_active": True,
        }

        try:
            with patch(
                "src.api.users.set_global_user_from_cognito",
                return_value=_FakeUserModel(payload),
            ):
                response = client.get(USERS_ME_PATH)

            assert response.status_code == 200
            data = response.json()
            assert data["auth_sub"] == "00s1service2account3def"
            assert data["email"] is None
        finally:
            from main import app

            app.dependency_overrides.clear()

    def test_users_me_inactive_user_still_returned(self, client):
        _override_authenticated_user()
        _override_db()

        payload = {
            "id": 999,
            "auth_sub": "00u1inactive2user3ghi",
            "email": "inactive@alliancegenome.org",
            "display_name": "Inactive User",
            "created_at": datetime.utcnow().isoformat(),
            "last_login": None,
            "is_active": False,
        }

        try:
            with patch(
                "src.api.users.set_global_user_from_cognito",
                return_value=_FakeUserModel(payload),
            ):
                response = client.get(USERS_ME_PATH)

            assert response.status_code == 200
            data = response.json()
            assert data["is_active"] is False
            assert data["auth_sub"] == "00u1inactive2user3ghi"
        finally:
            from main import app

            app.dependency_overrides.clear()

    def test_users_me_response_content_type_json(self, client):
        _override_authenticated_user()
        _override_db()

        payload = {
            "id": 123,
            "auth_sub": "test-user-sub-123",
            "email": "curator@alliancegenome.org",
            "display_name": "Test Curator",
            "created_at": datetime.utcnow().isoformat(),
            "last_login": datetime.utcnow().isoformat(),
            "is_active": True,
        }

        try:
            with patch(
                "src.api.users.set_global_user_from_cognito",
                return_value=_FakeUserModel(payload),
            ):
                response = client.get(USERS_ME_PATH)

            assert response.status_code == 200
            assert "application/json" in response.headers["content-type"]
        finally:
            from main import app

            app.dependency_overrides.clear()


class TestAuthProviderClaimParity:
    """Provider abstraction parity tests for group claim extraction."""

    def _provider(self, group_claim: str):
        pytest.importorskip("jose")
        pytest.importorskip("jwt")
        pytest.importorskip("requests")
        from src.auth.providers.oidc import OIDCAuthProvider

        return OIDCAuthProvider(
            {
                "issuer_url": "https://issuer.example.test",
                "client_id": "client-id",
                "client_secret": "client-secret",
                "redirect_uri": "http://localhost:3002/auth/callback",
                "group_claim": group_claim,
            }
        )

    def test_cognito_and_oidc_group_claim_parity(self):
        cognito_provider = self._provider("cognito:groups")
        oidc_provider = self._provider("groups")

        cognito_claims = {
            "sub": "user-123",
            "email": "user@example.org",
            "name": "Example User",
            "cognito:groups": ["FB", "MGI"],
        }
        oidc_claims = {
            "sub": "user-123",
            "email": "user@example.org",
            "name": "Example User",
            "groups": ["FB", "MGI"],
        }

        cognito_principal = cognito_provider.extract_principal(cognito_claims)
        oidc_principal = oidc_provider.extract_principal(oidc_claims)

        assert cognito_principal.subject == oidc_principal.subject
        assert cognito_principal.email == oidc_principal.email
        assert cognito_principal.groups == oidc_principal.groups == ["FB", "MGI"]

    def test_group_claim_extraction_for_realm_access_roles(self):
        provider = self._provider("realm_access.roles")
        claims = {
            "sub": "kc-user-1",
            "email": "kc-user@example.org",
            "preferred_username": "kc-user",
            "realm_access": {"roles": ["curator", "admin"]},
        }

        principal = provider.extract_principal(claims)
        assert principal.subject == "kc-user-1"
        assert principal.display_name == "kc-user"
        assert principal.groups == ["curator", "admin"]

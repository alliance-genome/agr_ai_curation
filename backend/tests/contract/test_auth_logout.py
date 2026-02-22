"""Contract tests for POST /api/auth/logout."""

import asyncio
from urllib.parse import parse_qs, urlparse
from unittest.mock import MagicMock

import pytest


LOGOUT_PATH = "/api/auth/logout"


@pytest.fixture
def client(monkeypatch):
    """Create test client with auth provider configured for deterministic tests."""
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
    from main import app
    from src.api import auth as auth_module

    # Reset cached provider singleton between tests so env changes are respected.
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
        "sub": "00u1abc2def3ghi4jkl",
        "uid": "00u1abc2def3ghi4jkl",
        "email": "curator@alliancegenome.org",
        "name": "Test Curator",
    }


class TestLogoutEndpoint:
    """Current API contract tests for logout behavior."""

    def test_logout_endpoint_exists(self, client):
        response = client.post(LOGOUT_PATH)
        assert response.status_code != 404

    def test_logout_requires_authentication(self, client):
        response = client.post(LOGOUT_PATH)
        assert response.status_code == 401
        assert "detail" in response.json()

    def test_logout_invalid_authorization_header_still_unauthorized(self, client):
        response = client.post(
            LOGOUT_PATH,
            headers={"Authorization": "Bearer invalid_malformed_token"},
        )
        assert response.status_code == 401

    def test_logout_success_response_schema(self, client):
        _override_authenticated_user()

        try:
            response = client.post(LOGOUT_PATH)
            assert response.status_code == 200

            data = response.json()
            assert data["status"] == "logged_out"
            assert isinstance(data["message"], str)
            assert "logout_url" in data
            assert isinstance(data["logout_url"], str)
            assert len(data["logout_url"]) > 0
        finally:
            from main import app

            app.dependency_overrides.clear()

    def test_logout_is_idempotent(self, client):
        _override_authenticated_user()

        try:
            response1 = client.post(LOGOUT_PATH)
            response2 = client.post(LOGOUT_PATH)

            assert response1.status_code == 200
            assert response2.status_code == 200
            assert response1.json()["status"] == "logged_out"
            assert response2.json()["status"] == "logged_out"
        finally:
            from main import app

            app.dependency_overrides.clear()

    def test_logout_response_content_type_json(self, client):
        _override_authenticated_user()

        try:
            response = client.post(LOGOUT_PATH)
            assert response.status_code == 200
            assert "application/json" in response.headers["content-type"]
        finally:
            from main import app

            app.dependency_overrides.clear()


class TestDevProviderBehavior:
    """Basic provider checks for dev auth provider."""

    def test_dev_provider_login_url_preserves_state(self):
        from src.auth.providers.dev import DevAuthProvider

        provider = DevAuthProvider()
        login_url = provider.get_login_url(state="random-state-123", code_challenge="unused")
        assert "state=random-state-123" in login_url

    def test_dev_provider_returns_expected_principal(self):
        from src.auth.providers.dev import DevAuthProvider

        provider = DevAuthProvider()
        claims = asyncio.run(provider.validate_token("dev-token"))
        principal = provider.extract_principal(claims)

        assert principal.subject == "dev-user-123"
        assert principal.email == "dev@localhost"
        assert principal.groups == ["developers"]


class TestOidcLogoutBehavior:
    """Logout URL parameter behavior for generic OIDC vs Cognito compatibility."""

    def test_generic_oidc_custom_logout_uses_post_logout_redirect_uri(self):
        from src.auth.providers.oidc import OIDCAuthProvider

        provider = OIDCAuthProvider(
            {
                "issuer_url": "https://issuer.example.org",
                "client_id": "oidc-client",
                "redirect_uri": "https://app.example.org/auth/callback",
                "logout_url": "https://issuer.example.org/logout",
            }
        )

        logout_url = provider.get_logout_url("https://app.example.org/")
        assert logout_url is not None
        parsed = urlparse(logout_url)
        params = parse_qs(parsed.query)
        assert params.get("post_logout_redirect_uri") == ["https://app.example.org/"]
        assert "logout_uri" not in params

    def test_cognito_provider_uses_logout_uri_param(self, monkeypatch):
        from src.auth.providers.cognito_config import create_cognito_provider

        monkeypatch.setenv("COGNITO_REGION", "us-east-1")
        monkeypatch.setenv("COGNITO_USER_POOL_ID", "us-east-1_example")
        monkeypatch.setenv("COGNITO_CLIENT_ID", "example-client-id")
        monkeypatch.setenv("COGNITO_CLIENT_SECRET", "example-secret")
        monkeypatch.setenv("COGNITO_DOMAIN", "https://auth.example.org")
        monkeypatch.setenv("COGNITO_REDIRECT_URI", "https://app.example.org/auth/callback")

        provider = create_cognito_provider()
        logout_url = provider.get_logout_url("https://app.example.org/")
        assert logout_url is not None
        parsed = urlparse(logout_url)
        params = parse_qs(parsed.query)
        assert params.get("logout_uri") == ["https://app.example.org/"]
        assert "post_logout_redirect_uri" not in params

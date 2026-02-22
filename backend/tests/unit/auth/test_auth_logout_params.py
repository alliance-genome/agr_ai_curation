"""Unit tests for auth provider logout redirect parameter behavior."""

from urllib.parse import parse_qs, urlparse

from src.auth import factory as auth_factory
from src.auth.providers.cognito_config import create_cognito_provider
from src.auth.providers.oidc import OIDCAuthProvider


def test_oidc_provider_default_custom_logout_param():
    """Generic OIDC custom logout URL should use post_logout_redirect_uri by default."""
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
    params = parse_qs(urlparse(logout_url).query)
    assert params.get("post_logout_redirect_uri") == ["https://app.example.org/"]
    assert "logout_uri" not in params


def test_oidc_provider_custom_logout_param_override():
    """OIDC provider should honor explicit logout_redirect_param override."""
    provider = OIDCAuthProvider(
        {
            "issuer_url": "https://issuer.example.org",
            "client_id": "oidc-client",
            "redirect_uri": "https://app.example.org/auth/callback",
            "logout_url": "https://issuer.example.org/logout",
            "logout_redirect_param": "redirect_uri",
        }
    )

    logout_url = provider.get_logout_url("https://app.example.org/")
    assert logout_url is not None
    params = parse_qs(urlparse(logout_url).query)
    assert params.get("redirect_uri") == ["https://app.example.org/"]
    assert "post_logout_redirect_uri" not in params


def test_oidc_discovery_logout_uses_configured_redirect_param(monkeypatch):
    """When using discovery end_session_endpoint, provider should use configured param."""
    provider = OIDCAuthProvider(
        {
            "issuer_url": "https://issuer.example.org",
            "client_id": "oidc-client",
            "redirect_uri": "https://app.example.org/auth/callback",
            "logout_redirect_param": "post_logout_redirect_uri",
        }
    )
    monkeypatch.setattr(
        provider,
        "_discover",
        lambda: {"end_session_endpoint": "https://issuer.example.org/end-session"},
    )

    logout_url = provider.get_logout_url("https://app.example.org/")
    assert logout_url is not None
    params = parse_qs(urlparse(logout_url).query)
    assert params.get("post_logout_redirect_uri") == ["https://app.example.org/"]


def test_factory_uses_default_oidc_logout_redirect_param(monkeypatch):
    """Factory should default OIDC logout_redirect_param when env var is absent."""
    monkeypatch.setenv("AUTH_PROVIDER", "oidc")
    monkeypatch.setenv("OIDC_ISSUER_URL", "https://issuer.example.org")
    monkeypatch.setenv("OIDC_CLIENT_ID", "oidc-client")
    monkeypatch.setenv("OIDC_REDIRECT_URI", "https://app.example.org/auth/callback")
    monkeypatch.delenv("OIDC_LOGOUT_REDIRECT_PARAM", raising=False)
    monkeypatch.setattr(auth_factory, "is_dev_mode", lambda: False)

    provider = auth_factory.create_auth_provider()

    assert isinstance(provider, OIDCAuthProvider)
    assert provider.logout_redirect_param == "post_logout_redirect_uri"


def test_factory_uses_env_oidc_logout_redirect_param(monkeypatch):
    """Factory should apply configured OIDC_LOGOUT_REDIRECT_PARAM."""
    monkeypatch.setenv("AUTH_PROVIDER", "oidc")
    monkeypatch.setenv("OIDC_ISSUER_URL", "https://issuer.example.org")
    monkeypatch.setenv("OIDC_CLIENT_ID", "oidc-client")
    monkeypatch.setenv("OIDC_REDIRECT_URI", "https://app.example.org/auth/callback")
    monkeypatch.setenv("OIDC_LOGOUT_REDIRECT_PARAM", "redirect_uri")
    monkeypatch.setattr(auth_factory, "is_dev_mode", lambda: False)

    provider = auth_factory.create_auth_provider()

    assert isinstance(provider, OIDCAuthProvider)
    assert provider.logout_redirect_param == "redirect_uri"


def test_cognito_provider_uses_logout_uri_param(monkeypatch):
    """Cognito provider should continue using logout_uri compatibility param."""
    monkeypatch.setenv("COGNITO_REGION", "us-east-1")
    monkeypatch.setenv("COGNITO_USER_POOL_ID", "us-east-1_example")
    monkeypatch.setenv("COGNITO_CLIENT_ID", "example-client-id")
    monkeypatch.setenv("COGNITO_CLIENT_SECRET", "example-secret")
    monkeypatch.setenv("COGNITO_DOMAIN", "https://auth.example.org")
    monkeypatch.setenv("COGNITO_REDIRECT_URI", "https://app.example.org/auth/callback")

    provider = create_cognito_provider()

    assert provider.logout_redirect_param == "logout_uri"
    logout_url = provider.get_logout_url("https://app.example.org/")
    assert logout_url is not None
    params = parse_qs(urlparse(logout_url).query)
    assert params.get("logout_uri") == ["https://app.example.org/"]

"""Additional unit tests for auth provider factory branches."""

import pytest

from src.auth import factory as auth_factory
from src.auth.providers.dev import DevAuthProvider


def test_create_auth_provider_returns_dev_provider_when_dev_mode(monkeypatch):
    monkeypatch.setattr(auth_factory, "is_dev_mode", lambda: True)
    provider = auth_factory.create_auth_provider()
    assert isinstance(provider, DevAuthProvider)


def test_create_auth_provider_raises_for_cognito_when_not_configured(monkeypatch):
    monkeypatch.setattr(auth_factory, "is_dev_mode", lambda: False)
    monkeypatch.setattr(auth_factory, "get_auth_provider", lambda: "cognito")
    monkeypatch.setattr(auth_factory, "is_cognito_configured", lambda: False)
    with pytest.raises(ValueError, match="not fully configured"):
        auth_factory.create_auth_provider()


def test_create_auth_provider_raises_for_unknown_provider(monkeypatch):
    monkeypatch.setattr(auth_factory, "is_dev_mode", lambda: False)
    monkeypatch.setattr(auth_factory, "get_auth_provider", lambda: "mystery")
    with pytest.raises(ValueError, match="Unknown AUTH_PROVIDER"):
        auth_factory.create_auth_provider()

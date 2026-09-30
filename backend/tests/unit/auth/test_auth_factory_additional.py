"""Shared auth factory selection, configuration failures, and dev gate."""

import pytest

from src.auth import factory as auth_factory
from auth_runtime.dev import DevAuthProvider


def test_create_auth_provider_returns_dev_provider_when_dev_mode(monkeypatch):
    monkeypatch.setattr(auth_factory, "is_dev_mode", lambda: True)
    assert isinstance(auth_factory.create_auth_provider(), DevAuthProvider)


def test_create_auth_provider_raises_for_cognito_when_not_configured(monkeypatch):
    monkeypatch.setattr(auth_factory, "is_dev_mode", lambda: False)
    monkeypatch.setenv("AUTH_PROVIDER", "cognito")
    monkeypatch.delenv("COGNITO_USER_POOL_ID", raising=False)
    with pytest.raises(ValueError, match="not fully configured"):
        auth_factory.create_auth_provider()


def test_create_auth_provider_raises_for_unknown_provider(monkeypatch):
    monkeypatch.setattr(auth_factory, "is_dev_mode", lambda: False)
    monkeypatch.setenv("AUTH_PROVIDER", "mystery")
    with pytest.raises(ValueError, match="Invalid AUTH_PROVIDER"):
        auth_factory.create_auth_provider()

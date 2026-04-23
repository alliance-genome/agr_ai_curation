"""Unit tests for auth provider initialization caching behavior."""

import logging

import pytest
from fastapi import HTTPException

from src.api import auth as auth_api


@pytest.fixture(autouse=True)
def _reset_auth_provider_state():
    """Isolate module-level provider cache between tests."""
    original_provider = auth_api._provider
    original_provider_failed = auth_api._provider_failed

    auth_api._provider = None
    auth_api._provider_failed = False

    try:
        yield
    finally:
        auth_api._provider = original_provider
        auth_api._provider_failed = original_provider_failed


def test_get_provider_or_503_caches_init_failure(monkeypatch, caplog):
    """Failed provider initialization should not retry on every request."""
    calls = {"count": 0}

    def _failing_factory():
        calls["count"] += 1
        raise ValueError("missing AUTH_PROVIDER")

    monkeypatch.setattr(auth_api, "create_auth_provider", _failing_factory)
    caplog.set_level(logging.ERROR, logger=auth_api.logger.name)

    with pytest.raises(HTTPException) as first_exc:
        auth_api._get_provider_or_503()
    with pytest.raises(HTTPException) as second_exc:
        auth_api._get_provider_or_503()

    assert calls["count"] == 1
    assert first_exc.value.status_code == 503
    assert second_exc.value.status_code == 503
    assert first_exc.value.detail == "Authentication not configured"
    assert second_exc.value.detail == "Authentication not configured"
    assert "missing AUTH_PROVIDER" in caplog.text

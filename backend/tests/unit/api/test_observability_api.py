"""Unit tests for dev-only observability smoke endpoints."""

from fastapi.testclient import TestClient

from main import create_app
from src.api import observability


def test_synthetic_observability_endpoint_hidden_by_default(monkeypatch):
    monkeypatch.setenv("DEV_MODE", "true")
    monkeypatch.setenv("SENTRY_SYNTHETIC_TEST_ENDPOINTS_ENABLED", "false")

    client = TestClient(create_app(), raise_server_exceptions=False)
    response = client.post("/api/observability/sentry/synthetic-unhandled")

    assert response.status_code == 404


def test_synthetic_unhandled_endpoint_raises_when_enabled(monkeypatch):
    monkeypatch.setenv("DEV_MODE", "true")
    monkeypatch.setenv("SENTRY_SYNTHETIC_TEST_ENDPOINTS_ENABLED", "true")

    client = TestClient(create_app(), raise_server_exceptions=False)
    response = client.post("/api/observability/sentry/synthetic-unhandled")

    assert response.status_code == 500


def test_synthetic_caught_alert_endpoint_reports_facade(monkeypatch):
    calls = []

    async def _fake_notify_tool_failure(**kwargs):
        calls.append(kwargs)
        return False

    monkeypatch.setenv("DEV_MODE", "true")
    monkeypatch.setenv("SENTRY_SYNTHETIC_TEST_ENDPOINTS_ENABLED", "true")
    monkeypatch.setattr(observability, "notify_tool_failure", _fake_notify_tool_failure)

    client = TestClient(create_app(), raise_server_exceptions=False)
    response = client.post("/api/observability/sentry/synthetic-caught-alert")

    assert response.status_code == 200
    assert response.json() == {"status": "reported", "sns_sent": False}
    assert calls == [
        {
            "error_type": "SyntheticSentryCaughtAlert",
            "error_message": "sanitized synthetic caught alert",
            "source": "infrastructure",
            "specialist_name": "sentry_synthetic_caught_alert",
            "trace_id": "synthetic-sentry-trace",
            "session_id": "synthetic-sentry-session",
            "curator_id": "synthetic-sentry-curator",
            "context": "sanitized synthetic context",
        }
    ]

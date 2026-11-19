"""HTTP and Sentry contracts for browser-detected Studio protocol failures."""

from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
import sentry_sdk

from src.api import agent_studio, auth
from src.lib.observability import sentry


URL = "/api/agent-studio/chat/protocol-error"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(auth, "is_dev_mode", lambda: False)
    monkeypatch.setenv("TESTING_API_KEY", "fake-studio-protocol-test-key")
    monkeypatch.setenv("TESTING_API_KEY_USER", "private-curator")
    app = FastAPI()
    app.include_router(agent_studio.router)
    with TestClient(app, headers={"X-API-Key": "fake-studio-protocol-test-key"}) as client:
        yield client


def test_protocol_reports_require_authentication(client, monkeypatch):
    capture = Mock()
    monkeypatch.setattr(agent_studio, "report_runtime_exception", capture)
    del client.headers["X-API-Key"]
    assert client.post(URL, json={"reason": "invalid_shape"}).status_code == 401
    capture.assert_not_called()


@pytest.mark.parametrize("bad_fields", [
    {"reason": "private error text"},
    {"session_id_hash": "private-raw-id"},
    {"session_id_hash": "sha256:" + "a" * 17},
    {"session_id_hash": "sha256:" + "a" * 16 + "\n"},
    {"event": {"prompt": "private prompt", "tool_result": "private tool result"}},
])
def test_protocol_reports_reject_untrusted_fields(client, monkeypatch, bad_fields):
    capture = Mock()
    monkeypatch.setattr(agent_studio, "report_runtime_exception", capture)
    assert client.post(URL, json={"reason": "invalid_shape", **bad_fields}).status_code == 422
    capture.assert_not_called()


@pytest.mark.parametrize("reason", [
    "malformed_json", "invalid_shape", "unknown_type", "correlation_mismatch", "missing_terminal",
])
def test_protocol_reports_emit_one_content_free_event(client, reason):
    events = []
    # An initial malformed event need not have any session correlation.
    report = {"reason": reason}
    if reason != "malformed_json":
        report["session_id_hash"] = sentry.hash_sentry_identifier("private-chat-session")
    with sentry_sdk.Client(
        dsn="http://public@example.invalid/1", transport=events.append,
        before_send=sentry.before_send, include_local_variables=False, default_integrations=False,
    ) as sdk, sentry_sdk.isolation_scope() as scope:
        scope.set_client(sdk)
        response = client.post(URL, json=report)
        sdk.flush()
    assert response.status_code == 204
    assert response.content == b""
    event, = events
    assert event["tags"]["failure_category"] == reason
    assert event["fingerprint"] == ["agent_studio", "chat_stream_protocol", reason]
    assert event["tags"].get("ai_curation.chat.session_id_hash") == report.get("session_id_hash")
    assert "private-" not in repr(event)
    assert "fake-studio-protocol-test-key" not in repr(event)


def test_protocol_report_capture_failure_stays_best_effort(client, monkeypatch, caplog):
    capture = Mock(side_effect=RuntimeError("private SDK exception text"))
    monkeypatch.setattr(sentry_sdk, "capture_exception", capture)
    assert client.post(URL, json={"reason": "missing_terminal"}).status_code == 204
    capture.assert_called_once()
    assert "private SDK exception text" not in caplog.text

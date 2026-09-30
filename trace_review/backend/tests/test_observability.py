"""Metadata privacy and non-masking guarantees for TraceReview reporting."""
import asyncio
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import requests
import sentry_sdk
from fastapi import HTTPException, Response
from jwt.exceptions import InvalidTokenError, PyJWKClientConnectionError, PyJWKClientError
from sentry_sdk.transport import Transport

from src import observability, main
from src.services import feedback_artifacts
from src.api import auth


@pytest.fixture
def reporter(monkeypatch):
    client = Mock()
    monkeypatch.setattr(observability, "_client", client)
    return client


def test_unconfigured_and_failed_initialization_are_nonmasking(monkeypatch):
    factory = Mock(side_effect=RuntimeError("secret-dsn"))
    monkeypatch.setattr(sentry_sdk, "Client", factory)
    monkeypatch.delenv("TRACE_REVIEW_SENTRY_DSN", raising=False)
    observability.initialize_sentry()
    factory.assert_not_called()
    monkeypatch.setenv("TRACE_REVIEW_SENTRY_DSN", "http://key@host/1")
    observability.initialize_sentry()
    factory.assert_not_called()
    monkeypatch.setenv("TRACE_REVIEW_SENTRY_DSN", "https://key@host/1")
    observability.initialize_sentry()
    factory.assert_called_once()


@pytest.mark.parametrize("operation", [
    "scores", "auth_configuration", "auth_validation", "auth_login",
    "auth_callback", "auth_logout",
])
def test_real_sdk_event_excludes_ambient_content(monkeypatch, operation):
    envelopes = []

    class RecordingTransport(Transport):
        def capture_envelope(self, envelope):
            envelopes.append(envelope)

    real_client = sentry_sdk.Client
    factory = Mock(side_effect=lambda **kwargs: real_client(
        **kwargs, transport=RecordingTransport,
    ))
    monkeypatch.setattr(sentry_sdk, "Client", factory)
    monkeypatch.setenv("TRACE_REVIEW_SENTRY_DSN", "https://key@host/1")
    monkeypatch.setenv("TRACE_REVIEW_SENTRY_RELEASE", "test-release")
    monkeypatch.setattr(observability, "_client", None)
    observability.initialize_sentry()
    assert factory.call_args.kwargs["default_integrations"] is False
    assert factory.call_args.kwargs["auto_enabling_integrations"] is False
    with sentry_sdk.isolation_scope() as scope:
        scope.set_user({"email": "private-user"})
        scope.set_extra("prompt", "private-prompt")
        scope.set_context("payload", {"response": "private-response"})
        scope.set_tag("token", "private-token")
        observability.report_failure(operation, trace_id="private-trace")
    observability.close_sentry()
    events = [item.get_event() for envelope in envelopes for item in envelope.items
              if item.type == "event"]
    assert len(events) == 1
    event = events[0]
    assert "private-" not in json.dumps(event)
    assert event["release"] == "test-release"
    assert event["contexts"]["trace_review"]["trace_id_hash"] == hashlib.sha256(
        b"private-trace").hexdigest()
    assert event["tags"] == {"component": "trace_review", "operation": operation, "source": "remote"}
    assert "exception" not in event
    assert "request" not in event
    assert "breadcrumbs" not in event


def test_reporter_failure_does_not_mask_original_exception(reporter):
    reporter.capture_event.side_effect = RuntimeError("reporter unavailable")
    observability.report_failure("scores", trace_id="private-trace")
    with pytest.raises(ValueError, match="original"):
        with observability.session_failure_scope("private-session", "local"):
            observability.report_failure("scores")
            raise ValueError("original")
    # Scope restored after exceptions: next failure reports independently.
    observability.report_failure("search")
    assert reporter.capture_event.call_count == 3


def test_session_aggregation_has_fixed_size_categories(reporter):
    with observability.session_failure_scope("private-session", "local"):
        for _ in range(200):
            observability.report_failure("scores", trace_id="private-trace")
        observability.report_failure("analysis")
    reporter.capture_event.assert_called_once()
    event = reporter.capture_event.call_args.args[0]
    assert event["tags"]["operation"] == "session_export"
    assert event["contexts"]["trace_review"] == {
        "session_id_hash": hashlib.sha256(b"private-session").hexdigest(),
        "scores_failures": 200, "analysis_failures": 1,
    }


@pytest.mark.parametrize("failure,operation", [
    (requests.ConnectionError("private-response"), "feedback_transport"),
    (503, "feedback_http"),
    (ValueError("private-response"), "feedback_json"),
    ([], "feedback_payload"),
])
@pytest.mark.parametrize("reporter_broken", [False, True])
def test_feedback_outages_capture_without_changing_result(
    monkeypatch, reporter, failure, operation, reporter_broken,
):
    monkeypatch.setenv("AI_CURATION_BACKEND_URL", "https://private-host")
    monkeypatch.setenv("TRACE_REVIEW_INTERNAL_API_TOKEN", "private-token")
    response = Mock(status_code=503 if failure == 503 else 200)
    if isinstance(failure, ValueError):
        response.json.side_effect = failure
    else:
        response.json.return_value = failure
    get = Mock(return_value=response)
    if isinstance(failure, requests.RequestException):
        get.side_effect = failure
    monkeypatch.setattr(feedback_artifacts.requests, "get", get)
    if reporter_broken:
        reporter.capture_event.side_effect = RuntimeError("reporter unavailable")
    result = feedback_artifacts.fetch_feedback_trace_artifacts("private-feedback")
    assert result is not None
    assert result["status"] == "unavailable"
    reporter.capture_event.assert_called_once()
    event = reporter.capture_event.call_args.args[0]
    assert event["tags"]["operation"] == operation
    assert "private-" not in json.dumps(event)


@pytest.mark.parametrize("status", [401, 403, 404])
def test_feedback_expected_client_errors_are_quiet(monkeypatch, reporter, status):
    monkeypatch.setenv("AI_CURATION_BACKEND_URL", "https://host")
    monkeypatch.setenv("TRACE_REVIEW_INTERNAL_API_TOKEN", "token")
    monkeypatch.setattr(feedback_artifacts.requests, "get", Mock(return_value=Mock(status_code=status)))
    feedback_artifacts.fetch_feedback_trace_artifacts("feedback")
    reporter.capture_event.assert_not_called()


def test_unconfigured_feedback_and_health_probes_are_quiet(monkeypatch, reporter):
    monkeypatch.delenv("AI_CURATION_BACKEND_URL", raising=False)
    result = feedback_artifacts.fetch_feedback_trace_artifacts("feedback")
    assert result is not None
    assert result["status"] == "not_configured"
    monkeypatch.setattr(main, "get_trace_review_preflight_diagnostics", lambda **kwargs: {
        "source_selection": {"valid": True, "selected_ready": True},
    })
    monkeypatch.setattr(main, "TraceExtractor", Mock(side_effect=RuntimeError("provider down")))
    app = SimpleNamespace(state=SimpleNamespace(cache_manager=SimpleNamespace(cache={}, ttl_hours=1)))
    assert main._health_payload(app)[1] == 200
    assert main._preflight_payload(app, "remote")[1] == 503
    reporter.capture_event.assert_not_called()


def test_startup_and_shutdown_use_owned_client(monkeypatch):
    initialize, close = Mock(), Mock()
    monkeypatch.setattr(main, "initialize_sentry", initialize)
    monkeypatch.setattr(main, "close_sentry", close)

    async def run():
        async with main.lifespan(SimpleNamespace(state=SimpleNamespace())):
            initialize.assert_called_once()
        close.assert_called_once()
    asyncio.run(run())


@pytest.mark.asyncio
@pytest.mark.parametrize("stage,failure,operation", [
    ("configuration", ValueError("private-configuration"), "auth_configuration"),
    ("validation", PyJWKClientConnectionError("private-jwks"), "auth_validation"),
    ("validation", PyJWKClientError("private-jwks-response"), "auth_validation"),
    ("validation", RuntimeError("private-runtime"), "auth_validation"),
    ("login", RuntimeError("private-login-url"), "auth_login"),
    ("callback", RuntimeError("private-callback-response"), "auth_callback"),
    ("logout", RuntimeError("private-logout-url"), "auth_logout"),
])
@pytest.mark.parametrize("reporter_broken", [False, True])
async def test_auth_outages_report_once_without_sensitive_context(
    monkeypatch, reporter, stage, failure, operation, reporter_broken,
):
    provider = Mock()
    provider.validate_token = AsyncMock(side_effect=failure)
    provider.get_login_url.side_effect = failure
    provider.get_logout_url.side_effect = failure
    monkeypatch.setattr(auth, "_configured_provider", Mock(
        side_effect=failure if stage == "configuration" else None,
        return_value=provider,
    ))
    monkeypatch.setattr(auth, "authenticate_callback", AsyncMock(side_effect=failure))
    monkeypatch.setenv("DEV_MODE", "false")
    monkeypatch.delenv("TRACE_REVIEW_INTERNAL_API_TOKEN", raising=False)
    request = SimpleNamespace(headers={"authorization": "private-header"}, cookies={
        "auth_token": "private-token", "oauth_state": "private-state",
        "oauth_code_verifier": "private-verifier",
    })
    if reporter_broken:
        reporter.capture_event.side_effect = RuntimeError("private-reporting-outage")
    with pytest.raises(HTTPException) as exc:
        if stage == "login":
            await auth.login(request)
        elif stage == "callback":
            await auth.callback(request, Response(), "private-code", "private-state")
        elif stage == "logout":
            await auth.logout(request)
        else:
            await auth._get_user_from_cookie_impl(request)
    assert exc.value.status_code == 503
    assert "private-" not in exc.value.detail
    reporter.capture_event.assert_called_once()
    event = reporter.capture_event.call_args.args[0]
    assert event["tags"]["operation"] == operation
    assert event["contexts"]["trace_review"] == {}
    assert "private-" not in json.dumps(event)
    assert "exception" not in event
    assert "request" not in event


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", [
    "invalid_token", "unknown_key", "missing_token", "invalid_state",
    "missing_verifier", "callback_invalid_token", "internal_token", "health",
])
async def test_expected_auth_rejections_and_health_are_quiet(monkeypatch, reporter, stage):
    provider = Mock()
    failure = (PyJWKClientError("Unable to find a signing key that matches: private-kid")
               if stage == "unknown_key" else InvalidTokenError("private-token"))
    provider.validate_token = AsyncMock(side_effect=failure)
    monkeypatch.setattr(auth, "_configured_provider", Mock(return_value=provider))
    monkeypatch.setattr(auth, "authenticate_callback", AsyncMock(side_effect=failure))
    monkeypatch.setenv("DEV_MODE", "false")
    monkeypatch.setenv("TRACE_REVIEW_INTERNAL_API_TOKEN", "expected-token")
    request = SimpleNamespace(headers={}, cookies={
        "auth_token": "private-token", "oauth_state": "state",
        "oauth_code_verifier": "verifier",
    })
    if stage == "health":
        monkeypatch.setattr(auth, "_configured_provider", Mock(side_effect=ValueError("private-config")))
        assert (await auth.health())["auth_configured"] is False
    else:
        with pytest.raises(HTTPException) as exc:
            if stage in {"invalid_state", "missing_verifier", "callback_invalid_token"}:
                if stage == "missing_verifier":
                    request.cookies.pop("oauth_code_verifier")
                await auth.callback(request, Response(), "code",
                                    "wrong" if stage == "invalid_state" else "state")
            else:
                if stage == "missing_token":
                    request.cookies.clear()
                if stage == "internal_token":
                    request.headers["authorization"] = "Bearer wrong-token"
                await auth._get_user_from_cookie_impl(request)
        assert 400 <= exc.value.status_code < 500
    reporter.capture_event.assert_not_called()

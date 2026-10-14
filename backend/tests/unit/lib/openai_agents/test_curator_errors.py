"""Provider overload and terminal errors never leak payloads or promise unsafe retries."""
import httpx
import pytest
from agents.models.openai_responses import ResponsesWebSocketError
from openai import APIConnectionError, APIStatusError

from src.lib.openai_agents.curator_errors import classify_failure, failure_for_event


def websocket(code, error_type="service_unavailable_error"):
    return ResponsesWebSocketError({"type": "error", "error": {
        "type": error_type, "code": code, "message": "PRIVATE PROVIDER PAYLOAD",
        "headers": {"x-retry-metadata": "NO_MORE_RETRY"},
    }})


@pytest.mark.parametrize("code, category, retryable", [
    ("server_is_overloaded", "provider_unavailable", True),
    ("rate_limit_exceeded", "provider_unavailable", True),
    ("insufficient_quota", "provider_configuration", False),
    ("context_length_exceeded", "context_limit", False),
    ("bio_policy", "provider_content_policy", False),
])
def test_typed_websocket_error_through_specialist_wrapper(code, category, retryable):
    cause = websocket(code)
    wrapped = RuntimeError("tool call wrapper with PRIVATE PROVIDER PAYLOAD")
    wrapped.__cause__ = cause
    result = classify_failure(wrapped)
    assert result.category == category
    assert result.retryable is retryable
    assert "PRIVATE" not in result.message
    assert failure_for_event(result.event_fields()) == result


@pytest.mark.parametrize("status, code, category, retryable", [
    (429, "rate_limit_exceeded", "provider_unavailable", True),
    (429, "insufficient_quota", "provider_configuration", False),
    (503, "internal_error", "provider_unavailable", True),
    (401, "bad_key", "provider_configuration", False),
    (400, "context_length_exceeded", "context_limit", False),
])
def test_http_provider_errors(status, code, category, retryable):
    response = httpx.Response(status, request=httpx.Request("POST", "https://example.test"))
    error = APIStatusError("PRIVATE", response=response, body={"code": code})
    result = classify_failure(error)
    assert (result.category, result.retryable) == (category, retryable)
    assert "PRIVATE" not in result.message


def test_connection_and_unknown_errors_are_sanitized():
    error = APIConnectionError(request=httpx.Request("POST", "https://example.test"))
    assert classify_failure(error).category == "provider_connection"
    assert "PRIVATE" not in classify_failure(ValueError("PRIVATE")).message
    assert failure_for_event({"error_type": "BuilderFinalizationMissing", "message": "PRIVATE"}).retryable is False

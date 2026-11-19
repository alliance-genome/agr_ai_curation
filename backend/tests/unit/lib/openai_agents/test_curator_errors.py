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


def invalid_request(code, param):
    return ResponsesWebSocketError({"type": "error", "status": 400, "error": {
        "type": "invalid_request_error", "code": code, "param": param,
        "message": "PRIVATE PROVIDER PAYLOAD",
    }})


def _through_sdk_wrapper(cause):
    """The chain seen in Sentry for ALL-1558: ResponsesWebSocketError -> UserError."""
    from agents.exceptions import UserError

    wrapped = UserError("PRIVATE PROVIDER PAYLOAD")
    wrapped.__cause__ = cause
    return wrapped


@pytest.mark.parametrize("param", ["input[2]", "input[0]", "input[17]"])
def test_a_reasoning_input_refusal_that_ends_a_run_is_temporary(param):
    result = classify_failure(_through_sdk_wrapper(invalid_request("unsupported_parameter", param)))
    assert (result.category, result.retryable, result.provider) == ("provider_unavailable", True, "openai")
    assert "will not fix" not in result.message and "PRIVATE" not in result.message
    assert failure_for_event(result.event_fields()) == result


@pytest.mark.parametrize("error", [
    invalid_request("unsupported_parameter", "input"),
    invalid_request("unsupported_parameter", "tools"),
    invalid_request("unsupported_parameter", "input[2].content"),
    invalid_request("unsupported_parameter", None),
    invalid_request("invalid_value", "input[2]"),
    websocket("something_new", "invalid_request_error"),
    websocket(None, None),
], ids=["item-ids-form", "other-param", "nested-param", "no-param", "other-code", "new-code", "untyped"])
def test_unrecognised_websocket_errors_are_unknown_not_configuration(error):
    result = classify_failure(_through_sdk_wrapper(error))
    assert (result.category, result.retryable, result.provider) == ("unknown", True, "openai")
    assert "will not fix" not in result.message and "PRIVATE" not in result.message
    assert failure_for_event(result.event_fields()) == result


@pytest.mark.parametrize("code", ["insufficient_quota", "invalid_api_key", "permission_denied"])
def test_access_quota_and_key_websocket_errors_keep_the_configuration_message(code):
    result = classify_failure(_through_sdk_wrapper(websocket(code, "invalid_request_error")))
    assert (result.category, result.retryable, result.provider) == ("provider_configuration", False, "openai")
    assert "will not fix" in result.message


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


def test_history_checkpoint_failure_survives_sdk_wrapping_without_exposing_payload():
    from src.lib.openai_agents.model_context_view import HistoryStorageUnavailable, ContextRecoveryError
    for error, expected, retryable in ((HistoryStorageUnavailable("private database detail"), "history_unavailable", True),
                                      (ContextRecoveryError("private content"), "protected_context_limit", False)):
        wrapper = RuntimeError("SDK tool error")
        wrapper.__cause__ = error
        failure = classify_failure(wrapper)
        assert failure.category == expected
        assert failure.retryable is retryable
        assert "private" not in failure.message
        assert "Completed actions were not repeated" in failure.message


def test_pdf_read_event_has_clear_terminal_message_without_payload():
    failure = failure_for_event({"error_type": "DocumentReadError", "message": "PRIVATE PDF"})
    assert failure.category == "pdf_read_failed"
    assert failure.retryable is False
    assert failure.message == "There was an error reading the PDF. We couldn't continue the extraction."



@pytest.mark.parametrize("wrapped", [False, True])
def test_pdf_read_failure_survives_wrappers_and_timeout_causes(wrapped):
    from agr_ai_curation_runtime.document_errors import DocumentReadError
    error = DocumentReadError("read_chunk")
    error.__cause__ = TimeoutError("PRIVATE SOURCE")
    if wrapped:
        outer = RuntimeError("SDK wrapper")
        outer.__cause__ = error
        error = outer
    failure = classify_failure(error)
    assert failure.category == "pdf_read_failed"
    assert "PRIVATE" not in failure.message

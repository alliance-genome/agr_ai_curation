"""Deterministic, payload-free failure messages shared by curator entry points."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping

from agents.models.openai_responses import ResponsesWebSocketError
from openai import APIConnectionError, APIStatusError, APITimeoutError

from agr_ai_curation_runtime.document_errors import document_read_failure

from .provider_errors import PROVIDER_POLICY_CHAT_MESSAGE, provider_policy_error


@dataclass(frozen=True)
class CuratorFailure:
    category: str
    message: str
    retryable: bool
    provider: str | None = None

    def event_fields(self) -> dict[str, Any]:
        return {"failure_category": self.category, "message": self.message,
                "retryable": self.retryable, "provider": self.provider}


_FAILURES = {
    "pdf_read_failed": CuratorFailure("pdf_read_failed", "There was an error reading the PDF. We couldn't continue the extraction.", False),
    "history_unavailable": CuratorFailure("history_unavailable", "This run stopped because the request grew too large while its history could not be saved. Completed actions were not repeated. You can restore your request and try again later.", True),
    "protected_context_limit": CuratorFailure("protected_context_limit", "This run stopped before required context would be lost. Please split the task into smaller requests. Completed actions were not repeated.", False),
    "provider_unavailable": CuratorFailure("provider_unavailable", "The AI service is temporarily unavailable or busy. This request did not finish. You can restore your request and try again later. If it keeps happening, report it using the feedback menu.", True),
    "provider_connection": CuratorFailure("provider_connection", "The connection to the AI service was interrupted before this request finished. You can restore your request and try again later.", True),
    "provider_configuration": CuratorFailure("provider_configuration", "The AI service could not accept this request because of a request, access, quota or configuration problem. Please report it using the feedback menu. Repeating the same request will not fix this problem.", False),
    "context_limit": CuratorFailure("context_limit", "This request exceeded an AI context or request-size limit. Try a smaller document scope or a new chat. Report the problem if the same workflow keeps failing.", False),
    "invalid_result": CuratorFailure("invalid_result", "The AI response could not be finalized as a valid result. This run did not complete. Please report it using the feedback menu before repeating this workflow.", False),
    "persistence_failed": CuratorFailure("persistence_failed", "The result could not be saved. This run did not complete, and unsaved output is not ready for curation. Keep any visible text and report the problem using the feedback menu.", False),
    "unknown": CuratorFailure("unknown", "This request could not be completed. You can restore the request to review it before trying again later. If it keeps failing, report it using the feedback menu.", True),
    "provider_content_policy": CuratorFailure("provider_content_policy", PROVIDER_POLICY_CHAT_MESSAGE, False, "openai"),
}
_EVENT_CATEGORIES = {
    "DocumentReadError": "pdf_read_failed",
    "HistoryStorageUnavailable": "history_unavailable",
    "ContextRecoveryError": "protected_context_limit",
    "ModelRequestBlockedError": "context_limit",
    "PayloadContractViolation": "context_limit",
    "SpecialistOutputError": "invalid_result",
    "StructuredFinalizationFailed": "invalid_result",
    "BuilderFinalizationMissing": "invalid_result",
    "MissingEvidenceRecords": "invalid_result",
    "GuardrailTriggered": "invalid_result",
    "extraction_persistence_failed": "persistence_failed",
    "validator_result_persistence_failed": "persistence_failed",
    "inline_extraction_persistence_failed": "persistence_failed",
}


def failure_for_event(data: Mapping[str, Any]) -> CuratorFailure:
    """Use trusted runtime codes; never expose arbitrary exception/model text."""
    category = data.get("failure_category")
    if isinstance(category, str) and category in _FAILURES:
        return replace(_FAILURES[category], provider=data.get("provider") if data.get("provider") in {"openai", "groq", "gemini", "openrouter"} else _FAILURES[category].provider)
    if data.get("error_type") == "ToolGroupCapError":
        # This exception deliberately writes a curator-safe configuration message.
        return CuratorFailure("configuration", str(data.get("message") or "The agent exceeds its configured tool limit. Please update its configuration."), False)
    code = data.get("reason") or data.get("error_type")
    return _FAILURES[_EVENT_CATEGORIES.get(code, "unknown") if isinstance(code, str) else "unknown"]


def classify_failure(error: BaseException) -> CuratorFailure:
    """Inspect typed causes, including SDK tool wrappers, without matching prose."""
    if document_read_failure(error) is not None:
        return _FAILURES["pdf_read_failed"]
    if provider_policy_error(error) is not None:
        return _FAILURES["provider_content_policy"]
    current: BaseException | None = error
    seen: set[int] = set()
    fallback = _FAILURES["unknown"]
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ResponsesWebSocketError):
            if current.code in {"insufficient_quota", "invalid_api_key", "permission_denied"}:
                return replace(_FAILURES["provider_configuration"], provider="openai")
            if current.code in {"context_length_exceeded", "request_too_large"}:
                return replace(_FAILURES["context_limit"], provider="openai")
            if current.code in {"server_is_overloaded", "rate_limit_exceeded"} or current.error_type in {"server_error", "service_unavailable_error", "rate_limit_error"}:
                return replace(_FAILURES["provider_unavailable"], provider="openai")
            return replace(_FAILURES["provider_configuration"], provider="openai")
        elif isinstance(current, (APIConnectionError, APITimeoutError, TimeoutError)):
            return _FAILURES["provider_connection"]
        elif isinstance(current, APIStatusError):
            if getattr(current, "code", None) in {"context_length_exceeded", "request_too_large"}:
                return _FAILURES["context_limit"]
            if getattr(current, "code", None) == "insufficient_quota" or current.status_code in {401, 403}:
                return _FAILURES["provider_configuration"]
            if current.status_code in {408, 429} or current.status_code >= 500:
                return _FAILURES["provider_unavailable"]
        details = getattr(current, "details", {})
        details = [details] if isinstance(details, Mapping) else details
        reason = next((item.get("reason") for item in details if isinstance(item, Mapping) and item.get("reason") in _EVENT_CATEGORIES), None) if isinstance(details, list) else None
        code = reason or type(current).__name__
        if code in _EVENT_CATEGORIES:
            fallback = _FAILURES[_EVENT_CATEGORIES[code]]
        current = current.__cause__ or current.__context__
    return fallback

"""Typed provider policy classification shared by chat and saved flows."""

from agents.models.openai_responses import ResponsesWebSocketError

from src.lib.observability.runtime import report_runtime_exception, sanitized_runtime_error
from src.lib.observability.sentry import hash_sentry_identifier


PROVIDER_POLICY_CHAT_MESSAGE = (
    "The AI provider's automatic safety check flagged this request as possible "
    "biological risk and stopped this extraction run. No finalized extraction "
    "or TSV was produced by the stopped run; internal staging may have occurred. "
    "Please report the paper using the feedback button so we can follow up. "
    "Do not retry this blocked request."
)


def is_provider_content_policy_refusal(error: BaseException) -> bool:
    """Recognize the provider's code, never infer policy from message text."""
    return isinstance(error, ResponsesWebSocketError) and error.code == "bio_policy"


def provider_policy_error(error: BaseException) -> BaseException | None:
    """Find a typed policy refusal through SDK cause/context wrappers."""
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if is_provider_content_policy_refusal(current):
            return current
        current = current.__cause__ or current.__context__
    return None


def chat_policy_failure() -> dict:
    """Safe terminal outcome for the supervisor and curator event stream."""
    return {
        "status": "error",
        "failure_category": "provider_content_policy",
        "provider": "openai",
        "error_code": "bio_policy",
        "retryable": False,
        "message": PROVIDER_POLICY_CHAT_MESSAGE,
    }


def report_chat_policy_failure(
    error: BaseException, *, tool_name: str | None,
    trace_id: str | None, session_id: str | None,
) -> None:
    """Own one sanitized terminal report per provider failure, including wrappers."""
    policy_error = provider_policy_error(error)
    if policy_error is None or getattr(policy_error, "_ai_curation_sentry_captured", False):
        return
    captured = report_runtime_exception(
        sanitized_runtime_error("AI provider safety check stopped extraction (bio_policy)"),
        component="chat",
        operation="provider_policy_failure",
        tags={
            "provider": "openai",
            "failure_category": "provider_content_policy",
            "tool_name": tool_name,
            "ai_curation.trace.id_hash": hash_sentry_identifier(trace_id),
            "ai_curation.chat.session_id_hash": hash_sentry_identifier(session_id),
        },
        context={"error_code": "bio_policy", "retryable": False},
    )
    if captured:
        # Reuse the common Sentry duplicate guard for subsequent SDK wrappers/logs.
        setattr(policy_error, "_ai_curation_sentry_captured", True)
        setattr(error, "_ai_curation_sentry_captured", True)

"""Runtime alert facade for infrastructure and tool-call failures."""

import importlib
import logging
from typing import Any, Optional


logger = logging.getLogger(__name__)


def _sentry_extra(
    *,
    error_type: str,
    source: str,
    specialist_name: Optional[str],
    trace_id: Optional[str],
    session_id: Optional[str],
) -> dict[str, Any]:
    """Build Sentry-safe structured context for a runtime alert."""

    return {
        "error_type": error_type or "UnknownError",
        "source": source or "unknown",
        "tool_name": specialist_name or "N/A",
        "trace_id": trace_id or None,
        "session_id": session_id or None,
    }


def _capture_tool_failure_to_sentry(
    *,
    error_type: str,
    source: str,
    specialist_name: Optional[str],
    trace_id: Optional[str],
    session_id: Optional[str],
) -> bool:
    """Best-effort Sentry capture for caught runtime failures."""

    try:
        sentry_sdk = importlib.import_module("sentry_sdk")
    except Exception as exc:
        logger.warning("Sentry SDK unavailable for tool failure capture: %s", exc)
        return False

    tool_name = specialist_name or "N/A"
    extra = _sentry_extra(
        error_type=error_type,
        source=source,
        specialist_name=specialist_name,
        trace_id=trace_id,
        session_id=session_id,
    )

    try:
        if not sentry_sdk.is_initialized():
            return False
        with sentry_sdk.new_scope() as scope:
            scope.set_level("error")
            scope.set_tag("alert_type", "tool_failure")
            scope.set_tag("source", source or "unknown")
            scope.set_tag("tool_name", tool_name)
            if trace_id:
                scope.set_tag("trace_id", trace_id)
            if session_id:
                scope.set_tag("session_id", session_id)
            scope.set_context("runtime_alert", extra)
            event_id = sentry_sdk.capture_message(
                f"Tool failure: {error_type or 'UnknownError'} ({tool_name})",
                level="error",
            )
        return bool(event_id)
    except Exception as exc:
        logger.warning("Failed to capture tool failure in Sentry: %s", exc)
        return False


async def notify_tool_failure(
    error_type: str,
    error_message: str,
    source: str,
    specialist_name: Optional[str],
    trace_id: Optional[str],
    session_id: Optional[str],
    curator_id: Optional[str],
    context: Optional[str] = None,
    capture_sentry: bool = True,
) -> bool:
    """Queue sanitized metadata in Sentry, never publish to SNS.

    True means the SDK returned a capture ID, not verified ingestion or operator
    delivery. Raw error/context and curator identity are intentionally excluded.
    An owning runtime boundary can disable capture to avoid duplicate reporting.
    """
    if not capture_sentry:
        return False
    return _capture_tool_failure_to_sentry(
        error_type=error_type,
        source=source,
        specialist_name=specialist_name,
        trace_id=trace_id,
        session_id=session_id,
    )

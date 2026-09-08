"""TraceReview-owned, metadata-only, best-effort Sentry reporting.

A dedicated client has no automatic integrations and never receives exceptions,
request data, or Langfuse payloads. Session scopes retain counts, not trace lists.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import logging
import os
from urllib.parse import urlsplit

import sentry_sdk

logger = logging.getLogger(__name__)
_client = None
_session_failures: ContextVar[dict | None] = ContextVar("session_failures", default=None)
_OPERATIONS = frozenset({
    "scores", "search", "session_listing", "session_export", "extraction",
    "analysis", "feedback_transport", "feedback_http", "feedback_json",
    "feedback_payload", "session_missing_trace_id",
})


def _before_send(event, hint):
    """Discard SDK/scope enrichment, including breadcrumbs and request context."""
    return {key: event[key] for key in (
        "event_id", "timestamp", "level", "message", "fingerprint", "tags",
        "release", "environment",
    ) if key in event} | {
        "contexts": {"trace_review": event.get("contexts", {}).get("trace_review", {})}
    }


def initialize_sentry() -> None:
    """Enable only for an explicitly configured HTTPS TraceReview project DSN."""
    global _client
    try:
        dsn = os.getenv("TRACE_REVIEW_SENTRY_DSN", "").strip()
        if not dsn:
            return
        if urlsplit(dsn).scheme != "https":
            raise ValueError("TraceReview Sentry requires HTTPS")
        _client = sentry_sdk.Client(
            dsn=dsn,
            environment=os.getenv("TRACE_REVIEW_SENTRY_ENVIRONMENT", "local"),
            release=os.getenv("TRACE_REVIEW_SENTRY_RELEASE") or None,
            default_integrations=False,
            auto_enabling_integrations=False,
            send_default_pii=False,
            include_local_variables=False,
            before_send=_before_send,
        )
    except Exception:
        logger.warning("TraceReview Sentry initialization failed")


def close_sentry() -> None:
    global _client
    client, _client = _client, None
    try:
        if client is not None:
            client.close()
    except Exception:
        logger.warning("TraceReview Sentry shutdown failed")


def report_failure(operation: str, *, source: str = "remote", trace_id: str | None = None,
                   session_id: str | None = None, feedback_id: str | None = None) -> None:
    """Report a fixed category; never accept exception text or arbitrary context."""
    try:
        if operation not in _OPERATIONS:
            return
        failures = _session_failures.get()
        if failures is not None:
            failures[operation] = failures.get(operation, 0) + 1
            return
        _capture(operation, source, {"trace_id": trace_id, "session_id": session_id,
                                    "feedback_id": feedback_id}, {})
    except Exception:
        logger.warning("TraceReview Sentry reporting failed")


def _capture(operation, source, identifiers, counts):
    if _client is None:
        return
    context = {f"{key}_hash": hashlib.sha256(value.encode()).hexdigest()
               for key, value in identifiers.items() if value}
    context.update({f"{key}_failures": value for key, value in counts.items()})
    _client.capture_event({
        "level": "error",
        "message": f"TraceReview {operation} failed",
        "fingerprint": ["trace_review", operation],
        "tags": {"component": "trace_review", "operation": operation,
                 "source": source if source in {"remote", "local", "auto"} else "unknown"},
        "contexts": {"trace_review": context},
    }, scope=sentry_sdk.Scope())


@contextmanager
def session_failure_scope(session_id: str, source: str):
    """Coalesce score/extraction/analysis failures into one session event."""
    failures = {}
    token = _session_failures.set(failures)
    try:
        yield
    finally:
        _session_failures.reset(token)
        try:
            if failures:
                _capture("session_export", source, {"session_id": session_id}, failures)
        except Exception:
            logger.warning("TraceReview Sentry reporting failed")

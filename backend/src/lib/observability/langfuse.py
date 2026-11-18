"""Content-free reporting for best-effort Langfuse lifecycle operations."""

import logging

from src.lib.observability import runtime

logger = logging.getLogger(__name__)


def report_langfuse_failure(operation: str, exc: Exception | None = None) -> None:
    """Report a configured lifecycle failure without interrupting the caller."""
    context = {"type": type(exc).__name__} if exc is not None else {}
    message = f"Langfuse tracing operation failed ({operation})"
    try:
        runtime.report_runtime_exception(
            runtime.sanitized_runtime_error(message),
            component="langfuse",
            operation=operation,
            context=context,
            fingerprint=["langfuse", operation],
        )
    except Exception:
        # A reporter outage must not change worker execution or shutdown.
        pass
    logger.warning("%s %s", message, context, extra={"sentry_skip_event": True})

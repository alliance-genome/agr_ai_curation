"""Content-free reporting for best-effort Langfuse lifecycle operations."""

import logging
from contextlib import contextmanager
from contextvars import ContextVar

from src.lib.observability import runtime

logger = logging.getLogger(__name__)
_owned_logger: ContextVar[str | None] = ContextVar("langfuse_owned_logger", default=None)


def _mark_owned_sdk_log(record: logging.LogRecord) -> bool:
    if record.name == _owned_logger.get():
        record.sentry_skip_event = True
    return True


@contextmanager
def own_langfuse_sdk_logs(logger_name: str):
    """Keep companion SDK logs from becoming duplicate Sentry events."""
    # addFilter is idempotent. The context distinguishes other threads and
    # other OTLP exporters using the very same logger.
    logging.getLogger(logger_name).addFilter(_mark_owned_sdk_log)
    token = _owned_logger.set(logger_name)
    try:
        yield
    finally:
        _owned_logger.reset(token)


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

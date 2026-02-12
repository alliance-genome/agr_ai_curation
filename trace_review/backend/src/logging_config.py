"""Centralized logging configuration for the Trace Review backend.

Slim variant of the main backend's logging_config.py, providing structured
JSON output for GELF/Kibana and human-readable output for local dev.

Usage:
    from src.logging_config import configure_logging
    configure_logging()
"""

import json
import logging
import os
import sys
import traceback
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Dict, Optional


request_id_var: ContextVar[Optional[str]] = ContextVar("request_id", default=None)


class ContextFilter(logging.Filter):
    """Inject request_id into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id") or record.request_id is None:  # type: ignore[union-attr]
            record.request_id = request_id_var.get()  # type: ignore[attr-defined]
        return True


class JsonFormatter(logging.Formatter):
    """Format log records as single-line JSON objects."""

    _SKIP_FIELDS = {
        "args", "created", "exc_info", "exc_text", "filename",
        "funcName", "levelname", "levelno", "lineno", "module",
        "msecs", "msg", "name", "pathname", "process", "processName",
        "relativeCreated", "stack_info", "thread", "threadName",
        "taskName", "request_id",
    }

    def format(self, record: logging.LogRecord) -> str:
        log_entry: Dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", None),
        }

        if record.exc_info and record.exc_info[1] is not None:
            log_entry["exc_info"] = "".join(traceback.format_exception(*record.exc_info))

        for key, value in record.__dict__.items():
            if key in self._SKIP_FIELDS or key in log_entry:
                continue
            try:
                json.dumps(value)
                log_entry[key] = value
            except (TypeError, ValueError):
                log_entry[key] = str(value)

        return json.dumps(log_entry, default=str)


class SimpleFormatter(logging.Formatter):
    """Human-readable format for local development."""

    def format(self, record: logging.LogRecord) -> str:
        req_id = getattr(record, "request_id", None)
        ctx_suffix = f" [request_id={req_id[:12]}]" if req_id else ""

        timestamp = datetime.fromtimestamp(record.created, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        base = f"{timestamp} {record.levelname:<8} {record.name} - {record.getMessage()}{ctx_suffix}"

        if record.exc_info and record.exc_info[1] is not None:
            base += "\n" + "".join(traceback.format_exception(*record.exc_info))

        return base


def configure_logging() -> None:
    """Configure root logging for the trace_review application."""
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    log_format = os.getenv("LOG_FORMAT", "").lower()
    if not log_format:
        log_format = "json"

    handler = logging.StreamHandler(sys.stdout)
    if log_format == "simple":
        handler.setFormatter(SimpleFormatter())
    else:
        handler.setFormatter(JsonFormatter())
    handler.addFilter(ContextFilter())

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.handlers.clear()
    root_logger.addHandler(handler)

    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def create_request_context_middleware(app) -> None:
    """Register request correlation middleware on a FastAPI app."""
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request
    from starlette.responses import Response

    class RequestContextMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next) -> Response:
            req_id = request.headers.get("X-Request-ID", uuid.uuid4().hex[:8])
            request_id_var.set(req_id)

            response = await call_next(request)
            response.headers["X-Request-ID"] = req_id
            return response

    app.add_middleware(RequestContextMiddleware)

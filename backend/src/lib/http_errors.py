"""Helpers for client-safe HTTP errors with detailed server-side logging."""

from __future__ import annotations

import logging
from typing import NoReturn

from fastapi import HTTPException

from src.lib.observability.runtime import report_runtime_exception


def _report_sanitized_http_exception(
    logger: logging.Logger,
    *,
    status_code: int,
    exc: Exception,
    level: int,
) -> bool:
    """Best-effort Sentry capture for caught server errors converted to HTTP errors."""

    if status_code < 500:
        return False

    try:
        return report_runtime_exception(
            exc,
            component="api",
            operation="sanitized_http_exception",
            context={
                "logger_name": logger.name,
                "status_code": status_code,
                "log_level": level,
                "level_name": logging.getLevelName(level),
            },
        )
    except Exception as report_exc:
        logger.warning("Failed to report sanitized HTTP exception to Sentry: %s", report_exc)
        return False


def log_exception(
    logger: logging.Logger,
    *,
    message: str,
    exc: Exception,
    level: int = logging.ERROR,
) -> None:
    """Log an exception with traceback so client responses can stay sanitized."""

    logger.log(level, message, exc_info=(type(exc), exc, exc.__traceback__))


def raise_sanitized_http_exception(
    logger: logging.Logger,
    *,
    status_code: int,
    detail: object,
    log_message: str,
    exc: Exception,
    level: int = logging.ERROR,
) -> NoReturn:
    """Log the underlying exception and raise a client-safe HTTPException."""

    _report_sanitized_http_exception(
        logger,
        status_code=status_code,
        exc=exc,
        level=level,
    )
    log_exception(logger, message=log_message, exc=exc, level=level)
    raise HTTPException(status_code=status_code, detail=detail) from exc

"""Helpers for client-safe HTTP errors with detailed server-side logging."""

from __future__ import annotations

import logging
from typing import NoReturn

from fastapi import HTTPException


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

    log_exception(logger, message=log_message, exc=exc, level=level)
    raise HTTPException(status_code=status_code, detail=detail) from exc

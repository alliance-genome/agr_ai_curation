"""Tests for sanitized HTTP error helpers."""

from __future__ import annotations

import logging

import pytest
from fastapi import HTTPException

from src.lib import http_errors


def test_raise_sanitized_http_exception_reports_5xx_to_runtime_observability(monkeypatch):
    calls = []
    logger = logging.getLogger("src.api.documents")
    exc = RuntimeError("database detail")

    def _fake_report_runtime_exception(reported_exc, **kwargs):
        calls.append((reported_exc, kwargs))
        return True

    monkeypatch.setattr(http_errors, "report_runtime_exception", _fake_report_runtime_exception)

    with pytest.raises(HTTPException) as raised:
        http_errors.raise_sanitized_http_exception(
            logger,
            status_code=500,
            detail="Failed to list documents",
            log_message="Error listing documents",
            exc=exc,
            level=logging.ERROR,
        )

    assert raised.value.status_code == 500
    assert raised.value.detail == "Failed to list documents"
    assert calls == [
        (
            exc,
            {
                "component": "api",
                "operation": "sanitized_http_exception",
                "context": {
                    "logger_name": "src.api.documents",
                    "status_code": 500,
                    "log_level": logging.ERROR,
                    "level_name": "ERROR",
                },
            },
        )
    ]


def test_raise_sanitized_http_exception_skips_4xx_runtime_reporting(monkeypatch):
    logger = logging.getLogger("src.api.documents")

    def _fake_report_runtime_exception(*args, **kwargs):
        raise AssertionError("4xx sanitized errors should not report to Sentry")

    monkeypatch.setattr(http_errors, "report_runtime_exception", _fake_report_runtime_exception)

    with pytest.raises(HTTPException) as raised:
        http_errors.raise_sanitized_http_exception(
            logger,
            status_code=400,
            detail="Invalid request",
            log_message="Invalid document upload",
            exc=ValueError("bad input"),
            level=logging.WARNING,
        )

    assert raised.value.status_code == 400
    assert raised.value.detail == "Invalid request"


def test_raise_sanitized_http_exception_is_best_effort_if_runtime_reporting_fails(
    monkeypatch,
):
    logger = logging.getLogger("src.api.documents")

    def _fake_report_runtime_exception(*args, **kwargs):
        raise RuntimeError("sentry unavailable")

    monkeypatch.setattr(http_errors, "report_runtime_exception", _fake_report_runtime_exception)

    with pytest.raises(HTTPException) as raised:
        http_errors.raise_sanitized_http_exception(
            logger,
            status_code=503,
            detail="Service unavailable",
            log_message="Worker unavailable",
            exc=RuntimeError("worker detail"),
        )

    assert raised.value.status_code == 503
    assert raised.value.detail == "Service unavailable"

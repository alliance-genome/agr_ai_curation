"""Tests for Agent Studio trace context API error handling."""

import asyncio

import pytest
from fastapi import HTTPException

from src.api import agent_studio as api_module
from src.lib import http_errors
from src.lib.agent_studio import trace_context_service


VALID_TRACE_ID = "01784cd8-7512-4830-b5f5-a427502ab923"


def test_get_trace_context_rejects_invalid_trace_id_without_reporting(monkeypatch):
    calls = []

    def _fake_report_runtime_exception(exc, **kwargs):
        calls.append((exc, kwargs))
        return True

    monkeypatch.setattr(http_errors, "report_runtime_exception", _fake_report_runtime_exception)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(api_module.get_trace_context("not-a-trace-id", user={"sub": "test"}))

    assert exc_info.value.status_code == 400
    assert "Invalid trace ID format" in str(exc_info.value.detail)
    assert calls == []


def test_get_trace_context_reports_extraction_failures(monkeypatch):
    calls = []

    async def _raise_trace_context_error(_trace_id):
        raise trace_context_service.TraceContextError("trace export failed")

    def _fake_report_runtime_exception(exc, **kwargs):
        calls.append((exc, kwargs))
        return True

    monkeypatch.setattr(
        trace_context_service,
        "get_trace_context_for_explorer",
        _raise_trace_context_error,
    )
    monkeypatch.setattr(http_errors, "report_runtime_exception", _fake_report_runtime_exception)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(api_module.get_trace_context(VALID_TRACE_ID, user={"sub": "test"}))

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Failed to extract trace context"
    assert len(calls) == 1
    assert isinstance(calls[0][0], trace_context_service.TraceContextError)
    assert calls[0][1]["component"] == "api"
    assert calls[0][1]["operation"] == "sanitized_http_exception"


def test_get_trace_context_reports_unexpected_failures(monkeypatch):
    calls = []

    async def _raise_unexpected(_trace_id):
        raise RuntimeError("trace service exploded")

    def _fake_report_runtime_exception(exc, **kwargs):
        calls.append((exc, kwargs))
        return True

    monkeypatch.setattr(
        trace_context_service,
        "get_trace_context_for_explorer",
        _raise_unexpected,
    )
    monkeypatch.setattr(http_errors, "report_runtime_exception", _fake_report_runtime_exception)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(api_module.get_trace_context(VALID_TRACE_ID, user={"sub": "test"}))

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Internal server error"
    assert len(calls) == 1
    assert isinstance(calls[0][0], RuntimeError)
    assert calls[0][1]["component"] == "api"
    assert calls[0][1]["operation"] == "sanitized_http_exception"

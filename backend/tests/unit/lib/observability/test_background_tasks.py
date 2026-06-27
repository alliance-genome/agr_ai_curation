"""Tests for observed FastAPI background task helpers."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from src.lib.observability import background_tasks


@pytest.fixture
def fake_sentry(monkeypatch):
    calls = {"exceptions": [], "tags": [], "contexts": [], "levels": []}

    class _Scope:
        def set_level(self, level):
            calls["levels"].append(level)

        def set_tag(self, key, value):
            calls["tags"].append((key, value))

        def set_context(self, key, value):
            calls["contexts"].append((key, value))

    class _ScopeManager:
        def __enter__(self):
            return _Scope()

        def __exit__(self, exc_type, exc, tb):
            return False

    fake_sdk = SimpleNamespace(
        new_scope=lambda: _ScopeManager(),
        capture_exception=lambda exc: calls["exceptions"].append(exc),
    )

    def _fake_import(name):
        if name == "sentry_sdk":
            return fake_sdk
        raise ImportError(name)

    monkeypatch.setattr(background_tasks.importlib, "import_module", _fake_import)
    return calls


def test_observed_sync_background_task_reports_and_reraises(fake_sentry):
    def _boom():
        raise RuntimeError("raw curator detail")

    wrapped = background_tasks.observed_background_task(
        _boom,
        task_name="sync.task",
        tags={"component": "unit", "document_id": "doc-1"},
        observability_context={"raw_text": "should be scrubbed by Sentry before_send"},
    )

    with pytest.raises(RuntimeError):
        wrapped()

    assert getattr(wrapped, "__observability_original_task__") is _boom
    assert getattr(wrapped, "__observability_task_name__") == "sync.task"
    assert fake_sentry["exceptions"]
    assert ("alert_type", "background_task_failure") in fake_sentry["tags"]
    assert ("task_name", "sync.task") in fake_sentry["tags"]
    assert ("document_id", "doc-1") in fake_sentry["tags"]


def test_observed_async_background_task_reports_and_reraises(fake_sentry):
    async def _boom():
        raise RuntimeError("async failure")

    wrapped = background_tasks.observed_background_task(
        _boom,
        task_name="async.task",
        tags={"component": "unit"},
    )

    with pytest.raises(RuntimeError):
        asyncio.run(wrapped())

    assert getattr(wrapped, "__observability_original_task__") is _boom
    assert fake_sentry["exceptions"]
    assert ("task_name", "async.task") in fake_sentry["tags"]


def test_report_background_task_exception_is_best_effort_when_sentry_missing(monkeypatch):
    def _raise_import(_name):
        raise ImportError("no sentry")

    monkeypatch.setattr(background_tasks.importlib, "import_module", _raise_import)

    result = background_tasks.report_background_task_exception(
        RuntimeError("boom"),
        task_name="missing.sentry",
    )

    assert result is False

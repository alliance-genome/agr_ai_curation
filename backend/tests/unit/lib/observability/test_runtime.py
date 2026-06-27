"""Tests for generic runtime exception observability helpers."""

from __future__ import annotations

from types import SimpleNamespace

from src.lib.observability import runtime


def test_report_runtime_exception_captures_with_safe_tags_and_context(monkeypatch):
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

    monkeypatch.setattr(runtime.importlib, "import_module", _fake_import)
    exc = RuntimeError("raw curator detail")

    reported = runtime.report_runtime_exception(
        exc,
        component="executable_run",
        operation="producer_failed",
        tags={"run_kind": "assistant_chat_turn"},
        context={"session_id": "session-1", "attempt": 2},
    )

    assert reported is True
    assert calls["exceptions"] == [exc]
    assert calls["levels"] == ["error"]
    assert ("alert_type", "runtime_exception") in calls["tags"]
    assert ("runtime_component", "executable_run") in calls["tags"]
    assert ("operation", "producer_failed") in calls["tags"]
    assert ("run_kind", "assistant_chat_turn") in calls["tags"]
    assert calls["contexts"] == [
        (
            "runtime_exception",
            {
                "component": "executable_run",
                "operation": "producer_failed",
                "session_id": "session-1",
                "attempt": 2,
            },
        )
    ]


def test_report_runtime_exception_is_best_effort_when_sdk_missing(monkeypatch):
    monkeypatch.setattr(
        runtime.importlib,
        "import_module",
        lambda name: (_ for _ in ()).throw(RuntimeError("missing sdk")),
    )

    assert (
        runtime.report_runtime_exception(
            RuntimeError("boom"),
            component="unit",
            operation="missing_sdk",
        )
        is False
    )

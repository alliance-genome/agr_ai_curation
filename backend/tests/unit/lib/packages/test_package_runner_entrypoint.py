"""Unit tests for package_runner_entrypoint request context hydration."""

from __future__ import annotations

import importlib
from types import SimpleNamespace

from src.lib.packages import package_runner_entrypoint


def test_apply_backend_request_context_sets_runtime_context(monkeypatch):
    calls: list[tuple[str, str | None]] = []
    fake_context_module = SimpleNamespace(
        clear_context=lambda: calls.append(("clear_context", None)),
        set_current_trace_id=lambda value: calls.append(("trace_id", value)),
        set_current_session_id=lambda value: calls.append(("session_id", value)),
        set_current_user_id=lambda value: calls.append(("user_id", value)),
        set_current_output_filename_stem=lambda value: calls.append(
            ("output_filename_stem", value)
        ),
    )
    original_import_module = importlib.import_module

    def _fake_import_module(name: str, package: str | None = None):
        if name == "src.lib.context":
            return fake_context_module
        return original_import_module(name, package)

    monkeypatch.setattr(importlib, "import_module", _fake_import_module)

    package_runner_entrypoint._apply_backend_request_context(
        {
            "trace_id": "trace-123",
            "session_id": "session-456",
            "user_id": "user-789",
            "output_filename_stem": "final_findings",
        }
    )

    assert calls == [
        ("clear_context", None),
        ("trace_id", "trace-123"),
        ("session_id", "session-456"),
        ("user_id", "user-789"),
        ("output_filename_stem", "final_findings"),
    ]


def test_apply_backend_request_context_ignores_blank_values(monkeypatch):
    calls: list[tuple[str, str | None]] = []
    fake_context_module = SimpleNamespace(
        clear_context=lambda: calls.append(("clear_context", None)),
        set_current_trace_id=lambda value: calls.append(("trace_id", value)),
        set_current_session_id=lambda value: calls.append(("session_id", value)),
        set_current_user_id=lambda value: calls.append(("user_id", value)),
        set_current_output_filename_stem=lambda value: calls.append(
            ("output_filename_stem", value)
        ),
    )
    original_import_module = importlib.import_module

    def _fake_import_module(name: str, package: str | None = None):
        if name == "src.lib.context":
            return fake_context_module
        return original_import_module(name, package)

    monkeypatch.setattr(importlib, "import_module", _fake_import_module)

    package_runner_entrypoint._apply_backend_request_context(
        {
            "trace_id": " ",
            "session_id": None,
            "user_id": "",
            "output_filename_stem": "  ",
        }
    )

    assert calls == [("clear_context", None)]

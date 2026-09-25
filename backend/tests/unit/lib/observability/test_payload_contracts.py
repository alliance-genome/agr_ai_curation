"""Tests for the shared payload-contract violation reporting helper."""

from __future__ import annotations

import logging
from types import SimpleNamespace

from src.lib.observability import payload_contracts, runtime


def _fake_sentry(monkeypatch, *, event_id="event-id"):
    calls = {"exceptions": [], "tags": [], "contexts": [], "fingerprints": []}

    class _Scope:
        def set_level(self, level):
            pass

        def set_tag(self, key, value):
            calls["tags"].append((key, value))

        def set_context(self, key, value):
            calls["contexts"].append((key, value))

        def __setattr__(self, key, value):
            if key == "fingerprint":
                calls["fingerprints"].append(value)
            object.__setattr__(self, key, value)

    class _ScopeManager:
        def __enter__(self):
            return _Scope()

        def __exit__(self, exc_type, exc, tb):
            return False

    fake_sdk = SimpleNamespace(
        new_scope=lambda: _ScopeManager(),
        capture_exception=lambda exc: calls["exceptions"].append(exc) or event_id,
    )

    def _fake_import(name):
        if name == "sentry_sdk":
            return fake_sdk
        raise ImportError(name)

    monkeypatch.setattr(runtime.importlib, "import_module", _fake_import)
    return calls


def _violation():
    return payload_contracts.PayloadContractViolation(
        category="provider_request_blocked",
        component="openai_responses.instructions",
        message="instructions exceed the provider limit",
        measured=1_422_809,
        limit=1_048_576,
        setting="OPENAI_INSTRUCTIONS_MAX_CHARS",
        field="instructions",
    )


def test_reports_compact_context_with_stable_fingerprint(monkeypatch, caplog):
    calls = _fake_sentry(monkeypatch)
    violation = _violation()

    with caplog.at_level(logging.WARNING, logger=payload_contracts.__name__):
        captured = payload_contracts.report_payload_contract_violation(
            violation,
            phase="flow_chat_output",
            provider="openai",
            model="gpt-5.4",
            agent="chat_output",
            trace_id="trace-123",
            session_id="session-456",
            correlation={"flow_run_id": "run-789"},
        )

    assert captured is True
    assert calls["exceptions"] == [violation]
    assert calls["fingerprints"] == [
        ["payload_contract", "provider_request_blocked", "openai_responses.instructions"]
    ]
    tags = dict(calls["tags"])
    assert tags["failure_category"] == "provider_request_blocked"
    assert tags["runtime_component"] == "payload_contract.openai_responses.instructions"
    assert tags["phase"] == "flow_chat_output"
    assert tags["provider"] == "openai"
    assert tags["ai_curation.trace.id_hash"].startswith("sha256:")
    assert "trace-123" not in tags.values()
    context = dict(calls["contexts"])["runtime_exception"]
    assert context["measured"] == 1_422_809
    assert context["limit"] == 1_048_576
    assert context["setting"] == "OPENAI_INSTRUCTIONS_MAX_CHARS"
    assert context["model"] == "gpt-5.4"
    assert context["flow_run_id"] == "run-789"
    assert "payload contract violation category=provider_request_blocked" in caplog.text


def test_captures_once_across_wrapper_boundaries(monkeypatch):
    calls = _fake_sentry(monkeypatch)
    violation = _violation()

    assert payload_contracts.report_payload_contract_violation(violation) is True
    assert payload_contracts.report_payload_contract_violation(violation) is False

    wrapper = RuntimeError("step failed")
    wrapper.__cause__ = violation
    assert (
        runtime.report_runtime_exception(wrapper, component="flow", operation="step")
        is False
    )
    assert calls["exceptions"] == [violation]


def test_sentry_unavailable_keeps_structured_log(monkeypatch, caplog):
    monkeypatch.setattr(
        runtime.importlib,
        "import_module",
        lambda name: (_ for _ in ()).throw(RuntimeError("missing sdk")),
    )
    violation = _violation()

    with caplog.at_level(logging.WARNING, logger=payload_contracts.__name__):
        assert payload_contracts.report_payload_contract_violation(violation) is False

    assert "measured=1422809" in caplog.text
    assert not getattr(violation, "_ai_curation_sentry_captured", False)


def test_diagnostic_is_compact_and_actionable():
    assert _violation().diagnostic() == {
        "category": "provider_request_blocked",
        "component": "openai_responses.instructions",
        "field": "instructions",
        "measured": 1_422_809,
        "unit": "characters",
        "limit": 1_048_576,
        "setting": "OPENAI_INSTRUCTIONS_MAX_CHARS",
        "message": "instructions exceed the provider limit",
    }


def test_caught_tool_result_budget_escape_reports_once_with_bounded_context(monkeypatch):
    """A tool that turns its budget escape into a tool-result error still reports it once."""
    from src.lib.observability import sentry as sentry_module

    calls = _fake_sentry(monkeypatch)
    violation = payload_contracts.PayloadContractViolation(
        category="tool_result_budget_escape",
        component="inspect_output_rows",
        message="tool result exceeds its response budget",
        measured=1_276_154,
        limit=60_000,
        setting="EXAMPLE_TOOL_RESULT_MAX_CHARS",
    )

    def tool():
        try:
            raise violation
        except payload_contracts.PayloadContractViolation as exc:
            payload_contracts.report_payload_contract_violation(
                exc,
                phase="tool_result",
                tool_name="inspect_output_rows",
                correlation={"rows_preview": "r" * 50_000},
            )
            return {"success": False, "error": exc.diagnostic()}

    result = tool()
    assert result["error"]["category"] == "tool_result_budget_escape"
    assert calls["exceptions"] == [violation]
    context = dict(calls["contexts"])["runtime_exception"]
    # Oversized correlation values are bounded, never a second dataset copy.
    assert len(context["rows_preview"]) <= 500
    # A wrapper that later logs the same failure does not create a second event.
    record = logging.LogRecord("wrapper", logging.ERROR, __file__, 1, "tool failed: %s", (violation,), None)
    assert sentry_module.before_send({"message": "tool failed"}, {"log_record": record}) is None


def test_caught_finalization_failure_is_not_duplicated_by_wrappers(monkeypatch):
    from src.lib.observability import sentry as sentry_module

    calls = _fake_sentry(monkeypatch)
    violation = payload_contracts.PayloadContractViolation(
        category="output_delivery_failure",
        component="flow_chat_output",
        message="rendered output could not be delivered",
    )
    payload_contracts.report_payload_contract_violation(violation, phase="finalization")
    try:
        raise RuntimeError("flow finalization failed") from violation
    except RuntimeError as wrapper:
        assert runtime.report_runtime_exception(wrapper, component="flow", operation="finalize") is False
        assert sentry_module.before_send(
            {"message": "x"}, {"exc_info": (RuntimeError, wrapper, None)}
        ) is None
    assert calls["exceptions"] == [violation]

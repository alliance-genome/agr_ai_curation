"""Unit tests for Langfuse client helpers."""

import builtins
import logging
import os
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from src.lib.openai_agents import langfuse_client as lc


@pytest.fixture(autouse=True)
def _reset_langfuse_state(monkeypatch):
    monkeypatch.setattr(lc, "_langfuse_client", None)
    monkeypatch.setattr(lc, "_openai_agents_instrumented", False)
    lc.clear_pending_configs()
    # Initialization sets process-wide exporter variables for the SDK.
    with patch.dict(os.environ):
        yield
    monkeypatch.setattr(lc, "_langfuse_client", None)
    monkeypatch.setattr(lc, "_openai_agents_instrumented", False)
    lc.clear_pending_configs()


@pytest.fixture
def report(monkeypatch):
    capture = Mock()
    monkeypatch.setattr("src.lib.observability.runtime.report_runtime_exception", capture)
    return capture


def assert_failure(report, caplog, operation, error_type=None):
    report.assert_called_once()
    exc, = report.call_args.args
    assert exc.__context__ is None
    assert exc.__cause__ is None
    assert report.call_args.kwargs == {
        "component": "langfuse",
        "operation": operation,
        "fingerprint": ["langfuse", operation],
        "context": {"type": error_type} if error_type else {},
    }
    assert "synthetic-private" not in repr(report.call_args)
    assert "synthetic-private" not in caplog.text
    warnings = [record for record in caplog.records if record.levelno >= logging.WARNING]
    assert len([record for record in warnings if record.name == "src.lib.observability.langfuse"]) == 1
    assert all(record.sentry_skip_event and record.exc_info is None for record in warnings)


def test_otel_context_detach_filter_suppresses_expected_error():
    filt = lc.OTELContextDetachFilter()
    record = logging.LogRecord(
        name="opentelemetry.context",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="Failed to detach context from async task",
        args=(),
        exc_info=None,
    )
    assert filt.filter(record) is False

    other = logging.LogRecord(
        name="other.logger",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="Failed to detach context from async task",
        args=(),
        exc_info=None,
    )
    assert filt.filter(other) is True


def test_is_langfuse_configured_reflects_required_values(monkeypatch):
    monkeypatch.setattr(lc, "LANGFUSE_HOST", "http://langfuse:3000")
    monkeypatch.setattr(lc, "LANGFUSE_PUBLIC_KEY", "pub")
    monkeypatch.setattr(lc, "LANGFUSE_SECRET_KEY", "sec")
    assert lc.is_langfuse_configured() is True

    monkeypatch.setattr(lc, "LANGFUSE_SECRET_KEY", None)
    assert lc.is_langfuse_configured() is False


def test_initialize_langfuse_returns_none_when_not_configured(monkeypatch, report, caplog):
    monkeypatch.setattr(lc, "is_langfuse_configured", lambda: False)
    assert lc.initialize_langfuse() is None
    lc.flush_langfuse()
    report.assert_not_called()
    assert not any(record.levelno >= logging.WARNING for record in caplog.records)


def test_initialize_langfuse_success_sets_env_and_client(monkeypatch):
    instrument_calls = []

    class FakeLangfuse:
        def __init__(self, host, public_key, secret_key):
            self.host = host
            self.public_key = public_key
            self.secret_key = secret_key

        def auth_check(self):
            return True

    fake_module = ModuleType("langfuse")
    setattr(fake_module, "Langfuse", FakeLangfuse)
    monkeypatch.setitem(sys.modules, "langfuse", fake_module)

    monkeypatch.setattr(lc, "is_langfuse_configured", lambda: True)
    monkeypatch.setattr(lc, "LANGFUSE_HOST", "http://langfuse:3000")
    monkeypatch.setattr(lc, "LANGFUSE_PUBLIC_KEY", "pub")
    monkeypatch.setattr(lc, "LANGFUSE_SECRET_KEY", "sec")
    monkeypatch.setattr(
        lc,
        "_instrument_openai_agents_tracing",
        lambda: instrument_calls.append("instrumented"),
    )

    client = lc.initialize_langfuse()
    assert isinstance(client, FakeLangfuse)
    assert os.environ["LANGFUSE_HOST"] == "http://langfuse:3000"
    assert os.environ["LANGFUSE_PUBLIC_KEY"] == "pub"
    assert os.environ["LANGFUSE_SECRET_KEY"] == "sec"
    assert os.environ["LANGFUSE_BASEURL"] == "http://langfuse:3000"
    assert os.environ["OPENAI_AGENTS_TRACE_INCLUDE_SENSITIVE_DATA"] == "true"
    assert instrument_calls == ["instrumented"]


@pytest.mark.parametrize("auth_raises", [True, False])
def test_initialize_langfuse_auth_check_failure_is_non_fatal(monkeypatch, report, caplog, auth_raises):
    class FakeLangfuse:
        def __init__(self, **_kwargs):
            pass

        def auth_check(self):
            logging.getLogger("langfuse").warning("SDK authentication failed")
            if auth_raises:
                raise RuntimeError("synthetic-private credential")
            return False

    fake_module = ModuleType("langfuse")
    setattr(fake_module, "Langfuse", FakeLangfuse)
    monkeypatch.setitem(sys.modules, "langfuse", fake_module)

    monkeypatch.setattr(lc, "is_langfuse_configured", lambda: True)
    monkeypatch.setattr(lc, "LANGFUSE_HOST", "http://langfuse:3000")
    monkeypatch.setattr(lc, "LANGFUSE_PUBLIC_KEY", "pub")
    monkeypatch.setattr(lc, "LANGFUSE_SECRET_KEY", "sec")
    monkeypatch.setattr(lc, "_instrument_openai_agents_tracing", lambda: None)

    assert isinstance(lc.initialize_langfuse(), FakeLangfuse)
    assert_failure(report, caplog, "authentication_failed", "RuntimeError" if auth_raises else None)
    logging.getLogger("langfuse").warning("independent SDK warning")
    assert not getattr(caplog.records[-1], "sentry_skip_event", False)


def test_initialize_langfuse_handles_import_error(monkeypatch, report, caplog):
    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "langfuse":
            raise ImportError("synthetic-private path")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(lc, "is_langfuse_configured", lambda: True)
    monkeypatch.setattr(lc, "LANGFUSE_HOST", "http://langfuse:3000")
    monkeypatch.setattr(lc, "LANGFUSE_PUBLIC_KEY", "pub")
    monkeypatch.setattr(lc, "LANGFUSE_SECRET_KEY", "sec")
    monkeypatch.delitem(sys.modules, "langfuse", raising=False)
    monkeypatch.setattr(builtins, "__import__", _fake_import)

    assert lc.initialize_langfuse() is None
    assert_failure(report, caplog, "initialization_failed", "ImportError")


def test_failed_instrumentation_reports_once_and_keeps_client(monkeypatch, report, caplog):
    client = Mock()
    client.auth_check.return_value = True
    monkeypatch.setattr(lc, "is_langfuse_configured", lambda: True)
    monkeypatch.setitem(sys.modules, "langfuse", SimpleNamespace(Langfuse=lambda **kwargs: client))
    instrumentor = Mock(side_effect=ImportError("synthetic-private instrumentation error"))
    monkeypatch.setitem(
        sys.modules, "openinference.instrumentation.openai_agents",
        SimpleNamespace(OpenAIAgentsInstrumentor=instrumentor),
    )
    assert lc.initialize_langfuse() is client
    assert not lc.is_openai_agents_tracing_enabled()
    assert_failure(report, caplog, "instrumentation_failed", "ImportError")


def test_instrument_openai_agents_tracing_is_idempotent(monkeypatch):
    calls = []
    cost_installations = []
    monkeypatch.setattr(
        "src.lib.observability.cost_tracing.install_cost_tracing",
        lambda: cost_installations.append(True),
    )

    class FakeInstrumentor:
        def instrument(self, **kwargs):
            calls.append(kwargs)

    openinference_module = ModuleType("openinference")
    instrumentation_module = ModuleType("openinference.instrumentation")
    agents_module = ModuleType("openinference.instrumentation.openai_agents")
    setattr(agents_module, "OpenAIAgentsInstrumentor", FakeInstrumentor)
    monkeypatch.setitem(sys.modules, "openinference", openinference_module)
    monkeypatch.setitem(sys.modules, "openinference.instrumentation", instrumentation_module)
    monkeypatch.setitem(
        sys.modules,
        "openinference.instrumentation.openai_agents",
        agents_module,
    )

    lc._instrument_openai_agents_tracing()
    lc._instrument_openai_agents_tracing()
    assert lc.is_openai_agents_tracing_enabled() is True
    assert calls == [{"exclusive_processor": True}]
    assert cost_installations == [True]


@pytest.mark.parametrize("reporter_raises", [False, True])
def test_get_and_flush_langfuse_handles_flush_exception(monkeypatch, report, caplog, reporter_raises):
    class FakeClient:
        def __init__(self):
            self.flushed = False

        def flush(self):
            self.flushed = True
            raise RuntimeError("synthetic-private trace payload")

    client = FakeClient()
    monkeypatch.setattr(lc, "_langfuse_client", client)

    if reporter_raises:
        report.side_effect = ValueError("synthetic-private reporter error")
    assert lc.get_langfuse() is client
    lc.flush_langfuse()
    assert client.flushed is True
    assert_failure(report, caplog, "flush_failed", "RuntimeError")


def test_create_trace_success_and_failures(monkeypatch):
    class FakeClient:
        def trace(self, **kwargs):
            return {"trace": kwargs}

    monkeypatch.setattr(lc, "_langfuse_client", None)
    assert lc.create_trace("x") is None

    monkeypatch.setattr(lc, "_langfuse_client", FakeClient())
    created = lc.create_trace("my-trace", session_id="s1", user_id="u1", metadata={"a": 1}, tags=["t"])
    assert created is not None
    assert created["trace"]["name"] == "my-trace"
    assert created["trace"]["session_id"] == "s1"

    class RaisingClient:
        def trace(self, **_kwargs):
            raise RuntimeError("trace failed")

    monkeypatch.setattr(lc, "_langfuse_client", RaisingClient())
    assert lc.create_trace("bad") is None


def test_log_agent_config_and_flush_when_not_configured(monkeypatch):
    lc.log_agent_config(
        agent_name="PDF Specialist",
        instructions="Do extraction",
        model="gpt-5.4-mini",
        tools=["search"],
        model_settings={"temperature": 0},
        metadata={"document_id": 123},
    )
    assert len(lc._get_pending_configs()) == 1

    monkeypatch.setattr(lc, "_langfuse_client", None)
    count = lc.flush_agent_configs(SimpleNamespace(trace_id="trace-1", id="span-1"))
    assert count == 0
    assert lc._get_pending_configs() == []


def test_flush_agent_configs_no_pending_returns_zero(monkeypatch):
    class FakeClient:
        def create_event(self, **_kwargs):
            raise AssertionError("Should not be called")

    monkeypatch.setattr(lc, "_langfuse_client", FakeClient())
    lc.clear_pending_configs()

    assert lc.flush_agent_configs(SimpleNamespace(trace_id="trace-1", id="span-1")) == 0


def test_flush_agent_configs_partial_failure_counts_success(monkeypatch):
    class FakeClient:
        def __init__(self):
            self.calls = []

        def create_event(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 2:
                raise RuntimeError("boom")

    client = FakeClient()
    monkeypatch.setattr(lc, "_langfuse_client", client)

    exposure = {
        "active_group_ids": ["GROUP_ALPHA"],
        "base_tool_ids": ["search_document"],
        "added_tool_ids": ["group_alpha_helper"],
        "denied_tool_ids": [],
    }
    lc.log_agent_config(
        "Agent One",
        "i1",
        "m1",
        metadata={"group_tool_exposure": exposure},
    )
    lc.log_agent_config("Agent Two", "i2", "m2")

    count = lc.flush_agent_configs(SimpleNamespace(trace_id="trace-xyz", id="span-xyz"))
    assert count == 1
    assert len(client.calls) == 2
    assert "input" not in client.calls[0]
    assert "output" not in client.calls[0]
    assert client.calls[0]["metadata"]["agent_config"]["agent_name"] == "Agent One"
    assert client.calls[0]["metadata"]["agent_config"]["metadata"] == {
        "group_tool_exposure": exposure
    }
    assert lc._get_pending_configs() == []

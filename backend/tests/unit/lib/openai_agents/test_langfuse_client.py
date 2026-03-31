"""Unit tests for Langfuse client helpers."""

import builtins
import logging
import os
import sys
from types import ModuleType, SimpleNamespace

import pytest

from src.lib.openai_agents import langfuse_client as lc


@pytest.fixture(autouse=True)
def _reset_langfuse_state(monkeypatch):
    monkeypatch.setattr(lc, "_langfuse_client", None)
    lc.clear_pending_configs()
    yield
    monkeypatch.setattr(lc, "_langfuse_client", None)
    lc.clear_pending_configs()


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


def test_initialize_langfuse_returns_none_when_not_configured(monkeypatch):
    monkeypatch.setattr(lc, "is_langfuse_configured", lambda: False)
    assert lc.initialize_langfuse() is None


def test_initialize_langfuse_success_sets_env_and_client(monkeypatch):
    class FakeLangfuse:
        def __init__(self, host, public_key, secret_key):
            self.host = host
            self.public_key = public_key
            self.secret_key = secret_key

        def auth_check(self):
            return {"ok": True}

    fake_module = ModuleType("langfuse")
    fake_module.Langfuse = FakeLangfuse
    monkeypatch.setitem(sys.modules, "langfuse", fake_module)

    monkeypatch.setattr(lc, "is_langfuse_configured", lambda: True)
    monkeypatch.setattr(lc, "LANGFUSE_HOST", "http://langfuse:3000")
    monkeypatch.setattr(lc, "LANGFUSE_PUBLIC_KEY", "pub")
    monkeypatch.setattr(lc, "LANGFUSE_SECRET_KEY", "sec")

    client = lc.initialize_langfuse()
    assert isinstance(client, FakeLangfuse)
    assert os.environ["LANGFUSE_HOST"] == "http://langfuse:3000"
    assert os.environ["LANGFUSE_PUBLIC_KEY"] == "pub"
    assert os.environ["LANGFUSE_SECRET_KEY"] == "sec"
    assert os.environ["LANGFUSE_BASEURL"] == "http://langfuse:3000"


def test_initialize_langfuse_auth_check_failure_is_non_fatal(monkeypatch):
    class FakeLangfuse:
        def __init__(self, **_kwargs):
            pass

        def auth_check(self):
            raise RuntimeError("startup race")

    fake_module = ModuleType("langfuse")
    fake_module.Langfuse = FakeLangfuse
    monkeypatch.setitem(sys.modules, "langfuse", fake_module)

    monkeypatch.setattr(lc, "is_langfuse_configured", lambda: True)
    monkeypatch.setattr(lc, "LANGFUSE_HOST", "http://langfuse:3000")
    monkeypatch.setattr(lc, "LANGFUSE_PUBLIC_KEY", "pub")
    monkeypatch.setattr(lc, "LANGFUSE_SECRET_KEY", "sec")

    assert isinstance(lc.initialize_langfuse(), FakeLangfuse)


def test_initialize_langfuse_handles_import_error(monkeypatch):
    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "langfuse":
            raise ImportError("missing langfuse")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(lc, "is_langfuse_configured", lambda: True)
    monkeypatch.setattr(lc, "LANGFUSE_HOST", "http://langfuse:3000")
    monkeypatch.setattr(lc, "LANGFUSE_PUBLIC_KEY", "pub")
    monkeypatch.setattr(lc, "LANGFUSE_SECRET_KEY", "sec")
    monkeypatch.delitem(sys.modules, "langfuse", raising=False)
    monkeypatch.setattr(builtins, "__import__", _fake_import)

    assert lc.initialize_langfuse() is None


def test_get_and_flush_langfuse_handles_flush_exception(monkeypatch):
    class FakeClient:
        def __init__(self):
            self.flushed = False

        def flush(self):
            self.flushed = True
            raise RuntimeError("flush failed")

    client = FakeClient()
    monkeypatch.setattr(lc, "_langfuse_client", client)

    assert lc.get_langfuse() is client
    lc.flush_langfuse()
    assert client.flushed is True


def test_create_trace_success_and_failures(monkeypatch):
    class FakeClient:
        def trace(self, **kwargs):
            return {"trace": kwargs}

    monkeypatch.setattr(lc, "_langfuse_client", None)
    assert lc.create_trace("x") is None

    monkeypatch.setattr(lc, "_langfuse_client", FakeClient())
    created = lc.create_trace("my-trace", session_id="s1", user_id="u1", metadata={"a": 1}, tags=["t"])
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
        model="gpt-5.4-nano",
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

    lc.log_agent_config("Agent One", "i1", "m1")
    lc.log_agent_config("Agent Two", "i2", "m2")

    count = lc.flush_agent_configs(SimpleNamespace(trace_id="trace-xyz", id="span-xyz"))
    assert count == 1
    assert len(client.calls) == 2
    assert lc._get_pending_configs() == []

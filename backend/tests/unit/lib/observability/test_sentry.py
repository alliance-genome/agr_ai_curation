"""Tests for Sentry observability setup and redaction."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.lib.observability import sentry


@pytest.fixture(autouse=True)
def reset_sentry(monkeypatch):
    sentry._reset_sentry_for_tests()
    for key in (
        "SENTRY_DSN",
        "SENTRY_ENVIRONMENT",
        "SENTRY_RELEASE",
        "SENTRY_TRACES_SAMPLE_RATE",
        "SENTRY_PROFILES_SAMPLE_RATE",
        "SENTRY_ALLOW_INSECURE_DSN",
        "APP_ENV",
        "ENVIRONMENT",
        "GIT_SHA",
    ):
        monkeypatch.delenv(key, raising=False)
    yield
    sentry._reset_sentry_for_tests()


def test_get_sentry_settings_defaults_to_disabled():
    settings = sentry.get_sentry_settings()

    assert settings.dsn is None
    assert settings.environment == "local"
    assert settings.release is None
    assert settings.traces_sample_rate is None
    assert settings.profiles_sample_rate is None
    assert settings.allow_insecure_dsn is False


def test_get_sentry_settings_parses_env(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "https://public@example.invalid/1")
    monkeypatch.setenv("SENTRY_ENVIRONMENT", "dev")
    monkeypatch.setenv("SENTRY_RELEASE", "abc123")
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "0.25")
    monkeypatch.setenv("SENTRY_PROFILES_SAMPLE_RATE", "0.5")
    monkeypatch.setenv("SENTRY_ALLOW_INSECURE_DSN", "true")

    settings = sentry.get_sentry_settings()

    assert settings.dsn == "https://public@example.invalid/1"
    assert settings.environment == "dev"
    assert settings.release == "abc123"
    assert settings.traces_sample_rate == 0.25
    assert settings.profiles_sample_rate == 0.5
    assert settings.allow_insecure_dsn is True


def test_before_send_redacts_sensitive_and_document_content():
    event = {
        "message": "raw prompt leaked sk-testsecret0123456789",
        "request": {
            "url": "https://example.org/api/chat?token=secret&query=paper",
            "query_string": "token=secret&query=paper",
            "headers": {
                "Authorization": "synthetic-auth-header-placeholder",
                "X-Request-ID": "req-123",
            },
            "cookies": {"session": "cookie"},
            "data": {"prompt": "curator entered unpublished text"},
        },
        "extra": {
            "document_text": "PDF-derived text",
            "note": "unknown curator free text",
            "query": "paper search string",
            "safe_count": 3,
            "nested": {"api_key": "sk-abcdefghijklmnopqrstuvwxyz"},
        },
        "contexts": {"runtime": {"name": "python"}, "custom": {"payload": "free text"}},
        "breadcrumbs": {"values": [{"message": "search query", "data": {"q": "term"}}]},
        "exception": {
            "values": [
                {
                    "type": "RuntimeError",
                    "value": "contains raw text",
                    "stacktrace": {"frames": [{"filename": "app.py", "vars": {"x": "secret"}}]},
                }
            ]
        },
        "threads": {"values": [{"stacktrace": {"frames": [{"vars": {"prompt": "text"}}]}}]},
    }

    scrubbed = sentry.before_send(event)

    assert scrubbed["message"] == "[Filtered]"
    assert scrubbed["request"]["url"] == "https://example.org/api/chat"
    assert "query_string" not in scrubbed["request"]
    assert "cookies" not in scrubbed["request"]
    assert "data" not in scrubbed["request"]
    assert scrubbed["request"]["headers"]["Authorization"] == "[Filtered]"
    assert scrubbed["request"]["headers"]["X-Request-ID"] == "req-123"
    assert scrubbed["extra"]["document_text"] == "[Filtered]"
    assert scrubbed["extra"]["note"] == "[Filtered]"
    assert scrubbed["extra"]["query"] == "[Filtered]"
    assert scrubbed["extra"]["safe_count"] == 3
    assert scrubbed["extra"]["nested"]["api_key"] == "[Filtered]"
    assert scrubbed["contexts"]["runtime"]["name"] == "[Filtered]"
    assert scrubbed["contexts"]["custom"]["payload"] == "[Filtered]"
    assert "message" not in scrubbed["breadcrumbs"]["values"][0]
    assert "data" not in scrubbed["breadcrumbs"]["values"][0]
    assert scrubbed["exception"]["values"][0]["value"] == "[Filtered]"
    assert "vars" not in scrubbed["exception"]["values"][0]["stacktrace"]["frames"][0]
    assert "vars" not in scrubbed["threads"]["values"][0]["stacktrace"]["frames"][0]


def test_before_send_preserves_sentry_trace_context_and_redacts_custom_contexts():
    event = {
        "contexts": {
            "trace": {
                "trace_id": "0123456789abcdef0123456789abcdef",
                "span_id": "0123456789abcdef",
                "parent_span_id": "fedcba9876543210",
                "op": "queue.process",
                "status": "internal_error",
                "type": "trace",
                "description": "curator prompt text should not survive",
            },
            "background_task": {
                "task_name": "src.api.chat_common.generate_title_for_user_text",
                "curator_note": "free text",
            },
            "custom": {"payload": "curator entered unpublished text"},
        },
        "exception": {"values": [{"type": "RuntimeError", "value": "raw exception text"}]},
    }

    scrubbed = sentry.before_send(event)

    assert scrubbed["contexts"]["trace"] == {
        "trace_id": "0123456789abcdef0123456789abcdef",
        "span_id": "0123456789abcdef",
        "parent_span_id": "fedcba9876543210",
        "op": "queue.process",
        "status": "internal_error",
        "type": "trace",
    }
    assert "description" not in scrubbed["contexts"]["trace"]
    assert scrubbed["contexts"]["background_task"]["task_name"] == "[Filtered]"
    assert scrubbed["contexts"]["background_task"]["curator_note"] == "[Filtered]"
    assert scrubbed["contexts"]["custom"]["payload"] == "[Filtered]"
    assert scrubbed["exception"]["values"][0]["value"] == "[Filtered]"


def test_before_send_omits_malformed_trace_context_fields():
    event = {
        "contexts": {
            "trace": {
                "trace_id": "not-a-valid-trace-id",
                "span_id": "not-a-span",
                "parent_span_id": "fedcba9876543210",
                "op": "search curator prompt text",
                "origin": "auto.http.starlette",
                "status": "unknown",
                "type": "trace",
                "sampled": "yes",
                "client_sample_rate": 0.25,
                "exclusive_time": True,
            }
        }
    }

    scrubbed = sentry.before_send(event)

    assert scrubbed["contexts"]["trace"] == {
        "parent_span_id": "fedcba9876543210",
        "origin": "auto.http.starlette",
        "status": "unknown",
        "type": "trace",
        "client_sample_rate": 0.25,
    }


def test_before_send_transaction_uses_same_redaction_policy():
    event = {
        "transaction": "GET /api/chat",
        "request": {"url": "https://example.org/api/chat?prompt=raw"},
        "extra": {"payload": "curator free text", "attempt": 2},
        "contexts": {
            "trace": {
                "trace_id": "0123456789abcdef0123456789abcdef",
                "span_id": "0123456789abcdef",
                "op": "http.server",
                "status": "ok",
                "type": "trace",
            }
        },
        "spans": [
            {
                "trace_id": "0123456789abcdef0123456789abcdef",
                "span_id": "fedcba9876543210",
                "parent_span_id": "0123456789abcdef",
                "op": "db.query",
                "status": "ok",
                "description": "SELECT * FROM curator_prompt_text",
                "data": {"prompt": "raw curator text", "row_count": 2},
                "tags": {"paper_title": "free text"},
                "start_timestamp": 1.25,
                "timestamp": 1.75,
            },
            "unexpected span text",
        ],
    }

    scrubbed = sentry.before_send_transaction(event)

    assert scrubbed["request"]["url"] == "https://example.org/api/chat"
    assert scrubbed["extra"]["payload"] == "[Filtered]"
    assert scrubbed["extra"]["attempt"] == 2
    assert scrubbed["contexts"]["trace"]["trace_id"] == "0123456789abcdef0123456789abcdef"
    assert scrubbed["spans"][0] == {
        "trace_id": "0123456789abcdef0123456789abcdef",
        "span_id": "fedcba9876543210",
        "parent_span_id": "0123456789abcdef",
        "op": "db.query",
        "status": "ok",
        "description": "[Filtered]",
        "data": {"prompt": "[Filtered]", "row_count": 2},
        "tags": {"paper_title": "[Filtered]"},
        "start_timestamp": 1.25,
        "timestamp": 1.75,
    }
    assert len(scrubbed["spans"]) == 1


def test_initialize_sentry_skips_without_dsn(monkeypatch):
    imported = []

    def fake_import(name: str):
        imported.append(name)
        raise AssertionError("Sentry SDK should not be imported without DSN")

    monkeypatch.setattr(sentry.importlib, "import_module", fake_import)

    assert sentry.initialize_sentry_if_configured() is False
    assert imported == []


def test_initialize_sentry_refuses_http_dsn_by_default(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "http://public@example.invalid/1")

    assert sentry.initialize_sentry_if_configured() is False


def test_initialize_sentry_calls_sdk_with_safe_options(monkeypatch):
    calls = {}

    class FakeSentrySdk:
        def init(self, **kwargs):
            calls["init"] = kwargs

        def set_tag(self, key, value):
            calls.setdefault("tags", {})[key] = value

    class FakeIntegration:
        def __init__(self):
            calls.setdefault("integrations", 0)
            calls["integrations"] += 1

    fake_modules = {
        "sentry_sdk": FakeSentrySdk(),
        "sentry_sdk.integrations.fastapi": SimpleNamespace(FastApiIntegration=FakeIntegration),
        "sentry_sdk.integrations.starlette": SimpleNamespace(StarletteIntegration=FakeIntegration),
    }

    def fake_import(name: str):
        return fake_modules[name]

    monkeypatch.setattr(sentry.importlib, "import_module", fake_import)
    monkeypatch.setenv("SENTRY_DSN", "https://public@example.invalid/1")
    monkeypatch.setenv("SENTRY_ENVIRONMENT", "dev")
    monkeypatch.setenv("SENTRY_RELEASE", "release-1")
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "0.125")

    assert sentry.initialize_sentry_if_configured() is True

    init = calls["init"]
    assert init["dsn"] == "https://public@example.invalid/1"
    assert init["environment"] == "dev"
    assert init["release"] == "release-1"
    assert init["traces_sample_rate"] == 0.125
    assert init["before_send"] is sentry.before_send
    assert init["before_send_transaction"] is sentry.before_send_transaction
    assert init["include_local_variables"] is False
    assert init["send_default_pii"] is False
    assert len(init["integrations"]) == 2
    assert calls["tags"] == {"app": "ai-curation", "component": "backend"}


def test_initialize_sentry_omits_tracing_when_unset(monkeypatch):
    calls = {}

    class FakeSentrySdk:
        def init(self, **kwargs):
            calls["init"] = kwargs

        def set_tag(self, key, value):
            calls.setdefault("tags", {})[key] = value

    class FakeIntegration:
        pass

    fake_modules = {
        "sentry_sdk": FakeSentrySdk(),
        "sentry_sdk.integrations.fastapi": SimpleNamespace(FastApiIntegration=FakeIntegration),
        "sentry_sdk.integrations.starlette": SimpleNamespace(StarletteIntegration=FakeIntegration),
    }
    monkeypatch.setattr(sentry.importlib, "import_module", lambda name: fake_modules[name])
    monkeypatch.setenv("SENTRY_DSN", "https://public@example.invalid/1")

    assert sentry.initialize_sentry_if_configured() is True
    assert "traces_sample_rate" not in calls["init"]
    assert "profiles_sample_rate" not in calls["init"]


def test_initialize_sentry_sdk_init_failure_is_non_fatal(monkeypatch):
    class FakeSentrySdk:
        def init(self, **kwargs):
            raise ValueError("bad dsn")

        def set_tag(self, key, value):
            raise AssertionError("tags should not be set after init failure")

    class FakeIntegration:
        pass

    fake_modules = {
        "sentry_sdk": FakeSentrySdk(),
        "sentry_sdk.integrations.fastapi": SimpleNamespace(FastApiIntegration=FakeIntegration),
        "sentry_sdk.integrations.starlette": SimpleNamespace(StarletteIntegration=FakeIntegration),
    }
    monkeypatch.setattr(sentry.importlib, "import_module", lambda name: fake_modules[name])
    monkeypatch.setenv("SENTRY_DSN", "https://public@example.invalid/1")

    assert sentry.initialize_sentry_if_configured() is False


def test_initialize_sentry_import_failure_is_non_fatal(monkeypatch):
    def _broken_import(name: str):
        if name == "sentry_sdk":
            raise RuntimeError("broken sentry import")
        raise ImportError(name)

    monkeypatch.setattr(sentry.importlib, "import_module", _broken_import)
    monkeypatch.setenv("SENTRY_DSN", "https://public@example.invalid/1")

    assert sentry.initialize_sentry_if_configured() is False


def test_initialize_sentry_allows_http_only_with_explicit_flag(monkeypatch):
    calls = {}

    class FakeSentrySdk:
        def init(self, **kwargs):
            calls["dsn"] = kwargs["dsn"]

        def set_tag(self, key, value):
            calls.setdefault("tags", {})[key] = value

    class FakeIntegration:
        pass

    fake_modules = {
        "sentry_sdk": FakeSentrySdk(),
        "sentry_sdk.integrations.fastapi": SimpleNamespace(FastApiIntegration=FakeIntegration),
        "sentry_sdk.integrations.starlette": SimpleNamespace(StarletteIntegration=FakeIntegration),
    }
    monkeypatch.setattr(sentry.importlib, "import_module", lambda name: fake_modules[name])
    monkeypatch.setenv("SENTRY_DSN", "http://public@example.invalid/1")
    monkeypatch.setenv("SENTRY_ALLOW_INSECURE_DSN", "true")

    assert sentry.initialize_sentry_if_configured() is True
    assert calls["dsn"] == "http://public@example.invalid/1"

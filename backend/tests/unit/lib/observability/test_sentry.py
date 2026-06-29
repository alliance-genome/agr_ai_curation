"""Tests for Sentry observability setup and redaction."""

from __future__ import annotations

import hashlib
import logging
import subprocess
import sys
import textwrap
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
        "SENTRY_SEND_DEFAULT_PII",
        "SENTRY_AI_AGENTS_MONITORING_ENABLED",
        "SENTRY_OPENAI_AGENTS_INTEGRATION_ENABLED",
        "SENTRY_OPENAI_INTEGRATION_ENABLED",
        "SENTRY_GEN_AI_STREAM_SPANS_ENABLED",
        "SENTRY_OPENAI_INCLUDE_PROMPTS",
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
    assert settings.send_default_pii is False
    assert settings.ai_agents_monitoring_enabled is False
    assert settings.openai_agents_integration_enabled is False
    assert settings.openai_integration_enabled is False
    assert settings.gen_ai_stream_spans_enabled is False
    assert settings.openai_include_prompts is False


def test_get_sentry_settings_parses_env(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "https://public@example.invalid/1")
    monkeypatch.setenv("SENTRY_ENVIRONMENT", "dev")
    monkeypatch.setenv("SENTRY_RELEASE", "abc123")
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "0.25")
    monkeypatch.setenv("SENTRY_PROFILES_SAMPLE_RATE", "0.5")
    monkeypatch.setenv("SENTRY_ALLOW_INSECURE_DSN", "true")
    monkeypatch.setenv("SENTRY_SEND_DEFAULT_PII", "true")
    monkeypatch.setenv("SENTRY_AI_AGENTS_MONITORING_ENABLED", "true")
    monkeypatch.setenv("SENTRY_OPENAI_AGENTS_INTEGRATION_ENABLED", "true")
    monkeypatch.setenv("SENTRY_OPENAI_INTEGRATION_ENABLED", "true")
    monkeypatch.setenv("SENTRY_GEN_AI_STREAM_SPANS_ENABLED", "true")
    monkeypatch.setenv("SENTRY_OPENAI_INCLUDE_PROMPTS", "true")

    settings = sentry.get_sentry_settings()

    assert settings.dsn == "https://public@example.invalid/1"
    assert settings.environment == "dev"
    assert settings.release == "abc123"
    assert settings.traces_sample_rate == 0.25
    assert settings.profiles_sample_rate == 0.5
    assert settings.allow_insecure_dsn is True
    assert settings.send_default_pii is True
    assert settings.ai_agents_monitoring_enabled is True
    assert settings.openai_agents_integration_enabled is True
    assert settings.openai_integration_enabled is True
    assert settings.gen_ai_stream_spans_enabled is True
    assert settings.openai_include_prompts is True


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


def test_before_send_preserves_request_url_identifiers_but_strips_query_string():
    event = {
        "request": {
            "url": (
                "https://example.org/api/agent-studio/trace/"
                "01784cd8-7512-4830-b5f5-a427502ab923/context"
                "?token=secret"
            ),
            "query_string": "token=secret",
        },
    }

    scrubbed = sentry.before_send(event)

    assert scrubbed["request"]["url"] == (
        "https://example.org/api/agent-studio/trace/"
        "01784cd8-7512-4830-b5f5-a427502ab923/context"
    )
    assert "01784cd8-7512-4830-b5f5-a427502ab923" in scrubbed["request"]["url"]
    assert "token=secret" not in scrubbed["request"]["url"]
    assert "query_string" not in scrubbed["request"]


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


def test_before_send_preserves_safe_runtime_exception_context_without_raw_ids():
    event = {
        "contexts": {
            "runtime_exception": {
                "component": "execute_flow_stream",
                "operation": "event_generator_failed",
                "session_id": "session-flow-error",
                "turn_id": "turn-flow-error",
                "trace_id": "trace-flow-error",
                "flow_id": "fb6a3770-ec3b-49ac-9d85-d38ea43cb4f8",
                "flow_run_id": "flow-run-123",
                "document_id": None,
                "stages_completed": ["parsing", "chunking"],
                "stages_completed_count": 2,
                "validate_first": False,
                "extraction_strategy": "auto",
                "logger_name": "src.api.documents",
                "level_name": "ERROR",
                "status_code": 500,
                "prompt": "curator free text",
                "notes": {"raw_text": "should not survive"},
            }
        },
        "exception": {"values": [{"type": "RuntimeError", "value": "raw exception text"}]},
    }

    scrubbed = sentry.before_send(event)
    runtime_context = scrubbed["contexts"]["runtime_exception"]

    assert runtime_context["component"] == "execute_flow_stream"
    assert runtime_context["operation"] == "event_generator_failed"
    assert runtime_context["session_id"] == (
        "sha256:" + hashlib.sha256(b"session-flow-error").hexdigest()[:16]
    )
    assert runtime_context["turn_id"] == (
        "sha256:" + hashlib.sha256(b"turn-flow-error").hexdigest()[:16]
    )
    assert runtime_context["trace_id"] == (
        "sha256:" + hashlib.sha256(b"trace-flow-error").hexdigest()[:16]
    )
    assert runtime_context["flow_id"] == (
        "sha256:" + hashlib.sha256(b"fb6a3770-ec3b-49ac-9d85-d38ea43cb4f8").hexdigest()[:16]
    )
    assert runtime_context["flow_run_id"] == (
        "sha256:" + hashlib.sha256(b"flow-run-123").hexdigest()[:16]
    )
    assert "document_id" not in runtime_context
    assert runtime_context["stages_completed"] == ["parsing", "chunking"]
    assert runtime_context["stages_completed_count"] == 2
    assert runtime_context["validate_first"] is False
    assert runtime_context["extraction_strategy"] == "auto"
    assert runtime_context["logger_name"] == "src.api.documents"
    assert runtime_context["level_name"] == "ERROR"
    assert runtime_context["status_code"] == 500
    assert runtime_context["prompt"] == "[Filtered]"
    assert runtime_context["notes"]["raw_text"] == "[Filtered]"
    assert scrubbed["exception"]["values"][0]["value"] == "[Filtered]"


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


def test_before_send_transaction_preserves_safe_gen_ai_metadata_without_content():
    event = {
        "spans": [
            {
                "trace_id": "trace-for-ai-test",
                "span_id": "fedcba9876543210",
                "op": "gen_ai.invoke_agent",
                "description": "Supervisor Agent prompt should not survive",
                "data": {
                    "gen_ai.agent.name": "Supervisor Agent",
                    "gen_ai.operation.name": "invoke_agent",
                    "gen_ai.request.model": "gpt-5.5",
                    "gen_ai.response.model": "gpt-5.5-2026-06-01",
                    "gen_ai.tool.name": "lookup_gene",
                    "gen_ai.conversation.id": "conversation-123",
                    "gen_ai.usage.input_tokens": 123,
                    "gen_ai.usage.output_tokens": 45,
                    "gen_ai.response.streaming": True,
                    "gen_ai.request.messages": [{"content": "curator paper text"}],
                    "gen_ai.response.text": "model output text",
                    "gen_ai.tool.input": {"query": "raw curator query"},
                    "gen_ai.tool.output": {"body": "raw tool body"},
                    "untrusted_note": "free text",
                },
            }
        ],
    }

    scrubbed = sentry.before_send_transaction(event)
    data = scrubbed["spans"][0]["data"]

    assert data["gen_ai.agent.name"] == "Supervisor Agent"
    assert data["gen_ai.operation.name"] == "invoke_agent"
    assert data["gen_ai.request.model"] == "gpt-5.5"
    assert data["gen_ai.response.model"] == "gpt-5.5-2026-06-01"
    assert data["gen_ai.tool.name"] == "lookup_gene"
    assert data["gen_ai.conversation.id"].startswith("sha256:")
    assert data["gen_ai.usage.input_tokens"] == 123
    assert data["gen_ai.usage.output_tokens"] == 45
    assert data["gen_ai.response.streaming"] is True
    assert data["gen_ai.request.messages"] == "[Filtered]"
    assert data["gen_ai.response.text"] == "[Filtered]"
    assert data["gen_ai.tool.input"] == "[Filtered]"
    assert data["gen_ai.tool.output"] == "[Filtered]"
    assert data["untrusted_note"] == "[Filtered]"
    assert scrubbed["spans"][0]["description"] == "[Filtered]"


def test_hash_identifier_preserves_existing_hashed_identifier():
    assert sentry._hash_identifier("sha256:0123456789abcdef") == "sha256:0123456789abcdef"


def test_gen_ai_conversation_scope_sets_hashed_conversation_id(monkeypatch):
    calls = []

    class FakeScope:
        def get_conversation_id(self):
            calls.append(("get", None))
            return None

        def remove_conversation_id(self):
            calls.append(("remove", None))

    fake_scope = FakeScope()
    fake_sentry_sdk = SimpleNamespace(get_current_scope=lambda: fake_scope)
    fake_sentry_ai = SimpleNamespace(
        set_conversation_id=lambda conversation_id: calls.append(
            ("set", conversation_id)
        )
    )
    fake_modules = {
        "sentry_sdk": fake_sentry_sdk,
        "sentry_sdk.ai": fake_sentry_ai,
    }

    monkeypatch.setenv("SENTRY_AI_AGENTS_MONITORING_ENABLED", "true")
    monkeypatch.setattr(sentry.importlib, "import_module", lambda name: fake_modules[name])

    with sentry.gen_ai_conversation_scope("session-123"):
        calls.append(("inside", None))

    assert calls == [
        ("get", None),
        ("set", sentry._hash_identifier("session-123")),
        ("inside", None),
        ("remove", None),
    ]


def test_gen_ai_conversation_scope_restores_previous_id(monkeypatch):
    calls = []

    class FakeScope:
        def get_conversation_id(self):
            return "sha256:previous123456"

        def set_conversation_id(self, conversation_id):
            calls.append(("restore", conversation_id))

    fake_scope = FakeScope()
    fake_sentry_sdk = SimpleNamespace(get_current_scope=lambda: fake_scope)
    fake_sentry_ai = SimpleNamespace(
        set_conversation_id=lambda conversation_id: calls.append(
            ("set", conversation_id)
        )
    )
    fake_modules = {
        "sentry_sdk": fake_sentry_sdk,
        "sentry_sdk.ai": fake_sentry_ai,
    }

    monkeypatch.setenv("SENTRY_AI_AGENTS_MONITORING_ENABLED", "true")
    monkeypatch.setattr(sentry.importlib, "import_module", lambda name: fake_modules[name])

    with sentry.gen_ai_conversation_scope("session-456"):
        pass

    assert calls == [
        ("set", sentry._hash_identifier("session-456")),
        ("restore", "sha256:previous123456"),
    ]


def test_gen_ai_conversation_scope_skips_when_ai_monitoring_disabled(monkeypatch):
    imported = []

    def fake_import(name: str):
        imported.append(name)
        raise AssertionError("Sentry SDK should not be imported")

    monkeypatch.setattr(sentry.importlib, "import_module", fake_import)

    with sentry.gen_ai_conversation_scope("session-123"):
        pass

    assert imported == []


def test_before_send_transaction_can_opt_into_gen_ai_content(monkeypatch):
    monkeypatch.setenv("SENTRY_OPENAI_INCLUDE_PROMPTS", "true")
    fake_secret = "sk-" + "test" + "secret" + "0123456789"
    event = {
        "spans": [
            {
                "trace_id": "trace-for-ai-test",
                "span_id": "fedcba9876543210",
                "data": {
                    "gen_ai.request.messages": [
                        {
                            "role": "user",
                            "content": f"curator paper text {fake_secret}",
                        }
                    ],
                    "gen_ai.tool.input": {"api" + "_key": fake_secret},
                },
            }
        ],
    }

    scrubbed = sentry.before_send_transaction(event)
    data = scrubbed["spans"][0]["data"]

    assert data["gen_ai.request.messages"] == [
        {"role": "user", "content": "curator paper text [Filtered]"}
    ]
    assert data["gen_ai.tool.input"]["api_key"] == "[Filtered]"


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

    class FakeFastApiIntegration:
        def __init__(self, **kwargs):
            calls["fastapi_integration"] = kwargs

    class FakeStarletteIntegration:
        def __init__(self, **kwargs):
            calls["starlette_integration"] = kwargs

    class FakeLoggingIntegration:
        def __init__(self, **kwargs):
            calls["logging_integration"] = kwargs

    fake_modules = {
        "sentry_sdk": FakeSentrySdk(),
        "sentry_sdk.integrations.fastapi": SimpleNamespace(
            FastApiIntegration=FakeFastApiIntegration
        ),
        "sentry_sdk.integrations.logging": SimpleNamespace(
            LoggingIntegration=FakeLoggingIntegration
        ),
        "sentry_sdk.integrations.starlette": SimpleNamespace(
            StarletteIntegration=FakeStarletteIntegration
        ),
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
    assert len(init["integrations"]) == 3
    assert "stream_gen_ai_spans" not in init
    assert calls["starlette_integration"] == {
        "failed_request_status_codes": set(),
    }
    assert calls["fastapi_integration"] == {
        "failed_request_status_codes": set(),
    }
    assert calls["logging_integration"] == {
        "level": logging.INFO,
        "event_level": None,
    }
    assert calls["tags"] == {"app": "ai-curation", "component": "backend"}


def test_initialize_sentry_omits_tracing_when_unset(monkeypatch):
    calls = {}

    class FakeSentrySdk:
        def init(self, **kwargs):
            calls["init"] = kwargs

        def set_tag(self, key, value):
            calls.setdefault("tags", {})[key] = value

    class FakeIntegration:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    fake_modules = {
        "sentry_sdk": FakeSentrySdk(),
        "sentry_sdk.integrations.fastapi": SimpleNamespace(FastApiIntegration=FakeIntegration),
        "sentry_sdk.integrations.logging": SimpleNamespace(LoggingIntegration=FakeIntegration),
        "sentry_sdk.integrations.starlette": SimpleNamespace(StarletteIntegration=FakeIntegration),
    }
    monkeypatch.setattr(sentry.importlib, "import_module", lambda name: fake_modules[name])
    monkeypatch.setenv("SENTRY_DSN", "https://public@example.invalid/1")

    assert sentry.initialize_sentry_if_configured() is True
    assert "traces_sample_rate" not in calls["init"]
    assert "profiles_sample_rate" not in calls["init"]


def test_initialize_sentry_adds_ai_integrations_when_enabled(monkeypatch):
    calls = {}

    class FakeSentrySdk:
        def init(self, **kwargs):
            calls["init"] = kwargs

        def set_tag(self, key, value):
            calls.setdefault("tags", {})[key] = value

    class FakeIntegration:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeOpenAIAgentsIntegration:
        def __init__(self, **kwargs):
            calls["openai_agents_integration"] = kwargs

    class FakeOpenAIIntegration:
        def __init__(self, **kwargs):
            calls["openai_integration"] = kwargs

    fake_modules = {
        "sentry_sdk": FakeSentrySdk(),
        "sentry_sdk.integrations.fastapi": SimpleNamespace(FastApiIntegration=FakeIntegration),
        "sentry_sdk.integrations.logging": SimpleNamespace(LoggingIntegration=FakeIntegration),
        "sentry_sdk.integrations.starlette": SimpleNamespace(StarletteIntegration=FakeIntegration),
        "sentry_sdk.integrations.openai_agents": SimpleNamespace(
            OpenAIAgentsIntegration=FakeOpenAIAgentsIntegration,
        ),
        "sentry_sdk.integrations.openai": SimpleNamespace(
            OpenAIIntegration=FakeOpenAIIntegration,
        ),
    }
    monkeypatch.setattr(sentry.importlib, "import_module", lambda name: fake_modules[name])
    monkeypatch.setenv("SENTRY_DSN", "https://public@example.invalid/1")
    monkeypatch.setenv("SENTRY_SEND_DEFAULT_PII", "true")
    monkeypatch.setenv("SENTRY_AI_AGENTS_MONITORING_ENABLED", "true")
    monkeypatch.setenv("SENTRY_OPENAI_AGENTS_INTEGRATION_ENABLED", "true")
    monkeypatch.setenv("SENTRY_OPENAI_INTEGRATION_ENABLED", "true")
    monkeypatch.setenv("SENTRY_GEN_AI_STREAM_SPANS_ENABLED", "true")
    monkeypatch.setenv("SENTRY_OPENAI_INCLUDE_PROMPTS", "true")

    assert sentry.initialize_sentry_if_configured() is True

    init = calls["init"]
    assert init["send_default_pii"] is True
    assert init["stream_gen_ai_spans"] is True
    assert len(init["integrations"]) == 5
    assert calls["openai_agents_integration"] == {}
    assert calls["openai_integration"] == {"include_prompts": True}


def test_initialize_sentry_streaming_requires_ai_monitoring_master_flag(monkeypatch):
    calls = {}

    class FakeSentrySdk:
        def init(self, **kwargs):
            calls["init"] = kwargs

        def set_tag(self, key, value):
            calls.setdefault("tags", {})[key] = value

    class FakeIntegration:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    fake_modules = {
        "sentry_sdk": FakeSentrySdk(),
        "sentry_sdk.integrations.fastapi": SimpleNamespace(FastApiIntegration=FakeIntegration),
        "sentry_sdk.integrations.logging": SimpleNamespace(LoggingIntegration=FakeIntegration),
        "sentry_sdk.integrations.starlette": SimpleNamespace(StarletteIntegration=FakeIntegration),
    }
    monkeypatch.setattr(sentry.importlib, "import_module", lambda name: fake_modules[name])
    monkeypatch.setenv("SENTRY_DSN", "https://public@example.invalid/1")
    monkeypatch.setenv("SENTRY_GEN_AI_STREAM_SPANS_ENABLED", "true")

    assert sentry.initialize_sentry_if_configured() is True
    assert "stream_gen_ai_spans" not in calls["init"]


def test_initialize_sentry_optional_ai_integration_failure_is_non_fatal(
    monkeypatch,
    caplog,
):
    calls = {}

    class FakeSentrySdk:
        def init(self, **kwargs):
            calls["init"] = kwargs

        def set_tag(self, key, value):
            calls.setdefault("tags", {})[key] = value

    class FakeIntegration:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    fake_modules = {
        "sentry_sdk": FakeSentrySdk(),
        "sentry_sdk.integrations.fastapi": SimpleNamespace(FastApiIntegration=FakeIntegration),
        "sentry_sdk.integrations.logging": SimpleNamespace(LoggingIntegration=FakeIntegration),
        "sentry_sdk.integrations.starlette": SimpleNamespace(StarletteIntegration=FakeIntegration),
    }

    def fake_import(name: str):
        if name == "sentry_sdk.integrations.openai_agents":
            raise ImportError("missing optional integration")
        return fake_modules[name]

    monkeypatch.setattr(sentry.importlib, "import_module", fake_import)
    monkeypatch.setenv("SENTRY_DSN", "https://public@example.invalid/1")
    monkeypatch.setenv("SENTRY_AI_AGENTS_MONITORING_ENABLED", "true")
    monkeypatch.setenv("SENTRY_OPENAI_AGENTS_INTEGRATION_ENABLED", "true")
    caplog.set_level(logging.WARNING)

    assert sentry.initialize_sentry_if_configured() is True
    assert len(calls["init"]["integrations"]) == 3
    assert "Optional Sentry integration" in caplog.text


def test_initialize_sentry_sdk_init_failure_is_non_fatal(monkeypatch):
    class FakeSentrySdk:
        def init(self, **kwargs):
            raise ValueError("bad dsn")

        def set_tag(self, key, value):
            raise AssertionError("tags should not be set after init failure")

    class FakeIntegration:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    fake_modules = {
        "sentry_sdk": FakeSentrySdk(),
        "sentry_sdk.integrations.fastapi": SimpleNamespace(FastApiIntegration=FakeIntegration),
        "sentry_sdk.integrations.logging": SimpleNamespace(LoggingIntegration=FakeIntegration),
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
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    fake_modules = {
        "sentry_sdk": FakeSentrySdk(),
        "sentry_sdk.integrations.fastapi": SimpleNamespace(FastApiIntegration=FakeIntegration),
        "sentry_sdk.integrations.logging": SimpleNamespace(LoggingIntegration=FakeIntegration),
        "sentry_sdk.integrations.starlette": SimpleNamespace(StarletteIntegration=FakeIntegration),
    }
    monkeypatch.setattr(sentry.importlib, "import_module", lambda name: fake_modules[name])
    monkeypatch.setenv("SENTRY_DSN", "http://public@example.invalid/1")
    monkeypatch.setenv("SENTRY_ALLOW_INSECURE_DSN", "true")

    assert sentry.initialize_sentry_if_configured() is True
    assert calls["dsn"] == "http://public@example.invalid/1"


def test_sanitized_http_exception_emits_one_real_sentry_event_with_framework_integrations():
    """Regression test for duplicate handled-HTTPException events from Starlette."""

    pytest.importorskip("sentry_sdk")
    script = textwrap.dedent(
        """
        import logging

        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration

        from src.lib.http_errors import raise_sanitized_http_exception

        events = []

        def transport(event):
            events.append(event)

        sentry_sdk.init(
            dsn="http://public@example.invalid/1",
            transport=transport,
            include_local_variables=False,
            send_default_pii=False,
            integrations=[
                StarletteIntegration(failed_request_status_codes=set()),
                FastApiIntegration(failed_request_status_codes=set()),
                LoggingIntegration(level=logging.INFO, event_level=None),
            ],
            default_integrations=True,
        )

        app = FastAPI()

        @app.get("/sanitized")
        def sanitized():
            try:
                raise RuntimeError("synthetic sanitized failure")
            except Exception as exc:
                raise_sanitized_http_exception(
                    logging.getLogger("test.api"),
                    status_code=500,
                    detail="safe detail",
                    log_message="safe log",
                    exc=exc,
                )

        response = TestClient(app, raise_server_exceptions=False).get("/sanitized")
        if response.status_code != 500:
            raise AssertionError(f"unexpected status {response.status_code}")
        if len(events) != 1:
            raise AssertionError(f"expected one Sentry event, got {len(events)}")
        values = events[0].get("exception", {}).get("values", [])
        exception_types = [value.get("type") for value in values]
        if exception_types != ["RuntimeError"]:
            raise AssertionError(f"unexpected exception chain {exception_types!r}")
        """
    )

    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr + completed.stdout


def test_real_sentry_request_url_preserves_trace_id_path_parameter_without_query_string():
    """Regression test for private operational IDs staying useful without query secrets."""

    pytest.importorskip("sentry_sdk")
    script = textwrap.dedent(
        """
        import logging

        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration

        from src.lib.http_errors import raise_sanitized_http_exception
        from src.lib.observability import sentry

        events = []
        trace_id = "01784cd8-7512-4830-b5f5-a427502ab923"

        def transport(event):
            events.append(event)

        sentry_sdk.init(
            dsn="http://public@example.invalid/1",
            transport=transport,
            include_local_variables=False,
            send_default_pii=False,
            before_send=sentry.before_send,
            integrations=[
                StarletteIntegration(failed_request_status_codes=set()),
                FastApiIntegration(failed_request_status_codes=set()),
                LoggingIntegration(level=logging.INFO, event_level=None),
            ],
            default_integrations=True,
        )

        app = FastAPI()

        @app.get("/api/agent-studio/trace/{trace_id}/context")
        def trace_context(trace_id: str):
            try:
                raise RuntimeError("synthetic trace context failure")
            except Exception as exc:
                raise_sanitized_http_exception(
                    logging.getLogger("test.agent_studio.trace_context"),
                    status_code=500,
                    detail="safe detail",
                    log_message="safe log",
                    exc=exc,
                )

        response = TestClient(app, raise_server_exceptions=False).get(
            f"/api/agent-studio/trace/{trace_id}/context"
        )
        if response.status_code != 500:
            raise AssertionError(f"unexpected status {response.status_code}")
        if len(events) != 1:
            raise AssertionError(f"expected one Sentry event, got {len(events)}")
        url = events[0].get("request", {}).get("url")
        expected = f"http://testserver/api/agent-studio/trace/{trace_id}/context"
        if url != expected:
            raise AssertionError(f"unexpected sanitized url {url!r}")
        if trace_id not in repr(events[0]):
            raise AssertionError("raw trace id was removed from Sentry event")
        if "token=secret" in repr(events[0]):
            raise AssertionError("query secret survived in Sentry event")
        """
    )

    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr + completed.stdout

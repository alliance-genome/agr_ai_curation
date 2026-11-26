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
        capture_exception=lambda exc: calls["exceptions"].append(exc) or "event-id",
    )

    def _fake_import(name):
        if name == "sentry_sdk":
            return fake_sdk
        raise ImportError(name)

    monkeypatch.setattr(runtime.importlib, "import_module", _fake_import)
    monkeypatch.setattr(
        runtime,
        "get_runtime_observability_tag_value_max_chars",
        lambda: 12,
    )
    monkeypatch.setattr(
        runtime,
        "get_runtime_observability_context_value_max_chars",
        lambda: 9,
    )
    exc = RuntimeError("raw curator detail")

    reported = runtime.report_runtime_exception(
        exc,
        component="executable_run",
        operation="producer_failed_long_name",
        tags={
            "run_kind": "assistant_chat_turn",
            "ai_curation.flow.id_hash": "sha256:1234567890abcdef",
            "phase": "flow_finalization",
            "session_id": "must-not-be-indexed",
        },
        context={
            "session_id": "session-1-long",
            "attempt": 2,
            "refusals": [{"unexpected": "ordinary nested input"}],
            "reason_counts": {"unexpected": 4},
        },
    )

    assert reported is True
    assert calls["exceptions"] == [exc]
    assert calls["levels"] == ["error"]
    assert ("alert_type", "runtime_exception") in calls["tags"]
    assert ("runtime_component", "executable_r") in calls["tags"]
    assert ("operation", "producer_fai") in calls["tags"]
    assert ("run_kind", "assistant_ch") in calls["tags"]
    assert ("ai_curation.flow.id_hash", "sha256:12345") in calls["tags"]
    assert ("phase", "flow_finaliz") in calls["tags"]
    assert ("session_id", "must-not-be-indexed") not in calls["tags"]
    assert calls["contexts"] == [
        (
            "runtime_exception",
            {
                "component": "executable_r",
                "operation": "producer_fai",
                "session_id": "session-1",
                "attempt": 2,
                "refusals": "[{'unexpe",
                "reason_counts": "{'unexpec",
            },
        )
    ]


def test_report_runtime_exception_is_best_effort_when_sdk_missing(monkeypatch, caplog):
    monkeypatch.setattr(
        runtime.importlib,
        "import_module",
        lambda name: (_ for _ in ()).throw(ImportError("fake-private-import-detail")),
    )

    assert (
        runtime.report_runtime_exception(
            RuntimeError("boom"),
            component="unit",
            operation="missing_sdk",
        )
        is False
    )
    assert "fake-private" not in caplog.text
    warning, = caplog.records
    assert warning.getMessage() == "Sentry SDK unavailable for runtime exception capture"
    assert warning.sentry_skip_event is True


def test_supplement_refusal_capture_strips_unexpected_keys_and_values(monkeypatch, caplog):
    import json

    import sentry_sdk
    from sentry_sdk.transport import Transport

    from src.lib.observability.sentry import before_send

    monkeypatch.setenv("SENTRY_CONTENT_REDACTION_ENABLED", "false")
    private = "fake-private-reference-and-curator-text"
    events = []

    class MemoryTransport(Transport):
        def capture_envelope(self, envelope):
            events.extend(item.get_event() for item in envelope.items if item.headers["type"] == "event")

    with sentry_sdk.Client(
        dsn="http://public@localhost/1", transport=MemoryTransport, before_send=before_send,
        default_integrations=False, include_local_variables=False,
    ) as client, sentry_sdk.isolation_scope() as scope:
        scope.set_client(client)
        assert runtime.report_runtime_exception(
            RuntimeError("Every chosen file was left out of the add"),
            component="document_files", operation="supplement_add_nothing_added",
            include_request=False,
            context={
                "reason_counts": {"too_large": 1, private: 1, "source_unavailable": True},
                "refusals": [
                    {
                        "reason_code": "too_large",
                        "measured_value": [private],
                        "limit_value": False,
                        "display_name": private,
                        "artifact_id_hash": private,
                    },
                    {"reason_code": private},
                ],
                "reference": private,
            },
        )

    event, = events
    context = event["contexts"]["runtime_exception"]
    assert context["reason_counts"] == {"too_large": 1}
    assert context["refusals"] == [{"reason_code": "too_large"}]
    assert context["omitted_count"] == 0
    assert private not in json.dumps(event)
    assert private not in caplog.text

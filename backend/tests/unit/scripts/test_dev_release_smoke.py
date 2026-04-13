from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest


def _load_smoke_module():
    repo_root = Path(__file__).resolve().parents[4]
    module_path = repo_root / "scripts" / "testing" / "dev_release_smoke.py"
    spec = importlib.util.spec_from_file_location("dev_release_smoke", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_compute_scope_limitations_includes_debug_relaxations():
    smoke = _load_smoke_module()
    args = SimpleNamespace(
        skip_provider_health=False,
        skip_user_info=True,
        skip_chat=False,
        skip_flow=True,
        skip_workspace=True,
        skip_batch=False,
        allow_dev_mode_fallback=True,
        allow_duplicate_reuse=True,
    )

    assert smoke.compute_scope_limitations(args) == [
        "user_info",
        "flow",
        "workspace",
        "dev_mode_fallback",
        "duplicate_reuse",
    ]


def test_ensure_worker_ready_rejects_degraded_auth_even_when_worker_available(monkeypatch):
    smoke = _load_smoke_module()
    checks: list[dict] = []
    degraded_payload = {
        "status": "degraded",
        "worker_available": True,
        "worker_state": "ready",
        "status_error": "invalid auth mode",
        "error": "invalid auth mode",
    }

    def _fake_http_request(method, url, **kwargs):
        del method, url, kwargs
        return smoke.Response(
            status_code=200,
            body=b'{"status":"degraded"}',
            text='{"status":"degraded"}',
            json_body=degraded_payload,
        )

    monkeypatch.setattr(smoke, "http_request", _fake_http_request)

    with pytest.raises(smoke.SmokeFailure, match="not release-ready"):
        smoke.ensure_worker_ready(
            base_url="http://example.test",
            headers={},
            wake_timeout_seconds=1.0,
            poll_interval_seconds=0.0,
            checks=checks,
        )

    assert checks
    assert checks[0]["step"] == "pdf_extraction_health_initial"
    assert checks[0]["ok"] is False


def test_ensure_worker_ready_allows_healthy_payload_with_status_error(monkeypatch):
    smoke = _load_smoke_module()
    checks: list[dict] = []
    healthy_payload = {
        "status": "healthy",
        "worker_available": True,
        "worker_state": "ready",
        "status_error": "status network error",
        "error": None,
    }

    def _fake_http_request(method, url, **kwargs):
        del method, url, kwargs
        return smoke.Response(
            status_code=200,
            body=b'{"status":"healthy"}',
            text='{"status":"healthy"}',
            json_body=healthy_payload,
        )

    monkeypatch.setattr(smoke, "http_request", _fake_http_request)

    payload = smoke.ensure_worker_ready(
        base_url="http://example.test",
        headers={},
        wake_timeout_seconds=1.0,
        poll_interval_seconds=0.0,
        checks=checks,
    )

    assert payload == healthy_payload
    assert checks[0]["ok"] is True


def test_ask_streaming_chat_question_returns_trace_and_model_summary(monkeypatch):
    smoke = _load_smoke_module()
    checks: list[dict] = []
    sse_body = (
        'data: {"type":"RUN_STARTED","trace_id":"trace-123","model":"gpt-5.4-nano"}\n'
        "\n"
        'data: {"type":"CHUNK_PROVENANCE","chunk_id":"chunk-1"}\n'
        "\n"
        'data: {"type":"RUN_FINISHED","response":"This paper studies zebrafish development in one concise curator-facing sentence."}\n'
        "\n"
    )

    def _fake_http_request(method, url, **kwargs):
        del method, url, kwargs
        return smoke.Response(
            status_code=200,
            body=sse_body.encode("utf-8"),
            text=sse_body,
            json_body=None,
        )

    monkeypatch.setattr(smoke, "http_request", _fake_http_request)

    summary = smoke.ask_streaming_chat_question(
        base_url="http://example.test",
        headers={"X-API-Key": "test-key"},
        session_id="session-stream-1",
        message="Summarize the loaded paper.",
        chat_model="gpt-5.4-nano",
        specialist_model="gpt-5.4-nano",
        expected_model=None,
        chat_timeout_seconds=5.0,
        checks=checks,
    )

    assert summary["trace_id"] == "trace-123"
    assert summary["model"] == "gpt-5.4-nano"
    assert "RUN_STARTED" in summary["event_types"]
    assert "RUN_FINISHED" in summary["event_types"]
    assert "zebrafish" in summary["response_preview"].lower()
    assert checks[-1]["step"] == "chat_stream"


def test_ask_streaming_chat_question_rejects_missing_trace_id(monkeypatch):
    smoke = _load_smoke_module()
    checks: list[dict] = []
    sse_body = (
        'data: {"type":"RUN_STARTED","model":"gpt-5.4-nano"}\n'
        "\n"
        'data: {"type":"CHUNK_PROVENANCE","chunk_id":"chunk-1"}\n'
        "\n"
        'data: {"type":"RUN_FINISHED","response":"This paper studies zebrafish development in one concise curator-facing sentence."}\n'
        "\n"
    )

    def _fake_http_request(method, url, **kwargs):
        del method, url, kwargs
        return smoke.Response(
            status_code=200,
            body=sse_body.encode("utf-8"),
            text=sse_body,
            json_body=None,
        )

    monkeypatch.setattr(smoke, "http_request", _fake_http_request)

    with pytest.raises(smoke.SmokeFailure, match="trace_id"):
        smoke.ask_streaming_chat_question(
            base_url="http://example.test",
            headers={"X-API-Key": "test-key"},
            session_id="session-stream-2",
            message="Summarize the loaded paper.",
            chat_model="gpt-5.4-nano",
            specialist_model="gpt-5.4-nano",
            expected_model=None,
            chat_timeout_seconds=5.0,
            checks=checks,
        )


def test_ask_chat_question_omits_model_overrides_when_not_requested(monkeypatch):
    smoke = _load_smoke_module()
    checks: list[dict] = []
    captured = {}

    def _fake_http_request(method, url, **kwargs):
        del method, url
        captured.update(kwargs)
        return smoke.Response(
            status_code=200,
            body=b'{"response":"crb is the focus gene"}',
            text='{"response":"crb is the focus gene"}',
            json_body={"response": "crb is the focus gene", "session_id": "session-defaults"},
        )

    monkeypatch.setattr(smoke, "http_request", _fake_http_request)

    answer = smoke.ask_chat_question(
        base_url="http://example.test",
        headers={"X-API-Key": "test-key"},
        session_id="session-defaults",
        message="What genes are the focus of the publication?",
        chat_model=None,
        specialist_model=None,
        chat_timeout_seconds=5.0,
        checks=checks,
    )

    assert answer == "crb is the focus gene"
    assert captured["json_body"] == {
        "message": "What genes are the focus of the publication?",
        "session_id": "session-defaults",
    }


def test_ask_streaming_chat_question_can_validate_runtime_default_model_without_request_override(monkeypatch):
    smoke = _load_smoke_module()
    checks: list[dict] = []
    captured = {}
    sse_body = (
        'data: {"type":"RUN_STARTED","trace_id":"trace-runtime","model":"gpt-5.4"}\n'
        "\n"
        'data: {"type":"CHUNK_PROVENANCE","chunk_id":"chunk-1"}\n'
        "\n"
        'data: {"type":"RUN_FINISHED","response":"crumbs (crb) is the main focus gene."}\n'
        "\n"
    )

    def _fake_http_request(method, url, **kwargs):
        del method, url
        captured.update(kwargs)
        return smoke.Response(
            status_code=200,
            body=sse_body.encode("utf-8"),
            text=sse_body,
            json_body=None,
        )

    monkeypatch.setattr(smoke, "http_request", _fake_http_request)

    summary = smoke.ask_streaming_chat_question(
        base_url="http://example.test",
        headers={"X-API-Key": "test-key"},
        session_id="session-stream-runtime-defaults",
        message="What genes are the focus of the publication?",
        chat_model=None,
        specialist_model=None,
        expected_model="gpt-5.4",
        chat_timeout_seconds=5.0,
        checks=checks,
    )

    assert captured["json_body"] == {
        "message": "What genes are the focus of the publication?",
        "session_id": "session-stream-runtime-defaults",
    }
    assert summary["trace_id"] == "trace-runtime"
    assert summary["model"] == "gpt-5.4"
    assert "crb" in summary["response_preview"].lower()


def test_require_safe_fixture_deletion_principal_rejects_non_test_user():
    smoke = _load_smoke_module()

    with pytest.raises(smoke.SmokeFailure, match="dedicated test/smoke principal"):
        smoke.require_safe_fixture_deletion_principal(
            {
                "auth_sub": "auth0|curator-prod-user",
                "sub": "auth0|curator-prod-user",
                "email": "curator@example.org",
                "user_id": "42",
            }
        )


def test_require_text_contains_any_snippet_accepts_focus_gene_answer():
    smoke = _load_smoke_module()

    smoke.require_text_contains_any_snippet(
        "The publication is mainly focused on crumbs (crb) in Drosophila photoreceptors.",
        snippets=("crb", "crumbs"),
        context="focus gene answer",
        raw_details="focus gene answer",
    )


def test_require_model_looks_expected_rejects_generic_litellm_repr():
    smoke = _load_smoke_module()

    with pytest.raises(smoke.SmokeFailure, match="did not match"):
        smoke.require_model_looks_expected(
            "<agents.extensions.models.litellm_model.LitellmModel object at 0x1234>",
            expected_model="gpt-5.4",
            context="Streaming chat RUN_STARTED",
        )


def test_apply_cleanup_failures_to_evidence_marks_pass_run_failed():
    smoke = _load_smoke_module()
    evidence = {
        "overall_status": "pass",
        "checks": [
            {"step": "health", "ok": True},
            {"step": "cleanup_document_exception:doc-1", "ok": False, "status_code": 500},
        ],
    }

    smoke.apply_cleanup_failures_to_evidence(evidence)

    assert evidence["overall_status"] == "fail"
    assert evidence["cleanup_failures"] == [
        {"step": "cleanup_document_exception:doc-1", "ok": False, "status_code": 500}
    ]
    assert "cleanup_document_exception:doc-1" in evidence["error"]


def test_require_safe_fixture_deletion_principal_allows_dedicated_test_user():
    smoke = _load_smoke_module()

    smoke.require_safe_fixture_deletion_principal(
        {
            "auth_sub": "api-key-testuser",
            "sub": "api-key-testuser",
            "email": "smoke-test@example.org",
            "user_id": "test-user-42",
        }
    )


def test_resolve_secondary_pdf_rejects_explicit_same_content_copy(tmp_path):
    smoke = _load_smoke_module()
    primary = (tmp_path / "primary.pdf").resolve()
    duplicate = (tmp_path / "duplicate.pdf").resolve()
    primary.write_bytes(b"same-pdf-bytes")
    duplicate.write_bytes(b"same-pdf-bytes")

    with pytest.raises(smoke.SmokeFailure, match="differ in content"):
        smoke.resolve_secondary_pdf(primary, str(duplicate))


def test_resolve_secondary_pdf_skips_same_content_candidates(tmp_path, monkeypatch):
    smoke = _load_smoke_module()
    primary = (tmp_path / "sample_fly_publication.pdf").resolve()
    duplicate = (tmp_path / "copy_of_sample_fly_publication.pdf").resolve()
    distinct = (tmp_path / "micropub-biology-001725.pdf").resolve()
    primary.write_bytes(b"same-pdf-bytes")
    duplicate.write_bytes(b"same-pdf-bytes")
    distinct.write_bytes(b"different-pdf-bytes")

    monkeypatch.setattr(
        smoke,
        "build_default_fixture_candidates",
        lambda: [primary, duplicate, distinct],
    )

    assert smoke.resolve_secondary_pdf(primary, None) == distinct


def test_check_workspace_bootstrap_availability_requires_eligibility(monkeypatch):
    smoke = _load_smoke_module()
    checks: list[dict] = []

    def _fake_http_request(method, url, **kwargs):
        del method, url, kwargs
        return smoke.Response(
            status_code=200,
            body=b'{"eligible":false}',
            text='{"eligible":false}',
            json_body={"eligible": False},
        )

    monkeypatch.setattr(smoke, "http_request", _fake_http_request)

    with pytest.raises(smoke.SmokeFailure, match="not eligible"):
        smoke.check_workspace_bootstrap_availability(
            base_url="http://example.test",
            headers={"X-API-Key": "test-key"},
            document_id="doc-1",
            flow_run_id="flow-1",
            adapter_key="gene",
            checks=checks,
        )


def test_fetch_workspace_payload_requires_candidates_and_entity_tags(monkeypatch):
    smoke = _load_smoke_module()
    checks: list[dict] = []

    def _fake_http_request(method, url, **kwargs):
        del method, url, kwargs
        payload = {
            "workspace": {
                "session": {"session_id": "session-1"},
                "candidates": [
                    {"candidate_id": "cand-1", "adapter_key": "gene"},
                ],
                "entity_tags": [
                    {"tag_id": "cand-1", "entity_name": "crb"},
                ],
                "active_candidate_id": "cand-1",
            }
        }
        return smoke.Response(
            status_code=200,
            body=b"{}",
            text="{}",
            json_body=payload,
        )

    monkeypatch.setattr(smoke, "http_request", _fake_http_request)

    payload = smoke.fetch_workspace_payload(
        base_url="http://example.test",
        headers={"X-API-Key": "test-key"},
        session_id="session-1",
        checks=checks,
    )

    assert payload["workspace"]["session"]["session_id"] == "session-1"
    assert checks[-1]["step"] == "workspace_fetch"
    assert checks[-1]["payload"]["candidate_count"] == 1
    assert checks[-1]["payload"]["entity_tag_count"] == 1

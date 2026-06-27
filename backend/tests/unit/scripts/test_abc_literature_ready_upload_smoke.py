from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest


def _load_ready_smoke_module():
    repo_root = Path(__file__).resolve().parents[4]
    module_path = repo_root / "scripts" / "testing" / "abc_literature_ready_upload_smoke.py"
    spec = importlib.util.spec_from_file_location("abc_literature_ready_upload_smoke", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _response(smoke, status_code: int, payload: Any = None, body: bytes | None = None):
    if body is None:
        if payload is None:
            body = b""
            text = ""
        else:
            text = json.dumps(payload)
            body = text.encode("utf-8")
    else:
        text = body.decode("utf-8", errors="replace")
    return smoke.dev_smoke.Response(
        status_code=status_code,
        body=body,
        text=text,
        json_body=payload,
    )


def test_parse_args_defaults_to_shared_stage_fixture(tmp_path, monkeypatch):
    smoke = _load_ready_smoke_module()
    monkeypatch.setenv(
        "ABC_LITERATURE_READY_UPLOAD_SMOKE_ENV_FILE",
        str(tmp_path / "missing.env"),
    )
    monkeypatch.delenv("ABC_LITERATURE_READY_UPLOAD_SMOKE_BACKEND_BASE_URL", raising=False)
    monkeypatch.delenv("ABC_LITERATURE_READY_UPLOAD_SMOKE_KNOWN_MD5", raising=False)
    monkeypatch.setenv("AWS_PROFILE", "unit-profile")

    args = smoke.parse_args([])

    assert args.backend_base_url == smoke.DEFAULT_BACKEND_BASE_URL
    assert args.aws_profile == "unit-profile"
    assert args.known_md5 == smoke.live_smoke.DEFAULT_KNOWN_MD5
    assert args.source_referencefile_id == smoke.live_smoke.DEFAULT_SOURCE_REFERENCEFILE_ID
    assert args.converted_referencefile_id == smoke.live_smoke.DEFAULT_CONVERTED_REFERENCEFILE_ID


def test_parse_args_loads_local_env_file_for_existing_curator(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "ABC_LITERATURE_READY_UPLOAD_SMOKE_CURATOR_USERNAME=ready@example.org",
                "ABC_LITERATURE_READY_UPLOAD_SMOKE_CURATOR_PASSWORD=unit-password",
                "ABC_LITERATURE_READY_UPLOAD_SMOKE_BACKEND_BASE_URL=http://env-backend.test",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    smoke = _load_ready_smoke_module()
    monkeypatch.delenv("ABC_LITERATURE_READY_UPLOAD_SMOKE_CURATOR_USERNAME", raising=False)
    monkeypatch.delenv("ABC_LITERATURE_READY_UPLOAD_SMOKE_CURATOR_PASSWORD", raising=False)
    monkeypatch.delenv("ABC_LITERATURE_READY_UPLOAD_SMOKE_BACKEND_BASE_URL", raising=False)

    args = smoke.parse_args(["--env-file", str(env_file)])

    assert args.curator_username == "ready@example.org"
    assert args.curator_password == "unit-password"
    assert args.backend_base_url == "http://env-backend.test"


def test_backend_cookie_headers_use_auth_token_cookie_only():
    smoke = _load_ready_smoke_module()

    headers = smoke._backend_cookie_headers("unit-token")

    assert headers["Cookie"] == "auth_token=unit-token"
    assert "Authorization" not in headers
    assert headers["Accept"] == "application/json"


def test_preflight_rejects_local_pdf_document_source(tmp_path):
    smoke = _load_ready_smoke_module()
    config = smoke.config_from_args(
        smoke.parse_args(
            [
                "--evidence-dir",
                str(tmp_path),
                "--backend-base-url",
                "http://backend.test",
            ]
        )
    )

    def requester(method, url, **kwargs):
        return _response(
            smoke,
            200,
            {
                "cognito_configured": True,
                "details": {
                    "document_source": {
                        "provider": "local_pdf",
                        "enabled": True,
                    }
                },
            },
        )

    with pytest.raises(smoke.ReadyUploadSmokeFailure, match="external provider"):
        smoke.preflight_backend_document_source(
            config=config,
            requester=requester,
            checks=[],
        )


def test_download_source_pdf_validates_fixture_md5(tmp_path):
    smoke = _load_ready_smoke_module()
    pdf_bytes = b"%PDF-1.4\nunit fixture\n%%EOF\n"
    config = smoke.config_from_args(
        smoke.parse_args(
            [
                "--evidence-dir",
                str(tmp_path),
                "--known-md5",
                hashlib.md5(pdf_bytes).hexdigest(),
                "--literature-base-url",
                "https://literature.test",
                "--source-referencefile-id",
                "source-1",
            ]
        )
    )
    calls: list[dict[str, Any]] = []

    def requester(method, url, **kwargs):
        calls.append({"method": method, "url": url, "kwargs": kwargs})
        return _response(smoke, 200, body=pdf_bytes)

    output_path = tmp_path / "source.pdf"
    checks: list[dict[str, Any]] = []
    payload = smoke.download_source_pdf(
        config=config,
        token="unit-token",
        output_path=output_path,
        requester=requester,
        checks=checks,
    )

    assert output_path.read_bytes() == pdf_bytes
    assert payload["md5"] == hashlib.md5(pdf_bytes).hexdigest()
    assert calls[0]["kwargs"]["headers"]["Authorization"] == "Bearer unit-token"
    assert checks[0]["step"] == "literature_source_pdf_download"


def test_run_smoke_happy_path_cleans_up_and_redacts(tmp_path):
    smoke = _load_ready_smoke_module()
    pdf_bytes = b"%PDF-1.4\nabc ready source fixture\n%%EOF\n"
    converted_markdown = b"# Converted Markdown\nAlpha beta.\n"
    source_md5 = hashlib.md5(pdf_bytes).hexdigest()
    document_id = "00000000-0000-4000-8000-000000000001"
    config = smoke.config_from_args(
        smoke.parse_args(
            [
                "--evidence-dir",
                str(tmp_path),
                "--backend-base-url",
                "http://backend.test",
                "--literature-base-url",
                "https://literature.test",
                "--aws-profile",
                "unit-profile",
                "--known-md5",
                source_md5,
                "--source-referencefile-id",
                "4040596",
                "--converted-referencefile-id",
                "4672234",
                "--curator-username",
                "ready-smoke-curator@example.org",
                "--curator-password",
                "unit-curator-password",
                "--poll-interval-seconds",
                "0",
            ]
        )
    )
    calls: list[tuple[str, str]] = []
    http_calls: list[dict[str, Any]] = []
    state = {"deleted": False}

    class FakeAwsClient:
        def caller_identity(self):
            return {"Account": "123456789012", "Arn": "arn:aws:iam::123456789012:user/unit"}

        def discover_client_secret(self):
            return "unit-client-secret"

        def initiate_auth(self, *, username, auth_parameters):
            assert username == "ready-smoke-curator@example.org"
            assert auth_parameters["PASSWORD"] == "unit-curator-password"
            assert auth_parameters["SECRET_HASH"]
            return {"AuthenticationResult": {"IdToken": f"secret-token-for-{username}"}}

    def requester(method, url, **kwargs):
        http_calls.append({"method": method, "url": url, "kwargs": kwargs})
        cookie = (kwargs.get("headers") or {}).get("Cookie", "")
        if url == "http://backend.test/weaviate/health":
            return _response(
                smoke,
                200,
                {
                    "cognito_configured": True,
                    "details": {
                        "document_source": {
                            "provider": "abc_literature",
                            "enabled": True,
                        }
                    },
                },
            )
        if url == "http://backend.test/api/users/me":
            assert cookie.startswith("auth_token=secret-token-for-")
            return _response(
                smoke,
                200,
                {
                    "auth_sub": "cognito-unit-sub",
                    "email": "unit@example.invalid",
                    "provider_groups": ["FBStaff", "FlyBaseCurator"],
                    "active_groups": ["FB"],
                },
            )
        if url == "https://literature.test/reference/referencefile/download_file/4040596":
            assert (kwargs.get("headers") or {}).get("Authorization", "").startswith(
                "Bearer secret-token-for-"
            )
            return _response(smoke, 200, body=pdf_bytes)
        if url == "https://literature.test/reference/referencefile/download_file/4672234":
            assert (kwargs.get("headers") or {}).get("Authorization", "").startswith(
                "Bearer secret-token-for-"
            )
            return _response(smoke, 200, body=converted_markdown)
        if url == "http://backend.test/weaviate/documents/upload":
            assert cookie.startswith("auth_token=secret-token-for-")
            assert b"abc ready source fixture" in kwargs["data"]
            return _response(
                smoke,
                201,
                {
                    "document_id": document_id,
                    "job_id": "job-1",
                    "status": "PENDING",
                },
            )
        if url == f"http://backend.test/weaviate/documents/{document_id}/status":
            return _response(
                smoke,
                200,
                {
                    "document_id": document_id,
                    "processing_status": "completed",
                    "chunk_count": 3,
                },
            )
        if method == "DELETE" and url == f"http://backend.test/weaviate/documents/{document_id}":
            state["deleted"] = True
            return _response(smoke, 200, {"success": True, "document_id": document_id})
        if url == f"http://backend.test/weaviate/documents/{document_id}":
            if state["deleted"]:
                return _response(smoke, 404, {"detail": f"Document with ID {document_id} not found"})
            return _response(
                smoke,
                200,
                {
                    "document_id": document_id,
                    "filename": "abc-literature-ready-smoke-23970418.pdf",
                    "source_provenance": {
                        "provider": "abc_literature",
                        "source_md5": source_md5,
                        "pdf_artifact_id": "4040596",
                        "converted_artifact_id": "4672234",
                        "viewer_mode": "local_pdf",
                    },
                },
            )
        if url == f"http://backend.test/weaviate/documents/{document_id}/download-info":
            return _response(
                smoke,
                200,
                {
                    "pdf_available": True,
                    "pdf_size": len(pdf_bytes),
                    "source_markdown_available": True,
                    "source_markdown_size": 42,
                    "processed_json_available": True,
                    "viewer_mode": "local_pdf",
                },
            )
        if url == (
            f"http://backend.test/weaviate/documents/{document_id}/chunks"
            "?page=1&page_size=100&include_metadata=false"
        ):
            return _response(
                smoke,
                200,
                {"pagination": {"total_items": 3}, "chunks": [{"chunk_id": "c1"}]},
            )
        if url == f"http://backend.test/weaviate/documents/{document_id}/download/source_markdown":
            return _response(smoke, 200, body=converted_markdown)
        if url == f"http://backend.test/weaviate/documents/{document_id}/download/pdf":
            return _response(smoke, 200, body=pdf_bytes)
        raise AssertionError(f"Unexpected request: {method} {url}")

    result = smoke.run_smoke(
        config,
        aws_client_factory=lambda _config: FakeAwsClient(),
        requester=requester,
        now=datetime(2026, 6, 25, 13, 30, tzinfo=timezone.utc),
    )

    assert result.exit_code == 0
    evidence_text = result.evidence_path.read_text(encoding="utf-8")
    evidence = json.loads(evidence_text)
    assert evidence["overall_status"] == "pass"
    assert evidence["cleanup"]["document"]["deleted"] is True
    assert evidence["cleanup"]["document"]["verified_deleted"] is True
    assert evidence["smoke_user"] == {
        "auth_source": "curator_username_password",
        "expected_provider_groups": ["FBStaff", "FlyBaseCurator"],
        "mode": "existing",
        "username": "ready-smoke-curator@example.org",
    }
    assert evidence["expected_converted_markdown"]["sha256"] == hashlib.sha256(
        converted_markdown
    ).hexdigest()
    assert any(
        check["step"] == "backend_source_markdown_download"
        and check["payload"]["matches_literature_converted_artifact"] is True
        for check in evidence["checks"]
    )
    assert "unit-curator-password" not in evidence_text
    assert "secret-token-for-" not in evidence_text
    assert "unit-client-secret" not in evidence_text
    assert not calls
    assert any(call["method"] == "DELETE" for call in http_calls)


def test_run_smoke_reports_document_cleanup_failure_without_user_cleanup(tmp_path):
    smoke = _load_ready_smoke_module()
    pdf_bytes = b"%PDF-1.4\nabc ready source fixture\n%%EOF\n"
    converted_markdown = b"# Converted Markdown\nAlpha beta.\n"
    source_md5 = hashlib.md5(pdf_bytes).hexdigest()
    document_id = "00000000-0000-4000-8000-000000000002"
    config = smoke.config_from_args(
        smoke.parse_args(
            [
                "--evidence-dir",
                str(tmp_path),
                "--backend-base-url",
                "http://backend.test",
                "--literature-base-url",
                "https://literature.test",
                "--aws-profile",
                "unit-profile",
                "--known-md5",
                source_md5,
                "--source-referencefile-id",
                "4040596",
                "--converted-referencefile-id",
                "4672234",
                "--curator-username",
                "ready-smoke-curator@example.org",
                "--curator-password",
                "unit-curator-password",
                "--poll-interval-seconds",
                "0",
            ]
        )
    )
    calls: list[tuple[str, str]] = []

    class FakeAwsClient:
        def caller_identity(self):
            return {"Account": "123456789012"}

        def discover_client_secret(self):
            return "unit-client-secret"

        def initiate_auth(self, *, username, auth_parameters):
            assert auth_parameters["SECRET_HASH"]
            return {"AuthenticationResult": {"IdToken": f"secret-token-for-{username}"}}

    def requester(method, url, **kwargs):
        if url == "http://backend.test/weaviate/health":
            return _response(
                smoke,
                200,
                {
                    "cognito_configured": True,
                    "details": {
                        "document_source": {
                            "provider": "abc_literature",
                            "enabled": True,
                        }
                    },
                },
            )
        if url == "http://backend.test/api/users/me":
            return _response(
                smoke,
                200,
                {
                    "auth_sub": "cognito-unit-sub",
                    "provider_groups": ["FBStaff"],
                    "active_groups": ["FB"],
                },
            )
        if url == "https://literature.test/reference/referencefile/download_file/4040596":
            return _response(smoke, 200, body=pdf_bytes)
        if url == "https://literature.test/reference/referencefile/download_file/4672234":
            return _response(smoke, 200, body=converted_markdown)
        if url == "http://backend.test/weaviate/documents/upload":
            return _response(smoke, 201, {"document_id": document_id, "status": "PENDING"})
        if url == f"http://backend.test/weaviate/documents/{document_id}/status":
            return _response(smoke, 200, {"document_id": document_id, "processing_status": "completed"})
        if method == "DELETE" and url == f"http://backend.test/weaviate/documents/{document_id}":
            return _response(smoke, 500, {"detail": "cleanup failed"})
        if url == f"http://backend.test/weaviate/documents/{document_id}":
            return _response(
                smoke,
                200,
                {
                    "document_id": document_id,
                    "source_provenance": {
                        "source_md5": source_md5,
                        "pdf_artifact_id": "4040596",
                        "converted_artifact_id": "4672234",
                        "viewer_mode": "local_pdf",
                    },
                },
            )
        if url == f"http://backend.test/weaviate/documents/{document_id}/download-info":
            return _response(
                smoke,
                200,
                {
                    "pdf_available": True,
                    "pdf_size": len(pdf_bytes),
                    "source_markdown_available": True,
                    "source_markdown_size": len(converted_markdown),
                    "viewer_mode": "local_pdf",
                },
            )
        if url == (
            f"http://backend.test/weaviate/documents/{document_id}/chunks"
            "?page=1&page_size=100&include_metadata=false"
        ):
            return _response(smoke, 200, {"pagination": {"total_items": 1}, "chunks": [{"chunk_id": "c1"}]})
        if url == f"http://backend.test/weaviate/documents/{document_id}/download/source_markdown":
            return _response(smoke, 200, body=converted_markdown)
        if url == f"http://backend.test/weaviate/documents/{document_id}/download/pdf":
            return _response(smoke, 200, body=pdf_bytes)
        raise AssertionError(f"Unexpected request: {method} {url}")

    result = smoke.run_smoke(
        config,
        aws_client_factory=lambda _config: FakeAwsClient(),
        requester=requester,
        now=datetime(2026, 6, 25, 13, 40, tzinfo=timezone.utc),
    )

    evidence_text = result.evidence_path.read_text(encoding="utf-8")
    evidence = json.loads(evidence_text)
    assert result.exit_code == 1
    assert evidence["overall_status"] == "fail"
    assert evidence["smoke_user"]["mode"] == "existing"
    assert "skipped_user_cleanup_reason" not in evidence["cleanup"]
    assert evidence["cleanup"]["failures"]
    assert not calls
    assert "unit-curator-password" not in evidence_text
    assert "secret-token-for-" not in evidence_text
    assert "unit-client-secret" not in evidence_text


def test_run_smoke_redacts_token_from_failure_evidence(tmp_path):
    smoke = _load_ready_smoke_module()
    pdf_bytes = b"%PDF-1.4\nabc ready source fixture\n%%EOF\n"
    config = smoke.config_from_args(
        smoke.parse_args(
            [
                "--evidence-dir",
                str(tmp_path),
                "--backend-base-url",
                "http://backend.test",
                "--known-md5",
                hashlib.md5(pdf_bytes).hexdigest(),
                "--curator-username",
                "ready-smoke-curator@example.org",
                "--curator-password",
                "unit-curator-password",
            ]
        )
    )

    class FakeAwsClient:
        def caller_identity(self):
            return {"Account": "123456789012"}

        def discover_client_secret(self):
            return "unit-client-secret"

        def initiate_auth(self, *, username, auth_parameters):
            return {"AuthenticationResult": {"IdToken": "secret-token-for-ready-smoke"}}

    def requester(method, url, **kwargs):
        if url == "http://backend.test/weaviate/health":
            return _response(smoke, 503, {"detail": "secret-token-for-ready-smoke leaked"})
        raise AssertionError(f"Unexpected request: {method} {url}")

    result = smoke.run_smoke(
        config,
        aws_client_factory=lambda _config: FakeAwsClient(),
        requester=requester,
        now=datetime(2026, 6, 25, 13, 45, tzinfo=timezone.utc),
    )

    evidence_text = result.evidence_path.read_text(encoding="utf-8")
    assert result.exit_code == 1
    assert "secret-token-for-ready-smoke" not in evidence_text
    assert "unit-curator-password" not in evidence_text
    assert "unit-client-secret" not in evidence_text
    assert "<redacted>" in evidence_text


def test_run_smoke_curator_id_token_skips_aws_client_and_redacts(tmp_path):
    smoke = _load_ready_smoke_module()
    config = smoke.config_from_args(
        smoke.parse_args(
            [
                "--evidence-dir",
                str(tmp_path),
                "--backend-base-url",
                "http://backend.test",
                "--curator-id-token",
                "secret-id-token-for-ready-smoke",
            ]
        )
    )

    def aws_client_factory(_config):
        raise AssertionError("IdToken mode should not create an AWS client")

    def requester(method, url, **kwargs):
        if url == "http://backend.test/weaviate/health":
            return _response(smoke, 503, {"detail": "secret-id-token-for-ready-smoke leaked"})
        raise AssertionError(f"Unexpected request: {method} {url}")

    result = smoke.run_smoke(
        config,
        aws_client_factory=aws_client_factory,
        requester=requester,
        now=datetime(2026, 6, 25, 15, 25, tzinfo=timezone.utc),
    )

    evidence_text = result.evidence_path.read_text(encoding="utf-8")
    evidence = json.loads(evidence_text)
    assert result.exit_code == 1
    assert evidence["smoke_user"]["auth_source"] == "curator_id_token"
    assert evidence["aws"]["client_secret_source"] == "not_needed_for_id_token"
    assert "secret-id-token-for-ready-smoke" not in evidence_text
    assert "<redacted>" in evidence_text

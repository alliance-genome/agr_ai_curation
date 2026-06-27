from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _load_upload_smoke_module():
    repo_root = Path(__file__).resolve().parents[4]
    module_path = repo_root / "scripts" / "testing" / "add_literature_upload_smoke.py"
    spec = importlib.util.spec_from_file_location("add_literature_upload_smoke", module_path)
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


def test_parse_args_loads_ready_upload_fake_curator_env(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "ABC_LITERATURE_READY_UPLOAD_SMOKE_CURATOR_USERNAME=fake@example.org",
                "ABC_LITERATURE_READY_UPLOAD_SMOKE_CURATOR_PASSWORD=unit-password",
                "ABC_LITERATURE_READY_UPLOAD_SMOKE_BACKEND_BASE_URL=http://backend.env",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    smoke = _load_upload_smoke_module()
    monkeypatch.delenv("ADD_LITERATURE_UPLOAD_SMOKE_CURATOR_USERNAME", raising=False)
    monkeypatch.delenv("ADD_LITERATURE_UPLOAD_SMOKE_CURATOR_PASSWORD", raising=False)

    args = smoke.parse_args(["--env-file", str(env_file), "--sample-pdf", "backend/tests/fixtures/sample_fly_publication.pdf"])

    assert args.curator_username == "fake@example.org"
    assert args.curator_password == "unit-password"
    assert args.backend_base_url == "http://backend.env"


def test_backend_cookie_headers_use_auth_token_cookie_only():
    smoke = _load_upload_smoke_module()

    headers = smoke._backend_cookie_headers("unit-token")

    assert headers["Cookie"] == "auth_token=unit-token"
    assert "Authorization" not in headers
    assert headers["Accept"] == "application/json"


def test_run_smoke_happy_path_uses_sample_pdf_jobs_and_cleanup(tmp_path):
    smoke = _load_upload_smoke_module()
    pdf_bytes = b"%PDF-1.4\nmanual add literature fixture\n%%EOF\n"
    sample_pdf = tmp_path / "manual-upload-smoke.pdf"
    sample_pdf.write_bytes(pdf_bytes)
    document_id = "00000000-0000-4000-8000-000000000101"
    job_id = "00000000-0000-4000-8000-000000000201"
    config = smoke.config_from_args(
        smoke.parse_args(
            [
                "--evidence-dir",
                str(tmp_path / "evidence"),
                "--backend-base-url",
                "http://backend.test",
                "--aws-profile",
                "unit-profile",
                "--sample-pdf",
                str(sample_pdf),
                "--curator-username",
                "fake-curator@example.org",
                "--curator-password",
                "unit-curator-password",
                "--poll-interval-seconds",
                "0",
            ]
        )
    )
    http_calls: list[dict[str, Any]] = []
    state = {"deleted": False}

    class FakeAwsClient:
        def caller_identity(self):
            return {"Account": "123456789012", "Arn": "arn:aws:iam::123456789012:user/unit"}

        def discover_client_secret(self):
            return "unit-client-secret"

        def initiate_auth(self, *, username, auth_parameters):
            assert username == "fake-curator@example.org"
            assert auth_parameters["PASSWORD"] == "unit-curator-password"
            assert auth_parameters["SECRET_HASH"]
            return {"AuthenticationResult": {"IdToken": f"secret-token-for-{username}"}}

    def requester(method, url, **kwargs):
        http_calls.append({"method": method, "url": url, "kwargs": kwargs})
        cookie = (kwargs.get("headers") or {}).get("Cookie", "")
        if url == "http://backend.test/weaviate/health":
            return _response(smoke, 200, {"cognito_configured": True, "details": {}})
        if url == "http://backend.test/api/users/me":
            assert cookie.startswith("auth_token=secret-token-for-")
            return _response(
                smoke,
                200,
                {
                    "auth_sub": "cognito-unit-sub",
                    "email": "unit@example.invalid",
                    "provider_groups": ["FBStaff", "FlyBaseCurator"],
                },
            )
        if url == "http://backend.test/weaviate/documents/upload":
            assert cookie.startswith("auth_token=secret-token-for-")
            assert b"manual add literature fixture" in kwargs["data"]
            return _response(
                smoke,
                201,
                {
                    "document_id": document_id,
                    "job_id": job_id,
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
                    "chunk_count": 2,
                },
            )
        if url == "http://backend.test/weaviate/pdf-jobs?window_days=7&limit=50&offset=0":
            return _response(
                smoke,
                200,
                {
                    "jobs": [
                        {
                            "job_id": job_id,
                            "document_id": document_id,
                            "status": "completed",
                            "filename": sample_pdf.name,
                        }
                    ],
                    "total": 1,
                    "limit": 50,
                    "offset": 0,
                },
            )
        if url == f"http://backend.test/weaviate/documents/{document_id}/download-info":
            return _response(
                smoke,
                200,
                {
                    "pdf_available": True,
                    "pdf_size": len(pdf_bytes),
                    "pdfx_json_available": True,
                },
            )
        if url == (
            f"http://backend.test/weaviate/documents/{document_id}/chunks"
            "?page=1&page_size=100&include_metadata=false"
        ):
            return _response(
                smoke,
                200,
                {"pagination": {"total_items": 2}, "chunks": [{"chunk_id": "c1"}]},
            )
        if url == f"http://backend.test/weaviate/documents/{document_id}/download/pdf":
            return _response(smoke, 200, body=pdf_bytes)
        if method == "DELETE" and url == f"http://backend.test/weaviate/documents/{document_id}":
            state["deleted"] = True
            return _response(smoke, 200, {"success": True, "document_id": document_id})
        if url == f"http://backend.test/weaviate/documents/{document_id}":
            if state["deleted"]:
                return _response(smoke, 404, {"detail": f"Document with ID {document_id} not found"})
        raise AssertionError(f"Unexpected request: {method} {url}")

    result = smoke.run_smoke(
        config,
        aws_client_factory=lambda _config: FakeAwsClient(),
        requester=requester,
        now=datetime(2026, 6, 26, 13, 30, tzinfo=timezone.utc),
    )

    assert result.exit_code == 0
    evidence_text = result.evidence_path.read_text(encoding="utf-8")
    evidence = json.loads(evidence_text)
    assert evidence["overall_status"] == "pass"
    assert evidence["sample_pdf"]["md5"] == hashlib.md5(pdf_bytes).hexdigest()
    assert evidence["document"]["document_id"] == document_id
    assert evidence["document"]["job_id"] == job_id
    assert evidence["cleanup"]["document"]["deleted"] is True
    assert evidence["cleanup"]["document"]["verified_deleted"] is True
    assert any(check["step"] == "backend_pdf_jobs_visible" for check in evidence["checks"])
    assert any(check["step"] == "backend_pdf_download_available" for check in evidence["checks"])
    assert "unit-curator-password" not in evidence_text
    assert "secret-token-for-" not in evidence_text
    assert "unit-client-secret" not in evidence_text
    assert any(call["method"] == "DELETE" for call in http_calls)


def test_run_smoke_reports_cleanup_failure(tmp_path):
    smoke = _load_upload_smoke_module()
    pdf_bytes = b"%PDF-1.4\ncleanup failure fixture\n%%EOF\n"
    sample_pdf = tmp_path / "manual-upload-smoke.pdf"
    sample_pdf.write_bytes(pdf_bytes)
    document_id = "00000000-0000-4000-8000-000000000102"
    job_id = "00000000-0000-4000-8000-000000000202"
    config = smoke.config_from_args(
        smoke.parse_args(
            [
                "--evidence-dir",
                str(tmp_path / "evidence"),
                "--backend-base-url",
                "http://backend.test",
                "--sample-pdf",
                str(sample_pdf),
                "--curator-username",
                "fake-curator@example.org",
                "--curator-password",
                "unit-curator-password",
                "--poll-interval-seconds",
                "0",
            ]
        )
    )

    class FakeAwsClient:
        def caller_identity(self):
            return {"Account": "123456789012"}

        def discover_client_secret(self):
            return "unit-client-secret"

        def initiate_auth(self, *, username, auth_parameters):
            return {"AuthenticationResult": {"IdToken": f"secret-token-for-{username}"}}

    def requester(method, url, **kwargs):
        if url == "http://backend.test/weaviate/health":
            return _response(smoke, 200, {"cognito_configured": True})
        if url == "http://backend.test/api/users/me":
            return _response(smoke, 200, {"auth_sub": "sub", "provider_groups": ["FBStaff"]})
        if url == "http://backend.test/weaviate/documents/upload":
            return _response(smoke, 201, {"document_id": document_id, "job_id": job_id})
        if url == f"http://backend.test/weaviate/documents/{document_id}/status":
            return _response(smoke, 200, {"processing_status": "completed"})
        if url == "http://backend.test/weaviate/pdf-jobs?window_days=7&limit=50&offset=0":
            return _response(smoke, 200, {"jobs": [{"job_id": job_id, "document_id": document_id}], "total": 1})
        if url == f"http://backend.test/weaviate/documents/{document_id}/download-info":
            return _response(smoke, 200, {"pdf_available": True, "pdf_size": len(pdf_bytes)})
        if url == (
            f"http://backend.test/weaviate/documents/{document_id}/chunks"
            "?page=1&page_size=100&include_metadata=false"
        ):
            return _response(smoke, 200, {"pagination": {"total_items": 1}, "chunks": [{"chunk_id": "c1"}]})
        if url == f"http://backend.test/weaviate/documents/{document_id}/download/pdf":
            return _response(smoke, 200, body=pdf_bytes)
        if method == "DELETE" and url == f"http://backend.test/weaviate/documents/{document_id}":
            return _response(smoke, 500, {"detail": "cleanup failed"})
        raise AssertionError(f"Unexpected request: {method} {url}")

    result = smoke.run_smoke(
        config,
        aws_client_factory=lambda _config: FakeAwsClient(),
        requester=requester,
        now=datetime(2026, 6, 26, 13, 40, tzinfo=timezone.utc),
    )

    evidence_text = result.evidence_path.read_text(encoding="utf-8")
    evidence = json.loads(evidence_text)
    assert result.exit_code == 1
    assert evidence["overall_status"] == "fail"
    assert evidence["cleanup"]["failures"]
    assert "unit-curator-password" not in evidence_text
    assert "secret-token-for-" not in evidence_text
    assert "unit-client-secret" not in evidence_text

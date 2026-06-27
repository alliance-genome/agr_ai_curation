from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _load_identifier_smoke_module():
    repo_root = Path(__file__).resolve().parents[4]
    module_path = repo_root / "scripts" / "testing" / "abc_literature_identifier_import_smoke.py"
    spec = importlib.util.spec_from_file_location("abc_literature_identifier_import_smoke", module_path)
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


def test_parse_args_defaults_identifier_to_fixture_pmid(tmp_path, monkeypatch):
    smoke = _load_identifier_smoke_module()
    monkeypatch.setenv("ABC_LITERATURE_READY_UPLOAD_SMOKE_ENV_FILE", str(tmp_path / "missing.env"))
    monkeypatch.delenv("ABC_LITERATURE_IDENTIFIER_IMPORT_SMOKE_IDENTIFIER", raising=False)

    args = smoke.parse_args(["--pmid", "23970418"])

    assert args.identifier == "PMID:23970418"


def test_parse_args_allows_identifier_env_override(tmp_path, monkeypatch):
    smoke = _load_identifier_smoke_module()
    env_file = tmp_path / ".env"
    env_file.write_text(
        "ABC_LITERATURE_IDENTIFIER_IMPORT_SMOKE_IDENTIFIER=AGRKB:101000000055784\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("ABC_LITERATURE_IDENTIFIER_IMPORT_SMOKE_IDENTIFIER", raising=False)

    args = smoke.parse_args(["--env-file", str(env_file), "--pmid", "23970418"])

    assert args.identifier == "AGRKB:101000000055784"


def test_import_source_identifier_validates_pdf_backed_payload(tmp_path):
    smoke = _load_identifier_smoke_module()
    config = smoke.config_from_args(
        smoke.parse_args(
            [
                "--evidence-dir",
                str(tmp_path),
                "--backend-base-url",
                "http://backend.test",
                "--identifier",
                "PMID:23970418",
                "--source-referencefile-id",
                "4040596",
                "--converted-referencefile-id",
                "4672234",
                "--known-md5",
                "source-md5",
            ]
        )
    )
    calls = []

    def requester(method, url, **kwargs):
        calls.append({"method": method, "url": url, "kwargs": kwargs})
        return _response(
            smoke,
            200,
            {
                "results": [
                    {
                        "identifier": "PMID:23970418",
                        "normalized_identifier": "PMID:23970418",
                        "status": "imported",
                        "message": "Import queued for background processing.",
                        "document_id": "doc-1",
                        "job_id": "job-1",
                        "filename": "paper.pdf",
                        "source_provenance": {
                            "provider": "abc_literature",
                            "source_md5": "source-md5",
                            "pdf_artifact_id": "4040596",
                            "converted_artifact_id": "4672234",
                            "viewer_mode": "local_pdf",
                        },
                    }
                ],
                "requested_count": 1,
                "imported_count": 1,
                "duplicate_count": 0,
                "error_count": 0,
            },
        )

    checks = []
    document_id, payload = smoke.import_source_identifier(
        config=config,
        token="curator-token",
        requester=requester,
        checks=checks,
    )

    assert document_id == "doc-1"
    assert payload["imported_count"] == 1
    assert calls[0]["method"] == "POST"
    assert calls[0]["url"] == "http://backend.test/weaviate/documents/import/source-identifiers"
    assert calls[0]["kwargs"]["json_body"] == {"identifiers": "PMID:23970418"}
    assert calls[0]["kwargs"]["headers"]["Cookie"] == "auth_token=curator-token"
    assert checks[0]["step"] == "backend_identifier_import_queued"


def test_resolve_source_identifier_validates_ready_pdf_backed_payload(tmp_path):
    smoke = _load_identifier_smoke_module()
    config = smoke.config_from_args(
        smoke.parse_args(
            [
                "--evidence-dir",
                str(tmp_path),
                "--backend-base-url",
                "http://backend.test",
                "--identifier",
                "PMID:23970418",
                "--source-referencefile-id",
                "4040596",
                "--converted-referencefile-id",
                "4672234",
                "--known-md5",
                "source-md5",
            ]
        )
    )
    calls = []

    def requester(method, url, **kwargs):
        calls.append({"method": method, "url": url, "kwargs": kwargs})
        return _response(
            smoke,
            200,
            {
                "results": [
                    {
                        "identifier": "PMID:23970418",
                        "normalized_identifier": "PMID:23970418",
                        "status": "resolved",
                        "message": "Ready to import.",
                        "filename": "paper.pdf",
                        "source_provenance": {
                            "provider": "abc_literature",
                            "source_md5": "source-md5",
                            "pdf_artifact_id": "4040596",
                            "converted_artifact_id": "4672234",
                            "viewer_mode": "local_pdf",
                        },
                    }
                ],
                "requested_count": 1,
                "imported_count": 0,
                "duplicate_count": 0,
                "error_count": 0,
            },
        )

    checks = []
    payload = smoke.resolve_source_identifier(
        config=config,
        token="curator-token",
        requester=requester,
        checks=checks,
    )

    assert payload["imported_count"] == 0
    assert calls[0]["method"] == "POST"
    assert calls[0]["url"] == "http://backend.test/weaviate/documents/resolve/source-identifiers"
    assert calls[0]["kwargs"]["json_body"] == {"identifiers": "PMID:23970418"}
    assert calls[0]["kwargs"]["headers"]["Cookie"] == "auth_token=curator-token"
    assert checks[0]["step"] == "backend_identifier_resolve_ready"


def test_run_smoke_happy_path_cleans_up_and_redacts(tmp_path):
    smoke = _load_identifier_smoke_module()
    pdf_bytes = b"%PDF-1.4\nabc identifier source fixture\n%%EOF\n"
    converted_markdown = b"# Converted Markdown\nAlpha beta.\n"
    source_md5 = __import__("hashlib").md5(pdf_bytes).hexdigest()
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
                "--known-md5",
                source_md5,
                "--source-referencefile-id",
                "4040596",
                "--converted-referencefile-id",
                "4672234",
                "--curator-username",
                "identifier-smoke-curator@example.org",
                "--curator-password",
                "unit-curator-password",
                "--identifier",
                "PMID:23970418",
                "--poll-interval-seconds",
                "0",
            ]
        )
    )
    state = {"deleted": False}

    class FakeAwsClient:
        def caller_identity(self):
            return {"Account": "123456789012", "Arn": "arn:aws:iam::123456789012:user/unit"}

        def discover_client_secret(self):
            return "unit-client-secret"

        def initiate_auth(self, *, username, auth_parameters):
            assert username == "identifier-smoke-curator@example.org"
            assert auth_parameters["PASSWORD"] == "unit-curator-password"
            return {"AuthenticationResult": {"IdToken": "secret-id-token"}}

    def aws_client_factory(_live_config):
        return FakeAwsClient()

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
                {"auth_sub": "identifier-smoke-user", "provider_groups": ["FBStaff"]},
            )
        if url == "https://literature.test/reference/referencefile/download_file/4040596":
            return _response(smoke, 200, body=pdf_bytes)
        if url == "https://literature.test/reference/referencefile/download_file/4672234":
            return _response(smoke, 200, body=converted_markdown)
        if url == "http://backend.test/weaviate/documents/resolve/source-identifiers":
            return _response(
                smoke,
                200,
                {
                    "results": [
                        {
                            "identifier": "PMID:23970418",
                            "normalized_identifier": "PMID:23970418",
                            "status": "resolved",
                            "message": "Ready to import.",
                            "filename": "paper.pdf",
                            "source_provenance": {
                                "provider": "abc_literature",
                                "source_md5": source_md5,
                                "pdf_artifact_id": "4040596",
                                "converted_artifact_id": "4672234",
                                "viewer_mode": "local_pdf",
                            },
                        }
                    ],
                    "requested_count": 1,
                    "imported_count": 0,
                    "duplicate_count": 0,
                    "error_count": 0,
                },
            )
        if url == "http://backend.test/weaviate/documents/import/source-identifiers":
            return _response(
                smoke,
                200,
                {
                    "results": [
                        {
                            "identifier": "PMID:23970418",
                            "normalized_identifier": "PMID:23970418",
                            "status": "imported",
                            "message": "Import queued for background processing.",
                            "document_id": document_id,
                            "job_id": "job-1",
                            "filename": "paper.pdf",
                            "source_provenance": {
                                "provider": "abc_literature",
                                "source_md5": source_md5,
                                "pdf_artifact_id": "4040596",
                                "converted_artifact_id": "4672234",
                                "viewer_mode": "local_pdf",
                            },
                        }
                    ],
                    "requested_count": 1,
                    "imported_count": 1,
                    "duplicate_count": 0,
                    "error_count": 0,
                },
            )
        if url == f"http://backend.test/weaviate/documents/{document_id}/status":
            return _response(smoke, 200, {"processing_status": "completed"})
        if method == "DELETE" and url == f"http://backend.test/weaviate/documents/{document_id}":
            state["deleted"] = True
            return _response(smoke, 200, {"success": True})
        if method == "GET" and url == f"http://backend.test/weaviate/documents/{document_id}":
            if state["deleted"]:
                return _response(smoke, 404, {"detail": "not found"})
            return _response(
                smoke,
                200,
                {
                    "source_provenance": {
                        "source_md5": source_md5,
                        "pdf_artifact_id": "4040596",
                        "converted_artifact_id": "4672234",
                        "viewer_mode": "local_pdf",
                    }
                },
            )
        if url == f"http://backend.test/weaviate/documents/{document_id}/download-info":
            return _response(
                smoke,
                200,
                {
                    "viewer_mode": "local_pdf",
                    "pdf_available": True,
                    "pdf_size": len(pdf_bytes),
                    "source_markdown_available": True,
                    "source_markdown_size": len(converted_markdown),
                },
            )
        if url == f"http://backend.test/weaviate/documents/{document_id}/chunks?page=1&page_size=100&include_metadata=false":
            return _response(
                smoke,
                200,
                {"chunks": [{"id": "chunk-1"}], "pagination": {"total_items": 1}},
            )
        if url == f"http://backend.test/weaviate/documents/{document_id}/download/source_markdown":
            return _response(smoke, 200, body=converted_markdown)
        if url == f"http://backend.test/weaviate/documents/{document_id}/download/pdf":
            return _response(smoke, 200, body=pdf_bytes)
        raise AssertionError(f"Unexpected request: {method} {url}")

    result = smoke.run_smoke(
        config,
        aws_client_factory=aws_client_factory,
        requester=requester,
        now=datetime(2026, 6, 25, 16, 30, tzinfo=timezone.utc),
    )

    assert result.exit_code == 0, result.evidence
    assert result.evidence["overall_status"] == "pass"
    assert any(
        check["step"] == "backend_identifier_resolve_ready"
        for check in result.evidence["checks"]
    )
    assert result.evidence["cleanup"]["document"]["deleted"] is True
    assert "unit-curator-password" not in result.evidence_path.read_text(encoding="utf-8")
    assert "secret-id-token" not in result.evidence_path.read_text(encoding="utf-8")

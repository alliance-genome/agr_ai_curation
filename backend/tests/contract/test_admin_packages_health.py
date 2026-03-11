"""Contract tests for GET /api/admin/health/packages."""

import pytest


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("EMBEDDING_MODEL", "text-embedding-3-small")
    monkeypatch.setenv("EMBEDDING_TOKEN_PREFLIGHT_ENABLED", "true")
    monkeypatch.setenv("EMBEDDING_MODEL_TOKEN_LIMIT", "8191")
    monkeypatch.setenv("EMBEDDING_TOKEN_SAFETY_MARGIN", "500")
    monkeypatch.setenv("CONTENT_PREVIEW_CHARS", "1600")

    from fastapi.testclient import TestClient
    import os
    import sys

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from main import app

    return TestClient(app)


def test_packages_health_contract_shape(client, monkeypatch):
    monkeypatch.setattr(
        "src.lib.packages.health.build_package_health_report",
        lambda: {
            "status": "degraded",
            "validated_at": "2026-03-11T00:00:00+00:00",
            "packages_dir": "/runtime/packages",
            "runtime_version": "1.0.0",
            "supported_package_api_version": "1.0.0",
            "validation_errors": [],
            "summary": {
                "total_discovered": 2,
                "loaded_count": 1,
                "failed_count": 1,
                "validation_error_count": 0,
            },
            "loaded_packages": [
                {
                    "package_id": "agr.base",
                    "display_name": "AGR Base Package",
                    "version": "1.2.3",
                    "status": "loaded",
                    "package_path": "/runtime/packages/agr.base",
                    "manifest_path": "/runtime/packages/agr.base/package.yaml",
                    "reason": None,
                }
            ],
            "failed_packages": [
                {
                    "package_id": "org.bad",
                    "display_name": "Bad Package",
                    "version": "0.1.0",
                    "status": "failed",
                    "package_path": "/runtime/packages/org.bad",
                    "manifest_path": "/runtime/packages/org.bad/package.yaml",
                    "reason": "Runtime version '1.0.0' is outside supported range",
                }
            ],
        },
    )

    response = client.get("/api/admin/health/packages")
    assert response.status_code == 200
    data = response.json()

    assert data["status"] == "degraded"
    assert data["packages_dir"] == "/runtime/packages"
    assert data["runtime_version"] == "1.0.0"
    assert data["supported_package_api_version"] == "1.0.0"
    assert data["summary"]["total_discovered"] == 2
    assert data["summary"]["loaded_count"] == 1
    assert data["summary"]["failed_count"] == 1
    assert data["summary"]["validation_error_count"] == 0
    assert data["loaded_packages"][0]["package_id"] == "agr.base"
    assert data["loaded_packages"][0]["reason"] is None
    assert data["failed_packages"][0]["package_id"] == "org.bad"
    assert "supported range" in data["failed_packages"][0]["reason"]

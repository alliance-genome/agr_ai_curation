"""Unit tests for runtime package admin health endpoint."""

import pytest

import src.api.admin.connections as admin_connections


@pytest.mark.asyncio
async def test_check_runtime_packages_returns_report(monkeypatch):
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
                    "reason": "Unsupported package_api_version '2.0.0'",
                }
            ],
        },
    )

    result = await admin_connections.check_runtime_packages()
    assert result.status == "degraded"
    assert result.summary.loaded_count == 1
    assert result.summary.failed_count == 1
    assert result.loaded_packages[0].package_id == "agr.base"
    assert result.failed_packages[0].reason == "Unsupported package_api_version '2.0.0'"

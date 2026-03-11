"""Package registry health and diagnostics reporting."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from typing import Optional

from .registry import load_package_registry


def build_package_health_report(
    packages_dir: Path | None = None,
    *,
    runtime_version: Optional[str] = None,
    supported_package_api_version: Optional[str] = None,
) -> dict[str, Any]:
    """Build a read-only diagnostics report for runtime package loading."""
    registry = load_package_registry(
        packages_dir,
        runtime_version=runtime_version,
        supported_package_api_version=supported_package_api_version,
        fail_on_validation_error=False,
    )

    if registry.validation_errors:
        status = "unhealthy"
    elif registry.failed_packages:
        status = "degraded"
    else:
        status = "healthy"

    def _package_item(package, *, status_value: str, reason: str | None) -> dict[str, Any]:
        return {
            "package_id": package.package_id,
            "display_name": package.display_name,
            "version": package.version,
            "status": status_value,
            "package_path": str(package.package_path),
            "manifest_path": str(package.manifest_path),
            "reason": reason,
        }

    loaded_packages = [
        _package_item(package, status_value="loaded", reason=None)
        for package in registry.loaded_packages
    ]
    failed_packages = [
        _package_item(package, status_value="failed", reason=package.reason)
        for package in registry.failed_packages
    ]

    return {
        "status": status,
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "packages_dir": str(registry.packages_dir),
        "runtime_version": registry.runtime_version,
        "supported_package_api_version": registry.supported_package_api_version,
        "validation_errors": list(registry.validation_errors),
        "summary": {
            "total_discovered": len(loaded_packages) + len(failed_packages),
            "loaded_count": len(loaded_packages),
            "failed_count": len(failed_packages),
            "validation_error_count": len(registry.validation_errors),
        },
        "loaded_packages": loaded_packages,
        "failed_packages": failed_packages,
    }

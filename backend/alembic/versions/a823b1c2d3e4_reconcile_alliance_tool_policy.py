"""Reconcile the Alliance API tool policy with installed package bindings.

Revision ID: a823b1c2d3e4
Revises: 5e6f7a8b9c0d
Create Date: 2026-08-24
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import Any

import sqlalchemy as sa
import yaml
from alembic import op  # pyright: ignore[reportAttributeAccessIssue]


revision: str = "a823b1c2d3e4"
down_revision: str | Sequence[str] | None = "5e6f7a8b9c0d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ALLIANCE_API_TOOL_ID = "alliance_api_call"
DEFAULT_APP_VERSION = "1.0.0"
DEFAULT_PACKAGE_API_VERSION = "1.0.0"


def _find_project_root() -> Path | None:
    current = Path(__file__).resolve().parent
    for candidate in (current, *current.parents):
        if (candidate / "backend").is_dir() and (candidate / "packages").is_dir():
            return candidate
    return None


def _runtime_packages_dir() -> Path:
    configured = str(os.getenv("AGR_RUNTIME_PACKAGES_DIR", "")).strip()
    if configured:
        return Path(configured).expanduser().resolve(strict=False)

    runtime_root = Path(os.getenv("AGR_RUNTIME_ROOT", "/runtime"))
    runtime_packages = (runtime_root / "packages").expanduser().resolve(strict=False)
    if runtime_packages.exists():
        return runtime_packages

    project_root = _find_project_root()
    if project_root is not None:
        return (project_root / "packages").resolve(strict=False)
    return runtime_packages


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"Package configuration at {path} must be a YAML mapping")
    return payload


def _binding_export_path(package_dir: Path, raw_path: Any) -> Path:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError(f"Tool binding export in {package_dir} must define a path")
    relative_path = PurePosixPath(raw_path)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise ValueError(
            f"Tool binding export path '{raw_path}' in {package_dir} must be relative"
        )
    return (package_dir / Path(*relative_path.parts)).resolve(strict=False)


def _core_semver(value: str) -> tuple[int, int, int]:
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", value)
    if match is None:
        raise ValueError(f"Invalid semantic version: {value}")
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch)


def _manifest_is_runtime_compatible(manifest: dict[str, Any]) -> bool:
    package_api_version = str(manifest.get("package_api_version", "")).strip()
    supported_api_version = os.getenv(
        "AGR_RUNTIME_PACKAGE_API_VERSION", DEFAULT_PACKAGE_API_VERSION
    )
    if package_api_version != supported_api_version:
        return False

    min_runtime_version = str(manifest.get("min_runtime_version", "")).strip()
    max_runtime_version = str(manifest.get("max_runtime_version", "")).strip()
    if not min_runtime_version or not max_runtime_version:
        raise ValueError("Package manifest must define runtime compatibility bounds")
    runtime_version = _core_semver(os.getenv("APP_VERSION", DEFAULT_APP_VERSION))
    return (
        _core_semver(min_runtime_version)
        <= runtime_version
        <= _core_semver(max_runtime_version)
    )


def _alliance_api_binding_is_installed(packages_dir: Path | None = None) -> bool:
    """Inspect installed package binding exports without importing live app code."""
    resolved_packages_dir = packages_dir or _runtime_packages_dir()
    if not resolved_packages_dir.exists():
        return False

    for package_dir in sorted(path for path in resolved_packages_dir.iterdir() if path.is_dir()):
        manifest_path = package_dir / "package.yaml"
        if not manifest_path.exists():
            continue
        manifest = _load_yaml_mapping(manifest_path)
        if not _manifest_is_runtime_compatible(manifest):
            continue
        exports = manifest.get("exports", [])
        if not isinstance(exports, list):
            raise ValueError(f"Package manifest at {manifest_path} field 'exports' must be a list")
        for raw_export in exports:
            if not isinstance(raw_export, dict) or raw_export.get("kind") != "tool_binding":
                continue
            bindings_path = _binding_export_path(package_dir, raw_export.get("path"))
            bindings = _load_yaml_mapping(bindings_path).get("tools", [])
            if not isinstance(bindings, list):
                raise ValueError(f"Tool bindings at {bindings_path} field 'tools' must be a list")
            if any(
                isinstance(binding, dict)
                and str(binding.get("tool_id", "")).strip() == ALLIANCE_API_TOOL_ID
                for binding in bindings
            ):
                return True
    return False


def upgrade() -> None:
    """Remove the stale seed from deployments without its Alliance binding."""
    if _alliance_api_binding_is_installed():
        return
    op.execute(
        sa.text("DELETE FROM tool_policies WHERE tool_key = :tool_key").bindparams(
            tool_key=ALLIANCE_API_TOOL_ID
        )
    )


def downgrade() -> None:
    """Do not recreate a policy that may be unbound in this deployment."""

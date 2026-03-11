"""Authoritative runtime package registry builder."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .discovery import discover_package_manifests
from .models import PackageManifest
from .paths import get_runtime_packages_dir

_SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")


def _parse_core_semver(value: str) -> tuple[int, int, int]:
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", value)
    if match is None:
        raise ValueError(f"Invalid semantic version: {value}")
    major, minor, patch = (int(part) for part in match.groups())
    return (major, minor, patch)


def _validate_semver_arg(value: str, field_name: str) -> str:
    if not _SEMVER_PATTERN.match(value):
        raise ValueError(
            f"{field_name} must use semantic version format like 1.2.3"
        )
    return value


def _get_default_runtime_versions() -> tuple[str, str]:
    """Resolve runtime-owned version defaults without importing config eagerly."""
    from src.config import get_app_version, get_runtime_package_api_version

    return (get_app_version(), get_runtime_package_api_version())


def _runtime_version_is_compatible(
    runtime_version: str,
    manifest: PackageManifest,
) -> bool:
    runtime_core = _parse_core_semver(runtime_version)
    return (
        _parse_core_semver(manifest.min_runtime_version)
        <= runtime_core
        <= _parse_core_semver(manifest.max_runtime_version)
    )


@dataclass(frozen=True)
class LoadedPackage:
    """A runtime package that passed discovery and compatibility checks."""

    package_id: str
    display_name: str
    version: str
    package_path: Path
    manifest_path: Path
    manifest: PackageManifest


@dataclass(frozen=True)
class PackageFailure:
    """A runtime package that could not be loaded into the active registry."""

    package_id: str
    package_path: Path
    manifest_path: Path
    reason: str
    display_name: str | None = None
    version: str | None = None


class PackageRegistryValidationError(ValueError):
    """Raised when registry validation errors make the registry unsafe to consume."""


@dataclass(frozen=True)
class PackageRegistry:
    """In-memory package registry plus package loading diagnostics."""

    packages_dir: Path
    runtime_version: str
    supported_package_api_version: str
    loaded_packages: tuple[LoadedPackage, ...]
    failed_packages: tuple[PackageFailure, ...]
    validation_errors: tuple[str, ...] = ()

    @property
    def packages_by_id(self) -> dict[str, LoadedPackage]:
        """Return loaded packages keyed by package ID."""
        return {package.package_id: package for package in self.loaded_packages}

    def get_package(self, package_id: str) -> LoadedPackage | None:
        """Return one loaded package by ID, if present."""
        return self.packages_by_id.get(package_id)

    def raise_for_validation_errors(self) -> None:
        """Raise a single actionable error if registry validation failed."""
        if not self.validation_errors:
            return
        raise PackageRegistryValidationError("; ".join(self.validation_errors))


def load_package_registry(
    packages_dir: Path | None = None,
    *,
    runtime_version: Optional[str] = None,
    supported_package_api_version: Optional[str] = None,
    fail_on_validation_error: bool = True,
) -> PackageRegistry:
    """Discover packages on disk and build the authoritative in-memory registry."""
    default_runtime_version, default_package_api_version = _get_default_runtime_versions()
    runtime_version = runtime_version or default_runtime_version
    supported_package_api_version = (
        supported_package_api_version or default_package_api_version
    )
    runtime_version = _validate_semver_arg(runtime_version, "runtime_version")
    supported_package_api_version = _validate_semver_arg(
        supported_package_api_version,
        "supported_package_api_version",
    )
    resolved_packages_dir = (packages_dir or get_runtime_packages_dir()).expanduser().resolve(
        strict=False
    )

    discovered_packages, discovery_failures = discover_package_manifests(resolved_packages_dir)

    validation_errors: list[str] = []
    failed_packages: list[PackageFailure] = [
        PackageFailure(
            package_id=failure.package_id,
            package_path=failure.package_path,
            manifest_path=failure.manifest_path,
            reason=failure.reason,
        )
        for failure in discovery_failures
    ]

    package_groups: dict[str, list] = {}
    for package in discovered_packages:
        package_groups.setdefault(package.manifest.package_id, []).append(package)

    duplicate_package_ids = {
        package_id: packages
        for package_id, packages in package_groups.items()
        if len(packages) > 1
    }

    for package_id, packages in sorted(duplicate_package_ids.items()):
        duplicate_paths = ", ".join(
            str(package.manifest_path) for package in sorted(packages, key=lambda item: item.manifest_path)
        )
        reason = f"Duplicate package_id '{package_id}' discovered at: {duplicate_paths}"
        validation_errors.append(reason)
        for package in packages:
            failed_packages.append(
                PackageFailure(
                    package_id=package_id,
                    display_name=package.manifest.display_name,
                    version=package.manifest.version,
                    package_path=package.package_path,
                    manifest_path=package.manifest_path,
                    reason=reason,
                )
            )

    loaded_packages: list[LoadedPackage] = []
    for package_id, packages in sorted(package_groups.items()):
        if package_id in duplicate_package_ids:
            continue

        package = packages[0]
        manifest = package.manifest

        if manifest.package_api_version != supported_package_api_version:
            failed_packages.append(
                PackageFailure(
                    package_id=manifest.package_id,
                    display_name=manifest.display_name,
                    version=manifest.version,
                    package_path=package.package_path,
                    manifest_path=package.manifest_path,
                    reason=(
                        "Unsupported package_api_version "
                        f"'{manifest.package_api_version}' for package '{manifest.package_id}'; "
                        f"runtime supports '{supported_package_api_version}'"
                    ),
                )
            )
            continue

        if not _runtime_version_is_compatible(runtime_version, manifest):
            failed_packages.append(
                PackageFailure(
                    package_id=manifest.package_id,
                    display_name=manifest.display_name,
                    version=manifest.version,
                    package_path=package.package_path,
                    manifest_path=package.manifest_path,
                    reason=(
                        f"Runtime version '{runtime_version}' is outside supported range "
                        f"'{manifest.min_runtime_version}' - '{manifest.max_runtime_version}' "
                        f"for package '{manifest.package_id}'"
                    ),
                )
            )
            continue

        loaded_packages.append(
            LoadedPackage(
                package_id=manifest.package_id,
                display_name=manifest.display_name,
                version=manifest.version,
                package_path=package.package_path,
                manifest_path=package.manifest_path,
                manifest=manifest,
            )
        )

    registry = PackageRegistry(
        packages_dir=resolved_packages_dir,
        runtime_version=runtime_version,
        supported_package_api_version=supported_package_api_version,
        loaded_packages=tuple(sorted(loaded_packages, key=lambda item: item.package_id)),
        failed_packages=tuple(
            sorted(
                failed_packages,
                key=lambda item: (item.package_id, str(item.manifest_path)),
            )
        ),
        validation_errors=tuple(validation_errors),
    )
    if fail_on_validation_error:
        registry.raise_for_validation_errors()
    return registry

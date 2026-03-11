"""Filesystem discovery helpers for runtime packages."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .manifest_loader import load_package_manifest
from .models import PackageManifest
from .paths import get_package_manifest_path, get_runtime_packages_dir


@dataclass(frozen=True)
class DiscoveredPackage:
    """A package directory with a successfully parsed manifest."""

    package_path: Path
    manifest_path: Path
    manifest: PackageManifest


@dataclass(frozen=True)
class PackageDiscoveryFailure:
    """A package directory that could not be loaded into a manifest."""

    package_id: str
    package_path: Path
    manifest_path: Path
    reason: str


def iter_runtime_package_dirs(packages_dir: Path | None = None) -> tuple[Path, ...]:
    """Return deterministic package directories beneath the runtime packages root."""
    root = (packages_dir or get_runtime_packages_dir()).expanduser().resolve(strict=False)
    if not root.exists():
        return ()

    return tuple(
        path
        for path in sorted(root.iterdir(), key=lambda item: item.name)
        if path.is_dir()
    )


def discover_package_manifests(
    packages_dir: Path | None = None,
) -> tuple[tuple[DiscoveredPackage, ...], tuple[PackageDiscoveryFailure, ...]]:
    """Load every package manifest that exists beneath the runtime packages directory."""
    discovered: list[DiscoveredPackage] = []
    failures: list[PackageDiscoveryFailure] = []

    for package_path in iter_runtime_package_dirs(packages_dir):
        manifest_path = get_package_manifest_path(package_path)
        try:
            manifest = load_package_manifest(manifest_path)
        except ValueError as exc:
            failures.append(
                PackageDiscoveryFailure(
                    package_id=package_path.name,
                    package_path=package_path,
                    manifest_path=manifest_path,
                    reason=str(exc),
                )
            )
            continue

        discovered.append(
            DiscoveredPackage(
                package_path=package_path,
                manifest_path=manifest_path,
                manifest=manifest,
            )
        )

    return (tuple(discovered), tuple(failures))

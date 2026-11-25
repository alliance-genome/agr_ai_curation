"""Build-time CLI for baking per-package virtual environments into Docker images.

Discovers every runtime package beneath ``--packages-dir`` that exports at least
one ``tool_binding`` and builds an isolated virtual environment plus fingerprint
metadata under ``<output-dir>/<package_id>/``. Non-empty package requirements
must be backed by a hashed ``requirements_lock_file`` declared in ``package.yaml``;
installation enforces ``--require-hashes --no-deps --no-build-isolation``, scans
the package venv's ``.pth`` files against the allowlist before executing the new
interpreter, and runs ``pip check``.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Collection
from pathlib import Path
from typing import Sequence

from .discovery import discover_package_manifests
from .env_manager import (
    ALLOWED_PACKAGE_VENV_PTH_FILES,
    BACKEND_DEPENDENCY_LOCK,
    PackageEnvironment,
    PackageEnvironmentBootstrapError,
    PackageEnvironmentManager,
)
from .models import ExportKind
from .paths import DEFAULT_PREBUILT_PACKAGE_ENVS_DIR
from .registry import LoadedPackage


def build_package_environments(
    *,
    packages_dir: Path,
    output_dir: Path,
    constraints_path: Path = BACKEND_DEPENDENCY_LOCK,
    host_python: Path | None = None,
    require_hashed_lock: bool = True,
    allowed_pth_files: Collection[str] = ALLOWED_PACKAGE_VENV_PTH_FILES,
) -> list[PackageEnvironment]:
    """Discover tool-bearing packages and build prebuilt environments for each."""
    resolved_packages_dir = packages_dir.expanduser().resolve(strict=False)
    resolved_output_dir = output_dir.expanduser().resolve(strict=False)
    resolved_output_dir.mkdir(parents=True, exist_ok=True)

    discovered, failures = discover_package_manifests(resolved_packages_dir)
    if failures:
        details = "; ".join(
            f"{failure.package_id} ({failure.manifest_path}): {failure.reason}"
            for failure in failures
        )
        raise RuntimeError(
            f"Failed to parse runtime package manifests under {resolved_packages_dir}: {details}"
        )

    seen_ids: dict[str, Path] = {}
    for item in discovered:
        package_id = item.manifest.package_id
        if package_id in seen_ids:
            raise RuntimeError(
                f"Duplicate package_id '{package_id}' discovered at "
                f"{seen_ids[package_id]} and {item.manifest_path}"
            )
        seen_ids[package_id] = item.manifest_path

    effective_python = host_python
    if effective_python is None:
        base_python = Path(sys.base_prefix) / "bin" / "python3"
        effective_python = base_python if base_python.is_file() else Path(sys.executable)

    manager = PackageEnvironmentManager(
        host_python=effective_python,
        constraints_path=constraints_path,
    )

    built: list[PackageEnvironment] = []
    for item in sorted(discovered, key=lambda entry: entry.manifest.package_id):
        if not any(
            export.kind == ExportKind.TOOL_BINDING
            for export in item.manifest.exports
        ):
            continue

        loaded = LoadedPackage(
            package_id=item.manifest.package_id,
            display_name=item.manifest.display_name,
            version=item.manifest.version,
            package_path=item.package_path,
            manifest_path=item.manifest_path,
            manifest=item.manifest,
        )
        target_dir = resolved_output_dir / loaded.package_id
        env = manager.build_environment(
            loaded,
            target_dir=target_dir,
            require_hashed_lock=require_hashed_lock,
            allowed_pth_files=allowed_pth_files,
        )
        built.append(env)
        print(
            f"[build-package-envs] Built {env.package_id} ({env.package_version}) "
            f"at {env.venv_dir} fingerprint={env.fingerprint}",
            flush=True,
        )

    print(
        f"[build-package-envs] Built {len(built)} prebuilt package environment(s) "
        f"under {resolved_output_dir}",
        flush=True,
    )
    return built


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build prebuilt per-package virtual environments from hashed lockfiles.",
    )
    parser.add_argument(
        "--packages-dir",
        type=Path,
        required=True,
        help="Directory containing runtime package subdirectories.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_PREBUILT_PACKAGE_ENVS_DIR,
        help=f"Destination directory for prebuilt environments (default: {DEFAULT_PREBUILT_PACKAGE_ENVS_DIR}).",
    )
    parser.add_argument(
        "--constraints",
        type=Path,
        default=BACKEND_DEPENDENCY_LOCK,
        help="Path to the backend dependency lockfile included in the environment fingerprint.",
    )
    parser.add_argument(
        "--host-python",
        type=Path,
        default=None,
        help="Base Python interpreter used to create package venvs (defaults to base_prefix python3).",
    )
    parser.add_argument(
        "--allowed-pth",
        type=str,
        default=None,
        help="Whitespace-separated allowlist of .pth filenames permitted inside built package venvs.",
    )
    args = parser.parse_args(argv)
    allowed_pth = (
        frozenset(args.allowed_pth.split())
        if args.allowed_pth is not None
        else ALLOWED_PACKAGE_VENV_PTH_FILES
    )

    try:
        build_package_environments(
            packages_dir=args.packages_dir,
            output_dir=args.output_dir,
            constraints_path=args.constraints,
            host_python=args.host_python,
            allowed_pth_files=allowed_pth,
        )
    except (PackageEnvironmentBootstrapError, RuntimeError, ValueError) as exc:
        print(f"[build-package-envs] ERROR: {exc}", file=sys.stderr, flush=True)
        if isinstance(exc, PackageEnvironmentBootstrapError) and exc.details:
            for key, value in sorted(exc.details.items()):
                if value:
                    print(f"[build-package-envs]   {key}: {value}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

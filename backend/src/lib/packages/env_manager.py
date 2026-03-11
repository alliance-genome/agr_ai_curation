"""Per-package Python environment bootstrap for isolated tool execution."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .paths import (
    get_package_runner_metadata_path,
    get_package_runner_package_state_dir,
    get_package_runner_venv_dir,
)
from .registry import LoadedPackage


@dataclass(frozen=True)
class PackageEnvironment:
    """Resolved package environment details for one loaded package."""

    package_id: str
    package_version: str
    venv_dir: Path
    python_executable: Path
    requirements_path: Path
    fingerprint: str
    reused: bool


class PackageEnvironmentBootstrapError(RuntimeError):
    """Raised when a package virtual environment cannot be prepared."""

    def __init__(
        self,
        package_id: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.package_id = package_id
        self.details = details or {}


class PackageEnvironmentManager:
    """Create and reuse one isolated virtual environment per package."""

    def __init__(self, *, host_python: Path | None = None) -> None:
        self._host_python = (host_python or Path(sys.executable)).expanduser().resolve(
            strict=False
        )

    def ensure_environment(self, package: LoadedPackage) -> PackageEnvironment:
        """Create or reuse the virtual environment for one loaded package."""
        requirements_path = (
            package.package_path / package.manifest.requirements_file
        ).expanduser().resolve(strict=False)
        if not requirements_path.is_file():
            raise PackageEnvironmentBootstrapError(
                package.package_id,
                f"Requirements file not found for package '{package.package_id}': {requirements_path}",
                details={"requirements_path": str(requirements_path)},
            )

        requirements_text = requirements_path.read_text(encoding="utf-8")
        fingerprint = self._build_fingerprint(package, requirements_text)

        state_dir = get_package_runner_package_state_dir(package.package_id)
        venv_dir = get_package_runner_venv_dir(package.package_id)
        metadata_path = get_package_runner_metadata_path(package.package_id)
        python_executable = self._resolve_venv_python(venv_dir)

        metadata = self._read_metadata(metadata_path)
        if (
            metadata is not None
            and metadata.get("fingerprint") == fingerprint
            and python_executable.is_file()
        ):
            return PackageEnvironment(
                package_id=package.package_id,
                package_version=package.version,
                venv_dir=venv_dir,
                python_executable=python_executable,
                requirements_path=requirements_path,
                fingerprint=fingerprint,
                reused=True,
            )

        state_dir.mkdir(parents=True, exist_ok=True)
        if venv_dir.exists():
            shutil.rmtree(venv_dir)
        if metadata_path.exists():
            metadata_path.unlink()

        self._run_command(
            [str(self._host_python), "-m", "venv", str(venv_dir)],
            package_id=package.package_id,
            step="create_venv",
        )

        python_executable = self._resolve_venv_python(venv_dir)
        if not python_executable.is_file():
            raise PackageEnvironmentBootstrapError(
                package.package_id,
                f"Virtual environment python was not created for package '{package.package_id}'",
                details={"venv_dir": str(venv_dir)},
            )

        if requirements_text.strip():
            self._run_command(
                [
                    str(python_executable),
                    "-m",
                    "pip",
                    "install",
                    "-r",
                    str(requirements_path),
                ],
                package_id=package.package_id,
                step="install_requirements",
            )

        metadata_payload = {
            "package_id": package.package_id,
            "package_version": package.version,
            "fingerprint": fingerprint,
            "requirements_path": str(requirements_path),
            "requirements_sha256": hashlib.sha256(
                requirements_text.encode("utf-8")
            ).hexdigest(),
            "python_package_root": package.manifest.python_package_root,
            "python_executable": str(python_executable),
        }
        metadata_path.write_text(
            json.dumps(metadata_payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )

        return PackageEnvironment(
            package_id=package.package_id,
            package_version=package.version,
            venv_dir=venv_dir,
            python_executable=python_executable,
            requirements_path=requirements_path,
            fingerprint=fingerprint,
            reused=False,
        )

    def _build_fingerprint(self, package: LoadedPackage, requirements_text: str) -> str:
        payload = {
            "package_id": package.package_id,
            "package_version": package.version,
            "python_package_root": package.manifest.python_package_root,
            "requirements_file": package.manifest.requirements_file,
            "requirements_sha256": hashlib.sha256(
                requirements_text.encode("utf-8")
            ).hexdigest(),
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode("utf-8")
        ).hexdigest()

    def _read_metadata(self, metadata_path: Path) -> dict[str, Any] | None:
        if not metadata_path.is_file():
            return None
        try:
            raw = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(raw, dict):
            return None
        return raw

    def _resolve_venv_python(self, venv_dir: Path) -> Path:
        bin_dir = "Scripts" if sys.platform.startswith("win") else "bin"
        executable = "python.exe" if sys.platform.startswith("win") else "python"
        return (venv_dir / bin_dir / executable).expanduser().resolve(strict=False)

    def _run_command(
        self,
        command: list[str],
        *,
        package_id: str,
        step: str,
    ) -> None:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode == 0:
            return
        raise PackageEnvironmentBootstrapError(
            package_id,
            f"Failed to {step} for package '{package_id}'",
            details={
                "step": step,
                "command": command,
                "returncode": completed.returncode,
                "stdout": completed.stdout.strip(),
                "stderr": completed.stderr.strip(),
            },
        )

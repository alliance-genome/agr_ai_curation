"""Per-package Python environment bootstrap for isolated tool execution."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from .paths import (
    DEFAULT_PACKAGE_RUNNER_METADATA_FILENAME,
    DEFAULT_PACKAGE_RUNNER_VENV_DIRNAME,
    get_package_runner_metadata_path,
    get_package_runner_package_state_dir,
    get_package_runner_venv_dir,
    get_prebuilt_package_env_dir,
    get_prebuilt_package_envs_dir,
    get_prebuilt_package_metadata_path,
    get_prebuilt_package_venv_dir,
)
from .registry import LoadedPackage

# Packages an isolated environment shares with the backend resolve to the backend's
# exact versions: the backend lock is the pip constraints file for every install.
BACKEND_DEPENDENCY_LOCK = Path(__file__).resolve().parents[3] / "requirements.lock.txt"

PACKAGE_ENV_BOOTSTRAP_MODE_ENV_VAR = "AGR_PACKAGE_ENV_BOOTSTRAP_MODE"
PACKAGE_ENV_BOOTSTRAP_MODE_DYNAMIC = "dynamic"
PACKAGE_ENV_BOOTSTRAP_MODE_PREBUILT = "prebuilt"
PACKAGE_ENV_BOOTSTRAP_MODES = frozenset(
    {
        PACKAGE_ENV_BOOTSTRAP_MODE_DYNAMIC,
        PACKAGE_ENV_BOOTSTRAP_MODE_PREBUILT,
    }
)


def parse_package_env_bootstrap_mode(raw: str | None) -> str:
    """Validate an AGR_PACKAGE_ENV_BOOTSTRAP_MODE value without silent fallback."""
    if raw is None:
        return PACKAGE_ENV_BOOTSTRAP_MODE_DYNAMIC
    normalized = raw.strip().lower()
    if normalized not in PACKAGE_ENV_BOOTSTRAP_MODES:
        allowed = ", ".join(sorted(PACKAGE_ENV_BOOTSTRAP_MODES))
        raise ValueError(
            f"{PACKAGE_ENV_BOOTSTRAP_MODE_ENV_VAR} must be one of {allowed}; got {raw!r}"
        )
    return normalized


def get_package_env_bootstrap_mode() -> str:
    """Return the configured package environment bootstrap mode."""
    return parse_package_env_bootstrap_mode(os.getenv(PACKAGE_ENV_BOOTSTRAP_MODE_ENV_VAR))


def _has_requirement_entries(text: str) -> bool:
    """Return whether a requirements file contains any non-comment requirement lines."""
    return any(
        line.strip() and not line.lstrip().startswith("#")
        for line in text.splitlines()
    )


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
    mode: str = PACKAGE_ENV_BOOTSTRAP_MODE_DYNAMIC


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


@dataclass(frozen=True)
class _ResolvedPackageRequirements:
    requirements_path: Path
    requirements_text: str
    requirements_lock_path: Path | None
    requirements_lock_text: str | None
    constraints_text: str
    fingerprint: str


class PackageEnvironmentManager:
    """Verify image-baked environments or create isolated virtual environments per package."""

    def __init__(
        self,
        *,
        host_python: Path | None = None,
        constraints_path: Path = BACKEND_DEPENDENCY_LOCK,
        mode: str | None = None,
        prebuilt_envs_dir: Path | None = None,
    ) -> None:
        self._host_python = (host_python or Path(sys.executable)).expanduser().resolve(
            strict=False
        )
        self._constraints_path = constraints_path
        self._mode = (
            parse_package_env_bootstrap_mode(mode) if mode is not None else None
        )
        self._prebuilt_envs_dir = (
            prebuilt_envs_dir.expanduser().resolve(strict=False)
            if prebuilt_envs_dir is not None
            else None
        )

    def ensure_environment(self, package: LoadedPackage) -> PackageEnvironment:
        """Verify a prebuilt environment or create/reuse a runtime virtual environment."""
        try:
            mode = (
                self._mode
                if self._mode is not None
                else get_package_env_bootstrap_mode()
            )
        except ValueError as exc:
            raise PackageEnvironmentBootstrapError(
                package.package_id,
                str(exc),
                details={"env_var": PACKAGE_ENV_BOOTSTRAP_MODE_ENV_VAR},
            ) from exc

        resolved = self._resolve_requirements(package)
        if mode == PACKAGE_ENV_BOOTSTRAP_MODE_PREBUILT:
            return self._verify_prebuilt_environment(package, resolved)

        return self._ensure_dynamic_environment(package, resolved)

    def build_environment(
        self,
        package: LoadedPackage,
        *,
        target_dir: Path,
        require_hashed_lock: bool = True,
        run_pip_check: bool = True,
    ) -> PackageEnvironment:
        """Build a self-contained prebuilt environment for one package at image build time."""
        resolved = self._resolve_requirements(package)
        has_requirements = _has_requirement_entries(resolved.requirements_text)
        if require_hashed_lock and has_requirements:
            if resolved.requirements_lock_path is None or resolved.requirements_lock_text is None:
                raise PackageEnvironmentBootstrapError(
                    package.package_id,
                    (
                        f"Prebuilt package environment for '{package.package_id}' requires "
                        "a hashed requirements_lock_file in package.yaml because "
                        f"'{package.manifest.requirements_file}' declares dependencies"
                    ),
                    details={
                        "requirements_file": package.manifest.requirements_file,
                        "requirements_lock_file": package.manifest.requirements_lock_file,
                    },
                )
            if not _has_requirement_entries(resolved.requirements_lock_text):
                raise PackageEnvironmentBootstrapError(
                    package.package_id,
                    (
                        f"Hashed requirements lockfile for package '{package.package_id}' "
                        f"is empty: {resolved.requirements_lock_path}"
                    ),
                    details={"requirements_lock_path": str(resolved.requirements_lock_path)},
                )

        resolved_target_dir = target_dir.expanduser().resolve(strict=False)
        venv_dir = resolved_target_dir / DEFAULT_PACKAGE_RUNNER_VENV_DIRNAME
        metadata_path = resolved_target_dir / DEFAULT_PACKAGE_RUNNER_METADATA_FILENAME
        resolved_target_dir.mkdir(parents=True, exist_ok=True)

        if venv_dir.exists():
            shutil.rmtree(venv_dir)
        if metadata_path.exists():
            metadata_path.unlink()

        python_executable = self._create_and_install_venv(
            package,
            resolved,
            venv_dir=venv_dir,
            run_pip_check=run_pip_check,
        )
        metadata_payload = self._build_metadata_payload(
            package,
            resolved,
            python_executable=python_executable,
            prebuilt=True,
        )
        metadata_path.write_text(
            json.dumps(metadata_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return PackageEnvironment(
            package_id=package.package_id,
            package_version=package.version,
            venv_dir=venv_dir,
            python_executable=python_executable,
            requirements_path=resolved.requirements_path,
            fingerprint=resolved.fingerprint,
            reused=False,
            mode=PACKAGE_ENV_BOOTSTRAP_MODE_PREBUILT,
        )

    def _resolve_requirements(
        self, package: LoadedPackage
    ) -> _ResolvedPackageRequirements:
        requirements_path = (
            package.package_path / package.manifest.requirements_file
        ).expanduser().resolve(strict=False)
        if not requirements_path.is_file():
            raise PackageEnvironmentBootstrapError(
                package.package_id,
                f"Requirements file not found for package '{package.package_id}': {requirements_path}",
                details={"requirements_path": str(requirements_path)},
            )

        requirements_lock_path: Path | None = None
        requirements_lock_text: str | None = None
        if package.manifest.requirements_lock_file is not None:
            requirements_lock_path = (
                package.package_path / package.manifest.requirements_lock_file
            ).expanduser().resolve(strict=False)
            if not requirements_lock_path.is_file():
                raise PackageEnvironmentBootstrapError(
                    package.package_id,
                    (
                        f"Requirements lockfile not found for package '{package.package_id}': "
                        f"{requirements_lock_path}"
                    ),
                    details={"requirements_lock_path": str(requirements_lock_path)},
                )
            requirements_lock_text = requirements_lock_path.read_text(encoding="utf-8")

        if not self._constraints_path.is_file():
            raise PackageEnvironmentBootstrapError(
                package.package_id,
                f"Backend dependency lock not found: {self._constraints_path}",
                details={"constraints_path": str(self._constraints_path)},
            )

        requirements_text = requirements_path.read_text(encoding="utf-8")
        constraints_text = self._constraints_path.read_text(encoding="utf-8")
        fingerprint = self._build_fingerprint(
            package,
            requirements_text,
            constraints_text,
            requirements_lock_text=requirements_lock_text,
        )
        return _ResolvedPackageRequirements(
            requirements_path=requirements_path,
            requirements_text=requirements_text,
            requirements_lock_path=requirements_lock_path,
            requirements_lock_text=requirements_lock_text,
            constraints_text=constraints_text,
            fingerprint=fingerprint,
        )

    def _verify_prebuilt_environment(
        self,
        package: LoadedPackage,
        resolved: _ResolvedPackageRequirements,
    ) -> PackageEnvironment:
        """Verify an image-baked package environment with zero download fallback."""
        prebuilt_root = (
            self._prebuilt_envs_dir
            if self._prebuilt_envs_dir is not None
            else get_prebuilt_package_envs_dir()
        )
        env_dir = get_prebuilt_package_env_dir(
            package.package_id,
            prebuilt_envs_dir=prebuilt_root,
        )
        venv_dir = get_prebuilt_package_venv_dir(
            package.package_id,
            prebuilt_envs_dir=prebuilt_root,
        )
        metadata_path = get_prebuilt_package_metadata_path(
            package.package_id,
            prebuilt_envs_dir=prebuilt_root,
        )
        python_executable = self._resolve_venv_python(venv_dir)
        metadata = self._read_metadata(metadata_path)

        if metadata is None:
            raise PackageEnvironmentBootstrapError(
                package.package_id,
                (
                    f"Prebuilt package environment metadata missing or invalid for package "
                    f"'{package.package_id}' at {metadata_path}. Prebuilt mode does not "
                    "install packages at runtime; rebuild the backend image with this package."
                ),
                details={
                    "mode": PACKAGE_ENV_BOOTSTRAP_MODE_PREBUILT,
                    "prebuilt_env_dir": str(env_dir),
                    "metadata_path": str(metadata_path),
                    "expected_fingerprint": resolved.fingerprint,
                },
            )

        actual_package_id = metadata.get("package_id")
        if actual_package_id != package.package_id:
            raise PackageEnvironmentBootstrapError(
                package.package_id,
                (
                    f"Prebuilt package environment package_id mismatch at {metadata_path}: "
                    f"expected '{package.package_id}', found {actual_package_id!r}. "
                    "Prebuilt mode does not install packages at runtime; rebuild the backend image."
                ),
                details={
                    "mode": PACKAGE_ENV_BOOTSTRAP_MODE_PREBUILT,
                    "prebuilt_env_dir": str(env_dir),
                    "metadata_path": str(metadata_path),
                    "expected_package_id": package.package_id,
                    "actual_package_id": actual_package_id,
                },
            )

        actual_fingerprint = metadata.get("fingerprint")
        if actual_fingerprint != resolved.fingerprint:
            raise PackageEnvironmentBootstrapError(
                package.package_id,
                (
                    f"Prebuilt package environment fingerprint mismatch for package "
                    f"'{package.package_id}' at {metadata_path}: expected "
                    f"{resolved.fingerprint}, found {actual_fingerprint!r}. "
                    "Prebuilt mode does not install packages at runtime; rebuild the "
                    "backend image to match the mounted package."
                ),
                details={
                    "mode": PACKAGE_ENV_BOOTSTRAP_MODE_PREBUILT,
                    "prebuilt_env_dir": str(env_dir),
                    "metadata_path": str(metadata_path),
                    "expected_fingerprint": resolved.fingerprint,
                    "actual_fingerprint": actual_fingerprint,
                },
            )

        if not python_executable.is_file():
            raise PackageEnvironmentBootstrapError(
                package.package_id,
                (
                    f"Prebuilt package environment interpreter missing for package "
                    f"'{package.package_id}': {python_executable}. Prebuilt mode does not "
                    "install packages at runtime; rebuild the backend image."
                ),
                details={
                    "mode": PACKAGE_ENV_BOOTSTRAP_MODE_PREBUILT,
                    "prebuilt_env_dir": str(env_dir),
                    "python_executable": str(python_executable),
                    "expected_fingerprint": resolved.fingerprint,
                },
            )

        return PackageEnvironment(
            package_id=package.package_id,
            package_version=package.version,
            venv_dir=venv_dir,
            python_executable=python_executable,
            requirements_path=resolved.requirements_path,
            fingerprint=resolved.fingerprint,
            reused=True,
            mode=PACKAGE_ENV_BOOTSTRAP_MODE_PREBUILT,
        )

    def _ensure_dynamic_environment(
        self,
        package: LoadedPackage,
        resolved: _ResolvedPackageRequirements,
    ) -> PackageEnvironment:
        state_dir = get_package_runner_package_state_dir(package.package_id)
        venv_dir = get_package_runner_venv_dir(package.package_id)
        metadata_path = get_package_runner_metadata_path(package.package_id)
        state_dir.mkdir(parents=True, exist_ok=True)
        with self._bootstrap_lock(state_dir):
            python_executable = self._resolve_venv_python(venv_dir)
            metadata = self._read_metadata(metadata_path)
            if (
                metadata is not None
                and metadata.get("fingerprint") == resolved.fingerprint
                and python_executable.is_file()
            ):
                return PackageEnvironment(
                    package_id=package.package_id,
                    package_version=package.version,
                    venv_dir=venv_dir,
                    python_executable=python_executable,
                    requirements_path=resolved.requirements_path,
                    fingerprint=resolved.fingerprint,
                    reused=True,
                    mode=PACKAGE_ENV_BOOTSTRAP_MODE_DYNAMIC,
                )

            if venv_dir.exists():
                shutil.rmtree(venv_dir)
            if metadata_path.exists():
                metadata_path.unlink()

            python_executable = self._create_and_install_venv(
                package,
                resolved,
                venv_dir=venv_dir,
                run_pip_check=False,
            )

            metadata_payload = self._build_metadata_payload(
                package,
                resolved,
                python_executable=python_executable,
                prebuilt=False,
            )
            metadata_path.write_text(
                json.dumps(metadata_payload, indent=2, sort_keys=True),
                encoding="utf-8",
            )

            return PackageEnvironment(
                package_id=package.package_id,
                package_version=package.version,
                venv_dir=venv_dir,
                python_executable=python_executable,
                requirements_path=resolved.requirements_path,
                fingerprint=resolved.fingerprint,
                reused=False,
                mode=PACKAGE_ENV_BOOTSTRAP_MODE_DYNAMIC,
            )

    def _create_and_install_venv(
        self,
        package: LoadedPackage,
        resolved: _ResolvedPackageRequirements,
        *,
        venv_dir: Path,
        run_pip_check: bool,
    ) -> Path:
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

        if resolved.requirements_lock_path is not None and resolved.requirements_lock_text is not None:
            if _has_requirement_entries(resolved.requirements_lock_text):
                self._run_command(
                    [
                        str(python_executable),
                        "-m",
                        "pip",
                        "install",
                        "--no-cache-dir",
                        "--require-hashes",
                        "--no-deps",
                        "--no-build-isolation",
                        "-r",
                        str(resolved.requirements_lock_path),
                    ],
                    package_id=package.package_id,
                    step="install_requirements",
                )
        elif _has_requirement_entries(resolved.requirements_text):
            self._run_command(
                [
                    str(python_executable),
                    "-m",
                    "pip",
                    "install",
                    "-r",
                    str(resolved.requirements_path),
                    "-c",
                    str(self._constraints_path),
                ],
                package_id=package.package_id,
                step="install_requirements",
            )

        if run_pip_check:
            self._run_command(
                [str(python_executable), "-m", "pip", "check"],
                package_id=package.package_id,
                step="verify_requirements",
            )

        return python_executable

    def _build_metadata_payload(
        self,
        package: LoadedPackage,
        resolved: _ResolvedPackageRequirements,
        *,
        python_executable: Path,
        prebuilt: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "package_id": package.package_id,
            "package_version": package.version,
            "fingerprint": resolved.fingerprint,
            "prebuilt": prebuilt,
            "requirements_file": package.manifest.requirements_file,
            "requirements_sha256": hashlib.sha256(
                resolved.requirements_text.encode("utf-8")
            ).hexdigest(),
            "requirements_lock_file": package.manifest.requirements_lock_file,
            "requirements_lock_sha256": (
                hashlib.sha256(resolved.requirements_lock_text.encode("utf-8")).hexdigest()
                if resolved.requirements_lock_text is not None
                else None
            ),
            "constraints_sha256": hashlib.sha256(
                resolved.constraints_text.encode("utf-8")
            ).hexdigest(),
            "python_package_root": package.manifest.python_package_root,
            "python_executable": str(python_executable),
        }
        if not prebuilt:
            payload["requirements_path"] = str(resolved.requirements_path)
            payload["constraints_path"] = str(self._constraints_path)
            if resolved.requirements_lock_path is not None:
                payload["requirements_lock_path"] = str(resolved.requirements_lock_path)
        return payload

    def _build_fingerprint(
        self,
        package: LoadedPackage,
        requirements_text: str,
        constraints_text: str,
        *,
        requirements_lock_text: str | None = None,
    ) -> str:
        # Path-root independent: omits host/build mount roots so build-time and
        # runtime fingerprints match identically across /build/packages and /runtime/packages.
        payload = {
            "constraints_sha256": hashlib.sha256(
                constraints_text.encode("utf-8")
            ).hexdigest(),
            "package_id": package.package_id,
            "package_version": package.version,
            "python_package_root": package.manifest.python_package_root,
            "requirements_file": package.manifest.requirements_file,
            "requirements_lock_file": package.manifest.requirements_lock_file,
            "requirements_lock_sha256": (
                hashlib.sha256(requirements_lock_text.encode("utf-8")).hexdigest()
                if requirements_lock_text is not None
                else None
            ),
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
        # Preserve the venv-local interpreter path. Resolving symlinks here can
        # collapse back to the base interpreter and defeat isolation.
        return (venv_dir / bin_dir / executable).expanduser()

    @contextmanager
    def _bootstrap_lock(self, state_dir: Path) -> Iterator[None]:
        """Serialize environment bootstrap so parallel tool calls cannot race."""
        lock_path = state_dir / ".bootstrap.lock"
        with lock_path.open("a+", encoding="utf-8") as handle:
            if os.name != "nt":
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                return

            yield

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

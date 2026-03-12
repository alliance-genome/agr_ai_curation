"""Shared helpers for package-owned YAML defaults plus runtime override files."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from src.lib.packages import ExportKind, load_package_registry
from src.lib.packages.paths import get_runtime_config_dir, get_runtime_packages_dir

_RUNTIME_CONFIG_FILENAMES = (
    "models.yaml",
    "providers.yaml",
    "tool_policy_defaults.yaml",
)


@dataclass(frozen=True)
class YamlConfigSource:
    """One YAML-backed config source with stable provenance metadata."""

    label: str
    path: Path
    payload: dict[str, Any]

    def describe(self) -> str:
        """Return a concise human-readable source description."""
        return f"{self.label} at {self.path}"


def _find_project_root() -> Path | None:
    """Find the repository root by looking for common project markers."""
    current = Path(__file__).resolve()
    if current.is_file():
        current = current.parent

    for candidate in (current, *current.parents):
        if (candidate / "docker-compose.test.yml").exists():
            return candidate
        if (candidate / "backend").is_dir() and (candidate / "packages").is_dir():
            return candidate
        config_dir = candidate / "config"
        if (candidate / "packages").is_dir() and any(
            (config_dir / filename).exists()
            for filename in _RUNTIME_CONFIG_FILENAMES
        ):
            return candidate
    return None


def get_default_packages_dir() -> Path:
    """Return the effective default packages directory."""
    runtime_packages_dir = get_runtime_packages_dir()
    if runtime_packages_dir.exists():
        return runtime_packages_dir

    project_root = _find_project_root()
    if project_root:
        return project_root / "packages"

    return runtime_packages_dir


def resolve_packages_dir(packages_dir: Path | None) -> Path:
    """Resolve the package search root without requiring it to exist."""
    return (packages_dir or get_default_packages_dir()).expanduser().resolve(strict=False)


def _resolve_runtime_override_path(
    explicit_path: Path | None,
    *,
    env_var: str,
    filename: str,
) -> tuple[Path, bool]:
    """Resolve a runtime override file path and whether it was explicitly configured."""
    if explicit_path is not None:
        return explicit_path.expanduser().resolve(strict=False), True

    env_path = os.environ.get(env_var)
    if env_path:
        return Path(env_path).expanduser().resolve(strict=False), True

    runtime_path = (get_runtime_config_dir() / filename).expanduser().resolve(strict=False)
    if runtime_path.exists():
        return runtime_path, False

    project_root = _find_project_root()
    if project_root:
        return (project_root / "config" / filename).expanduser().resolve(strict=False), False

    return runtime_path, False


def _load_yaml_mapping(path: Path, *, label: str) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"{label} not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in {label} at {path}: {exc}") from exc

    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(
            f"{label} at {path} must contain a top-level YAML mapping, found "
            f"{type(data).__name__}"
        )
    return data


def load_package_yaml_sources(
    *,
    export_kind: ExportKind,
    packages_dir: Path | None = None,
) -> tuple[YamlConfigSource, ...]:
    """Load all YAML exports of one kind from the active runtime package registry."""
    resolved_packages_dir = resolve_packages_dir(packages_dir)
    if not resolved_packages_dir.exists():
        return ()

    registry = load_package_registry(
        resolved_packages_dir,
        fail_on_validation_error=True,
    )
    sources: list[YamlConfigSource] = []

    for package in registry.loaded_packages:
        exports = sorted(
            (
                export
                for export in package.manifest.exports
                if export.kind == export_kind
            ),
            key=lambda item: (item.name, item.path),
        )
        for export in exports:
            export_path = (package.package_path / export.path).expanduser().resolve(strict=False)
            label = f"package default '{package.package_id}' export '{export.name}'"
            payload = _load_yaml_mapping(export_path, label=label)
            sources.append(
                YamlConfigSource(
                    label=label,
                    path=export_path,
                    payload=payload,
                )
            )

    return tuple(sources)


def load_optional_runtime_yaml_source(
    *,
    explicit_path: Path | None,
    env_var: str,
    filename: str,
) -> YamlConfigSource | None:
    """Load the runtime override YAML file when present."""
    resolved_path, explicitly_configured = _resolve_runtime_override_path(
        explicit_path,
        env_var=env_var,
        filename=filename,
    )
    if not resolved_path.exists():
        if explicitly_configured:
            raise FileNotFoundError(f"Runtime override file not found: {resolved_path}")
        return None

    label = f"runtime override '{filename}'"
    payload = _load_yaml_mapping(resolved_path, label=label)
    return YamlConfigSource(label=label, path=resolved_path, payload=payload)

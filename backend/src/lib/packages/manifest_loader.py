"""Load and validate runtime package contract files."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import TypeVar

import yaml
from pydantic import BaseModel, ValidationError

from .models import ExportKind, PackageManifest, RuntimeOverrides, ToolBindingsManifest

T = TypeVar("T", bound=BaseModel)


class PackageContractError(ValueError):
    """Base error for invalid runtime package contract files."""


class PackageManifestError(PackageContractError):
    """Raised when ``package.yaml`` is missing or invalid."""


class AgentBundleRegistrationError(PackageContractError):
    """Raised when package-owned agent bundles are not declared in ``agent_bundles``."""


class ToolBindingsError(PackageContractError):
    """Raised when ``tools/bindings.yaml`` is missing or invalid."""


class RuntimeOverridesError(PackageContractError):
    """Raised when ``runtime/config/overrides.yaml`` is missing or invalid."""


def _load_yaml_mapping(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except FileNotFoundError as exc:
        raise PackageContractError(f"Contract file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise PackageContractError(f"Invalid YAML in {path}: {exc}") from exc

    if data is None:
        raise PackageContractError(f"{path} is empty; expected a YAML mapping")
    if not isinstance(data, dict):
        raise PackageContractError(
            f"{path} must contain a top-level YAML mapping, found {type(data).__name__}"
        )
    return data


def _format_validation_error(error: ValidationError) -> str:
    parts: list[str] = []
    for item in error.errors():
        location = ".".join(str(part) for part in item["loc"]) or "model"
        parts.append(f"{location}: {item['msg']}")
    return "; ".join(parts)


def _load_contract_model(path: Path, model_type: type[T], error_type: type[PackageContractError]) -> T:
    try:
        raw_data = _load_yaml_mapping(path)
        return model_type.model_validate(raw_data)
    except ValidationError as exc:
        details = _format_validation_error(exc)
        raise error_type(f"Invalid {path.name} at {path}: {details}") from exc
    except PackageContractError as exc:
        raise error_type(str(exc)) from exc


def _collect_undeclared_agent_bundle_directories(
    path: Path,
    manifest: PackageManifest,
    raw_data: dict,
) -> tuple[str, ...]:
    """Return package-owned agent bundles that exist on disk but are not declared."""
    package_dir = path.parent
    declared_bundle_paths: set[tuple[str, str]] = set()
    for export in manifest.exports:
        if export.kind is not ExportKind.AGENT:
            continue
        export_path = PurePosixPath(export.path)
        agents_dir = export_path.parent.as_posix()
        declared_bundle_paths.add((agents_dir, export_path.name))

    bundle_payloads = raw_data.get("agent_bundles") or []
    agent_roots: dict[str, Path] = {}
    if bundle_payloads:
        for bundle in bundle_payloads:
            agents_dir = str(PurePosixPath(str(bundle.get("agents_dir", "agents"))))
            agent_roots.setdefault(agents_dir, package_dir / agents_dir)
    else:
        agent_roots["agents"] = package_dir / "agents"

    missing_bundle_dirs: list[str] = []
    for agents_dir, agents_root in sorted(agent_roots.items()):
        if not agents_root.exists() or not agents_root.is_dir():
            continue

        for child in sorted(agents_root.iterdir(), key=lambda item: item.name):
            if not child.is_dir() or child.name.startswith(("_", ".")):
                continue
            if not (child / "agent.yaml").exists():
                continue
            if (agents_dir, child.name) in declared_bundle_paths:
                continue
            missing_bundle_dirs.append(
                child.name if agents_dir == "." else f"{agents_dir}/{child.name}"
            )

    return tuple(missing_bundle_dirs)


def _format_agent_bundle_registration_error(path: Path, missing_bundle_dirs: tuple[str, ...]) -> str:
    missing_list = ", ".join(missing_bundle_dirs)
    return (
        f"Invalid {path.name} at {path}: agent_bundles is missing package-owned "
        f"agent directories with agent.yaml: {missing_list}. Add each bundle name "
        "to agent_bundles to activate it."
    )


def validate_agent_bundle_directory_registration(
    path: Path,
    manifest: PackageManifest,
) -> None:
    """Raise when package-owned agent bundles exist on disk but are undeclared."""
    try:
        raw_data = _load_yaml_mapping(path)
    except PackageContractError as exc:
        raise AgentBundleRegistrationError(str(exc)) from exc

    missing_bundle_dirs = _collect_undeclared_agent_bundle_directories(path, manifest, raw_data)
    if not missing_bundle_dirs:
        return

    raise AgentBundleRegistrationError(
        _format_agent_bundle_registration_error(path, missing_bundle_dirs)
    )


def load_package_manifest(path: Path) -> PackageManifest:
    """Load and validate a package manifest file."""
    try:
        raw_data = _load_yaml_mapping(path)
        return PackageManifest.model_validate(dict(raw_data))
    except ValidationError as exc:
        details = _format_validation_error(exc)
        raise PackageManifestError(f"Invalid {path.name} at {path}: {details}") from exc
    except PackageContractError as exc:
        raise PackageManifestError(str(exc)) from exc


def load_tool_bindings(path: Path) -> ToolBindingsManifest:
    """Load and validate a tool bindings contract file."""
    return _load_contract_model(path, ToolBindingsManifest, ToolBindingsError)


def load_runtime_overrides(path: Path) -> RuntimeOverrides:
    """Load and validate a deployment override contract file."""
    return _load_contract_model(path, RuntimeOverrides, RuntimeOverridesError)

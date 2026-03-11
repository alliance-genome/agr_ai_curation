"""Load and validate runtime package contract files."""

from __future__ import annotations

from pathlib import Path
from typing import TypeVar

import yaml
from pydantic import BaseModel, ValidationError

from .models import PackageManifest, RuntimeOverrides, ToolBindingsManifest

T = TypeVar("T", bound=BaseModel)


class PackageContractError(ValueError):
    """Base error for invalid runtime package contract files."""


class PackageManifestError(PackageContractError):
    """Raised when ``package.yaml`` is missing or invalid."""


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


def load_package_manifest(path: Path) -> PackageManifest:
    """Load and validate a package manifest file."""
    return _load_contract_model(path, PackageManifest, PackageManifestError)


def load_tool_bindings(path: Path) -> ToolBindingsManifest:
    """Load and validate a tool bindings contract file."""
    return _load_contract_model(path, ToolBindingsManifest, ToolBindingsError)


def load_runtime_overrides(path: Path) -> RuntimeOverrides:
    """Load and validate a deployment override contract file."""
    return _load_contract_model(path, RuntimeOverrides, RuntimeOverridesError)

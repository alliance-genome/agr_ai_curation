"""Load and validate provider-agnostic domain-pack contract files."""

from __future__ import annotations

from pathlib import Path
from typing import TypeVar

import yaml
from pydantic import BaseModel, ValidationError

from src.schemas.domain_pack_metadata import DomainFixturePack, DomainPackMetadata

T = TypeVar("T", bound=BaseModel)


class DomainPackContractError(ValueError):
    """Base error for invalid domain-pack contract files."""


class DomainPackMetadataError(DomainPackContractError):
    """Raised when ``domain_pack.yaml`` is missing or invalid."""


class DomainFixturePackError(DomainPackContractError):
    """Raised when a fixture-pack contract is missing or invalid."""


def _load_yaml_mapping(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except FileNotFoundError as exc:
        raise DomainPackContractError(f"Contract file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise DomainPackContractError(f"Invalid YAML in {path}: {exc}") from exc

    if data is None:
        raise DomainPackContractError(f"{path} is empty; expected a YAML mapping")
    if not isinstance(data, dict):
        raise DomainPackContractError(
            f"{path} must contain a top-level YAML mapping, found {type(data).__name__}"
        )
    return data


def _format_validation_error(error: ValidationError) -> str:
    parts: list[str] = []
    for item in error.errors():
        location = ".".join(str(part) for part in item["loc"]) or "model"
        parts.append(f"{location}: {item['msg']}")
    return "; ".join(parts)


def _load_contract_model(
    path: Path,
    model_type: type[T],
    error_type: type[DomainPackContractError],
) -> T:
    try:
        raw_data = _load_yaml_mapping(path)
        return model_type.model_validate(raw_data)
    except ValidationError as exc:
        details = _format_validation_error(exc)
        raise error_type(f"Invalid {path.name} at {path}: {details}") from exc
    except DomainPackContractError as exc:
        raise error_type(str(exc)) from exc


def load_domain_pack_metadata(path: Path) -> DomainPackMetadata:
    """Load and validate a domain-pack metadata file."""

    metadata = _load_contract_model(path, DomainPackMetadata, DomainPackMetadataError)
    try:
        from .supervisor_manifest import validate_supervisor_manifest_policies

        validate_supervisor_manifest_policies(metadata)
    except ValueError as exc:
        raise DomainPackMetadataError(
            f"Invalid {path.name} at {path}: {exc}"
        ) from exc
    return metadata


def load_domain_fixture_pack(path: Path) -> DomainFixturePack:
    """Load and validate a provider-neutral fixture-pack file."""

    return _load_contract_model(path, DomainFixturePack, DomainFixturePackError)


__all__ = [
    "DomainFixturePackError",
    "DomainPackContractError",
    "DomainPackMetadataError",
    "load_domain_fixture_pack",
    "load_domain_pack_metadata",
]

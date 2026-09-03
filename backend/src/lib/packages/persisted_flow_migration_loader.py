"""Load package-owned forward migrations for persisted flow definitions."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from .models import ExportKind
from .registry import PackageRegistry, load_package_registry
from .tool_registry import resolve_default_packages_dir


class RetiredFlowAttachment(BaseModel):
    """One exact attachment selection removed by a forward migration."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    attachment_id: str = Field(min_length=1)
    validator_binding_id: str | None


class PersistedFlowMigration(BaseModel):
    """A package-owned, narrowly targeted saved-flow repair."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    migration_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    retired_binding_id: str = Field(min_length=1)
    retired_attachments: tuple[RetiredFlowAttachment, ...] = Field(min_length=1)

    @field_validator("retired_attachments", mode="before")
    @classmethod
    def _freeze_retired_attachments(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("migration_id", "agent_id", "retired_binding_id")
    @classmethod
    def _reject_surrounding_whitespace(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("must not include surrounding whitespace")
        return value

    @model_validator(mode="after")
    def _require_unique_attachment_ids(self) -> "PersistedFlowMigration":
        attachment_ids = [item.attachment_id for item in self.retired_attachments]
        duplicates = sorted(
            {item for item in attachment_ids if attachment_ids.count(item) > 1}
        )
        if duplicates:
            raise ValueError(
                "retired_attachments contain duplicate attachment IDs: "
                + ", ".join(duplicates)
            )
        return self


class PersistedFlowMigrationManifest(BaseModel):
    """Strict versioned contract for one migration export."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    persisted_flow_migrations_api_version: Literal["1.0.0"]
    migrations: tuple[PersistedFlowMigration, ...] = ()

    @field_validator("migrations", mode="before")
    @classmethod
    def _freeze_migrations(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value


@dataclass(frozen=True, slots=True)
class LoadedPersistedFlowMigrationManifest:
    """One validated package contribution with source provenance."""

    package_id: str
    export_name: str
    source_path: Path
    manifest: PersistedFlowMigrationManifest


@dataclass(frozen=True, slots=True)
class PersistedFlowMigrationCatalog:
    """Merged migration declarations from all installed packages."""

    contributions: tuple[LoadedPersistedFlowMigrationManifest, ...]

    @property
    def migrations(self) -> tuple[PersistedFlowMigration, ...]:
        return tuple(
            migration
            for contribution in self.contributions
            for migration in contribution.manifest.migrations
        )


class PersistedFlowMigrationLoadError(ValueError):
    """Raised when package migration metadata is invalid or ambiguous."""


def _format_validation_error(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in item['loc']) or 'model'}: {item['msg']}"
        for item in exc.errors()
    )


def _load_contributions(
    registry: PackageRegistry,
) -> tuple[LoadedPersistedFlowMigrationManifest, ...]:
    loaded: list[LoadedPersistedFlowMigrationManifest] = []
    for package in registry.loaded_packages:
        for export in package.manifest.exports:
            if export.kind is not ExportKind.PERSISTED_FLOW_MIGRATIONS:
                continue
            source_path = (package.package_path / export.path).resolve(strict=False)
            try:
                source_path.relative_to(package.package_path.resolve(strict=False))
            except ValueError as exc:
                raise PersistedFlowMigrationLoadError(
                    f"Persisted-flow migration export '{export.name}' from package "
                    f"'{package.package_id}' resolves outside its package root: {source_path}"
                ) from exc
            try:
                with source_path.open("r", encoding="utf-8") as handle:
                    raw_data = yaml.safe_load(handle)
            except FileNotFoundError as exc:
                raise PersistedFlowMigrationLoadError(
                    f"Persisted-flow migration export '{export.name}' from package "
                    f"'{package.package_id}' was not found at {source_path}"
                ) from exc
            except yaml.YAMLError as exc:
                raise PersistedFlowMigrationLoadError(
                    f"Invalid YAML in persisted-flow migration export '{export.name}' "
                    f"from package '{package.package_id}' at {source_path}: {exc}"
                ) from exc
            if not isinstance(raw_data, dict):
                raise PersistedFlowMigrationLoadError(
                    f"Persisted-flow migration export '{export.name}' from package "
                    f"'{package.package_id}' at {source_path} must contain a YAML mapping"
                )
            try:
                manifest = PersistedFlowMigrationManifest.model_validate(raw_data)
            except ValidationError as exc:
                raise PersistedFlowMigrationLoadError(
                    f"Invalid persisted-flow migration export '{export.name}' from "
                    f"package '{package.package_id}' at {source_path}: "
                    f"{_format_validation_error(exc)}"
                ) from exc
            loaded.append(
                LoadedPersistedFlowMigrationManifest(
                    package_id=package.package_id,
                    export_name=export.name,
                    source_path=source_path,
                    manifest=manifest,
                )
            )
    return tuple(loaded)


def build_persisted_flow_migration_catalog(
    registry: PackageRegistry,
) -> PersistedFlowMigrationCatalog:
    """Load declarations and reject cross-package migration ambiguity."""

    contributions = _load_contributions(registry)
    migration_owners: dict[str, LoadedPersistedFlowMigrationManifest] = {}
    attachment_owners: dict[str, LoadedPersistedFlowMigrationManifest] = {}
    for contribution in contributions:
        for migration in contribution.manifest.migrations:
            existing = migration_owners.get(migration.migration_id)
            if existing is not None:
                raise PersistedFlowMigrationLoadError(
                    f"Persisted-flow migration ID collision '{migration.migration_id}' "
                    f"between {existing.package_id}:{existing.source_path} and "
                    f"{contribution.package_id}:{contribution.source_path}"
                )
            migration_owners[migration.migration_id] = contribution
            for attachment in migration.retired_attachments:
                existing = attachment_owners.get(attachment.attachment_id)
                if existing is not None:
                    raise PersistedFlowMigrationLoadError(
                        "Retired persisted-flow attachment collision "
                        f"'{attachment.attachment_id}' between "
                        f"{existing.package_id}:{existing.source_path} and "
                        f"{contribution.package_id}:{contribution.source_path}"
                    )
                attachment_owners[attachment.attachment_id] = contribution
    return PersistedFlowMigrationCatalog(contributions=contributions)


@cache
def _load_persisted_flow_migration_catalog_for_path(
    packages_dir: Path,
) -> PersistedFlowMigrationCatalog:
    registry = load_package_registry(packages_dir, fail_on_validation_error=True)
    return build_persisted_flow_migration_catalog(registry)


def load_persisted_flow_migration_catalog(
    packages_dir: Path | None = None,
) -> PersistedFlowMigrationCatalog:
    """Load migration declarations from the active package directory."""

    resolved = (packages_dir or resolve_default_packages_dir()).resolve(strict=False)
    return _load_persisted_flow_migration_catalog_for_path(resolved)


__all__ = [
    "PersistedFlowMigration",
    "PersistedFlowMigrationCatalog",
    "PersistedFlowMigrationLoadError",
    "RetiredFlowAttachment",
    "build_persisted_flow_migration_catalog",
    "load_persisted_flow_migration_catalog",
]

"""Load and merge package-declared document-source provider exports."""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path

from src.lib.document_sources.registration import (
    DocumentSourceProviderRegistration,
)

from .import_paths import extend_sys_path_for_package
from .models import ExportKind, PackageExport
from .registry import LoadedPackage, PackageRegistry, load_package_registry
from .tool_registry import resolve_default_packages_dir


REGISTRATION_ATTRIBUTE = "DOCUMENT_SOURCE_PROVIDER_REGISTRATION"


@dataclass(frozen=True, slots=True)
class DocumentSourceProviderExportSource:
    """Package and manifest provenance for one provider registration."""

    package_id: str
    package_version: str
    package_display_name: str
    package_path: Path
    manifest_path: Path
    export_name: str
    export_description: str
    module_path: Path

    def describe(self, provider_id: str) -> str:
        """Return deterministic, non-secret registration provenance."""

        return (
            f"provider_id '{provider_id}' from package '{self.package_id}', "
            f"manifest '{self.manifest_path}', export '{self.export_name}', "
            f"path '{self.module_path}'"
        )


@dataclass(frozen=True, slots=True)
class RegisteredDocumentSourceProvider:
    """One validated registration paired with its package provenance."""

    registration: DocumentSourceProviderRegistration
    source: DocumentSourceProviderExportSource

    @property
    def provider_id(self) -> str:
        return self.registration.provider_id


@dataclass(frozen=True, slots=True)
class DocumentSourceProviderCollision:
    """All package exports that declare one provider ID."""

    provider_id: str
    candidates: tuple[RegisteredDocumentSourceProvider, ...]


class DocumentSourceProviderRegistryValidationError(ValueError):
    """Raised when package-owned provider registrations are unsafe to use."""


class DocumentSourceProviderExportLoadError(ValueError):
    """Raised when one manifest export cannot produce a valid registration."""


@dataclass(frozen=True, slots=True)
class DocumentSourceProviderRegistry:
    """Merged external-provider registry with collision diagnostics."""

    package_registry: PackageRegistry
    providers: tuple[RegisteredDocumentSourceProvider, ...]
    collisions: tuple[DocumentSourceProviderCollision, ...] = ()
    validation_errors: tuple[str, ...] = ()

    @property
    def providers_by_id(self) -> dict[str, RegisteredDocumentSourceProvider]:
        return {provider.provider_id: provider for provider in self.providers}

    def get(self, provider_id: str) -> RegisteredDocumentSourceProvider | None:
        return self.providers_by_id.get(provider_id.strip().lower())

    def raise_for_validation_errors(self) -> None:
        if self.validation_errors:
            raise DocumentSourceProviderRegistryValidationError(
                "; ".join(self.validation_errors)
            )


def load_document_source_provider_registry(
    packages_dir: Path | None = None,
    *,
    runtime_version: str | None = None,
    supported_package_api_version: str | None = None,
    fail_on_validation_error: bool = True,
) -> DocumentSourceProviderRegistry:
    """Load packages and build their document-source provider registry."""

    package_registry = load_package_registry(
        packages_dir or resolve_default_packages_dir(),
        runtime_version=runtime_version,
        supported_package_api_version=supported_package_api_version,
        fail_on_validation_error=fail_on_validation_error,
    )
    return build_document_source_provider_registry(
        package_registry,
        fail_on_validation_error=fail_on_validation_error,
    )


def build_document_source_provider_registry(
    package_registry: PackageRegistry,
    *,
    fail_on_validation_error: bool = True,
) -> DocumentSourceProviderRegistry:
    """Merge dedicated provider exports and reject every provider-ID collision."""

    validation_errors = list(package_registry.validation_errors)
    candidates_by_id: dict[str, list[RegisteredDocumentSourceProvider]] = {}

    for package in package_registry.loaded_packages:
        for export in package.manifest.exports:
            if export.kind is not ExportKind.DOCUMENT_SOURCE_PROVIDER:
                continue
            try:
                loaded = _load_provider_export(package, export)
            except DocumentSourceProviderExportLoadError as exc:
                validation_errors.append(str(exc))
                continue
            candidates_by_id.setdefault(loaded.provider_id, []).append(loaded)

    providers: list[RegisteredDocumentSourceProvider] = []
    collisions: list[DocumentSourceProviderCollision] = []
    for provider_id, raw_candidates in sorted(candidates_by_id.items()):
        candidates = tuple(
            sorted(
                raw_candidates,
                key=lambda candidate: (
                    candidate.source.package_id,
                    candidate.source.export_name,
                    str(candidate.source.module_path),
                ),
            )
        )
        if len(candidates) == 1:
            providers.append(candidates[0])
            continue

        collisions.append(
            DocumentSourceProviderCollision(
                provider_id=provider_id,
                candidates=candidates,
            )
        )
        provenance = "; ".join(
            candidate.source.describe(provider_id) for candidate in candidates
        )
        validation_errors.append(
            f"Duplicate document-source provider registration: {provenance}"
        )

    registry = DocumentSourceProviderRegistry(
        package_registry=package_registry,
        providers=tuple(providers),
        collisions=tuple(collisions),
        validation_errors=tuple(validation_errors),
    )
    if fail_on_validation_error:
        registry.raise_for_validation_errors()
    return registry


def _load_provider_export(
    package: LoadedPackage,
    export: PackageExport,
) -> RegisteredDocumentSourceProvider:
    module_path = (package.package_path / export.path).expanduser().resolve(strict=False)
    source = DocumentSourceProviderExportSource(
        package_id=package.package_id,
        package_version=package.version,
        package_display_name=package.display_name,
        package_path=package.package_path,
        manifest_path=package.manifest_path,
        export_name=export.name,
        export_description=export.description,
        module_path=module_path,
    )
    provider_hint = export.name

    if not module_path.exists():
        raise DocumentSourceProviderExportLoadError(
            f"Document-source {source.describe(provider_hint)} does not exist"
        )
    if module_path.suffix != ".py":
        raise DocumentSourceProviderExportLoadError(
            f"Document-source {source.describe(provider_hint)} must point to a Python module"
        )

    extend_sys_path_for_package(package)
    module_name = _module_name_for_export(package, export)
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise DocumentSourceProviderExportLoadError(
            f"Document-source {source.describe(provider_hint)} could not create a module spec"
        )

    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise DocumentSourceProviderExportLoadError(
            f"Document-source {source.describe(provider_hint)} failed to import "
            f"({type(exc).__name__})"
        ) from exc

    registration = getattr(module, REGISTRATION_ATTRIBUTE, None)
    if not isinstance(registration, DocumentSourceProviderRegistration):
        raise DocumentSourceProviderExportLoadError(
            f"Document-source {source.describe(provider_hint)} must define "
            f"'{REGISTRATION_ATTRIBUTE}' as a DocumentSourceProviderRegistration"
        )

    if registration.provider_id != export.name:
        raise DocumentSourceProviderExportLoadError(
            f"Document-source {source.describe(registration.provider_id)} must use "
            f"the provider ID as its export name; got '{export.name}'"
        )

    return RegisteredDocumentSourceProvider(
        registration=registration,
        source=source,
    )


def _module_name_for_export(package: LoadedPackage, export: PackageExport) -> str:
    package_segment = package.package_id.replace(".", "_").replace("-", "_")
    export_segment = export.name.replace(".", "_").replace("-", "_")
    return f"_agr_document_source_provider_{package_segment}_{export_segment}"

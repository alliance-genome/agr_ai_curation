"""Load package-declared external document-source provider registrations."""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from .document_source_provider_models import DocumentSourceProviderRegistration
from .import_paths import extend_sys_path_for_package
from .models import ExportKind, PackageExport
from .registry import LoadedPackage, PackageRegistry, load_package_registry


_LOCAL_PDF_PROVIDER_ID = "local_pdf"


class DocumentSourceProviderLoadError(ValueError):
    """Raised when package-owned document-source registrations are invalid."""


@dataclass(frozen=True, slots=True)
class DocumentSourceProviderSource:
    """Package provenance for one external document-source provider."""

    package_id: str
    manifest_path: Path
    export_name: str
    module_path: Path

    def describe(self) -> str:
        return (
            f"package '{self.package_id}' manifest {self.manifest_path}, "
            f"export '{self.export_name}' at {self.module_path}"
        )


@dataclass(frozen=True, slots=True)
class LoadedDocumentSourceProviderRegistration:
    """One validated registration plus its package provenance."""

    registration: DocumentSourceProviderRegistration
    source: DocumentSourceProviderSource


@dataclass(frozen=True, slots=True)
class DocumentSourceProviderCatalog:
    """Immutable catalog of package-owned external document-source providers."""

    registrations: tuple[LoadedDocumentSourceProviderRegistration, ...]

    @property
    def registrations_by_provider_id(
        self,
    ) -> dict[str, LoadedDocumentSourceProviderRegistration]:
        return {
            loaded.registration.provider_id: loaded for loaded in self.registrations
        }

    def get(self, provider_id: str) -> LoadedDocumentSourceProviderRegistration | None:
        return self.registrations_by_provider_id.get(provider_id.strip().lower())


def load_package_document_source_provider_exports(
    package: LoadedPackage,
) -> tuple[LoadedDocumentSourceProviderRegistration, ...]:
    """Load every document-source provider export declared by one package."""

    loaded: list[LoadedDocumentSourceProviderRegistration] = []
    for export in package.manifest.exports:
        if export.kind is not ExportKind.DOCUMENT_SOURCE_PROVIDER:
            continue
        loaded.extend(_load_export(package, export))
    return tuple(loaded)


def _load_export(
    package: LoadedPackage,
    export: PackageExport,
) -> tuple[LoadedDocumentSourceProviderRegistration, ...]:
    module_path = (package.package_path / export.path).expanduser().resolve(strict=False)
    source = DocumentSourceProviderSource(
        package_id=package.package_id,
        manifest_path=package.manifest_path,
        export_name=export.name,
        module_path=module_path,
    )
    if not module_path.is_file():
        raise DocumentSourceProviderLoadError(
            f"Document-source provider {source.describe()} does not exist"
        )
    if module_path.suffix != ".py":
        raise DocumentSourceProviderLoadError(
            f"Document-source provider {source.describe()} must point to a Python module"
        )

    extend_sys_path_for_package(package)
    module_name = _module_name_for_export(package, export)
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise DocumentSourceProviderLoadError(
            f"Could not create a module spec for document-source provider {source.describe()}"
        )
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise DocumentSourceProviderLoadError(
            f"Failed to import document-source provider {source.describe()}: {exc}"
        ) from exc

    registration_hook = getattr(
        module,
        "get_document_source_provider_registrations",
        None,
    )
    if not callable(registration_hook):
        raise DocumentSourceProviderLoadError(
            f"Document-source provider {source.describe()} must define callable "
            "get_document_source_provider_registrations()"
        )
    try:
        registrations = registration_hook()
    except Exception as exc:
        raise DocumentSourceProviderLoadError(
            f"Failed to enumerate document-source provider {source.describe()}: {exc}"
        ) from exc

    if not isinstance(registrations, (list, tuple)):
        raise DocumentSourceProviderLoadError(
            f"Document-source provider {source.describe()} must return a list or tuple "
            "of DocumentSourceProviderRegistration values"
        )
    loaded: list[LoadedDocumentSourceProviderRegistration] = []
    for index, registration in enumerate(registrations):
        if not isinstance(registration, DocumentSourceProviderRegistration):
            raise DocumentSourceProviderLoadError(
                f"Document-source provider {source.describe()} returned invalid registration "
                f"at index {index}; expected DocumentSourceProviderRegistration"
            )
        loaded.append(
            LoadedDocumentSourceProviderRegistration(
                registration=registration,
                source=source,
            )
        )
    return tuple(loaded)


def build_document_source_provider_catalog(
    registry: PackageRegistry,
) -> DocumentSourceProviderCatalog:
    """Build the external-provider catalog and reject ambiguous provider IDs."""

    registrations: list[LoadedDocumentSourceProviderRegistration] = []
    owners: dict[str, LoadedDocumentSourceProviderRegistration] = {}
    for package in registry.loaded_packages:
        for loaded in load_package_document_source_provider_exports(package):
            provider_id = loaded.registration.provider_id
            if provider_id == _LOCAL_PDF_PROVIDER_ID:
                raise DocumentSourceProviderLoadError(
                    f"Document-source provider ID '{provider_id}' is reserved for the "
                    f"built-in local upload flow and cannot be registered by "
                    f"{loaded.source.describe()}"
                )
            existing = owners.get(provider_id)
            if existing is not None:
                raise DocumentSourceProviderLoadError(
                    f"Document-source provider ID collision '{provider_id}' between "
                    f"{existing.source.describe()} and {loaded.source.describe()}"
                )
            owners[provider_id] = loaded
            registrations.append(loaded)

    return DocumentSourceProviderCatalog(registrations=tuple(registrations))


@cache
def _load_document_source_provider_catalog_for_path(
    packages_dir: Path,
) -> DocumentSourceProviderCatalog:
    registry = load_package_registry(packages_dir, fail_on_validation_error=True)
    return build_document_source_provider_catalog(registry)


def load_document_source_provider_catalog(
    packages_dir: Path | None = None,
) -> DocumentSourceProviderCatalog:
    """Load the catalog for the active runtime package directory."""

    if packages_dir is None:
        from .tool_registry import resolve_default_packages_dir

        packages_dir = resolve_default_packages_dir()
    return _load_document_source_provider_catalog_for_path(
        packages_dir.expanduser().resolve(strict=False)
    )


def _module_name_for_export(package: LoadedPackage, export: PackageExport) -> str:
    package_segment = package.package_id.replace(".", "_").replace("-", "_")
    export_segment = export.name.replace(".", "_").replace("-", "_")
    return f"_document_source_provider_{package_segment}_{export_segment}"

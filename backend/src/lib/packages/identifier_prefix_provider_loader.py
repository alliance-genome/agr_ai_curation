"""Load package-declared identifier-prefix provider exports."""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .import_paths import extend_sys_path_for_package
from .models import ExportKind, PackageExport
from .registry import LoadedPackage, PackageRegistry


IdentifierPrefixProvider = Callable[[str], object]


class IdentifierPrefixProviderLoadError(ValueError):
    """Raised when a package-owned identifier-prefix provider is invalid."""


@dataclass(frozen=True, slots=True)
class IdentifierPrefixProviderSource:
    """Package provenance for one identifier-prefix provider."""

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
class LoadedIdentifierPrefixProvider:
    """One validated provider callable plus its package provenance."""

    provider: IdentifierPrefixProvider
    source: IdentifierPrefixProviderSource


@dataclass(frozen=True, slots=True)
class IdentifierPrefixProviderCatalog:
    """All identifier-prefix providers installed by loaded packages."""

    providers: tuple[LoadedIdentifierPrefixProvider, ...]


def load_package_identifier_prefix_provider_exports(
    package: LoadedPackage,
) -> tuple[LoadedIdentifierPrefixProvider, ...]:
    """Load every identifier-prefix provider declared by one package."""

    loaded: list[LoadedIdentifierPrefixProvider] = []
    for export in package.manifest.exports:
        if export.kind is ExportKind.IDENTIFIER_PREFIXES:
            loaded.append(_load_export(package, export))
    return tuple(loaded)


def _load_export(
    package: LoadedPackage,
    export: PackageExport,
) -> LoadedIdentifierPrefixProvider:
    module_path = (package.package_path / export.path).expanduser().resolve(strict=False)
    source = IdentifierPrefixProviderSource(
        package_id=package.package_id,
        manifest_path=package.manifest_path,
        export_name=export.name,
        module_path=module_path,
    )
    if not module_path.is_file():
        raise IdentifierPrefixProviderLoadError(
            f"Identifier-prefix provider {source.describe()} does not exist"
        )
    if module_path.suffix != ".py":
        raise IdentifierPrefixProviderLoadError(
            f"Identifier-prefix provider {source.describe()} must point to a Python module"
        )

    extend_sys_path_for_package(package)
    spec = importlib.util.spec_from_file_location(
        _module_name_for_export(package, export),
        module_path,
    )
    if spec is None or spec.loader is None:
        raise IdentifierPrefixProviderLoadError(
            f"Could not create a module spec for identifier-prefix provider "
            f"{source.describe()}"
        )
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise IdentifierPrefixProviderLoadError(
            f"Failed to import identifier-prefix provider {source.describe()}: {exc}"
        ) from exc

    provider = getattr(module, "get_identifier_prefixes", None)
    if not callable(provider):
        raise IdentifierPrefixProviderLoadError(
            f"Identifier-prefix provider {source.describe()} must define callable "
            "get_identifier_prefixes(database_url)"
        )
    return LoadedIdentifierPrefixProvider(provider=provider, source=source)


def build_identifier_prefix_provider_catalog(
    registry: PackageRegistry,
) -> IdentifierPrefixProviderCatalog:
    """Build the provider catalog from an already validated package registry."""

    providers = tuple(
        loaded
        for package in registry.loaded_packages
        for loaded in load_package_identifier_prefix_provider_exports(package)
    )
    return IdentifierPrefixProviderCatalog(providers=providers)


def _module_name_for_export(package: LoadedPackage, export: PackageExport) -> str:
    package_segment = package.package_id.replace(".", "_").replace("-", "_")
    export_segment = export.name.replace(".", "_").replace("-", "_")
    return f"_identifier_prefix_provider_{package_segment}_{export_segment}"

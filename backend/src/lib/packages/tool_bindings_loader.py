"""Load package-declared tool binding exports."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .manifest_loader import load_tool_bindings
from .models import ExportKind, PackageExport, ToolBindingsManifest
from .registry import LoadedPackage


@dataclass(frozen=True)
class LoadedToolBindingExport:
    """One parsed tool-binding export declared by a loaded package."""

    package_id: str
    package_version: str
    package_display_name: str
    package_path: Path
    export_name: str
    export_description: str
    bindings_path: Path
    manifest: ToolBindingsManifest


class ToolBindingLoadError(ValueError):
    """Raised when a package declares an invalid tool bindings export."""


def load_package_tool_binding_exports(
    package: LoadedPackage,
) -> tuple[LoadedToolBindingExport, ...]:
    """Load every tool binding export declared by one package manifest."""
    loaded_exports: list[LoadedToolBindingExport] = []

    for export in package.manifest.exports:
        if export.kind is not ExportKind.TOOL_BINDING:
            continue
        loaded_exports.append(_load_tool_binding_export(package, export))

    return tuple(loaded_exports)


def _load_tool_binding_export(
    package: LoadedPackage,
    export: PackageExport,
) -> LoadedToolBindingExport:
    bindings_path = (package.package_path / export.path).expanduser().resolve(strict=False)
    manifest = load_tool_bindings(bindings_path)
    if manifest.package_id != package.package_id:
        raise ToolBindingLoadError(
            f"Tool bindings export '{export.name}' at {bindings_path} declares "
            f"package_id '{manifest.package_id}', expected '{package.package_id}'"
        )

    return LoadedToolBindingExport(
        package_id=package.package_id,
        package_version=package.version,
        package_display_name=package.display_name,
        package_path=package.package_path,
        export_name=export.name,
        export_description=export.description,
        bindings_path=bindings_path,
        manifest=manifest,
    )

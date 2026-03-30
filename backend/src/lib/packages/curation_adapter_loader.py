"""Load package-declared curation adapter exports."""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .models import ExportKind, PackageExport
from .registry import LoadedPackage


CurationAdapterRegistrationHook = Callable[[object], None]


@dataclass(frozen=True)
class LoadedCurationAdapterExport:
    """One curation-adapter registration hook declared by a loaded package."""

    package_id: str
    package_version: str
    package_display_name: str
    package_path: Path
    export_name: str
    export_description: str
    module_path: Path
    register_hook: CurationAdapterRegistrationHook


class CurationAdapterLoadError(ValueError):
    """Raised when a package declares an invalid curation adapter export."""


def load_package_curation_adapter_exports(
    package: LoadedPackage,
) -> tuple[LoadedCurationAdapterExport, ...]:
    """Load every curation adapter export declared by one package manifest."""

    loaded_exports: list[LoadedCurationAdapterExport] = []

    for export in package.manifest.exports:
        if export.kind is not ExportKind.CURATION_ADAPTER:
            continue
        loaded_exports.append(_load_curation_adapter_export(package, export))

    return tuple(loaded_exports)


def _load_curation_adapter_export(
    package: LoadedPackage,
    export: PackageExport,
) -> LoadedCurationAdapterExport:
    module_path = (package.package_path / export.path).expanduser().resolve(strict=False)
    if not module_path.exists():
        raise CurationAdapterLoadError(
            f"Curation adapter export '{export.name}' does not exist: {module_path}"
        )
    if module_path.suffix != ".py":
        raise CurationAdapterLoadError(
            f"Curation adapter export '{export.name}' must point to a Python module file: {module_path}"
        )

    _extend_sys_path_for_package(package)
    module_name = _module_name_for_export(package, export)
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise CurationAdapterLoadError(
            f"Could not create a module spec for curation adapter export '{export.name}'"
        )

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    register_hook = getattr(module, "register_curation_adapters", None)
    if not callable(register_hook):
        raise CurationAdapterLoadError(
            f"Curation adapter export '{export.name}' must define a callable "
            "'register_curation_adapters(registry)'"
        )

    return LoadedCurationAdapterExport(
        package_id=package.package_id,
        package_version=package.version,
        package_display_name=package.display_name,
        package_path=package.package_path,
        export_name=export.name,
        export_description=export.description,
        module_path=module_path,
        register_hook=register_hook,
    )


def _extend_sys_path_for_package(package: LoadedPackage) -> None:
    python_package_root = (
        package.package_path / package.manifest.python_package_root
    ).expanduser().resolve(strict=False)
    for candidate in (
        python_package_root.parent,
        python_package_root,
        package.package_path,
    ):
        candidate_text = str(candidate)
        if candidate_text not in sys.path:
            sys.path.insert(0, candidate_text)


def _module_name_for_export(package: LoadedPackage, export: PackageExport) -> str:
    package_segment = package.package_id.replace(".", "_").replace("-", "_")
    export_segment = export.name.replace(".", "_").replace("-", "_")
    return f"_agr_curation_adapter_export_{package_segment}_{export_segment}"

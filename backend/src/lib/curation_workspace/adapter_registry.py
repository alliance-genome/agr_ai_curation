"""Package-driven registry for curation workspace adapters."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from src.lib.packages import (
    load_package_curation_adapter_exports,
    load_package_registry,
)
from src.lib.packages.paths import get_runtime_packages_dir


@dataclass(frozen=True)
class RegisteredCurationAdapter:
    """One fully registered curation adapter."""

    adapter_key: str
    candidate_normalizer: Any
    export_adapter: Any | None = None


class CurationAdapterRegistry:
    """Simple in-memory registry keyed by adapter_key."""

    def __init__(self) -> None:
        self._candidate_normalizers: dict[str, Any] = {}
        self._export_adapters: dict[str, Any] = {}

    def register_adapter(
        self,
        *,
        adapter_key: str,
        candidate_normalizer: Any,
        export_adapter: Any | None = None,
    ) -> None:
        normalized_key = str(adapter_key).strip()
        if not normalized_key:
            raise ValueError("adapter_key must not be blank")

        existing_normalizer = self._candidate_normalizers.get(normalized_key)
        if existing_normalizer is not None and existing_normalizer is not candidate_normalizer:
            raise ValueError(f"Curation adapter '{normalized_key}' is already registered")
        self._candidate_normalizers[normalized_key] = candidate_normalizer

        if export_adapter is not None:
            existing_export_adapter = self._export_adapters.get(normalized_key)
            if existing_export_adapter is not None and existing_export_adapter is not export_adapter:
                raise ValueError(f"Curation export adapter '{normalized_key}' is already registered")
            self._export_adapters[normalized_key] = export_adapter

    def get_candidate_normalizer(self, adapter_key: str) -> Any | None:
        return self._candidate_normalizers.get(str(adapter_key).strip())

    def require_candidate_normalizer(self, adapter_key: str) -> Any:
        normalizer = self.get_candidate_normalizer(adapter_key)
        if normalizer is None:
            known_keys = ", ".join(sorted(self._candidate_normalizers))
            raise KeyError(
                f"Unknown curation adapter '{adapter_key}'. Registered adapters: {known_keys}"
            )
        return normalizer

    def candidate_normalizers(self) -> dict[str, Any]:
        return dict(self._candidate_normalizers)

    def export_adapters(self) -> tuple[Any, ...]:
        return tuple(
            self._export_adapters[adapter_key]
            for adapter_key in sorted(self._export_adapters)
        )

    def adapter_keys(self) -> tuple[str, ...]:
        return tuple(sorted(self._candidate_normalizers))


def build_curation_adapter_registry() -> CurationAdapterRegistry:
    """Build the adapter registry from package-owned exports."""

    package_registry = load_package_registry(packages_dir=_default_packages_dir())
    registry = CurationAdapterRegistry()

    for package in package_registry.loaded_packages:
        for export in load_package_curation_adapter_exports(package):
            export.register_hook(registry)

    return registry


@lru_cache(maxsize=1)
def load_curation_adapter_registry() -> CurationAdapterRegistry:
    """Return a cached package-driven curation adapter registry."""

    return build_curation_adapter_registry()


def _default_packages_dir() -> Path:
    runtime_packages_dir = get_runtime_packages_dir()
    if runtime_packages_dir.exists():
        return runtime_packages_dir

    current = Path(__file__).resolve()
    for candidate in (current.parent, *current.parents):
        if (candidate / "packages").is_dir() and (candidate / "backend").is_dir():
            return candidate / "packages"
        if (candidate / "packages").is_dir() and (candidate / "config" / "agents").is_dir():
            return candidate / "packages"

    return runtime_packages_dir

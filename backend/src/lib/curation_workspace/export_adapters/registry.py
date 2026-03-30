"""Registry for deterministic curation-workspace export adapters."""

from __future__ import annotations

from typing import Iterable

from src.lib.curation_workspace.adapter_registry import load_curation_adapter_registry
from src.lib.curation_workspace.export_adapters.base import DeterministicExportAdapter


class ExportAdapterRegistry:
    """Simple in-memory registry keyed by adapter_key."""

    def __init__(self, adapters: Iterable[DeterministicExportAdapter] = ()) -> None:
        self._adapters: dict[str, DeterministicExportAdapter] = {}
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter: DeterministicExportAdapter) -> DeterministicExportAdapter:
        """Register one export adapter instance under its adapter key."""

        existing = self._adapters.get(adapter.adapter_key)
        if existing is not None and existing is not adapter:
            raise ValueError(
                f"Export adapter '{adapter.adapter_key}' is already registered"
            )

        self._adapters[adapter.adapter_key] = adapter
        return adapter

    def get(self, adapter_key: str) -> DeterministicExportAdapter | None:
        """Return one adapter by key when available."""

        return self._adapters.get(adapter_key)

    def require(self, adapter_key: str) -> DeterministicExportAdapter:
        """Return one adapter or raise when the key is unknown."""

        adapter = self.get(adapter_key)
        if adapter is None:
            known_keys = ", ".join(sorted(self._adapters))
            raise KeyError(
                f"Unknown export adapter '{adapter_key}'. Registered adapters: {known_keys}"
            )
        return adapter

    def adapter_keys(self) -> tuple[str, ...]:
        """Return registered adapter keys in sorted order for deterministic inspection."""

        return tuple(sorted(self._adapters))


def build_default_export_adapter_registry() -> ExportAdapterRegistry:
    """Build the package-driven export registry for workspace-backed adapters."""

    return ExportAdapterRegistry(
        adapters=load_curation_adapter_registry().export_adapters()
    )

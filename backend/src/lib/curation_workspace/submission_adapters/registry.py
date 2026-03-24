"""Registry for curation-workspace submission transport adapters."""

from __future__ import annotations

from typing import Iterable

from src.lib.curation_workspace.submission_adapters.base import SubmissionTransportAdapter
from src.lib.curation_workspace.submission_adapters.noop import (
    DEFAULT_NOOP_SUBMISSION_TARGET_KEY,
    NoOpSubmissionAdapter,
)


class SubmissionAdapterRegistry:
    """Simple in-memory registry keyed by submission target key."""

    def __init__(self, adapters: Iterable[SubmissionTransportAdapter] = ()) -> None:
        self._adapters: dict[str, SubmissionTransportAdapter] = {}
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter: SubmissionTransportAdapter) -> SubmissionTransportAdapter:
        """Register one transport adapter for each of its supported target keys."""

        for target_key in adapter.supported_target_keys:
            existing = self._adapters.get(target_key)
            if existing is not None and existing is not adapter:
                raise ValueError(
                    f"Submission target '{target_key}' is already registered"
                )
            self._adapters[target_key] = adapter
        return adapter

    def get(self, target_key: str) -> SubmissionTransportAdapter | None:
        """Return one transport adapter by submission target key when available."""

        return self._adapters.get(target_key)

    def require(self, target_key: str) -> SubmissionTransportAdapter:
        """Return one transport adapter or raise when the target key is unknown."""

        adapter = self.get(target_key)
        if adapter is None:
            known_targets = ", ".join(sorted(self._adapters))
            raise KeyError(
                f"Unknown submission target '{target_key}'. Registered targets: {known_targets}"
            )
        return adapter

    def target_keys(self) -> tuple[str, ...]:
        """Return registered target keys in sorted order for deterministic inspection."""

        return tuple(sorted(self._adapters))


def build_default_submission_adapter_registry() -> SubmissionAdapterRegistry:
    """Build the default submission registry for workspace-backed transports."""

    return SubmissionAdapterRegistry(
        adapters=(
            NoOpSubmissionAdapter(target_key=DEFAULT_NOOP_SUBMISSION_TARGET_KEY),
        )
    )

"""Provider-agnostic domain-pack discovery and registry."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from src.schemas.domain_pack_metadata import DomainPackFixturePackRef, DomainPackMetadata

from .loader import load_domain_pack_metadata
from .paths import get_domain_pack_metadata_path, get_domain_packs_dir


@dataclass(frozen=True)
class LoadedDomainPack:
    """A domain pack that passed metadata validation and registry checks."""

    pack_id: str
    display_name: str
    version: str
    pack_path: Path
    metadata_path: Path
    metadata: DomainPackMetadata


@dataclass(frozen=True)
class DomainPackDiscoveryFailure:
    """A domain-pack directory that could not be loaded into metadata."""

    pack_id: str
    pack_path: Path
    metadata_path: Path
    reason: str


class DomainPackRegistryValidationError(ValueError):
    """Raised when domain-pack registry validation makes the registry unsafe."""


@dataclass(frozen=True)
class DomainPackRegistry:
    """In-memory registry of loaded domain packs and loading diagnostics."""

    packs_dir: Path
    loaded_packs: tuple[LoadedDomainPack, ...]
    failed_packs: tuple[DomainPackDiscoveryFailure, ...]
    validation_errors: tuple[str, ...] = ()

    @cached_property
    def packs_by_id(self) -> dict[str, LoadedDomainPack]:
        """Return loaded packs keyed by pack ID."""

        return {pack.pack_id: pack for pack in self.loaded_packs}

    @cached_property
    def fixture_packs_by_id(self) -> dict[tuple[str, str], DomainPackFixturePackRef]:
        """Return fixture-pack refs keyed by ``(pack_id, fixture_pack_id)``."""

        refs: dict[tuple[str, str], DomainPackFixturePackRef] = {}
        for pack in self.loaded_packs:
            for fixture_pack in pack.metadata.fixture_packs:
                refs[(pack.pack_id, fixture_pack.fixture_pack_id)] = fixture_pack
        return refs

    def get_pack(self, pack_id: str) -> LoadedDomainPack | None:
        """Return one loaded domain pack by ID, if present."""

        return self.packs_by_id.get(pack_id)

    def get_fixture_pack_ref(
        self,
        pack_id: str,
        fixture_pack_id: str,
    ) -> DomainPackFixturePackRef | None:
        """Return a fixture-pack metadata ref for one loaded domain pack."""

        return self.fixture_packs_by_id.get((pack_id, fixture_pack_id))

    def raise_for_validation_errors(self) -> None:
        """Raise a single actionable error when registry validation failed."""

        if not self.validation_errors:
            return
        raise DomainPackRegistryValidationError("; ".join(self.validation_errors))


def iter_domain_pack_dirs(packs_dir: Path | None = None) -> tuple[Path, ...]:
    """Return deterministic domain-pack directories beneath the runtime root."""

    root = (packs_dir or get_domain_packs_dir()).expanduser().resolve(strict=False)
    if not root.exists():
        return ()

    return tuple(
        path
        for path in sorted(root.iterdir(), key=lambda item: item.name)
        if path.is_dir()
    )


def discover_domain_pack_metadata(
    packs_dir: Path | None = None,
) -> tuple[tuple[LoadedDomainPack, ...], tuple[DomainPackDiscoveryFailure, ...]]:
    """Load every domain-pack metadata file beneath the domain-pack directory."""

    discovered: list[LoadedDomainPack] = []
    failures: list[DomainPackDiscoveryFailure] = []

    for pack_path in iter_domain_pack_dirs(packs_dir):
        metadata_path = get_domain_pack_metadata_path(pack_path)
        try:
            metadata = load_domain_pack_metadata(metadata_path)
        except ValueError as exc:
            failures.append(
                DomainPackDiscoveryFailure(
                    pack_id=pack_path.name,
                    pack_path=pack_path,
                    metadata_path=metadata_path,
                    reason=str(exc),
                )
            )
            continue

        discovered.append(
            LoadedDomainPack(
                pack_id=metadata.pack_id,
                display_name=metadata.display_name,
                version=metadata.version,
                pack_path=pack_path,
                metadata_path=metadata_path,
                metadata=metadata,
            )
        )

    return (
        tuple(sorted(discovered, key=lambda item: item.pack_id)),
        tuple(sorted(failures, key=lambda item: (item.pack_id, str(item.metadata_path)))),
    )


def load_domain_pack_registry(
    packs_dir: Path | None = None,
    *,
    fail_on_validation_error: bool = True,
) -> DomainPackRegistry:
    """Discover domain packs on disk and build the in-memory registry."""

    resolved_packs_dir = (packs_dir or get_domain_packs_dir()).expanduser().resolve(
        strict=False
    )
    discovered_packs, discovery_failures = discover_domain_pack_metadata(resolved_packs_dir)

    validation_errors: list[str] = []
    failed_packs: list[DomainPackDiscoveryFailure] = list(discovery_failures)
    pack_groups: dict[str, list[LoadedDomainPack]] = {}
    for pack in discovered_packs:
        pack_groups.setdefault(pack.pack_id, []).append(pack)

    duplicate_pack_ids = {
        pack_id: packs for pack_id, packs in pack_groups.items() if len(packs) > 1
    }
    for pack_id, packs in sorted(duplicate_pack_ids.items()):
        duplicate_paths = ", ".join(
            str(pack.metadata_path) for pack in sorted(packs, key=lambda item: item.metadata_path)
        )
        reason = f"Duplicate pack_id '{pack_id}' discovered at: {duplicate_paths}"
        validation_errors.append(reason)
        for pack in packs:
            failed_packs.append(
                DomainPackDiscoveryFailure(
                    pack_id=pack.pack_id,
                    pack_path=pack.pack_path,
                    metadata_path=pack.metadata_path,
                    reason=reason,
                )
            )

    loaded_packs = [
        packs[0]
        for pack_id, packs in sorted(pack_groups.items())
        if pack_id not in duplicate_pack_ids
    ]

    registry = DomainPackRegistry(
        packs_dir=resolved_packs_dir,
        loaded_packs=tuple(sorted(loaded_packs, key=lambda item: item.pack_id)),
        failed_packs=tuple(
            sorted(failed_packs, key=lambda item: (item.pack_id, str(item.metadata_path)))
        ),
        validation_errors=tuple(validation_errors),
    )
    if fail_on_validation_error:
        registry.raise_for_validation_errors()
    return registry


__all__ = [
    "DomainPackDiscoveryFailure",
    "DomainPackRegistry",
    "DomainPackRegistryValidationError",
    "LoadedDomainPack",
    "discover_domain_pack_metadata",
    "iter_domain_pack_dirs",
    "load_domain_pack_registry",
]

"""Alliance package-owned hooks for loading bundled domain packs."""

from __future__ import annotations

from src.lib.domain_packs.registry import (
    DomainPackRegistry,
    LoadedDomainPack,
    load_domain_pack_registry,
)

from .paths import get_alliance_domain_packs_dir
from .schema_refs import ALLIANCE_BASE_DOMAIN_PACK_ID


def load_alliance_domain_pack_registry(
    *,
    fail_on_validation_error: bool = True,
) -> DomainPackRegistry:
    """Load Alliance-bundled domain packs from the package source tree."""

    return load_domain_pack_registry(
        get_alliance_domain_packs_dir(),
        fail_on_validation_error=fail_on_validation_error,
    )


def load_alliance_domain_packs() -> tuple[LoadedDomainPack, ...]:
    """Return validated Alliance-bundled domain packs."""

    return load_alliance_domain_pack_registry().loaded_packs


def get_alliance_domain_pack(
    pack_id: str = ALLIANCE_BASE_DOMAIN_PACK_ID,
) -> LoadedDomainPack:
    """Return one Alliance domain pack, raising when it is not bundled."""

    registry = load_alliance_domain_pack_registry()
    loaded_pack = registry.get_pack(pack_id)
    if loaded_pack is None:
        raise KeyError(f"Alliance domain pack '{pack_id}' is not bundled")
    return loaded_pack


__all__ = [
    "get_alliance_domain_pack",
    "load_alliance_domain_pack_registry",
    "load_alliance_domain_packs",
]

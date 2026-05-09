"""Filesystem paths for Alliance-bundled domain packs."""

from __future__ import annotations

from pathlib import Path

from .schema_refs import ALLIANCE_BASE_DOMAIN_PACK_ID


def get_alliance_package_root() -> Path:
    """Return the checked-out Alliance runtime package root."""

    return Path(__file__).resolve().parents[4]


def get_alliance_domain_packs_dir() -> Path:
    """Return the Alliance package directory containing bundled domain packs."""

    return get_alliance_package_root() / "domain_packs"


def get_alliance_domain_pack_metadata_path(
    pack_id: str = ALLIANCE_BASE_DOMAIN_PACK_ID,
) -> Path:
    """Return the metadata path for one bundled Alliance domain pack."""

    return get_alliance_domain_packs_dir() / pack_id / "domain_pack.yaml"


__all__ = [
    "get_alliance_domain_pack_metadata_path",
    "get_alliance_domain_packs_dir",
    "get_alliance_package_root",
]

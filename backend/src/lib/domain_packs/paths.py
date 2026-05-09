"""Runtime path contract for provider-agnostic domain packs."""

from __future__ import annotations

import os
from pathlib import Path

from src.lib.packages.paths import get_runtime_root

DEFAULT_DOMAIN_PACKS_DIRNAME = "domain_packs"
DEFAULT_DOMAIN_PACK_METADATA_FILENAME = "domain_pack.yaml"


def _normalize_path(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def get_domain_packs_dir() -> Path:
    """Return the runtime directory that contains installed domain packs."""

    raw_value = os.getenv("AGR_DOMAIN_PACKS_DIR")
    if raw_value and raw_value.strip():
        candidate = Path(raw_value)
        if candidate.is_absolute():
            return _normalize_path(candidate)
        if ".." in candidate.parts:
            raise ValueError(
                "Relative AGR_DOMAIN_PACKS_DIR must not traverse parent directories"
            )
        return _normalize_path(get_runtime_root() / candidate)

    return _normalize_path(get_runtime_root() / DEFAULT_DOMAIN_PACKS_DIRNAME)


def get_domain_pack_metadata_path(domain_pack_dir: Path) -> Path:
    """Return the metadata file path for a domain-pack directory."""

    return domain_pack_dir / DEFAULT_DOMAIN_PACK_METADATA_FILENAME


__all__ = [
    "DEFAULT_DOMAIN_PACK_METADATA_FILENAME",
    "DEFAULT_DOMAIN_PACKS_DIRNAME",
    "get_domain_pack_metadata_path",
    "get_domain_packs_dir",
]

"""Shared helpers for document chunk dictionaries."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_CHUNK_IDENTIFIER_FIELDS = ("id", "chunk_id", "chunkId")
_METADATA_CHUNK_IDENTIFIER_FIELDS = ("chunk_id", "chunkId")


def resolve_chunk_identifier(
    chunk: Mapping[str, Any],
    metadata: Mapping[str, Any] | None = None,
) -> str | None:
    """Return the first non-empty concrete chunk identifier from known chunk shapes."""
    if metadata is None:
        raw_metadata = chunk.get("metadata")
        metadata = raw_metadata if isinstance(raw_metadata, Mapping) else {}

    for field_name in _CHUNK_IDENTIFIER_FIELDS:
        normalized = str(chunk.get(field_name) or "").strip()
        if normalized:
            return normalized

    for field_name in _METADATA_CHUNK_IDENTIFIER_FIELDS:
        normalized = str(metadata.get(field_name) or "").strip()
        if normalized:
            return normalized

    return None

"""Canonical hashing for persisted domain-envelope JSON payloads."""

from __future__ import annotations

from hashlib import sha256
import json
from typing import Any, Mapping


def canonical_domain_envelope_payload_hash(payload: Mapping[str, Any]) -> str:
    """Hash a domain-envelope mapping after PostgreSQL-compatible key ordering."""

    canonical_payload = json.dumps(
        _postgres_jsonb_key_order(payload),
        ensure_ascii=False,
        separators=(", ", ": "),
    )
    return sha256(canonical_payload.encode("utf-8")).hexdigest()


def _postgres_jsonb_key_order(value: Any) -> Any:
    """Mirror JSONB object-key ordering while retaining Python numeric semantics."""

    if isinstance(value, Mapping):
        return {
            key: _postgres_jsonb_key_order(value[key])
            for key in sorted(value, key=_postgres_jsonb_sort_key)
        }
    if isinstance(value, list):
        return [_postgres_jsonb_key_order(item) for item in value]
    return value


def _postgres_jsonb_sort_key(value: Any) -> tuple[int, bytes]:
    encoded = str(value).encode("utf-8")
    return len(encoded), encoded


__all__ = ["canonical_domain_envelope_payload_hash"]

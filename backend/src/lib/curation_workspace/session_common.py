"""Shared scalar helpers for curation workspace session services."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Sequence
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import asc, desc

from src.lib.curation_workspace.models import (
    CurationValidationSnapshot as ValidationSnapshotModel,
)
from src.schemas.curation_workspace import CurationSortDirection

LIKE_ESCAPE_CHAR = "\\"

def _normalize_uuid(value: str | UUID, *, field_name: str) -> UUID:
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid {field_name}: {value}",
        ) from exc


def normalize_uuid(value: str | UUID, *, field_name: str) -> UUID:
    """Public UUID normalization helper shared across curation workspace services."""

    return _normalize_uuid(value, field_name=field_name)


def _normalized_optional_string(value: str | None, *, field_name: str) -> str | None:
    if value is None:
        return None

    normalized = str(value).strip()
    if not normalized:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{field_name} must not be empty",
        )

    return normalized


def _normalized_required_string(value: str, *, field_name: str) -> str:
    normalized = _normalized_optional_string(value, field_name=field_name)
    if normalized is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{field_name} is required",
        )
    return normalized


def _ordered_clause(expression: Any, direction: CurationSortDirection, *, nulls_last: bool = False) -> Any:
    ordered = asc(expression) if direction == CurationSortDirection.ASC else desc(expression)
    if nulls_last:
        ordered = ordered.nulls_last()
    return ordered


def _escape_like_pattern(value: str) -> str:
    escaped = value.replace(LIKE_ESCAPE_CHAR, LIKE_ESCAPE_CHAR * 2)
    escaped = escaped.replace("%", f"{LIKE_ESCAPE_CHAR}%")
    escaped = escaped.replace("_", f"{LIKE_ESCAPE_CHAR}_")
    return escaped


def _stable_serialize(value: Any) -> str:
    if value is None:
        return "null"
    return json.dumps(
        value,
        # Draft comparisons should remain stable for unexpected passthrough values
        # rather than failing during dirty-field detection.
        default=str,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _draft_values_equal(left: Any, right: Any) -> bool:
    return _stable_serialize(left) == _stable_serialize(right)


def _latest_snapshot_record(
    snapshots: Sequence[ValidationSnapshotModel],
) -> ValidationSnapshotModel | None:
    if not snapshots:
        return None

    ordered_snapshots = sorted(
        snapshots,
        key=lambda snapshot: snapshot.completed_at
        or snapshot.requested_at
        or datetime.min.replace(tzinfo=timezone.utc),
    )
    return ordered_snapshots[-1]

def _actor_claims_payload(actor_claims: dict[str, Any]) -> dict[str, str]:
    actor_id = actor_claims.get("sub") or actor_claims.get("uid") or "unknown"
    display_name = actor_claims.get("name") or actor_claims.get("email") or actor_id
    payload = {
        "actor_id": actor_id,
        "display_name": display_name,
    }
    if actor_claims.get("email"):
        payload["email"] = actor_claims["email"]
    return payload


def build_actor_claims_payload(actor_claims: dict[str, Any]) -> dict[str, str]:
    """Public actor payload helper shared across curation workspace services."""

    return _actor_claims_payload(actor_claims)

__all__ = [
    "LIKE_ESCAPE_CHAR",
    "build_actor_claims_payload",
    "normalize_uuid",
]

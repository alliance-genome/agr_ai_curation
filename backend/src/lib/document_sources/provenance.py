"""Provider-neutral provenance helpers for document-source backed imports."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from src.models.sql.pdf_document import PDFDocument


_SCALAR_PROVENANCE_FIELDS = (
    "provider",
    "reference_id",
    "reference_curie",
    "source_file_id",
    "pdf_artifact_id",
    "converted_artifact_id",
    "source_md5",
    "file_class",
    "file_extension",
    "artifact_status",
    "import_status",
    "imported_at",
    "access_scope",
    "viewer_mode",
)

_SQL_TO_PUBLIC_FIELD = {
    "source_provider": "provider",
    "source_provider_reference_id": "reference_id",
    "source_provider_reference_curie": "reference_curie",
    "source_provider_source_file_id": "source_file_id",
    "source_provider_pdf_artifact_id": "pdf_artifact_id",
    "source_provider_converted_artifact_id": "converted_artifact_id",
    "source_external_ids": "external_ids",
    "source_md5": "source_md5",
    "source_file_class": "file_class",
    "source_file_extension": "file_extension",
    "source_artifact_status": "artifact_status",
    "source_import_status": "import_status",
    "source_imported_at": "imported_at",
    "source_access_scope": "access_scope",
    "source_access_mods": "access_mods",
    "viewer_mode": "viewer_mode",
}

_SENSITIVE_KEY_PARTS = (
    "secret",
    "token",
    "cookie",
    "authorization",
    "password",
    "credential",
    "markdown",
    "content",
    "payload",
    "path",
)


def build_document_source_provenance(document: Any) -> dict[str, Any] | None:
    """Return compact, non-secret source provenance for API/Weaviate metadata."""

    return sanitize_document_source_provenance(_raw_provenance_from_document(document))


def sanitize_document_source_provenance(raw: Any) -> dict[str, Any] | None:
    """Allowlist compact provenance fields before public or Weaviate exposure."""

    raw_mapping = _coerce_mapping(raw)
    if not raw_mapping:
        return None

    provider = _string_or_none(raw_mapping.get("provider"))
    if provider is None:
        return None

    sanitized: dict[str, Any] = {"provider": provider}
    for key in _SCALAR_PROVENANCE_FIELDS:
        if key == "provider":
            continue
        value = raw_mapping.get(key)
        if key == "imported_at":
            value = _serialize_datetime(value)
        else:
            value = _string_or_none(value)
        if value is not None:
            sanitized[key] = value

    external_ids = _sanitize_external_ids(raw_mapping.get("external_ids"))
    if external_ids:
        sanitized["external_ids"] = external_ids

    access_mods = _sanitize_access_mods(raw_mapping.get("access_mods"))
    if access_mods:
        sanitized["access_mods"] = access_mods

    return sanitized


def find_existing_document_by_source(
    db: Session,
    *,
    user_id: int,
    source_provider: str,
    reference_id: str | None = None,
    reference_curie: str | None = None,
    converted_artifact_id: str | None = None,
    source_md5: str | None = None,
) -> PDFDocument | None:
    """Find a user-owned document with matching provider provenance."""

    match_clauses = []
    if reference_id:
        match_clauses.append(PDFDocument.source_provider_reference_id == reference_id)
    if reference_curie:
        match_clauses.append(PDFDocument.source_provider_reference_curie == reference_curie)
    if converted_artifact_id:
        match_clauses.append(
            PDFDocument.source_provider_converted_artifact_id == converted_artifact_id
        )
    if source_md5:
        match_clauses.append(PDFDocument.source_md5 == source_md5)

    if not match_clauses:
        return None

    return db.execute(
        select(PDFDocument)
        .where(
            PDFDocument.user_id == user_id,
            PDFDocument.source_provider == source_provider,
            or_(*match_clauses),
        )
        .limit(1)
    ).scalar_one_or_none()


def _serialize_datetime(value: object) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        return value
    return None


def _raw_provenance_from_document(document: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {}
    for sql_field, public_field in _SQL_TO_PUBLIC_FIELD.items():
        value = getattr(document, sql_field, None)
        if value is not None:
            raw[public_field] = value
    return raw


def _coerce_mapping(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if hasattr(raw, "model_dump"):
        payload = raw.model_dump(exclude_none=True)
        return payload if isinstance(payload, dict) else {}
    if isinstance(raw, dict):
        return dict(raw)
    return {}


def _string_or_none(value: object) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    if isinstance(value, (int, float, bool)):
        return str(value)
    return None


def _sanitize_external_ids(value: object) -> dict[str, str | list[str]] | None:
    if not isinstance(value, dict):
        return None

    sanitized: dict[str, str | list[str]] = {}
    for raw_key, raw_value in value.items():
        key = _string_or_none(raw_key)
        if key is None or _is_sensitive_key(key):
            continue

        scalar_value = _string_or_none(raw_value)
        if scalar_value is not None:
            sanitized[key] = scalar_value
            continue

        if isinstance(raw_value, list):
            values = [
                item
                for item in (_string_or_none(item) for item in raw_value)
                if item is not None
            ]
            if values:
                sanitized[key] = values

    return sanitized or None


def _sanitize_access_mods(value: object) -> dict[str, list[str]] | None:
    if not isinstance(value, dict):
        return None

    raw_mods = value.get("mods")
    if isinstance(raw_mods, str):
        raw_values: list[object] = [raw_mods]
    elif isinstance(raw_mods, list):
        raw_values = raw_mods
    else:
        return None

    mods = [
        mod
        for mod in (_string_or_none(raw_value) for raw_value in raw_values)
        if mod is not None
    ]
    return {"mods": mods} if mods else None


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower()
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)

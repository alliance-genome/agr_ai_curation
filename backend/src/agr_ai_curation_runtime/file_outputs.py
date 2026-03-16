"""Public file-output helpers for package-owned export tools."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

FileOutputType = Literal["csv", "tsv", "json"]


@dataclass(frozen=True)
class FileOutputRequestContext:
    """Request-scoped metadata captured at tool invocation time."""

    trace_id: str | None
    session_id: str | None
    curator_id: str | None


@dataclass(frozen=True)
class PersistedFileOutput:
    """File metadata returned after storage and registration succeed."""

    file_id: str
    filename: str
    file_type: FileOutputType
    size_bytes: int
    hash_sha256: str
    download_url: str
    warnings: tuple[str, ...]


def _load_context_module():
    return import_module("src.lib.context")


def _load_storage_service_class():
    return import_module("src.lib.file_outputs.storage").FileOutputStorageService


def _load_session_factory():
    return import_module("src.models.sql.database").SessionLocal


def _load_file_output_model():
    return import_module("src.models.sql.file_output").FileOutput


def get_current_file_output_context() -> FileOutputRequestContext:
    """Read the current request context from backend context variables."""
    context_module = _load_context_module()
    return FileOutputRequestContext(
        trace_id=context_module.get_current_trace_id(),
        session_id=context_module.get_current_session_id(),
        curator_id=context_module.get_current_user_id(),
    )


def persist_file_output(
    *,
    content: str | bytes,
    file_type: FileOutputType,
    descriptor: str,
    agent_name: str,
    context: FileOutputRequestContext | None = None,
) -> PersistedFileOutput:
    """Persist file content and register it with the backend download store."""
    active_context = context or get_current_file_output_context()
    effective_trace_id, effective_session_id, effective_curator_id = (
        _build_effective_context(active_context)
    )

    storage_service = _load_storage_service_class()()
    file_path, file_hash, file_size, warnings = storage_service.save_output(
        trace_id=effective_trace_id,
        session_id=effective_session_id,
        content=content,
        file_type=file_type,
        descriptor=descriptor,
    )

    full_filename = Path(file_path).name
    session = _load_session_factory()()
    file_output_model = _load_file_output_model()
    tool_label = file_type.upper()

    try:
        file_output = file_output_model(
            filename=full_filename,
            file_path=str(file_path),
            file_type=file_type,
            file_size=file_size,
            file_hash=file_hash,
            curator_id=effective_curator_id,
            session_id=effective_session_id,
            trace_id=effective_trace_id,
            agent_name=agent_name,
        )
        session.add(file_output)
        session.commit()
        session.refresh(file_output)
        file_id = str(file_output.id)
        logger.info(
            "[%s Tool] Registered file in database: %s, filename=%s, size=%s bytes",
            tool_label,
            file_id,
            full_filename,
            file_size,
        )
    except Exception as exc:
        session.rollback()
        logger.error("[%s Tool] Failed to register file in database: %s", tool_label, exc)
        raise
    finally:
        session.close()

    return PersistedFileOutput(
        file_id=file_id,
        filename=full_filename,
        file_type=file_type,
        size_bytes=file_size,
        hash_sha256=file_hash,
        download_url=_build_download_url(file_id),
        warnings=tuple(warnings),
    )


def _build_effective_context(
    context: FileOutputRequestContext,
) -> tuple[str, str, str]:
    fallback_id = uuid.uuid4().hex
    return (
        context.trace_id or fallback_id[:32],
        context.session_id or fallback_id[:8],
        context.curator_id or "unknown",
    )


def _build_download_url(file_id: str) -> str:
    return f"/api/files/{file_id}/download"


__all__ = [
    "FileOutputRequestContext",
    "PersistedFileOutput",
    "get_current_file_output_context",
    "persist_file_output",
]

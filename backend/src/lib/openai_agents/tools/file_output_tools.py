"""Structured CSV/TSV/JSON file output persistence for formatter agents.

Formatter agents never provide raw file bytes or row arrays. They produce a
validated projection plan through runtime-bound formatter tools; this module
serializes the resulting ``FlowOutputProjectionResult`` and registers exactly
one downloadable file for the session/descriptor/format identity.
"""

import csv
import hashlib
import io
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from src.lib.flows.output_projection import FlowOutputProjectionResult
from src.lib.file_outputs.storage import FileOutputStorageService, sanitize_output_descriptor
from src.lib.openai_agents.config import (
    get_flow_output_branch_descriptor_prefix_chars,
    get_flow_output_branch_identity_hash_chars,
    get_flow_output_branch_node_suffix_chars,
    get_flow_output_branch_storage_descriptor_chars,
)
from src.models.sql.database import SessionLocal
from src.models.sql.file_output import FileOutput
from ..models import FileInfo

logger = logging.getLogger(__name__)


def _get_context_from_contextvars() -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Get trace_id, session_id, curator_id from context variables.

    These are set by the API layer (chat.py, executor.py) at the start
    of each request and by the runner when the Langfuse trace is created.

    Returns:
        Tuple of (trace_id, session_id, curator_id) - any may be None
    """
    from src.lib.context import (
        get_current_trace_id,
        get_current_session_id,
        get_current_user_id,
    )

    return (
        get_current_trace_id(),
        get_current_session_id(),
        get_current_user_id(),
    )


def _resolve_output_descriptor(filename: str) -> str:
    """Prefer a flow-resolved filename stem override when one is active."""

    from src.lib.context import get_current_output_filename_stem

    override = str(get_current_output_filename_stem() or "").strip()
    return override or filename


def _build_download_url(file_id: str) -> str:
    """Build the download URL for a file."""
    return f"/api/files/{file_id}/download"


def _require_projected_file_context(
    trace_id: Optional[str],
    session_id: Optional[str],
    curator_id: Optional[str],
) -> tuple[str, str, str]:
    """Return required context for structured formatter saves, or fail loudly."""

    if not trace_id:
        raise ValueError("Structured projected file output requires trace_id context")
    if not session_id:
        raise ValueError("Structured projected file output requires session_id context")
    if not curator_id:
        raise ValueError("Structured projected file output requires curator_id context")
    return trace_id, session_id, curator_id


def _find_existing_projected_file_output(
    db,
    *,
    session_id: str,
    curator_id: str,
    file_type: str,
    descriptor: str,
    file_path: str,
    branch_identity: str | None,
) -> FileOutput | None:
    """Find one structured formatter row by canonical session/descriptor/format identity."""

    file_output = (
        db.query(FileOutput)
        .filter(FileOutput.file_path == file_path)
        .one_or_none()
    )
    if file_output is not None:
        metadata = file_output.file_metadata or {}
        if branch_identity is None or (
            isinstance(metadata, dict)
            and str(metadata.get("flow_output_branch_identity") or "")
            == branch_identity
        ):
            return file_output

    candidates = (
        db.query(FileOutput)
        .filter(
            FileOutput.session_id == session_id,
            FileOutput.curator_id == curator_id,
            FileOutput.file_type == file_type,
        )
        .order_by(FileOutput.created_at.desc())
        .all()
    )
    for candidate in candidates:
        metadata = candidate.file_metadata or {}
        if not isinstance(metadata, dict):
            continue
        if (
            metadata.get("structured_projection") is True
            and str(metadata.get("descriptor") or "") == descriptor
            and (
                branch_identity is None
                or str(metadata.get("flow_output_branch_identity") or "")
                == branch_identity
            )
        ):
            return candidate
    return None


def _mime_type_for_file_type(file_type: str) -> str:
    if file_type == "csv":
        return "text/csv"
    if file_type == "tsv":
        return "text/tab-separated-values"
    if file_type == "json":
        return "application/json"
    raise ValueError(f"Unsupported file type: {file_type}")


def _projection_content_for_file_type(
    *,
    output_format: str,
    projection: FlowOutputProjectionResult,
) -> str:
    if output_format == "csv":
        output = io.StringIO()
        column_keys = [column.key for column in projection.columns]
        writer = csv.DictWriter(output, fieldnames=column_keys, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(projection.rows)
        return output.getvalue()
    if output_format == "tsv":
        output = io.StringIO()
        column_keys = [column.key for column in projection.columns]
        writer = csv.DictWriter(
            output,
            fieldnames=column_keys,
            delimiter="\t",
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(
            [
                {
                    key: str(value or "").strip()
                    for key, value in row.items()
                }
                for row in projection.rows
            ]
        )
        return output.getvalue()
    if output_format == "json":
        json_data = projection.json_data if projection.json_data is not None else projection.rows
        return json.dumps(json_data, indent=2, ensure_ascii=False)
    raise ValueError(f"Unsupported projected file output format: {output_format}")


async def save_projected_file_output(
    output_format: str,
    projection: FlowOutputProjectionResult,
    filename_hint: str,
    formatter_agent_id: str,
) -> dict:
    """Persist a validated projection result as one idempotent formatter file."""

    normalized_format = str(output_format or "").strip().lower()
    if normalized_format not in {"csv", "tsv", "json"}:
        raise ValueError(f"Unsupported projected file output format: {output_format}")

    trace_id, session_id, curator_id = _get_context_from_contextvars()
    effective_trace_id, effective_session_id, effective_curator_id = _require_projected_file_context(
        trace_id,
        session_id,
        curator_id,
    )
    descriptor = _resolve_output_descriptor(filename_hint)
    from src.lib.context import get_current_flow_output_attachment

    flow_output_attachment = get_current_flow_output_attachment() or {}
    formatter_node_id = str(
        flow_output_attachment.get("formatter_node_id") or ""
    ).strip()
    flow_run_id = str(flow_output_attachment.get("flow_run_id") or "").strip()
    branch_identity = (
        f"{flow_run_id or effective_trace_id}:{formatter_node_id}"
        if formatter_node_id
        else None
    )
    storage_descriptor = descriptor
    if formatter_node_id:
        identity_hash = hashlib.sha256(
            f"{descriptor}\0{branch_identity}".encode("utf-8")
        ).hexdigest()[:get_flow_output_branch_identity_hash_chars()]
        descriptor_prefix = sanitize_output_descriptor(
            descriptor,
            max_length=get_flow_output_branch_descriptor_prefix_chars(),
        )
        node_suffix = sanitize_output_descriptor(
            formatter_node_id,
            max_length=get_flow_output_branch_node_suffix_chars(),
        )
        storage_descriptor = (
            f"{descriptor_prefix}_{node_suffix}_{identity_hash}"[
                :get_flow_output_branch_storage_descriptor_chars()
            ].strip("_-")
        )
    content = _projection_content_for_file_type(
        output_format=normalized_format,
        projection=projection,
    )

    storage = FileOutputStorageService()
    file_path, file_hash, file_size, warnings = storage.save_output(
        trace_id=effective_trace_id,
        session_id=effective_session_id,
        content=content,
        file_type=normalized_format,  # type: ignore[arg-type]
        descriptor=storage_descriptor,
        stable_filename=True,
    )

    for warning in warnings:
        logger.warning('[%s Tool] Validation warning: %s', normalized_format.upper(), warning)

    full_filename = file_path.name
    db = SessionLocal()
    try:
        file_output = _find_existing_projected_file_output(
            db,
            session_id=effective_session_id,
            curator_id=effective_curator_id,
            file_type=normalized_format,
            descriptor=descriptor,
            file_path=str(file_path),
            branch_identity=branch_identity,
        )
        projection_summary = {
            "format": normalized_format,
            "row_source": projection.row_source,
            "row_count": projection.total_count,
            "column_keys": [column.key for column in projection.columns],
            "truncated": projection.truncated,
        }
        branch_metadata = {
            key: value
            for key, value in flow_output_attachment.items()
            if value
            and key
            in {
                "flow_id",
                "flow_run_id",
                "formatter_node_id",
                "source_node_id",
                "document_id",
                "formatter_label",
                "source_label",
                "source_extraction_result_ids",
                "source_keys",
                "source_envelope_ids",
            }
        }
        if file_output is None:
            file_output = FileOutput(
                filename=full_filename,
                file_path=str(file_path),
                file_type=normalized_format,
                file_size=file_size,
                file_hash=file_hash,
                curator_id=effective_curator_id,
                session_id=effective_session_id,
                trace_id=effective_trace_id,
                agent_name=formatter_agent_id,
                file_metadata={
                    "structured_projection": True,
                    "descriptor": descriptor,
                    "storage_descriptor": storage_descriptor,
                    "flow_output_branch_identity": branch_identity,
                    "projection_summary": projection_summary,
                    **branch_metadata,
                },
            )
            db.add(file_output)
        else:
            previous_file_path = str(file_output.file_path or "")
            file_output.filename = full_filename
            file_output.file_path = str(file_path)
            file_output.file_type = normalized_format
            file_output.file_size = file_size
            file_output.file_hash = file_hash
            file_output.curator_id = effective_curator_id
            file_output.session_id = effective_session_id
            file_output.trace_id = effective_trace_id
            file_output.agent_name = formatter_agent_id
            metadata = dict(file_output.file_metadata or {})
            metadata.update(
                {
                    "structured_projection": True,
                    "descriptor": descriptor,
                    "storage_descriptor": storage_descriptor,
                    "flow_output_branch_identity": branch_identity,
                    "projection_summary": projection_summary,
                    **branch_metadata,
                }
            )
            file_output.file_metadata = metadata
            if previous_file_path and previous_file_path != str(file_path):
                removed_previous_file = storage.delete_output(previous_file_path)
                if not removed_previous_file:
                    logger.warning(
                        "[%s Tool] Previous structured projected file was not removed: %s",
                        normalized_format.upper(),
                        previous_file_path,
                    )
        db.commit()
        db.refresh(file_output)
        file_id = str(file_output.id)
        logger.info(
            "[%s Tool] Registered structured projected file: %s, filename=%s, size=%s bytes",
            normalized_format.upper(),
            file_id,
            full_filename,
            file_size,
        )
    except Exception as exc:
        db.rollback()
        logger.error('[%s Tool] Failed to register projected file: %s', normalized_format.upper(), exc)
        raise
    finally:
        db.close()

    file_info = FileInfo(
        file_id=file_id,
        filename=full_filename,
        format=normalized_format,
        size_bytes=file_size,
        hash_sha256=file_hash,
        mime_type=_mime_type_for_file_type(normalized_format),
        download_url=_build_download_url(file_id),
        created_at=datetime.now(timezone.utc),
        trace_id=effective_trace_id,
        session_id=effective_session_id,
        curator_id=effective_curator_id,
        flow_id=flow_output_attachment.get("flow_id") or None,
        flow_run_id=flow_output_attachment.get("flow_run_id") or None,
        formatter_node_id=formatter_node_id or None,
        source_node_id=flow_output_attachment.get("source_node_id") or None,
        formatter_label=flow_output_attachment.get("formatter_label") or None,
        source_label=flow_output_attachment.get("source_label") or None,
        source_extraction_result_ids=list(
            flow_output_attachment.get("source_extraction_result_ids") or []
        ),
        source_keys=list(flow_output_attachment.get("source_keys") or []),
        source_envelope_ids=list(
            flow_output_attachment.get("source_envelope_ids") or []
        ),
        document_id=flow_output_attachment.get("document_id") or None,
    )
    return file_info.model_dump(mode="json")

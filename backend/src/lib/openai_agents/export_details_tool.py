"""Exact export diagnostics for the authenticated current session."""

from __future__ import annotations

import json
from uuid import UUID

from agents import function_tool

from src.lib.context import get_current_session_id, get_current_user_id
from src.lib.export_accounting import (
    diagnostics_sha256 as hash_diagnostics,
    exact_diagnostics_json,
)
from src.lib.observability.runtime import (
    report_runtime_exception,
    sanitized_runtime_error,
)
from src.lib.openai_agents.config import get_supervisor_field_text_limit
from src.models.sql.database import SessionLocal
from src.models.sql.file_output import FileOutput


def read_export_details_payload(
    file_id: str, diagnostics_sha256: str, offset: int = 0, max_chars: int | None = None
) -> dict:
    user, session = get_current_user_id(), get_current_session_id()
    if not user or not session:
        return {"status": "unavailable", "reason": "Authenticated session is required"}
    try:
        identity = UUID(file_id)
    except ValueError:
        return {"status": "invalid", "reason": "Invalid file reference"}
    if offset < 0 or (max_chars is not None and max_chars < 1):
        return {"status": "invalid", "reason": "Invalid detail window"}
    try:
        with SessionLocal() as db:
            file = (
                db.query(FileOutput)
                .filter(
                    FileOutput.id == identity,
                    FileOutput.curator_id == user,
                    FileOutput.session_id == session,
                )
                .one_or_none()
            )
            if file is None:
                return {
                    "status": "unavailable",
                    "reason": "File is not available in this session",
                }
            diagnostics = (file.file_metadata or {}).get("export_diagnostics")
            if diagnostics is None:
                return {
                    "status": "unavailable",
                    "reason": "This file has no recorded export diagnostics",
                }
            actual_hash = hash_diagnostics(diagnostics)
            if actual_hash != diagnostics_sha256:
                return {
                    "status": "stale",
                    "reason": "Export diagnostics changed; do not combine different versions",
                }
            text = exact_diagnostics_json(diagnostics)
        if offset > len(text):
            return {
                "status": "invalid",
                "reason": "Detail offset is outside the recorded data",
            }
        limit = get_supervisor_field_text_limit()
        length = min(max_chars, limit) if max_chars is not None else limit
        end = min(offset + length, len(text))
        return {
            "status": "ok",
            "file_id": file_id,
            "diagnostics_sha256": actual_hash,
            "encoding": "json",
            "offset": offset,
            "text": text[offset:end],
            "total_chars": len(text),
            "next_offset": end if end < len(text) else None,
        }
    except Exception as exc:
        report_runtime_exception(
            sanitized_runtime_error("Export diagnostics read failed"),
            component="export_diagnostics",
            operation="read",
            context={"error_type": type(exc).__name__},
        )
        return {"status": "error", "reason": "Export diagnostics could not be read"}


def make_export_details_tool():
    @function_tool(
        name_override="read_export_details",
        description_override=(
            "Read exact saved export accounting, applied filters and full warnings for a file in "
            "this authenticated session. Pass file_id and diagnostics_sha256 from export_details.read "
            "in its successful save receipt. Continue with next_offset until null and concatenate "
            "text chunks to recover the JSON. This is read-only and never reruns extraction or saves a file."
        ),
    )
    async def read_export_details(
        file_id: str,
        diagnostics_sha256: str,
        offset: int = 0,
        max_chars: int | None = None,
    ) -> str:
        return json.dumps(
            read_export_details_payload(file_id, diagnostics_sha256, offset, max_chars),
            ensure_ascii=False,
        )

    return read_export_details

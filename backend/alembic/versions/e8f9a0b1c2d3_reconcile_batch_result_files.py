"""Reconcile canonical batch result manifests and remove the legacy alias.

Revision ID: e8f9a0b1c2d3
Revises: d6e7f8a9b0c1
Create Date: 2026-08-13 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from alembic import op  # pyright: ignore[reportAttributeAccessIssue]


revision: str = "e8f9a0b1c2d3"
down_revision: str | Sequence[str] | None = "d6e7f8a9b0c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FILE_DOWNLOAD_PATTERNS = (
    re.compile(r"^/api/files/([0-9a-fA-F-]+)/download$"),
    re.compile(r"^/api/weaviate/documents/download/([0-9a-fA-F-]+)$"),
)
_REQUIRED_RESULT_FIELDS = ("file_id", "filename", "download_url")


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _candidate_file_id(entry: Mapping[str, Any]) -> UUID | None:
    raw_file_id = entry.get("file_id")
    if isinstance(raw_file_id, str) and raw_file_id.strip():
        try:
            return UUID(raw_file_id)
        except ValueError:
            return None

    download_url = entry.get("download_url")
    if not isinstance(download_url, str) or not download_url.strip():
        return None
    for pattern in _FILE_DOWNLOAD_PATTERNS:
        match = pattern.fullmatch(download_url)
        if match:
            try:
                return UUID(match.group(1))
            except ValueError:
                return None
    return None


def _reconcile_batch_results(
    connection: sa.Connection,
    *,
    has_legacy_column: bool,
) -> None:
    legacy_projection = (
        "result_file_path" if has_legacy_column else "NULL::text AS result_file_path"
    )
    rows = connection.execute(
        sa.text(
            f"""
            SELECT id, result_files, {legacy_projection}
            FROM batch_documents
            WHERE result_files IS NOT NULL
               OR {"result_file_path IS NOT NULL" if has_legacy_column else "FALSE"}
            ORDER BY id
            FOR UPDATE
            """
        )
    ).mappings()

    updates: list[tuple[UUID, list[dict[str, Any]]]] = []
    irreconcilable_ids: list[str] = []
    file_lookup = sa.text(
        "SELECT id, filename FROM file_outputs WHERE id = :file_id"
    )

    for row in rows:
        row_id = row["id"]
        raw_manifest = row["result_files"]
        legacy_url = row["result_file_path"]
        if raw_manifest is None and not _nonempty_string(legacy_url):
            continue
        if raw_manifest is None or raw_manifest == []:
            entries: Any = (
                [{"download_url": legacy_url}]
                if _nonempty_string(legacy_url)
                else []
            )
        else:
            entries = raw_manifest

        if not isinstance(entries, list) or not all(
            isinstance(entry, Mapping) for entry in entries
        ):
            irreconcilable_ids.append(str(row_id))
            continue

        reconciled: list[dict[str, Any]] = []
        row_is_reconcilable = True
        for raw_entry in entries:
            entry = dict(raw_entry)
            if all(_nonempty_string(entry.get(field)) for field in _REQUIRED_RESULT_FIELDS):
                try:
                    UUID(entry["file_id"])
                except ValueError:
                    row_is_reconcilable = False
                    break
                else:
                    reconciled.append(entry)
                    continue

            file_id = _candidate_file_id(entry)
            if file_id is None:
                row_is_reconcilable = False
                break
            file_output = connection.execute(
                file_lookup,
                {"file_id": file_id},
            ).mappings().one_or_none()
            if file_output is None:
                row_is_reconcilable = False
                break

            entry.update(
                {
                    "file_id": str(file_output["id"]),
                    "filename": file_output["filename"],
                    "download_url": f"/api/files/{file_output['id']}/download",
                }
            )
            reconciled.append(entry)

        if not row_is_reconcilable:
            irreconcilable_ids.append(str(row_id))
            continue
        if reconciled != raw_manifest:
            updates.append((row_id, reconciled))

    if irreconcilable_ids:
        raise RuntimeError(
            "Cannot reconcile batch result manifests for batch_document rows: "
            + ", ".join(sorted(irreconcilable_ids))
        )

    update_statement = sa.text(
        "UPDATE batch_documents SET result_files = :result_files WHERE id = :row_id"
    ).bindparams(sa.bindparam("result_files", type_=sa.JSON()))
    for row_id, result_files in updates:
        connection.execute(
            update_statement,
            {"row_id": row_id, "result_files": result_files},
        )


def upgrade() -> None:
    connection = op.get_bind()
    columns = {
        column["name"] for column in sa.inspect(connection).get_columns("batch_documents")
    }
    has_legacy_column = "result_file_path" in columns
    _reconcile_batch_results(connection, has_legacy_column=has_legacy_column)
    if has_legacy_column:
        # Removed legacy result_file_path/URL-only fallback — superseded by
        # canonical result_files after the v0.8.11 reconciliation (follow-up to ALL-749).
        op.drop_column("batch_documents", "result_file_path")


def downgrade() -> None:
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("batch_documents")
    }
    if "result_file_path" not in columns:
        op.add_column(
            "batch_documents",
            sa.Column("result_file_path", sa.String(length=500), nullable=True),
        )
    op.execute(
        """
        UPDATE batch_documents
        SET result_file_path = result_files->0->>'download_url'
        WHERE jsonb_array_length(COALESCE(result_files, '[]'::jsonb)) > 0
        """
    )

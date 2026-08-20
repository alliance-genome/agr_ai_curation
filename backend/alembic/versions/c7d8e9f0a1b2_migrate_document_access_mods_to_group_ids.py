"""Migrate document-source access metadata to neutral group IDs.

Revision ID: c7d8e9f0a1b2
Revises: 2b3c4d5e6f7a
Create Date: 2026-08-20
"""

from collections.abc import Mapping, Sequence
from typing import Any

from alembic import op
import sqlalchemy as sa


revision: str = "c7d8e9f0a1b2"
down_revision: str | Sequence[str] | None = "2b3c4d5e6f7a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Rename and flatten legacy access metadata after fail-closed validation."""

    connection = op.get_bind()
    invalid_document_ids = _invalid_legacy_document_ids(connection)
    if invalid_document_ids:
        raise RuntimeError(
            "Cannot migrate document access groups; restricted or populated legacy "
            "rows have invalid source_access_mods values: "
            + ", ".join(invalid_document_ids)
        )

    op.alter_column(
        "pdf_documents",
        "source_access_mods",
        new_column_name="source_access_group_ids",
    )
    op.execute(
        sa.text(
            """
            UPDATE pdf_documents
            SET source_access_group_ids = source_access_group_ids -> 'mods'
            WHERE source_access_group_ids IS NOT NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE pdf_processing_jobs
            SET metadata_json = jsonb_set(
                metadata_json,
                '{document_source}',
                (metadata_json -> 'document_source') - 'per_mod_status'
            )
            WHERE jsonb_typeof(metadata_json -> 'document_source') = 'object'
              AND (metadata_json -> 'document_source') ? 'per_mod_status'
            """
        )
    )


def downgrade() -> None:
    """Restore the former nested access object and column name.

    Removed provider diagnostics are intentionally not reconstructed.
    """

    op.execute(
        sa.text(
            """
            UPDATE pdf_documents
            SET source_access_group_ids = jsonb_build_object(
                'mods', source_access_group_ids
            )
            WHERE source_access_group_ids IS NOT NULL
            """
        )
    )
    op.alter_column(
        "pdf_documents",
        "source_access_group_ids",
        new_column_name="source_access_mods",
    )


def _invalid_legacy_document_ids(connection: Any) -> list[str]:
    rows = connection.execute(
        sa.text(
            """
            SELECT id, source_access_scope, source_access_mods
            FROM pdf_documents
            ORDER BY id
            """
        )
    ).mappings()

    invalid: list[str] = []
    for row in rows:
        scope = str(row["source_access_scope"] or "").strip().lower()
        value = row["source_access_mods"]
        if value is None:
            if scope == "restricted":
                invalid.append(str(row["id"]))
            continue
        group_ids = _legacy_group_ids(value)
        if group_ids is None or (scope == "restricted" and not group_ids):
            invalid.append(str(row["id"]))
    return invalid


def _legacy_group_ids(value: object) -> list[str] | None:
    if not isinstance(value, Mapping):
        return None
    raw_group_ids = value.get("mods")
    if not isinstance(raw_group_ids, list):
        return None
    if any(not isinstance(item, str) or not item.strip() for item in raw_group_ids):
        return None
    return raw_group_ids

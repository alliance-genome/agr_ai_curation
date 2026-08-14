"""Require every PDF document to have an authenticated owner.

Revision ID: 2b3c4d5e6f7a
Revises: 1a2b3c4d5e6f
Create Date: 2026-08-14
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from alembic import op  # pyright: ignore[reportAttributeAccessIssue]


revision: str = "2b3c4d5e6f7a"
down_revision: str | Sequence[str] | None = "1a2b3c4d5e6f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _canonical_auth_sub(row_id: UUID, file_path: Any) -> str | None:
    """Return the owner identity encoded by a canonical relative PDF path."""

    if not isinstance(file_path, str) or file_path.startswith("/"):
        return None
    parts = file_path.split("/")
    if (
        len(parts) != 3
        or any(part in {"", ".", ".."} for part in parts)
        or parts[1] != str(row_id)
    ):
        return None
    return parts[0]


def _reconcile_null_owners(connection: sa.Connection) -> None:
    """Plan all exact owner matches before applying any row updates."""

    candidates = connection.execute(
        sa.text(
            """
            SELECT id, file_path
            FROM pdf_documents
            WHERE user_id IS NULL
            ORDER BY id
            FOR UPDATE
            """
        )
    ).mappings()

    user_lookup = sa.text(
        "SELECT user_id FROM users WHERE auth_sub = :auth_sub ORDER BY user_id"
    )
    planned_updates: list[tuple[UUID, int]] = []
    irreconcilable_ids: list[str] = []

    for candidate in candidates:
        row_id = candidate["id"]
        auth_sub = _canonical_auth_sub(row_id, candidate["file_path"])
        if auth_sub is None:
            irreconcilable_ids.append(str(row_id))
            continue

        matches = (
            connection.execute(
                user_lookup,
                {"auth_sub": auth_sub},
            )
            .mappings()
            .all()
        )
        if len(matches) != 1:
            irreconcilable_ids.append(str(row_id))
            continue
        planned_updates.append((row_id, matches[0]["user_id"]))

    if irreconcilable_ids:
        raise RuntimeError(
            "Cannot determine an authenticated owner for pdf_documents rows: "
            + ", ".join(sorted(irreconcilable_ids))
        )

    update_owner = sa.text(
        "UPDATE pdf_documents SET user_id = :user_id WHERE id = :document_id"
    )
    for document_id, user_id in planned_updates:
        connection.execute(
            update_owner,
            {"document_id": document_id, "user_id": user_id},
        )


def upgrade() -> None:
    """Reconcile canonical legacy rows, then enforce required ownership."""

    _reconcile_null_owners(op.get_bind())
    op.alter_column(
        "pdf_documents",
        "user_id",
        existing_type=sa.Integer(),
        nullable=False,
    )


def downgrade() -> None:
    """Restore the historical nullable column shape."""

    op.alter_column(
        "pdf_documents",
        "user_id",
        existing_type=sa.Integer(),
        nullable=True,
    )

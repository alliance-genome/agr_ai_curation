"""Reconcile nonterminal documents omitted by the prior PDF job repair.

Revision ID: 6f7a8b9c0d1e
Revises: 5e6f7a8b9c0d
Create Date: 2026-08-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op  # pyright: ignore[reportAttributeAccessIssue]


revision: str = "6f7a8b9c0d1e"
down_revision: str | Sequence[str] | None = "5e6f7a8b9c0d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Fail nonterminal documents when their deterministic latest job failed."""
    op.execute(
        sa.text(
            """
            WITH ranked_jobs AS (
                SELECT
                    document_id,
                    status,
                    message,
                    error_message,
                    created_at,
                    started_at,
                    updated_at,
                    completed_at,
                    ROW_NUMBER() OVER (
                        PARTITION BY document_id
                        -- updated_at is mutable activity time, not creation chronology.
                        ORDER BY created_at DESC, id DESC
                    ) AS job_rank
                FROM pdf_processing_jobs
            )
            UPDATE pdf_documents AS document
            SET
                status = 'failed',
                processing_started_at = COALESCE(
                    document.processing_started_at,
                    latest.started_at,
                    latest.created_at
                ),
                -- Historical terminal rows may predate runtime timestamp invariants.
                processing_completed_at = COALESCE(
                    latest.completed_at,
                    latest.updated_at,
                    latest.created_at
                ),
                error_message = substr(
                    COALESCE(
                        latest.error_message,
                        latest.message,
                        'PDF processing job ' || latest.status
                    ),
                    1,
                    1000
                )
            FROM ranked_jobs AS latest
            WHERE latest.document_id = document.id
              AND latest.job_rank = 1
              AND latest.status IN ('failed', 'cancelled')
              AND document.status NOT IN ('completed', 'failed')
            """
        )
    )


def downgrade() -> None:
    """No-op: the prior inconsistent document state cannot be reconstructed."""

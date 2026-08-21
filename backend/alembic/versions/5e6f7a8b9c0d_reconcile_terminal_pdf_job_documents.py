"""Reconcile documents from their latest failed or cancelled PDF job.

Revision ID: 5e6f7a8b9c0d
Revises: 4d5e6f7a8b9c
Create Date: 2026-08-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op  # pyright: ignore[reportAttributeAccessIssue]


revision: str = "5e6f7a8b9c0d"
down_revision: str | Sequence[str] | None = "4d5e6f7a8b9c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Fail active documents only when their deterministic latest job failed."""
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
              AND document.status IN (
                  'processing',
                  'parsing',
                  'chunking',
                  'embedding',
                  'storing'
              )
            """
        )
    )


def downgrade() -> None:
    """No-op: the prior inconsistent active status cannot be reconstructed."""


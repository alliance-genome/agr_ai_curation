"""Add ABC Literature provenance columns to PDF documents.

Revision ID: x9y0z1a2b3c4
Revises: w8x9y0z1a2b3
Create Date: 2026-06-24 00:00:00.000000
"""

from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "x9y0z1a2b3c4"
down_revision: Union[str, Sequence[str], None] = "w8x9y0z1a2b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    """Upgrade schema."""

    op.add_column(
        "pdf_documents",
        sa.Column("source_provider", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "pdf_documents",
        sa.Column("source_provider_reference_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "pdf_documents",
        sa.Column(
            "source_provider_reference_curie",
            sa.String(length=128),
            nullable=True,
        ),
    )
    op.add_column(
        "pdf_documents",
        sa.Column(
            "source_provider_source_file_id",
            sa.String(length=64),
            nullable=True,
        ),
    )
    op.add_column(
        "pdf_documents",
        sa.Column(
            "source_provider_converted_artifact_id",
            sa.String(length=64),
            nullable=True,
        ),
    )
    op.add_column(
        "pdf_documents",
        sa.Column(
            "source_provider_pdf_artifact_id",
            sa.String(length=64),
            nullable=True,
        ),
    )
    op.add_column(
        "pdf_documents",
        sa.Column("source_external_ids", JSONB, nullable=True),
    )
    op.add_column(
        "pdf_documents",
        sa.Column("source_md5", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "pdf_documents",
        sa.Column("source_file_class", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "pdf_documents",
        sa.Column("source_file_extension", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "pdf_documents",
        sa.Column("source_artifact_status", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "pdf_documents",
        sa.Column("source_import_status", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "pdf_documents",
        sa.Column("source_imported_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "pdf_documents",
        sa.Column("source_payload_path", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "pdf_documents",
        sa.Column("source_markdown_path", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "pdf_documents",
        sa.Column("source_access_scope", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "pdf_documents",
        sa.Column("source_access_mods", JSONB, nullable=True),
    )
    op.add_column(
        "pdf_documents",
        sa.Column("viewer_mode", sa.String(length=64), nullable=True),
    )

    op.create_index(
        "ix_pdf_documents_source_reference",
        "pdf_documents",
        ["source_provider", "source_provider_reference_curie"],
        unique=False,
        postgresql_where=sa.text(
            "source_provider IS NOT NULL "
            "AND source_provider_reference_curie IS NOT NULL"
        ),
    )
    op.create_index(
        "ix_pdf_documents_source_reference_id",
        "pdf_documents",
        ["source_provider", "source_provider_reference_id"],
        unique=False,
        postgresql_where=sa.text(
            "source_provider IS NOT NULL "
            "AND source_provider_reference_id IS NOT NULL"
        ),
    )
    op.create_index(
        "ix_pdf_documents_source_artifact",
        "pdf_documents",
        ["source_provider", "source_provider_converted_artifact_id"],
        unique=False,
        postgresql_where=sa.text(
            "source_provider IS NOT NULL "
            "AND source_provider_converted_artifact_id IS NOT NULL"
        ),
    )
    op.create_index(
        "ix_pdf_documents_source_md5",
        "pdf_documents",
        ["source_md5"],
        unique=False,
        postgresql_where=sa.text("source_md5 IS NOT NULL"),
    )


def downgrade() -> None:
    """Downgrade schema."""

    op.drop_index("ix_pdf_documents_source_md5", table_name="pdf_documents")
    op.drop_index("ix_pdf_documents_source_artifact", table_name="pdf_documents")
    op.drop_index("ix_pdf_documents_source_reference_id", table_name="pdf_documents")
    op.drop_index("ix_pdf_documents_source_reference", table_name="pdf_documents")

    op.drop_column("pdf_documents", "viewer_mode")
    op.drop_column("pdf_documents", "source_access_mods")
    op.drop_column("pdf_documents", "source_access_scope")
    op.drop_column("pdf_documents", "source_markdown_path")
    op.drop_column("pdf_documents", "source_payload_path")
    op.drop_column("pdf_documents", "source_imported_at")
    op.drop_column("pdf_documents", "source_import_status")
    op.drop_column("pdf_documents", "source_artifact_status")
    op.drop_column("pdf_documents", "source_file_extension")
    op.drop_column("pdf_documents", "source_file_class")
    op.drop_column("pdf_documents", "source_md5")
    op.drop_column("pdf_documents", "source_external_ids")
    op.drop_column("pdf_documents", "source_provider_pdf_artifact_id")
    op.drop_column("pdf_documents", "source_provider_converted_artifact_id")
    op.drop_column("pdf_documents", "source_provider_source_file_id")
    op.drop_column("pdf_documents", "source_provider_reference_curie")
    op.drop_column("pdf_documents", "source_provider_reference_id")
    op.drop_column("pdf_documents", "source_provider")

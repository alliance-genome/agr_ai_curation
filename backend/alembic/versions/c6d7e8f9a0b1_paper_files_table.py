"""Every paper gets a list of files: move per-file columns to ``pdf_document_files``.

Each existing ``pdf_documents`` row becomes a paper with exactly one ``main``
file row that carries the paper's former per-file columns unchanged. The paper
keeps its identity, name, title, owner, status, times and reference
provenance, and gains ``document_kind`` (from the retired
``viewer_mode = 'benchmark_frozen'``) and ``next_chunk_index``.

This migration changes only PostgreSQL. It reads nothing from the search index
or disk, so a file's piece range, piece count and text size stay NULL here and
``next_chunk_index`` stays NULL on existing papers; the one-time fill step
(``python -m src.lib.document_files.fill_existing``) measures and records them
before traffic reopens.

Forward-only, and safe to restart: Alembic runs every pending migration in one
transaction, so a failure anywhere leaves the database exactly as it was. Any
row this migration cannot convert stops it with ``PaperFilesMigrationError``
naming the affected internal row ids; nothing is skipped and no missing value
is invented. The copy is one set-based statement (about 1,200 papers in
production), so there is no per-row work to batch. It verifies its own result
before dropping the old columns. The schema changes take short ACCESS EXCLUSIVE
locks on ``pdf_documents``; run it only in the release maintenance window with
traffic stopped (see docs/developer/guides/PAPER_FILES_MIGRATION.md).

Revision ID: c6d7e8f9a0b1
Revises: a9b8c7d6e5f4
Create Date: 2026-11-20
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "c6d7e8f9a0b1"
down_revision: Union[str, Sequence[str], None] = "a9b8c7d6e5f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ``viewer_mode`` values written by any release, and the file display mode each
# becomes. NULL is what plain uploads store; every reader has always shown it
# as a local PDF (``api/pdf_viewer.py`` ``_viewer_mode``).
_DISPLAY_MODE_SQL = """
    CASE
        WHEN d.viewer_mode IS NULL THEN 'local_pdf'
        WHEN d.viewer_mode = 'local_pdf' THEN 'local_pdf'
        WHEN d.viewer_mode = 'provider_pdf_proxy' THEN 'provider_pdf_proxy'
        WHEN d.viewer_mode = 'text_only' THEN 'text'
        -- Frozen benchmark inputs are JSON, Markdown or XML text
        -- (src/lib/benchmarks/document_inputs.py); they never store a PDF.
        WHEN d.viewer_mode = 'benchmark_frozen' THEN 'text'
    END
"""
_KNOWN_VIEWER_MODES = ("local_pdf", "provider_pdf_proxy", "text_only", "benchmark_frozen")

# Per-file columns copied unchanged from the paper to its main file.
_COPIED_FILE_COLUMNS = (
    "file_path",
    "file_hash",
    "file_size",
    "pdfx_json_path",
    "processed_json_path",
    "hierarchy_metadata",
    "source_provider_source_file_id",
    "source_provider_converted_artifact_id",
    "source_provider_pdf_artifact_id",
    "source_md5",
    "source_converted_artifact_sha256",
    "source_file_class",
    "source_file_extension",
    "source_artifact_status",
    "source_import_status",
    "source_imported_at",
    "source_payload_path",
    "source_markdown_path",
    "source_access_scope",
    "source_access_group_ids",
)
# Columns dropped from the paper. ``page_count`` moves too; a text file keeps no
# page count because it has no pages (the old NOT NULL column held a placeholder).
_DROPPED_PAPER_COLUMNS = (*_COPIED_FILE_COLUMNS, "page_count", "viewer_mode")

_ID_LIST_LIMIT = 50


class PaperFilesMigrationError(RuntimeError):
    """The paper-files migration found rows it cannot convert or verify."""


def _ids(rows) -> str:
    ids = [str(row[0]) for row in rows]
    shown = ", ".join(ids[:_ID_LIST_LIMIT])
    more = len(ids) - _ID_LIST_LIMIT
    return f"{shown} (and {more} more)" if more > 0 else shown


def _require_convertible_rows(bind) -> None:
    unknown = bind.execute(sa.text(
        "SELECT id FROM pdf_documents "
        "WHERE viewer_mode IS NOT NULL AND viewer_mode NOT IN :modes ORDER BY id"
    ).bindparams(sa.bindparam("modes", expanding=True)), {"modes": list(_KNOWN_VIEWER_MODES)}).all()
    if unknown:
        raise PaperFilesMigrationError(
            f"{len(unknown)} papers have an unknown viewer_mode; nothing was changed: {_ids(unknown)}"
        )
    missing = bind.execute(sa.text(
        "SELECT id FROM pdf_documents "
        "WHERE file_path IS NULL OR file_hash IS NULL OR file_size IS NULL OR file_size <= 0 "
        "OR filename IS NULL OR status IS NULL OR upload_timestamp IS NULL "
        "OR ((viewer_mode IS NULL OR viewer_mode IN ('local_pdf', 'provider_pdf_proxy')) "
        "    AND (page_count IS NULL OR page_count <= 0)) "
        "ORDER BY id"
    )).all()
    if missing:
        raise PaperFilesMigrationError(
            f"{len(missing)} papers lack a required file value; nothing was changed: {_ids(missing)}"
        )


def _create_files_table() -> None:
    op.create_table(
        "pdf_document_files",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pdf_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.Column("tab_order", sa.Integer(), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("display_mode", sa.String(32), nullable=False),
        sa.Column("file_path", sa.String(500), nullable=False),
        sa.Column("file_hash", sa.String(64), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("pdfx_json_path", sa.String(500), nullable=True),
        sa.Column("processed_json_path", sa.String(500), nullable=True),
        sa.Column("hierarchy_metadata", postgresql.JSONB(), nullable=True),
        sa.Column("source_provider_source_file_id", sa.String(64), nullable=True),
        sa.Column("source_provider_converted_artifact_id", sa.String(64), nullable=True),
        sa.Column("source_provider_pdf_artifact_id", sa.String(64), nullable=True),
        sa.Column("source_md5", sa.String(64), nullable=True),
        sa.Column("source_converted_artifact_sha256", sa.String(64), nullable=True),
        sa.Column("source_file_class", sa.String(128), nullable=True),
        sa.Column("source_file_extension", sa.String(32), nullable=True),
        sa.Column("source_artifact_status", sa.String(64), nullable=True),
        sa.Column("source_import_status", sa.String(64), nullable=True),
        sa.Column("source_imported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_payload_path", sa.String(500), nullable=True),
        sa.Column("source_markdown_path", sa.String(500), nullable=True),
        sa.Column("source_access_scope", sa.String(32), nullable=True),
        sa.Column("source_access_group_ids", postgresql.JSONB(), nullable=True),
        sa.Column("chunk_index_start", sa.Integer(), nullable=True),
        sa.Column("chunk_index_end", sa.Integer(), nullable=True),
        sa.Column("chunk_count", sa.Integer(), nullable=True),
        sa.Column("text_bytes", sa.BigInteger(), nullable=True),
        sa.CheckConstraint("role IN ('main', 'supplement')", name="ck_pdf_document_files_role"),
        sa.CheckConstraint(
            "status IN ('pending', 'ready', 'failed')", name="ck_pdf_document_files_status",
        ),
        sa.CheckConstraint(
            "display_mode IN ('local_pdf', 'provider_pdf_proxy', 'text')",
            name="ck_pdf_document_files_display_mode",
        ),
        sa.CheckConstraint("tab_order >= 0", name="ck_pdf_document_files_tab_order"),
        sa.CheckConstraint("file_size > 0", name="ck_pdf_document_files_file_size"),
        sa.CheckConstraint(
            "(display_mode = 'text' AND page_count IS NULL) "
            "OR (display_mode <> 'text' AND page_count IS NOT NULL AND page_count > 0)",
            name="ck_pdf_document_files_page_count",
        ),
        sa.CheckConstraint(
            "source_converted_artifact_sha256 IS NULL "
            "OR source_converted_artifact_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_pdf_document_files_converted_artifact_sha256",
        ),
        sa.CheckConstraint(
            "(chunk_index_start IS NULL AND chunk_index_end IS NULL) "
            "OR (chunk_index_start >= 0 AND chunk_index_end >= chunk_index_start "
            "AND chunk_count = chunk_index_end - chunk_index_start + 1)",
            name="ck_pdf_document_files_chunk_range",
        ),
        sa.CheckConstraint(
            "chunk_count IS NULL OR chunk_count >= 0", name="ck_pdf_document_files_chunk_count",
        ),
        sa.CheckConstraint(
            "text_bytes IS NULL OR text_bytes >= 0", name="ck_pdf_document_files_text_bytes",
        ),
        sa.UniqueConstraint("file_path", name="uq_pdf_document_files_file_path"),
        sa.UniqueConstraint("document_id", "tab_order", name="uq_pdf_document_files_tab_order"),
        sa.UniqueConstraint(
            "document_id", "source_provider_source_file_id", name="uq_pdf_document_files_source_file",
        ),
    )
    op.create_index("ix_pdf_document_files_document_id", "pdf_document_files", ["document_id"])
    op.create_index(
        "uq_pdf_document_files_one_main",
        "pdf_document_files",
        ["document_id"],
        unique=True,
        postgresql_where=sa.text("role = 'main'"),
    )
    op.create_index(
        "uq_pdf_document_files_main_file_hash",
        "pdf_document_files",
        ["file_hash"],
        unique=True,
        postgresql_where=sa.text("role = 'main'"),
    )
    op.create_index(
        "ix_pdf_document_files_source_md5",
        "pdf_document_files",
        ["source_md5"],
        postgresql_where=sa.text("source_md5 IS NOT NULL"),
    )
    op.create_index(
        "ix_pdf_document_files_converted_artifact",
        "pdf_document_files",
        ["source_provider_converted_artifact_id"],
        postgresql_where=sa.text("source_provider_converted_artifact_id IS NOT NULL"),
    )


def _copy_main_files(bind) -> None:
    columns = ", ".join(_COPIED_FILE_COLUMNS)
    selected = ", ".join(f"d.{column}" for column in _COPIED_FILE_COLUMNS)
    bind.execute(sa.text(f"""
        INSERT INTO pdf_document_files (
            id, document_id, role, display_name, status, error_message, tab_order,
            added_at, display_mode, page_count, {columns}
        )
        SELECT
            gen_random_uuid(), d.id, 'main', d.filename,
            CASE WHEN d.status = 'completed' THEN 'ready' ELSE 'failed' END,
            d.error_message, 0, d.upload_timestamp,
            {_DISPLAY_MODE_SQL},
            CASE WHEN {_DISPLAY_MODE_SQL} = 'text' THEN NULL ELSE d.page_count END,
            {selected}
        FROM pdf_documents d
    """))


def _verify_copy(bind, paper_count: int) -> None:
    file_count = bind.scalar(sa.text("SELECT count(*) FROM pdf_document_files"))
    main_count = bind.scalar(sa.text("SELECT count(*) FROM pdf_document_files WHERE role = 'main'"))
    if file_count != paper_count or main_count != paper_count:
        raise PaperFilesMigrationError(
            f"Expected {paper_count} main files, found {main_count} main of {file_count} files"
        )
    without_main = bind.execute(sa.text(
        "SELECT d.id FROM pdf_documents d "
        "LEFT JOIN pdf_document_files f ON f.document_id = d.id AND f.role = 'main' "
        "WHERE f.id IS NULL ORDER BY d.id"
    )).all()
    if without_main:
        raise PaperFilesMigrationError(
            f"{len(without_main)} papers have no main file after the copy: {_ids(without_main)}"
        )
    differs = " OR ".join(
        f"f.{column} IS DISTINCT FROM d.{column}" for column in _COPIED_FILE_COLUMNS
    )
    changed = bind.execute(sa.text(f"""
        SELECT d.id FROM pdf_documents d
        JOIN pdf_document_files f ON f.document_id = d.id AND f.role = 'main'
        WHERE {differs}
           OR f.display_name IS DISTINCT FROM d.filename
           OR f.added_at IS DISTINCT FROM d.upload_timestamp
           OR f.display_mode IS DISTINCT FROM ({_DISPLAY_MODE_SQL})
           OR (f.display_mode <> 'text' AND f.page_count IS DISTINCT FROM d.page_count)
           OR f.status IS DISTINCT FROM
              (CASE WHEN d.status = 'completed' THEN 'ready' ELSE 'failed' END)
        ORDER BY d.id
    """)).all()
    if changed:
        raise PaperFilesMigrationError(
            f"{len(changed)} main files differ from their paper after the copy: {_ids(changed)}"
        )


def _add_paper_columns(bind) -> None:
    op.add_column("pdf_documents", sa.Column("document_kind", sa.String(32), nullable=True))
    op.add_column("pdf_documents", sa.Column("next_chunk_index", sa.Integer(), nullable=True))
    bind.execute(sa.text(
        "UPDATE pdf_documents SET document_kind = CASE "
        "WHEN viewer_mode = 'benchmark_frozen' THEN 'benchmark_frozen' ELSE 'library' END"
    ))
    op.alter_column("pdf_documents", "document_kind", nullable=False)
    op.create_check_constraint(
        "ck_pdf_documents_document_kind",
        "pdf_documents",
        "document_kind IN ('library', 'benchmark_frozen')",
    )
    op.create_check_constraint(
        "ck_pdf_documents_next_chunk_index",
        "pdf_documents",
        "next_chunk_index IS NULL OR next_chunk_index >= 0",
    )


def _verify_kinds(bind) -> None:
    wrong = bind.execute(sa.text(
        "SELECT id FROM pdf_documents WHERE document_kind IS DISTINCT FROM "
        "(CASE WHEN viewer_mode = 'benchmark_frozen' THEN 'benchmark_frozen' ELSE 'library' END) "
        "ORDER BY id"
    )).all()
    if wrong:
        raise PaperFilesMigrationError(f"{len(wrong)} papers have the wrong kind: {_ids(wrong)}")


def upgrade() -> None:
    bind = op.get_bind()
    paper_count = bind.scalar(sa.text("SELECT count(*) FROM pdf_documents"))
    _require_convertible_rows(bind)
    _create_files_table()
    _copy_main_files(bind)
    _verify_copy(bind, paper_count)
    _add_paper_columns(bind)
    _verify_kinds(bind)
    # Dependent indexes and constraints on these columns are dropped with them.
    for column in _DROPPED_PAPER_COLUMNS:
        op.drop_column("pdf_documents", column)
    final_count = bind.scalar(sa.text("SELECT count(*) FROM pdf_documents"))
    if final_count != paper_count:
        raise PaperFilesMigrationError(
            f"Paper count changed during the migration: {paper_count} before, {final_count} after"
        )


def downgrade() -> None:
    raise RuntimeError(
        "Paper files are forward-only; inside the maintenance window, restore the "
        "pre-migration database backup, and after traffic reopens fix forward"
    )

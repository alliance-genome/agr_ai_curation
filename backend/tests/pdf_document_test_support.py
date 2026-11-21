"""Shared helpers for persistence tests that need an owned PDF document."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.sql.user import User


def ensure_test_pdf_owner(session: Session, *, auth_sub: str) -> int:
    """Return a durable test user ID for a PDF-document fixture."""

    owner = session.scalar(select(User).where(User.auth_sub == auth_sub))
    if owner is None:
        owner = User(auth_sub=auth_sub, is_active=True)
        session.add(owner)
        session.flush()
    return int(owner.id)


def new_paper(**columns):
    """Build a test paper and its main file from one set of column values.

    Values named after ``pdf_document_files`` columns go to the main file and the
    rest to the paper. The main file defaults to a stored local PDF named after
    the paper, ready when the paper is completed, and its pieces start at 0.
    """

    from src.models.sql.pdf_document import PDFDocument, PDFDocumentFile

    file_columns = set(PDFDocumentFile.__table__.c.keys()) - {"id", "document_id", "status", "error_message"}
    file_values = {key: columns.pop(key) for key in list(columns) if key in file_columns}
    paper = PDFDocument(**columns)
    if paper.next_chunk_index is None and "next_chunk_index" not in columns:
        paper.next_chunk_index = 0
    status = "ready" if columns.get("status") == "completed" else "pending"
    file_values.setdefault("role", "main")
    file_values.setdefault("display_name", columns.get("filename") or "made-up.pdf")
    file_values.setdefault("tab_order", 0)
    file_values.setdefault("display_mode", "local_pdf")
    # SQLite test schemas do not apply the server-side default.
    file_values.setdefault("added_at", columns.get("upload_timestamp") or datetime.now(timezone.utc))
    paper.files.append(PDFDocumentFile(status=status, **file_values))
    return paper


def new_supplements_only_paper(**columns):
    """Build a test paper that holds supplements only, with no main file.

    The supplement is a converted text file (display mode ``text``, no page
    count), the shape a supplement import writes. Its hash and path are its
    stored Markdown's (both are required on every file row).
    """

    from src.models.sql.pdf_document import PDFDocument, PDFDocumentFile

    paper = PDFDocument(**columns)
    if paper.next_chunk_index is None and "next_chunk_index" not in columns:
        paper.next_chunk_index = 0
    paper.files.append(
        PDFDocumentFile(
            role="supplement",
            display_name="Table_S1.xlsx",
            status="ready",
            tab_order=1,
            display_mode="text",
            page_count=None,
            file_hash=uuid4().hex * 2,
            # A text supplement's stored file is its converted Markdown.
            file_path=f"owner/source_markdown/{uuid4()}.md",
            file_size=2048,
            added_at=columns.get("upload_timestamp") or datetime.now(timezone.utc),
            chunk_index_start=0,
            chunk_index_end=2,
            chunk_count=3,
            text_bytes=1800,
        )
    )
    return paper

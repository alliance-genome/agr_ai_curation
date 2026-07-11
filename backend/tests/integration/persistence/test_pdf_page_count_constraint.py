"""Persistence coverage for the positive-only PDF page-count invariant."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError

from src.models.sql.database import SessionLocal
from src.models.sql.pdf_document import PDFDocument


BACKEND_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module", autouse=True)
def migrated_database():
    alembic_config = Config(str(BACKEND_ROOT / "alembic.ini"))
    command.upgrade(alembic_config, "head")


@pytest.fixture
def db_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.execute(
            delete(PDFDocument).where(
                PDFDocument.filename.like("page_count_constraint_%")
            )
        )
        session.commit()
        session.close()


def _document(page_count: int) -> PDFDocument:
    document_id = uuid4()
    return PDFDocument(
        id=document_id,
        filename=f"page_count_constraint_{document_id}.pdf",
        file_path=f"test/{document_id}.pdf",
        file_hash=document_id.hex * 2,
        file_size=512,
        page_count=page_count,
        upload_timestamp=datetime.now(timezone.utc),
    )


@pytest.mark.parametrize("page_count", [51, 100, 121])
def test_database_accepts_positive_page_counts_above_legacy_ceiling(
    db_session,
    page_count,
):
    document = _document(page_count)
    db_session.add(document)
    db_session.commit()

    assert db_session.get(PDFDocument, document.id).page_count == page_count


def test_database_rejects_non_positive_page_count(db_session):
    db_session.add(_document(0))

    with pytest.raises(IntegrityError):
        db_session.commit()

    db_session.rollback()

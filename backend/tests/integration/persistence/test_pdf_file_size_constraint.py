"""Persistence coverage for the positive-only PDF file-size invariant."""

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
                PDFDocument.filename.like("file_size_constraint_%")
            )
        )
        session.commit()
        session.close()


def _document(file_size: int) -> PDFDocument:
    document_id = uuid4()
    return PDFDocument(
        id=document_id,
        filename=f"file_size_constraint_{document_id}.pdf",
        file_path=f"test/{document_id}.pdf",
        file_hash=document_id.hex * 2,
        file_size=file_size,
        page_count=1,
        upload_timestamp=datetime.now(timezone.utc),
    )


def test_upgrade_from_prior_head_relaxes_former_ceiling():
    alembic_config = Config(str(BACKEND_ROOT / "alembic.ini"))
    document = _document(550 * 1024 * 1024)

    command.downgrade(alembic_config, "0f1e2d3c4b5a")
    try:
        legacy_session = SessionLocal()
        try:
            legacy_session.add(document)
            with pytest.raises(IntegrityError):
                legacy_session.commit()
            legacy_session.rollback()
        finally:
            legacy_session.close()
    finally:
        command.upgrade(alembic_config, "head")

    upgraded_session = SessionLocal()
    try:
        upgraded_session.add(document)
        upgraded_session.commit()
        assert upgraded_session.get(PDFDocument, document.id).file_size == (
            550 * 1024 * 1024
        )
    finally:
        upgraded_session.rollback()
        upgraded_session.execute(
            delete(PDFDocument).where(PDFDocument.id == document.id)
        )
        upgraded_session.commit()
        upgraded_session.close()


def test_database_accepts_positive_size_above_former_ceiling(db_session):
    document = _document(550 * 1024 * 1024)
    db_session.add(document)
    db_session.commit()

    assert db_session.get(PDFDocument, document.id).file_size == 550 * 1024 * 1024


@pytest.mark.parametrize("file_size", [0, -1])
def test_database_rejects_non_positive_file_size(db_session, file_size):
    db_session.add(_document(file_size))

    with pytest.raises(IntegrityError):
        db_session.commit()

    db_session.rollback()

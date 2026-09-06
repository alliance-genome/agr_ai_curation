"""Persistence coverage for the positive-only PDF file-size invariant."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import delete, text
from sqlalchemy.exc import IntegrityError

from src.models.sql.database import SessionLocal
from src.models.sql.pdf_document import PDFDocument
from tests.pdf_document_test_support import ensure_test_pdf_owner


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


def _document(file_size: int, *, user_id: int) -> PDFDocument:
    document_id = uuid4()
    return PDFDocument(
        id=document_id,
        user_id=user_id,
        filename=f"file_size_constraint_{document_id}.pdf",
        file_path=f"test/{document_id}.pdf",
        file_hash=document_id.hex * 2,
        file_size=file_size,
        page_count=1,
        upload_timestamp=datetime.now(timezone.utc),
    )


def test_upgrade_from_prior_head_relaxes_former_ceiling():
    alembic_config = Config(str(BACKEND_ROOT / "alembic.ini"))
    with SessionLocal() as owner_session:
        owner_id = ensure_test_pdf_owner(
            owner_session,
            auth_sub="test_pdf_owner_file_size_constraint",
        )
        owner_session.commit()
    document = _document(550 * 1024 * 1024, user_id=owner_id)

    command.downgrade(alembic_config, "0f1e2d3c4b5a")
    try:
        legacy_session = SessionLocal()
        try:
            # Use the historical schema rather than today's ORM columns while
            # proving that the former file-size constraint rejects this row.
            with pytest.raises(IntegrityError) as rejected:
                legacy_session.execute(
                    text("""
                        INSERT INTO pdf_documents
                          (id, user_id, filename, file_path, file_hash, file_size,
                           page_count, upload_timestamp)
                        VALUES
                          (:id, :user_id, :filename, :file_path, :file_hash,
                           :file_size, :page_count, :upload_timestamp)
                    """),
                    {
                        "id": document.id, "user_id": owner_id,
                        "filename": document.filename, "file_path": document.file_path,
                        "file_hash": document.file_hash, "file_size": document.file_size,
                        "page_count": document.page_count,
                        "upload_timestamp": document.upload_timestamp,
                    },
                )
            assert rejected.value.orig.diag.constraint_name == "ck_pdf_documents_file_size"
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
    owner_id = ensure_test_pdf_owner(
        db_session,
        auth_sub="test_pdf_owner_file_size_constraint",
    )
    document = _document(550 * 1024 * 1024, user_id=owner_id)
    db_session.add(document)
    db_session.commit()

    assert db_session.get(PDFDocument, document.id).file_size == 550 * 1024 * 1024


@pytest.mark.parametrize("file_size", [0, -1])
def test_database_rejects_non_positive_file_size(db_session, file_size):
    owner_id = ensure_test_pdf_owner(
        db_session,
        auth_sub="test_pdf_owner_file_size_constraint",
    )
    db_session.add(_document(file_size, user_id=owner_id))

    with pytest.raises(IntegrityError):
        db_session.commit()

    db_session.rollback()

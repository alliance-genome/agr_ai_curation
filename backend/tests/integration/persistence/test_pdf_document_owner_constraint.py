"""Real-Postgres coverage for required PDF-document ownership."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from src.models.sql.database import SessionLocal, engine
from src.models.sql.pdf_document import PDFDocument
from src.models.sql.user import User


BACKEND_ROOT = Path(__file__).resolve().parents[3]
PRIOR_HEAD = "1a2b3c4d5e6f"


def _config() -> Config:
    return Config(str(BACKEND_ROOT / "alembic.ini"))


def _column_is_nullable() -> bool:
    with engine.connect() as connection:
        column = next(
            item
            for item in inspect(connection).get_columns("pdf_documents")
            if item["name"] == "user_id"
        )
    return bool(column["nullable"])


def _insert_document(
    session,
    *,
    document_id: UUID,
    file_path: str,
    user_id: int | None,
) -> None:
    session.execute(
        PDFDocument.__table__.insert().values(
            id=document_id,
            user_id=user_id,
            filename=f"owner_constraint_{document_id}.pdf",
            file_path=file_path,
            file_hash=document_id.hex * 2,
            file_size=1024,
            page_count=1,
        )
    )


def _delete_fixture_rows(*, document_ids: list[UUID], auth_sub: str | None) -> None:
    with SessionLocal() as session:
        session.query(PDFDocument).filter(PDFDocument.id.in_(document_ids)).delete(
            synchronize_session=False
        )
        if auth_sub is not None:
            session.query(User).filter(User.auth_sub == auth_sub).delete(
                synchronize_session=False
            )
        session.commit()


def test_upgrade_reconciles_canonical_path_before_requiring_owner():
    config = _config()
    document_id = uuid4()
    auth_sub = f"test_pdf_owner_migration_{uuid4().hex}"

    command.upgrade(config, "head")
    command.downgrade(config, PRIOR_HEAD)
    try:
        with SessionLocal() as session:
            owner = User(auth_sub=auth_sub, is_active=True)
            session.add(owner)
            session.commit()
            owner_id = int(owner.id)
            _insert_document(
                session,
                document_id=document_id,
                file_path=f"{auth_sub}/{document_id}/paper.pdf",
                user_id=None,
            )
            session.commit()

        command.upgrade(config, "head")

        with SessionLocal() as session:
            assert session.get(PDFDocument, document_id).user_id == owner_id
        assert _column_is_nullable() is False
    finally:
        _delete_fixture_rows(document_ids=[document_id], auth_sub=auth_sub)
        command.upgrade(config, "head")


def test_upgrade_reports_all_bad_rows_without_partial_data_or_schema_changes():
    config = _config()
    valid_id = UUID("11111111-1111-1111-1111-111111111129")
    invalid_id = UUID("22222222-2222-2222-2222-222222222229")
    auth_sub = f"test_pdf_owner_atomic_{uuid4().hex}"

    command.upgrade(config, "head")
    command.downgrade(config, PRIOR_HEAD)
    try:
        with SessionLocal() as session:
            owner = User(auth_sub=auth_sub, is_active=True)
            session.add(owner)
            session.flush()
            _insert_document(
                session,
                document_id=valid_id,
                file_path=f"{auth_sub}/{valid_id}/paper.pdf",
                user_id=None,
            )
            _insert_document(
                session,
                document_id=invalid_id,
                file_path="legacy/path-without-an-owner.pdf",
                user_id=None,
            )
            session.commit()

        with pytest.raises(RuntimeError) as exc_info:
            command.upgrade(config, "head")

        assert str(valid_id) not in str(exc_info.value)
        assert str(invalid_id) in str(exc_info.value)
        with SessionLocal() as session:
            rows = {
                row.id: row.user_id
                for row in session.query(PDFDocument)
                .filter(PDFDocument.id.in_([valid_id, invalid_id]))
                .all()
            }
        assert rows == {valid_id: None, invalid_id: None}
        assert _column_is_nullable() is True
    finally:
        _delete_fixture_rows(
            document_ids=[valid_id, invalid_id],
            auth_sub=auth_sub,
        )
        command.upgrade(config, "head")


def test_required_owner_constraint_rejects_null_and_accepts_owned_row():
    command.upgrade(_config(), "head")
    document_id = uuid4()
    auth_sub = f"test_pdf_owner_constraint_{uuid4().hex}"
    try:
        with SessionLocal() as session:
            owner = User(auth_sub=auth_sub, is_active=True)
            session.add(owner)
            session.commit()
            owner_id = int(owner.id)
            with pytest.raises(IntegrityError):
                _insert_document(
                    session,
                    document_id=document_id,
                    file_path=f"{auth_sub}/{document_id}/paper.pdf",
                    user_id=None,
                )
                session.commit()
            session.rollback()

            _insert_document(
                session,
                document_id=document_id,
                file_path=f"{auth_sub}/{document_id}/paper.pdf",
                user_id=owner_id,
            )
            session.commit()

        assert _column_is_nullable() is False
    finally:
        _delete_fixture_rows(document_ids=[document_id], auth_sub=auth_sub)

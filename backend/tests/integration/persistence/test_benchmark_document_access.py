"""Benchmark runtime copies are not ordinary curator-library documents."""

from pathlib import Path
from uuid import uuid4

from alembic import command  # pyright: ignore[reportAttributeAccessIssue]
from alembic.config import Config  # pyright: ignore[reportMissingImports]
from fastapi import HTTPException
import pytest

from src.models.sql.database import SessionLocal
from src.models.sql.pdf_document import PDFDocument
from src.models.sql.user import User
from src.services.document_access import owned_documents_select, require_owned_document


def test_library_policy_excludes_benchmark_copies_but_preserves_normal_ownership():
    command.upgrade(Config(str(Path(__file__).resolve().parents[3] / "alembic.ini")), "head")
    with SessionLocal() as session:
        user = User(auth_sub=f"access-test-{uuid4()}", is_active=True)
        session.add(user)
        session.flush()
        documents = []
        for mode in (None, "local_pdf", "benchmark_frozen"):
            identifier = uuid4()
            document = PDFDocument(
                id=identifier, filename=f"{identifier}.pdf", user_id=user.id,
                file_path=f"synthetic-access-test/{identifier}", file_hash=identifier.hex * 2,
                file_size=1, page_count=1, viewer_mode=mode,
            )
            session.add(document)
            documents.append(document)
        session.flush()
        assert set(session.scalars(owned_documents_select(user.id))) == set(documents[:2])
        for document in documents[:2]:
            assert require_owned_document(session, document.id, user.id) is document
            with pytest.raises(HTTPException) as error:
                require_owned_document(session, document.id, user.id + 1)
            assert error.value.status_code == 403
        for requesting_owner in (user.id, user.id + 1):
            with pytest.raises(HTTPException) as error:
                require_owned_document(session, documents[2].id, requesting_owner, for_update=True)
            assert error.value.status_code == 404
        session.rollback()

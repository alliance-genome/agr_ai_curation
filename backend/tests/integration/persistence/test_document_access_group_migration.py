"""Real-Postgres coverage for document access-group migration safety."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import inspect, text

from src.models.sql.database import SessionLocal, engine
from src.models.sql.pdf_document import PDFDocument
from src.models.sql.user import User


BACKEND_ROOT = Path(__file__).resolve().parents[3]
PRIOR_HEAD = "2b3c4d5e6f7a"


def _config() -> Config:
    return Config(str(BACKEND_ROOT / "alembic.ini"))


def _column_names() -> set[str]:
    with engine.connect() as connection:
        return {column["name"] for column in inspect(connection).get_columns("pdf_documents")}


def _create_document(*, access_scope: str, access_metadata: object | None):
    document_id = uuid4()
    auth_sub = f"access-group-migration-{uuid4().hex}"
    with SessionLocal() as session:
        owner = User(auth_sub=auth_sub, is_active=True)
        session.add(owner)
        session.flush()
        document = PDFDocument(
            id=document_id,
            user_id=int(owner.id),
            filename=f"{document_id}.pdf",
            file_path=f"{auth_sub}/{document_id}/paper.pdf",
            file_hash=document_id.hex * 2,
            file_size=1024,
            page_count=1,
        )
        session.add(document)
        session.flush()
        session.execute(
            text(
                """
                UPDATE pdf_documents
                SET source_access_scope = :access_scope,
                    source_access_mods = CAST(:access_metadata AS jsonb)
                WHERE id = :document_id
                """
            ),
            {
                "access_scope": access_scope,
                "access_metadata": (
                    json.dumps(access_metadata) if access_metadata is not None else None
                ),
                "document_id": document_id,
            },
        )
        session.commit()
    return document_id, auth_sub


def _delete_fixture(*, document_id, auth_sub: str) -> None:
    with engine.begin() as connection:
        connection.execute(
            text("DELETE FROM pdf_documents WHERE id = :document_id"),
            {"document_id": document_id},
        )
        connection.execute(
            text("DELETE FROM users WHERE auth_sub = :auth_sub"),
            {"auth_sub": auth_sub},
        )


def test_upgrade_preserves_restricted_group_ids_as_flat_json_array() -> None:
    command.upgrade(_config(), "head")
    command.downgrade(_config(), PRIOR_HEAD)
    document_id, auth_sub = _create_document(
        access_scope="restricted",
        access_metadata={"mods": ["team-alpha", "lab-2"]},
    )
    job_id = uuid4()
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO pdf_processing_jobs (
                        id, document_id, user_id, status, progress_percentage,
                        metadata_json
                    )
                    SELECT :job_id, :document_id, user_id, 'running', 20,
                           CAST(:metadata_json AS jsonb)
                    FROM pdf_documents
                    WHERE id = :document_id
                    """
                ),
                {
                    "job_id": job_id,
                    "document_id": document_id,
                    "metadata_json": json.dumps(
                        {
                            "document_source": {
                                "conversion_status": "running",
                                "per_mod_status": [
                                    {"mod": "legacy", "main_converted": True}
                                ],
                            }
                        }
                    ),
                },
            )
        command.upgrade(_config(), "head")

        with engine.connect() as connection:
            value = connection.execute(
                text(
                    "SELECT source_access_group_ids FROM pdf_documents WHERE id = :document_id"
                ),
                {"document_id": document_id},
            ).scalar_one()
            job_metadata = connection.execute(
                text("SELECT metadata_json FROM pdf_processing_jobs WHERE id = :job_id"),
                {"job_id": job_id},
            ).scalar_one()
        assert value == ["team-alpha", "lab-2"]
        assert job_metadata == {"document_source": {"conversion_status": "running"}}
        assert "source_access_mods" not in _column_names()
    finally:
        _delete_fixture(document_id=document_id, auth_sub=auth_sub)
        command.upgrade(_config(), "head")


def test_upgrade_fails_closed_before_renaming_restricted_row_without_groups() -> None:
    command.upgrade(_config(), "head")
    command.downgrade(_config(), PRIOR_HEAD)
    document_id, auth_sub = _create_document(
        access_scope="restricted",
        access_metadata=None,
    )
    try:
        with pytest.raises(RuntimeError, match=str(document_id)):
            command.upgrade(_config(), "head")

        columns = _column_names()
        assert "source_access_mods" in columns
        assert "source_access_group_ids" not in columns
    finally:
        _delete_fixture(document_id=document_id, auth_sub=auth_sub)
        command.upgrade(_config(), "head")

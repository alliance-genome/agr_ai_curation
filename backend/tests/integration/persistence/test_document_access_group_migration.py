"""Persistence coverage for the document access-group data migration."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
import sqlalchemy as sa

from src.models.sql.database import SessionLocal, engine
from tests.pdf_document_test_support import ensure_test_pdf_owner


BACKEND_ROOT = Path(__file__).resolve().parents[3]


def test_legacy_access_groups_migrate_without_widening_or_loss():
    alembic_config = Config(str(BACKEND_ROOT / "alembic.ini"))
    command.upgrade(alembic_config, "head")
    with SessionLocal() as owner_session:
        owner_id = ensure_test_pdf_owner(
            owner_session,
            auth_sub="test_pdf_owner_access_group_migration",
        )
        owner_session.commit()

    retained_id = uuid4()
    malformed_ids = (uuid4(), uuid4())
    command.downgrade(alembic_config, "2b3c4d5e6f7a")
    try:
        with engine.begin() as connection:
            insert = sa.text(
                """
                INSERT INTO pdf_documents (
                    id, filename, file_path, file_hash, file_size, page_count,
                    user_id, source_access_scope, source_access_mods
                ) VALUES (
                    :id, :filename, :file_path, :file_hash, 1024, 1,
                    :user_id, 'restricted', CAST(:access_policy AS jsonb)
                )
                """
            )
            for document_id, access_policy in (
                (retained_id, {"mods": ["FB", "WB"]}),
                (malformed_ids[0], {"unexpected": ["FB"]}),
                (malformed_ids[1], {"mods": ["FB", {"unexpected": "WB"}]}),
            ):
                connection.execute(
                    insert,
                    {
                        "id": document_id,
                        "filename": f"access_group_migration_{document_id}.pdf",
                        "file_path": f"migration/{document_id}.pdf",
                        "file_hash": document_id.hex * 2,
                        "user_id": owner_id,
                        "access_policy": json.dumps(access_policy),
                    },
                )

        command.upgrade(alembic_config, "head")
        with engine.begin() as connection:
            rows = dict(
                connection.execute(
                    sa.text(
                        """
                        SELECT id, source_access_group_ids
                        FROM pdf_documents
                        WHERE id IN (:retained_id, :malformed_id, :malformed_item_id)
                        """
                    ),
                    {
                        "retained_id": retained_id,
                        "malformed_id": malformed_ids[0],
                        "malformed_item_id": malformed_ids[1],
                    },
                ).all()
            )
            assert rows[retained_id] == ["FB", "WB"]
            assert rows[malformed_ids[0]] is None
            assert rows[malformed_ids[1]] is None
    finally:
        command.upgrade(alembic_config, "head")
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "DELETE FROM pdf_documents "
                    "WHERE id IN (:first, :second, :third)"
                ),
                {
                    "first": retained_id,
                    "second": malformed_ids[0],
                    "third": malformed_ids[1],
                },
            )

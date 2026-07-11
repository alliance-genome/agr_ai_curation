"""Executable migration tests for scoped domain-envelope ownership."""

# pyright: reportAttributeAccessIssue=false, reportMissingImports=false

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from src.lib.domain_envelopes.persistence import domain_envelope_payload_hash
from src.models.sql.database import engine
from src.schemas.domain_envelope import DomainEnvelope


BACKEND_ROOT = Path(__file__).resolve().parents[3]
PARENT_REVISION = "c3d4e5f6a7b8"
SCOPED_REVISION = "e7f8a9b0c1d2"
DOCUMENT_ID = UUID("00000000-0000-0000-0000-000000000697")
SESSION_ID = UUID("00000000-0000-0000-0000-000000006697")
OTHER_SESSION_ID = UUID("00000000-0000-0000-0000-000000016697")
ENVELOPE_ID = "scope-migration-envelope-697"


@pytest.fixture
def legacy_schema():
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    command.upgrade(config, "head")
    _cleanup_rows()
    engine.dispose()
    command.downgrade(config, PARENT_REVISION)
    try:
        yield config
    finally:
        engine.dispose()
        with engine.connect() as connection:
            current_revision = connection.scalar(
                text("SELECT version_num FROM alembic_version")
            )
        if current_revision == SCOPED_REVISION:
            _cleanup_rows()
            engine.dispose()
            command.downgrade(config, PARENT_REVISION)
        else:
            _cleanup_rows()
        engine.dispose()
        command.upgrade(config, "head")


def _cleanup_rows() -> None:
    with engine.begin() as connection:
        connection.execute(
            text("DELETE FROM curation_candidates WHERE envelope_id = :envelope_id"),
            {"envelope_id": ENVELOPE_ID},
        )
        connection.execute(
            text("DELETE FROM domain_envelopes WHERE envelope_id = :envelope_id"),
            {"envelope_id": ENVELOPE_ID},
        )
        connection.execute(
            text(
                "DELETE FROM curation_review_sessions "
                "WHERE id IN (:session_id, :other_session_id)"
            ),
            {"session_id": SESSION_ID, "other_session_id": OTHER_SESSION_ID},
        )
        connection.execute(
            text("DELETE FROM pdf_documents WHERE id = :document_id"),
            {"document_id": DOCUMENT_ID},
        )


def _seed_document_and_sessions(*, include_other_session: bool = False) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO pdf_documents (
                  id, filename, file_path, file_hash, file_size, page_count
                ) VALUES (
                  :document_id, 'scope_migration_697.pdf',
                  '/tmp/scope_migration_697.pdf', '00000000000000000000000000000697',
                  4096, 4
                )
                """
            ),
            {"document_id": DOCUMENT_ID},
        )
        session_ids = [SESSION_ID]
        if include_other_session:
            session_ids.append(OTHER_SESSION_ID)
        for session_id in session_ids:
            connection.execute(
                text(
                    """
                    INSERT INTO curation_review_sessions (
                      id, status, adapter_key, document_id, prepared_at
                    ) VALUES (
                      :session_id, 'new', 'gene', :document_id, now()
                    )
                    """
                ),
                {"session_id": session_id, "document_id": DOCUMENT_ID},
            )


def _seed_legacy_envelope(
    *,
    document_id: UUID | None,
    metadata: dict[str, object],
) -> None:
    envelope_json = {
        "envelope_id": ENVELOPE_ID,
        "domain_pack_id": "gene",
        "domain_pack_version": "0.1.0",
        "status": "extracted",
        "extracted_objects": [],
        "validation_findings": [],
        "history": [],
        "metadata": metadata,
    }
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO domain_envelopes (
                  envelope_id, project_key, domain_pack_key, domain_pack_version,
                  status, document_id, session_id, envelope_json
                ) VALUES (
                  :envelope_id, 'agr', 'gene', '0.1.0', 'extracted',
                  :document_id, NULL, CAST(:envelope_json AS jsonb)
                )
                """
            ),
            {
                "envelope_id": ENVELOPE_ID,
                "document_id": document_id,
                "envelope_json": json.dumps(envelope_json),
            },
        )


def _seed_candidate(*, session_id: UUID, candidate_id: UUID) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO curation_candidates (
                  id, session_id, source, status, "order", adapter_key,
                  envelope_id, object_id, envelope_revision
                ) VALUES (
                  :candidate_id, :session_id, 'extracted', 'pending', 0, 'gene',
                  :envelope_id, 'gene-object-1', 1
                )
                """
            ),
            {
                "candidate_id": candidate_id,
                "session_id": session_id,
                "envelope_id": ENVELOPE_ID,
            },
        )


def test_scope_migration_repairs_unambiguous_owner_and_enforces_constraints(
    legacy_schema,
):
    _seed_document_and_sessions(include_other_session=True)
    _seed_legacy_envelope(
        document_id=None,
        metadata={
            "source_adapter_key": "gene",
            "source_extraction_result_id": "extract-697",
            "numeric_payload": {
                "scientific": 1e-7,
                "trailing_zero": 1.2300,
            },
        },
    )
    _seed_candidate(
        session_id=SESSION_ID,
        candidate_id=UUID("00000000-0000-0000-0000-000000026697"),
    )

    engine.dispose()
    command.upgrade(legacy_schema, SCOPED_REVISION)

    with engine.connect() as connection:
        repaired = connection.execute(
            text(
                """
                SELECT document_id, session_id, adapter_key, envelope_json,
                       source_extraction_result_id, source_payload_hash
                FROM domain_envelopes
                WHERE envelope_id = :envelope_id
                """
            ),
            {"envelope_id": ENVELOPE_ID},
        ).mappings().one()
    assert repaired["document_id"] == DOCUMENT_ID
    assert repaired["session_id"] == SESSION_ID
    assert repaired["adapter_key"] == "gene"
    assert repaired["source_extraction_result_id"] == "extract-697"
    assert repaired["source_payload_hash"] == domain_envelope_payload_hash(
        DomainEnvelope.model_validate(repaired["envelope_json"])
    )

    with pytest.raises(DBAPIError, match="identity scope is immutable"):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE domain_envelopes SET flow_run_id = 'other-run' "
                    "WHERE envelope_id = :envelope_id"
                ),
                {"envelope_id": ENVELOPE_ID},
            )

    with pytest.raises(IntegrityError):
        _seed_candidate(
            session_id=OTHER_SESSION_ID,
            candidate_id=UUID("00000000-0000-0000-0000-000000036697"),
        )


def test_scope_migration_rejects_unbound_legacy_envelope(legacy_schema):
    _seed_document_and_sessions()
    _seed_legacy_envelope(
        document_id=DOCUMENT_ID,
        metadata={"source_adapter_key": "gene"},
    )

    engine.dispose()
    with pytest.raises(DBAPIError, match="null or unbound row requires explicit repair"):
        command.upgrade(legacy_schema, SCOPED_REVISION)


def test_scope_migration_rejects_cross_session_legacy_collision(legacy_schema):
    _seed_document_and_sessions(include_other_session=True)
    _seed_legacy_envelope(
        document_id=DOCUMENT_ID,
        metadata={
            "source_adapter_key": "gene",
            "source_extraction_result_id": "extract-697",
        },
    )
    _seed_candidate(
        session_id=SESSION_ID,
        candidate_id=UUID("00000000-0000-0000-0000-000000046697"),
    )
    _seed_candidate(
        session_id=OTHER_SESSION_ID,
        candidate_id=UUID("00000000-0000-0000-0000-000000056697"),
    )

    engine.dispose()
    with pytest.raises(DBAPIError, match="linked to multiple review sessions"):
        command.upgrade(legacy_schema, SCOPED_REVISION)

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

from src.lib.domain_envelope_payload_hash import canonical_domain_envelope_payload_hash
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
CLONE_ENVELOPE_ID = f"{ENVELOPE_ID}:session:{OTHER_SESSION_ID}"


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
        envelope_params = {
            "envelope_id": ENVELOPE_ID,
            "clone_envelope_id": CLONE_ENVELOPE_ID,
        }
        connection.execute(
            text(
                "DELETE FROM validation_snapshots "
                "WHERE envelope_id = :envelope_id "
                "OR envelope_id = :clone_envelope_id"
            ),
            envelope_params,
        )
        connection.execute(
            text(
                "DELETE FROM curation_candidates "
                "WHERE envelope_id = :envelope_id "
                "OR envelope_id = :clone_envelope_id"
            ),
            envelope_params,
        )
        for table_name in (
            "domain_envelope_projection_index",
            "domain_validation_findings",
            "domain_envelope_history",
            "domain_envelope_objects",
        ):
            connection.execute(
                text(
                    f"DELETE FROM {table_name} "
                    "WHERE envelope_id = :envelope_id "
                    "OR envelope_id = :clone_envelope_id"
                ),
                envelope_params,
            )
        connection.execute(
            text(
                "DELETE FROM domain_envelopes "
                "WHERE envelope_id = :envelope_id "
                "OR envelope_id = :clone_envelope_id"
            ),
            envelope_params,
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


def _seed_legacy_envelope_graph(*, snapshot_candidate_id: UUID) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO domain_envelope_objects (
                  envelope_id, object_id, envelope_revision, object_index,
                  object_type, status, validation_state, object_json
                ) VALUES (
                  :envelope_id, 'gene-object-1', 1, 0, 'gene', 'extracted',
                  'not_validated', '{"object_id": "gene-object-1"}'::jsonb
                )
                """
            ),
            {"envelope_id": ENVELOPE_ID},
        )
        connection.execute(
            text(
                """
                INSERT INTO domain_envelope_history (
                  envelope_id, event_id, envelope_revision, event_index,
                  event_type, occurred_at, actor_type, event_json
                ) VALUES (
                  :envelope_id, 'event-697', 1, 0, 'created', now(),
                  'system', '{"event_id": "event-697"}'::jsonb
                )
                """
            ),
            {"envelope_id": ENVELOPE_ID},
        )
        connection.execute(
            text(
                """
                INSERT INTO domain_envelope_projection_index (
                  envelope_id, object_id, envelope_revision, projection_type,
                  projection_key, projection_json
                ) VALUES (
                  :envelope_id, 'gene-object-1', 1, 'workspace',
                  'gene-object-1', '{"object_id": "gene-object-1"}'::jsonb
                )
                """
            ),
            {"envelope_id": ENVELOPE_ID},
        )
        connection.execute(
            text(
                """
                INSERT INTO domain_validation_findings (
                  envelope_id, envelope_revision, finding_index, object_id,
                  severity, status, finding_json
                ) VALUES (
                  :envelope_id, 1, 0, 'gene-object-1', 'info', 'open',
                  '{"finding_id": "finding-697"}'::jsonb
                )
                """
            ),
            {"envelope_id": ENVELOPE_ID},
        )
        connection.execute(
            text(
                """
                INSERT INTO validation_snapshots (
                  scope, session_id, candidate_id, state, summary,
                  envelope_id, envelope_revision
                ) VALUES (
                  'candidate', :session_id, :candidate_id, 'not_requested',
                  '{}'::jsonb, :envelope_id, 1
                )
                """
            ),
            {
                "session_id": OTHER_SESSION_ID,
                "candidate_id": snapshot_candidate_id,
                "envelope_id": ENVELOPE_ID,
            },
        )


def _use_legacy_objects_payload_key() -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE domain_envelopes
                SET envelope_json =
                  (envelope_json - 'extracted_objects')
                  || jsonb_build_object('objects', envelope_json->'extracted_objects')
                WHERE envelope_id = :envelope_id
                """
            ),
            {"envelope_id": ENVELOPE_ID},
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


def test_scope_migration_splits_cross_session_legacy_collision(legacy_schema):
    _seed_document_and_sessions(include_other_session=True)
    _seed_legacy_envelope(
        document_id=DOCUMENT_ID,
        metadata={
            "source_adapter_key": "gene",
            "source_extraction_result_id": "extract-697",
        },
    )
    _use_legacy_objects_payload_key()
    canonical_candidate_id = UUID("00000000-0000-0000-0000-000000046697")
    clone_candidate_id = UUID("00000000-0000-0000-0000-000000056697")
    _seed_candidate(
        session_id=SESSION_ID,
        candidate_id=canonical_candidate_id,
    )
    _seed_candidate(
        session_id=OTHER_SESSION_ID,
        candidate_id=clone_candidate_id,
    )
    _seed_legacy_envelope_graph(snapshot_candidate_id=clone_candidate_id)

    engine.dispose()
    command.upgrade(legacy_schema, SCOPED_REVISION)

    with engine.connect() as connection:
        envelopes = connection.execute(
            text(
                """
                SELECT envelope_id, session_id, source_extraction_result_id,
                       source_payload_hash, envelope_json
                FROM domain_envelopes
                WHERE envelope_id = :envelope_id
                   OR envelope_id = :clone_envelope_id
                ORDER BY envelope_id
                """
            ),
            {
                "envelope_id": ENVELOPE_ID,
                "clone_envelope_id": CLONE_ENVELOPE_ID,
            },
        ).mappings().all()
        candidate_owners = dict(
            connection.execute(
                text(
                    """
                    SELECT session_id, envelope_id
                    FROM curation_candidates
                    WHERE id = :canonical_candidate_id
                       OR id = :clone_candidate_id
                    """
                ),
                {
                    "canonical_candidate_id": canonical_candidate_id,
                    "clone_candidate_id": clone_candidate_id,
                },
            ).all()
        )
        snapshot_envelope_id = connection.scalar(
            text(
                "SELECT envelope_id FROM validation_snapshots "
                "WHERE candidate_id = :candidate_id"
            ),
            {"candidate_id": clone_candidate_id},
        )
        child_counts = {}
        for table_name in (
            "domain_envelope_objects",
            "domain_envelope_history",
            "domain_envelope_projection_index",
            "domain_validation_findings",
        ):
            child_counts[table_name] = dict(
                connection.execute(
                    text(
                        f"SELECT envelope_id, count(*) FROM {table_name} "
                        "WHERE envelope_id = :envelope_id "
                        "OR envelope_id = :clone_envelope_id "
                        "GROUP BY envelope_id"
                    ),
                    {
                        "envelope_id": ENVELOPE_ID,
                        "clone_envelope_id": CLONE_ENVELOPE_ID,
                    },
                ).all()
            )

    assert len(envelopes) == 2
    by_id = {row["envelope_id"]: row for row in envelopes}
    assert by_id[ENVELOPE_ID]["session_id"] == SESSION_ID
    assert by_id[CLONE_ENVELOPE_ID]["session_id"] == OTHER_SESSION_ID
    assert by_id[ENVELOPE_ID]["source_extraction_result_id"] == "extract-697"
    assert by_id[CLONE_ENVELOPE_ID]["source_extraction_result_id"] is None
    for envelope_id, envelope in by_id.items():
        assert envelope["envelope_json"]["envelope_id"] == envelope_id
        assert envelope["source_payload_hash"] == canonical_domain_envelope_payload_hash(
            envelope["envelope_json"]
        )
    assert candidate_owners == {
        SESSION_ID: ENVELOPE_ID,
        OTHER_SESSION_ID: CLONE_ENVELOPE_ID,
    }
    assert snapshot_envelope_id == CLONE_ENVELOPE_ID
    for counts in child_counts.values():
        assert counts == {ENVELOPE_ID: 1, CLONE_ENVELOPE_ID: 1}

    with pytest.raises(IntegrityError):
        _seed_candidate(
            session_id=OTHER_SESSION_ID,
            candidate_id=UUID("00000000-0000-0000-0000-000000066697"),
        )

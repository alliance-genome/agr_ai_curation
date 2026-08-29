"""Integration coverage for the bounded legacy envelope payload repair."""

# pyright: reportAttributeAccessIssue=false, reportMissingImports=false

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import delete, select, text

from src.lib.curation_workspace.models import (
    CurationCandidate,
    CurationReviewSession,
    CurationValidationSnapshot,
    DomainEnvelopeHistory,
    DomainEnvelopeModel,
    DomainEnvelopeObject,
    DomainEnvelopeProjectionIndex,
    DomainValidationFinding,
)
from src.lib.domain_envelopes.legacy_payload_repair import (
    LegacyDomainEnvelopeRepairError,
    LegacyDomainEnvelopeRepairExpectations,
    repair_legacy_domain_envelope_payloads,
)
from src.lib.domain_envelopes.persistence import (
    DomainEnvelopeCheckpointRequest,
    write_domain_envelope_checkpoint,
)
from src.models.sql.database import SessionLocal
from src.models.sql.pdf_document import PDFDocument
from src.schemas.curation_workspace import (
    CurationCandidateSource,
    CurationCandidateStatus,
    CurationSessionStatus,
    CurationValidationScope,
    CurationValidationSnapshotState,
)
from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    CuratableObjectStatus,
    DomainEnvelope,
    DomainEnvelopeStatus,
    HistoryActorType,
    HistoryEvent,
    HistoryEventKind,
    ValidationFinding,
    ValidationFindingSeverity,
)
from tests.pdf_document_test_support import ensure_test_pdf_owner

BACKEND_ROOT = Path(__file__).resolve().parents[3]
DOCUMENT_ID = UUID("00000000-0000-0000-0000-000000000915")
SESSION_ID = UUID("00000000-0000-0000-0000-000000009150")
ENVELOPE_ID = "legacy-payload-repair-915"
CANDIDATE_ID = UUID("00000000-0000-0000-0000-000000009151")


@pytest.fixture(scope="module", autouse=True)
def migrated_database():
    command.upgrade(Config(str(BACKEND_ROOT / "alembic.ini")), "head")


@pytest.fixture
def db_session():
    db = SessionLocal()
    _cleanup(db)
    owner_id = ensure_test_pdf_owner(db, auth_sub="legacy_payload_repair_owner")
    document = PDFDocument(
        id=DOCUMENT_ID,
        user_id=owner_id,
        filename="legacy_payload_repair_915.pdf",
        file_path="/tmp/legacy_payload_repair_915.pdf",
        file_hash="9" * 64,
        file_size=915,
        page_count=1,
        upload_timestamp=datetime(2026, 8, 29, tzinfo=timezone.utc),
        last_accessed=datetime(2026, 8, 29, tzinfo=timezone.utc),
        status="processed",
    )
    db.add(document)
    db.flush()
    session = CurationReviewSession(
        id=SESSION_ID,
        status=CurationSessionStatus.NEW,
        adapter_key="generic",
        document_id=DOCUMENT_ID,
        prepared_at=datetime(2026, 8, 29, tzinfo=timezone.utc),
    )
    db.add(session)
    db.commit()
    try:
        yield db
    finally:
        db.rollback()
        _cleanup(db)
        db.close()


def _cleanup(db) -> None:
    db.execute(
        delete(CurationValidationSnapshot).where(
            CurationValidationSnapshot.envelope_id == ENVELOPE_ID
        )
    )
    db.execute(delete(CurationCandidate).where(CurationCandidate.id == CANDIDATE_ID))
    for model in (
        DomainEnvelopeProjectionIndex,
        DomainEnvelopeHistory,
        DomainValidationFinding,
        DomainEnvelopeObject,
    ):
        db.execute(delete(model).where(model.envelope_id == ENVELOPE_ID))
    db.execute(
        delete(DomainEnvelopeModel).where(
            DomainEnvelopeModel.envelope_id == ENVELOPE_ID
        )
    )
    db.execute(
        delete(CurationReviewSession).where(CurationReviewSession.id == SESSION_ID)
    )
    db.execute(delete(PDFDocument).where(PDFDocument.id == DOCUMENT_ID))
    db.commit()


def _seed_legacy_graph(db) -> None:
    envelope = DomainEnvelope(
        envelope_id=ENVELOPE_ID,
        domain_pack_id="generic",
        domain_pack_version="0.9.0",
        status=DomainEnvelopeStatus.EXTRACTED,
        extracted_objects=[
            CuratableObjectEnvelope(
                object_type="GenericObject",
                object_id="object-915",
                status=CuratableObjectStatus.EXTRACTED,
                payload={"label": "preserve me"},
                metadata={
                    "projections": [
                        {
                            "projection_type": "workspace_row",
                            "projection_key": "object-915",
                            "projection_json": {"label": "preserve me"},
                        }
                    ]
                },
            )
        ],
        validation_findings=[
            ValidationFinding(
                finding_id="finding-915",
                severity=ValidationFindingSeverity.INFO,
                message="preserve finding",
            )
        ],
        history=[
            HistoryEvent(
                event_id="event-915",
                event_type=HistoryEventKind.CREATED,
                timestamp=datetime(2026, 8, 29, tzinfo=timezone.utc),
                actor_type=HistoryActorType.SYSTEM,
            )
        ],
    )
    write_domain_envelope_checkpoint(
        db,
        DomainEnvelopeCheckpointRequest(
            project_key="agr_ai_curation",
            envelope=envelope,
            expected_revision=0,
            document_id=DOCUMENT_ID,
            session_id=SESSION_ID,
            adapter_key="generic",
        ),
    )
    candidate = CurationCandidate(
        id=CANDIDATE_ID,
        session_id=SESSION_ID,
        source=CurationCandidateSource.EXTRACTED,
        status=CurationCandidateStatus.PENDING,
        order=0,
        adapter_key="generic",
        envelope_id=ENVELOPE_ID,
        object_id="object-915",
        envelope_revision=1,
    )
    db.add(candidate)
    db.flush()
    db.add(
        CurationValidationSnapshot(
            scope=CurationValidationScope.CANDIDATE,
            session_id=SESSION_ID,
            candidate_id=CANDIDATE_ID,
            adapter_key="generic",
            envelope_id=ENVELOPE_ID,
            envelope_revision=1,
            state=CurationValidationSnapshotState.COMPLETED,
            field_results={"object-915.label": {"status": "valid"}},
            summary={"status": "valid", "finding_count": 1},
            warnings=["preserve snapshot"],
            requested_at=datetime(2026, 8, 29, 1, 0, tzinfo=timezone.utc),
            completed_at=datetime(2026, 8, 29, 1, 1, tzinfo=timezone.utc),
        )
    )
    db.flush()
    db.execute(
        text("""
            UPDATE domain_envelopes
            SET envelope_json =
              (envelope_json - 'extracted_objects')
              || jsonb_build_object('objects', envelope_json->'extracted_objects')
            WHERE envelope_id = :envelope_id
            """),
        {"envelope_id": ENVELOPE_ID},
    )
    db.commit()


def _protected_state(db) -> dict:
    envelope = (
        db.execute(
            text("""
            SELECT revision, source_payload_hash, created_at, updated_at,
                   checkpointed_at
            FROM domain_envelopes WHERE envelope_id = :envelope_id
            """),
            {"envelope_id": ENVELOPE_ID},
        )
        .mappings()
        .one()
    )
    counts = {}
    for table_name in (
        "domain_envelope_objects",
        "domain_validation_findings",
        "domain_envelope_history",
        "domain_envelope_projection_index",
        "curation_candidates",
        "validation_snapshots",
    ):
        counts[table_name] = db.scalar(
            text(f"SELECT count(*) FROM {table_name} WHERE envelope_id = :envelope_id"),
            {"envelope_id": ENVELOPE_ID},
        )
    snapshots = (
        db.execute(
            text("""
            SELECT id, scope, session_id, candidate_id, adapter_key,
                   envelope_id, envelope_revision, state, field_results,
                   summary, warnings, requested_at, completed_at
            FROM validation_snapshots
            WHERE envelope_id = :envelope_id
            ORDER BY id
            """),
            {"envelope_id": ENVELOPE_ID},
        )
        .mappings()
        .all()
    )
    return {
        "envelope": dict(envelope),
        "counts": counts,
        "validation_snapshots": [dict(snapshot) for snapshot in snapshots],
    }


def test_dry_run_and_apply_preserve_the_persisted_graph(db_session) -> None:
    _seed_legacy_graph(db_session)
    before = _protected_state(db_session)

    dry_run = repair_legacy_domain_envelope_payloads(db_session)
    db_session.rollback()

    assert dry_run.to_json() == {
        "mode": "dry-run",
        "envelope_count": 1,
        "candidate_reference_count": 1,
        "session_count": 1,
        "object_count": 1,
        "repaired_envelope_count": 0,
        "envelope_ids": [ENVELOPE_ID],
    }
    assert _protected_state(db_session) == before

    applied = repair_legacy_domain_envelope_payloads(
        db_session,
        apply=True,
        expectations=LegacyDomainEnvelopeRepairExpectations(
            envelopes=1,
            candidate_references=1,
            sessions=1,
            objects=1,
        ),
    )
    db_session.commit()

    payload = db_session.scalar(
        select(DomainEnvelopeModel.envelope_json).where(
            DomainEnvelopeModel.envelope_id == ENVELOPE_ID
        )
    )
    assert "objects" not in payload
    assert len(payload["extracted_objects"]) == 1
    assert DomainEnvelope.model_validate(payload).envelope_id == ENVELOPE_ID
    assert applied.repaired_envelope_count == 1
    assert _protected_state(db_session) == before

    repeated = repair_legacy_domain_envelope_payloads(db_session)
    assert repeated.envelope_count == 0


def test_apply_fails_closed_when_counts_do_not_match(db_session) -> None:
    _seed_legacy_graph(db_session)

    with pytest.raises(LegacyDomainEnvelopeRepairError, match="expected 2, found 1"):
        repair_legacy_domain_envelope_payloads(
            db_session,
            apply=True,
            expectations=LegacyDomainEnvelopeRepairExpectations(
                envelopes=2,
                candidate_references=1,
                sessions=1,
                objects=1,
            ),
        )
    db_session.rollback()

    payload = db_session.scalar(
        select(DomainEnvelopeModel.envelope_json).where(
            DomainEnvelopeModel.envelope_id == ENVELOPE_ID
        )
    )
    assert "objects" in payload

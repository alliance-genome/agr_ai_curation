"""Persistence integration tests for the legacy workspace envelope migration."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import delete, func, select

from src.lib.curation_workspace.models import (
    CurationActionLogEntry,
    CurationCandidate,
    CurationDraft,
    CurationEvidenceRecord,
    CurationExtractionResultRecord,
    CurationReviewSession,
    CurationSubmissionRecord,
    CurationValidationSnapshot,
    DomainEnvelopeHistory,
    DomainEnvelopeModel,
    DomainEnvelopeObject,
    DomainEnvelopeProjectionIndex,
    DomainValidationFinding,
)
from src.lib.domain_envelopes.migration import (
    LEGACY_WORKSPACE_PROJECTION_TYPE,
    LegacyCurationWorkspaceMigrationOptions,
    migrate_legacy_curation_workspace_to_domain_envelopes,
)
from src.models.sql.database import SessionLocal
from src.models.sql.pdf_document import PDFDocument
from src.schemas.curation_workspace import (
    CurationActionType,
    CurationActorType,
    CurationCandidateSource,
    CurationCandidateStatus,
    CurationEvidenceSource,
    CurationExtractionSourceKind,
    CurationSessionStatus,
    CurationSubmissionStatus,
    CurationValidationScope,
    CurationValidationSnapshotState,
    FieldValidationStatus,
    SubmissionMode,
)


BACKEND_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module", autouse=True)
def migrated_database():
    alembic_config = Config(str(BACKEND_ROOT / "alembic.ini"))
    command.upgrade(alembic_config, "head")


@pytest.fixture
def db_session():
    session = SessionLocal()
    _clean_tables(session)
    try:
        yield session
    finally:
        session.rollback()
        _clean_tables(session)
        session.close()


def _clean_tables(session):
    for model in (
        DomainEnvelopeProjectionIndex,
        DomainEnvelopeHistory,
        DomainValidationFinding,
        DomainEnvelopeObject,
        CurationActionLogEntry,
        CurationSubmissionRecord,
        CurationValidationSnapshot,
        CurationDraft,
        CurationEvidenceRecord,
        CurationCandidate,
        DomainEnvelopeModel,
        CurationExtractionResultRecord,
        CurationReviewSession,
    ):
        session.execute(delete(model))
    session.execute(delete(PDFDocument).where(PDFDocument.filename.like("legacy_migration_%")))
    session.commit()


def _now() -> datetime:
    return datetime(2026, 5, 9, 16, 0, tzinfo=timezone.utc)


def _seed_retained_workspace(session):
    document = PDFDocument(
        id=uuid4(),
        filename=f"legacy_migration_{uuid4()}.pdf",
        title="Legacy migration paper",
        file_path=f"/tmp/legacy_migration_{uuid4()}.pdf",
        file_hash=uuid4().hex + uuid4().hex,
        file_size=4096,
        page_count=4,
        upload_timestamp=_now(),
        last_accessed=_now(),
        status="processed",
    )
    session.add(document)
    session.flush()

    extraction = CurationExtractionResultRecord(
        id=uuid4(),
        document_id=document.id,
        adapter_key="reference_adapter",
        agent_key="gene_extractor",
        source_kind=CurationExtractionSourceKind.FLOW,
        flow_run_id="flow-integration",
        candidate_count=1,
        conversation_summary="Integration extraction summary.",
        payload_json={
            "raw_mentions": [{"text": "xyz-1"}],
            "items": [{"gene": {"symbol": "xyz-1"}}],
            "ambiguities": [],
            "exclusions": [],
        },
        extraction_metadata={"run": "integration"},
        created_at=_now(),
    )
    session.add(extraction)
    session.flush()

    review_session = CurationReviewSession(
        id=uuid4(),
        status=CurationSessionStatus.IN_PROGRESS,
        adapter_key="reference_adapter",
        document_id=document.id,
        flow_run_id="flow-integration",
        session_version=1,
        total_candidates=1,
        reviewed_candidates=0,
        pending_candidates=1,
        accepted_candidates=0,
        rejected_candidates=0,
        manual_candidates=0,
        tags=[],
        warnings=[],
        prepared_at=_now(),
        created_at=_now(),
        updated_at=_now(),
    )
    session.add(review_session)
    session.flush()

    candidate = CurationCandidate(
        id=uuid4(),
        session_id=review_session.id,
        source=CurationCandidateSource.EXTRACTED,
        status=CurationCandidateStatus.PENDING,
        order=0,
        adapter_key="reference_adapter",
        display_label="xyz-1",
        extraction_result_id=extraction.id,
        normalized_payload={"entity": {"name": "xyz-1"}},
        candidate_metadata={"normalization_notes": ["integration"]},
        created_at=_now(),
        updated_at=_now(),
    )
    session.add(candidate)
    session.flush()

    session.add(
        CurationDraft(
            id=uuid4(),
            candidate_id=candidate.id,
            adapter_key="reference_adapter",
            version=1,
            title="xyz-1",
            summary="Draft summary.",
            fields=[{"field_key": "entity_name", "label": "Entity", "value": "xyz-1"}],
            notes="Draft notes.",
            draft_metadata={},
            created_at=_now(),
            updated_at=_now(),
        )
    )
    session.add(
        CurationEvidenceRecord(
            id=uuid4(),
            candidate_id=candidate.id,
            source=CurationEvidenceSource.EXTRACTED,
            field_keys=["entity_name"],
            field_group_keys=[],
            is_primary=True,
            anchor={
                "anchor_kind": "sentence",
                "locator_quality": "exact_quote",
                "supports_decision": "supports",
                "sentence_text": "xyz-1 is expressed.",
            },
            warnings=[],
            created_at=_now(),
            updated_at=_now(),
        )
    )
    session.add(
        CurationValidationSnapshot(
            id=uuid4(),
            scope=CurationValidationScope.CANDIDATE,
            session_id=review_session.id,
            candidate_id=candidate.id,
            adapter_key="reference_adapter",
            state=CurationValidationSnapshotState.COMPLETED,
            field_results={
                "entity_name": {"status": FieldValidationStatus.VALIDATED.value}
            },
            summary={
                "state": CurationValidationSnapshotState.COMPLETED.value,
                "counts": {"validated": 1},
                "warnings": [],
                "stale_field_keys": [],
            },
            warnings=[],
            requested_at=_now(),
            completed_at=_now(),
        )
    )
    session.add(
        CurationSubmissionRecord(
            id=uuid4(),
            session_id=review_session.id,
            adapter_key="reference_adapter",
            mode=SubmissionMode.PREVIEW,
            target_key="json_bundle",
            status=CurationSubmissionStatus.PREVIEW_READY,
            readiness=[],
            payload={"payload_json": {"candidate_ids": [str(candidate.id)]}},
            validation_errors=[],
            warnings=[],
            requested_at=_now(),
            completed_at=_now(),
        )
    )
    session.commit()
    return review_session, candidate


@pytest.mark.integration
def test_legacy_workspace_migration_persists_envelope_indexes_history_and_links(db_session):
    review_session, candidate = _seed_retained_workspace(db_session)

    summary = migrate_legacy_curation_workspace_to_domain_envelopes(
        db_session,
        options=LegacyCurationWorkspaceMigrationOptions(project_key="integration"),
    )

    assert summary.blocker_count == 0
    assert summary.migrated_envelopes == 1
    assert summary.linked_candidate_projection_refs == 1

    db_session.refresh(candidate)
    assert candidate.envelope_id is not None
    assert candidate.object_id == f"legacy-curation-candidate:{candidate.id}"
    assert candidate.envelope_revision == 1

    envelope = db_session.get(DomainEnvelopeModel, candidate.envelope_id)
    assert envelope is not None
    assert envelope.session_id == review_session.id
    assert envelope.envelope_json["metadata"]["legacy_session"]["session_id"] == str(
        review_session.id
    )

    assert (
        db_session.scalar(select(func.count()).select_from(DomainEnvelopeObject)) == 1
    )
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(DomainEnvelopeProjectionIndex)
            .where(DomainEnvelopeProjectionIndex.projection_type == LEGACY_WORKSPACE_PROJECTION_TYPE)
        )
        == 1
    )
    assert db_session.scalar(select(func.count()).select_from(DomainEnvelopeHistory)) >= 5

    second_summary = migrate_legacy_curation_workspace_to_domain_envelopes(
        db_session,
        options=LegacyCurationWorkspaceMigrationOptions(project_key="integration"),
    )
    assert second_summary.migrated_envelopes == 0
    assert second_summary.linked_candidate_projection_refs == 0
    assert db_session.scalar(select(func.count()).select_from(DomainEnvelopeModel)) == 1


@pytest.mark.integration
def test_legacy_workspace_migration_blocks_cross_session_action_log_candidate(db_session):
    review_session_a, _candidate_a = _seed_retained_workspace(db_session)
    _review_session_b, candidate_b = _seed_retained_workspace(db_session)
    action = CurationActionLogEntry(
        id=uuid4(),
        session_id=review_session_a.id,
        candidate_id=candidate_b.id,
        action_type=CurationActionType.CANDIDATE_ACCEPTED,
        actor_type=CurationActorType.USER,
        actor={"actor_id": "curator-1"},
        occurred_at=_now(),
        previous_candidate_status=CurationCandidateStatus.PENDING,
        new_candidate_status=CurationCandidateStatus.ACCEPTED,
        changed_field_keys=[],
        evidence_anchor_ids=[],
        message="Cross-session retained action log.",
        action_metadata={},
    )
    db_session.add(action)
    db_session.commit()

    summary = migrate_legacy_curation_workspace_to_domain_envelopes(
        db_session,
        options=LegacyCurationWorkspaceMigrationOptions(project_key="integration"),
    )

    assert summary.migrated_envelopes == 0
    assert summary.blocker_count == 2
    assert {blocker.source_table for blocker in summary.blockers} == {
        "curation_action_log"
    }
    assert {blocker.source_id for blocker in summary.blockers} == {str(action.id)}
    assert db_session.scalar(select(func.count()).select_from(DomainEnvelopeModel)) == 0


@pytest.mark.integration
def test_legacy_workspace_migration_blocks_cross_session_action_log_draft(db_session):
    review_session_a, candidate_a = _seed_retained_workspace(db_session)
    _review_session_b, candidate_b = _seed_retained_workspace(db_session)
    assert candidate_b.draft is not None
    action = CurationActionLogEntry(
        id=uuid4(),
        session_id=review_session_a.id,
        candidate_id=candidate_a.id,
        draft_id=candidate_b.draft.id,
        action_type=CurationActionType.CANDIDATE_ACCEPTED,
        actor_type=CurationActorType.USER,
        actor={"actor_id": "curator-1"},
        occurred_at=_now(),
        previous_candidate_status=CurationCandidateStatus.PENDING,
        new_candidate_status=CurationCandidateStatus.ACCEPTED,
        changed_field_keys=["entity_name"],
        evidence_anchor_ids=[],
        message="Cross-session retained draft action log.",
        action_metadata={},
    )
    db_session.add(action)
    db_session.commit()

    summary = migrate_legacy_curation_workspace_to_domain_envelopes(
        db_session,
        options=LegacyCurationWorkspaceMigrationOptions(project_key="integration"),
    )

    assert summary.migrated_envelopes == 0
    assert summary.blocker_count == 2
    assert {blocker.source_table for blocker in summary.blockers} == {
        "curation_action_log"
    }
    assert {blocker.source_id for blocker in summary.blockers} == {str(action.id)}
    assert db_session.scalar(select(func.count()).select_from(DomainEnvelopeModel)) == 0


@pytest.mark.integration
def test_legacy_workspace_migration_blocks_cross_session_validation_snapshot(db_session):
    review_session_a, _candidate_a = _seed_retained_workspace(db_session)
    _review_session_b, candidate_b = _seed_retained_workspace(db_session)
    snapshot = CurationValidationSnapshot(
        id=uuid4(),
        scope=CurationValidationScope.CANDIDATE,
        session_id=review_session_a.id,
        candidate_id=candidate_b.id,
        adapter_key="reference_adapter",
        state=CurationValidationSnapshotState.COMPLETED,
        field_results={
            "entity_name": {"status": FieldValidationStatus.VALIDATED.value}
        },
        summary={
            "state": CurationValidationSnapshotState.COMPLETED.value,
            "counts": {"validated": 1},
            "warnings": [],
            "stale_field_keys": [],
        },
        warnings=[],
        requested_at=_now(),
        completed_at=_now(),
    )
    db_session.add(snapshot)
    db_session.commit()

    summary = migrate_legacy_curation_workspace_to_domain_envelopes(
        db_session,
        options=LegacyCurationWorkspaceMigrationOptions(project_key="integration"),
    )

    assert summary.migrated_envelopes == 0
    assert summary.blocker_count == 2
    assert {blocker.source_table for blocker in summary.blockers} == {
        "validation_snapshots"
    }
    assert {blocker.source_id for blocker in summary.blockers} == {str(snapshot.id)}
    assert db_session.scalar(select(func.count()).select_from(DomainEnvelopeModel)) == 0

"""Unit tests for the legacy curation workspace envelope migration."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

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
    LEGACY_CANDIDATE_OBJECT_TYPE,
    LEGACY_EXTRACTION_OBJECT_TYPE,
    LEGACY_WORKSPACE_PROJECTION_TYPE,
    MIGRATION_NAME,
    LegacyCurationWorkspaceMigrationOptions,
    migrate_legacy_curation_workspace_to_domain_envelopes,
)
from src.models.sql.database import Base
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
    EvidenceAnchorKind,
    EvidenceLocatorQuality,
    EvidenceSupportsDecision,
    FieldValidationStatus,
    SubmissionMode,
)


@compiles(PostgresUUID, "sqlite")
def _compile_pg_uuid_for_sqlite(_type, _compiler, **_kwargs):
    return "CHAR(36)"


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


TEST_TABLES = [
    PDFDocument.__table__,
    CurationReviewSession.__table__,
    CurationExtractionResultRecord.__table__,
    DomainEnvelopeModel.__table__,
    DomainEnvelopeObject.__table__,
    DomainValidationFinding.__table__,
    DomainEnvelopeHistory.__table__,
    DomainEnvelopeProjectionIndex.__table__,
    CurationCandidate.__table__,
    CurationEvidenceRecord.__table__,
    CurationDraft.__table__,
    CurationSubmissionRecord.__table__,
    CurationValidationSnapshot.__table__,
    CurationActionLogEntry.__table__,
]


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    restored_defaults = []
    restored_indexes = []
    for table in TEST_TABLES:
        restored_indexes.append((table, set(table.indexes)))
        table.indexes.clear()
        for column in table.columns:
            restored_defaults.append((column, column.server_default))
            if table.name.startswith("domain_") and column.name in {
                "created_at",
                "updated_at",
            }:
                continue
            column.server_default = None

    Base.metadata.create_all(bind=engine, tables=TEST_TABLES)
    session_local = sessionmaker(
        bind=engine,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )
    session = session_local()

    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine, tables=TEST_TABLES)
        for table, indexes in restored_indexes:
            table.indexes.update(indexes)
        for column, server_default in restored_defaults:
            column.server_default = server_default


def _now() -> datetime:
    return datetime(2026, 5, 9, 15, 30, tzinfo=timezone.utc)


def _create_document(db_session: Session) -> PDFDocument:
    now = _now()
    document = PDFDocument(
        id=uuid4(),
        filename=f"paper-{uuid4()}.pdf",
        title="Legacy paper",
        file_path=f"/tmp/{uuid4()}.pdf",
        file_hash=uuid4().hex + uuid4().hex,
        file_size=2048,
        page_count=3,
        upload_timestamp=now,
        last_accessed=now,
        status="processed",
    )
    db_session.add(document)
    db_session.commit()
    return document


def _create_extraction_result(
    db_session: Session,
    *,
    document_id: UUID,
) -> CurationExtractionResultRecord:
    extraction_result = CurationExtractionResultRecord(
        id=uuid4(),
        document_id=document_id,
        adapter_key="reference_adapter",
        agent_key="gene_extractor",
        source_kind=CurationExtractionSourceKind.CHAT,
        origin_session_id="chat-session-1",
        trace_id="trace-legacy",
        flow_run_id="flow-legacy",
        user_id="curator-1",
        candidate_count=1,
        conversation_summary="Legacy extraction summary.",
        payload_json={
            "raw_mentions": [{"text": "abc-1"}],
            "exclusions": [{"reason": "out of scope"}],
            "ambiguities": [{"field": "gene", "candidates": ["abc-1", "abc-2"]}],
            "normalization_notes": ["Resolver returned two candidates."],
            "items": [{"gene": {"symbol": "abc-1"}}],
        },
        extraction_metadata={"model": "legacy-model", "normalization_notes": ["kept"]},
        created_at=_now(),
    )
    db_session.add(extraction_result)
    db_session.commit()
    return extraction_result


def _create_legacy_session(db_session: Session):
    document = _create_document(db_session)
    extraction_result = _create_extraction_result(
        db_session,
        document_id=document.id,
    )
    session_row = CurationReviewSession(
        id=uuid4(),
        status=CurationSessionStatus.READY_FOR_SUBMISSION,
        adapter_key="reference_adapter",
        profile_key="primary",
        document_id=document.id,
        flow_run_id="flow-legacy",
        session_version=2,
        notes="Legacy session notes.",
        tags=["retained"],
        total_candidates=1,
        reviewed_candidates=1,
        pending_candidates=0,
        accepted_candidates=1,
        rejected_candidates=0,
        manual_candidates=0,
        warnings=["session warning"],
        prepared_at=_now(),
        last_worked_at=_now(),
        created_at=_now(),
        updated_at=_now(),
    )
    db_session.add(session_row)
    db_session.flush()

    candidate = CurationCandidate(
        id=uuid4(),
        session_id=session_row.id,
        source=CurationCandidateSource.EXTRACTED,
        status=CurationCandidateStatus.ACCEPTED,
        order=0,
        adapter_key="reference_adapter",
        profile_key="primary",
        display_label="abc-1",
        secondary_label="gene",
        conversation_summary="Candidate summary.",
        extraction_result_id=extraction_result.id,
        normalized_payload={
            "entity": {"name": "abc-1"},
            "normalization_notes": ["ambiguous resolver result reviewed"],
        },
        candidate_metadata={"raw_mentions": ["abc-1"], "legacy": True},
        created_at=_now(),
        updated_at=_now(),
        last_reviewed_at=_now(),
    )
    db_session.add(candidate)
    db_session.flush()

    draft = CurationDraft(
        id=uuid4(),
        candidate_id=candidate.id,
        adapter_key="reference_adapter",
        version=3,
        title="abc-1",
        summary="Draft summary.",
        fields=[
            {
                "field_key": "entity_name",
                "label": "Entity",
                "value": "abc-1",
                "metadata": {"normalization_notes": ["manual curator note"]},
            }
        ],
        notes="Draft notes.",
        draft_metadata={"source": "legacy"},
        created_at=_now(),
        updated_at=_now(),
        last_saved_at=_now(),
    )
    db_session.add(draft)
    evidence = CurationEvidenceRecord(
        id=uuid4(),
        candidate_id=candidate.id,
        source=CurationEvidenceSource.EXTRACTED,
        field_keys=["entity_name"],
        field_group_keys=["entity"],
        is_primary=True,
        anchor={
            "anchor_kind": EvidenceAnchorKind.SENTENCE.value,
            "locator_quality": EvidenceLocatorQuality.EXACT_QUOTE.value,
            "supports_decision": EvidenceSupportsDecision.SUPPORTS.value,
            "sentence_text": "abc-1 is expressed in neurons.",
            "page_number": 2,
            "chunk_ids": ["chunk-1"],
        },
        warnings=[],
        created_at=_now(),
        updated_at=_now(),
    )
    db_session.add(evidence)
    validation = CurationValidationSnapshot(
        id=uuid4(),
        scope=CurationValidationScope.CANDIDATE,
        session_id=session_row.id,
        candidate_id=candidate.id,
        adapter_key="reference_adapter",
        state=CurationValidationSnapshotState.COMPLETED,
        field_results={
            "entity_name": {
                "status": FieldValidationStatus.AMBIGUOUS.value,
                "candidate_matches": [
                    {"label": "abc-1", "identifier": "AGR:1"},
                    {"label": "abc-2", "identifier": "AGR:2"},
                ],
                "warnings": ["multiple resolver matches"],
            }
        },
        summary={
            "state": CurationValidationSnapshotState.COMPLETED.value,
            "counts": {"ambiguous": 1},
            "warnings": ["multiple resolver matches"],
            "stale_field_keys": [],
        },
        warnings=["validation warning"],
        requested_at=_now(),
        completed_at=_now(),
    )
    db_session.add(validation)
    submission = CurationSubmissionRecord(
        id=uuid4(),
        session_id=session_row.id,
        adapter_key="reference_adapter",
        mode=SubmissionMode.EXPORT,
        target_key="json_bundle",
        status=CurationSubmissionStatus.EXPORT_READY,
        readiness=[],
        payload={"payload_json": {"candidate_ids": [str(candidate.id)]}},
        external_reference="export-1",
        response_message="Export ready.",
        validation_errors=[],
        warnings=[],
        requested_at=_now(),
        completed_at=_now(),
    )
    db_session.add(submission)
    action = CurationActionLogEntry(
        id=uuid4(),
        session_id=session_row.id,
        candidate_id=candidate.id,
        draft_id=draft.id,
        action_type=CurationActionType.CANDIDATE_ACCEPTED,
        actor_type=CurationActorType.USER,
        actor={"actor_id": "curator-1"},
        occurred_at=_now(),
        previous_candidate_status=CurationCandidateStatus.PENDING,
        new_candidate_status=CurationCandidateStatus.ACCEPTED,
        changed_field_keys=["entity_name"],
        evidence_anchor_ids=[],
        message="Accepted legacy candidate.",
        action_metadata={"legacy": True},
    )
    db_session.add(action)
    db_session.commit()
    return document, extraction_result, session_row, candidate


def _run_migration(db_session: Session):
    return migrate_legacy_curation_workspace_to_domain_envelopes(
        db_session,
        options=LegacyCurationWorkspaceMigrationOptions(project_key="test"),
    )


def _history_source_refs(db_session: Session) -> set[tuple[str, str]]:
    refs: set[tuple[str, str]] = set()
    for row in db_session.scalars(select(DomainEnvelopeHistory)).all():
        migration = row.event_json["details"]["legacy_migration"]
        assert migration["name"] == MIGRATION_NAME
        refs.update(
            (source["table_name"], source["row_id"])
            for source in migration["source_records"]
        )
    return refs


def test_migrates_legacy_workspace_rows_to_envelopes_projection_refs_and_history(db_session):
    _document, extraction_result, session_row, candidate = _create_legacy_session(db_session)

    summary = _run_migration(db_session)

    assert summary.blocker_count == 0
    assert summary.inspected_sessions == 1
    assert summary.inspected_extraction_results == 1
    assert summary.migrated_envelopes == 1
    assert summary.linked_candidate_projection_refs == 1

    db_session.refresh(candidate)
    assert candidate.envelope_id is not None
    assert candidate.object_id == f"legacy-curation-candidate:{candidate.id}"
    assert candidate.envelope_revision == 1

    envelope = db_session.get(DomainEnvelopeModel, candidate.envelope_id)
    assert envelope is not None
    assert envelope.session_id == session_row.id
    assert envelope.document_id == session_row.document_id
    assert envelope.envelope_json["metadata"]["legacy_extraction_results"][0][
        "payload_json"
    ]["raw_mentions"] == [{"text": "abc-1"}]
    assert envelope.envelope_json["objects"][0]["object_type"] == LEGACY_CANDIDATE_OBJECT_TYPE
    assert envelope.envelope_json["objects"][0]["payload"]["draft"]["fields"][0][
        "metadata"
    ]["normalization_notes"] == ["manual curator note"]

    projection = db_session.scalar(
        select(DomainEnvelopeProjectionIndex).where(
            DomainEnvelopeProjectionIndex.projection_type == LEGACY_WORKSPACE_PROJECTION_TYPE
        )
    )
    assert projection is not None
    assert projection.projection_key == str(candidate.id)

    finding = db_session.scalar(select(DomainValidationFinding))
    assert finding is not None
    assert finding.code == "legacy_validation.ambiguous"

    source_refs = _history_source_refs(db_session)
    assert ("extraction_results", str(extraction_result.id)) in source_refs
    assert ("curation_review_sessions", str(session_row.id)) in source_refs
    assert ("curation_candidates", str(candidate.id)) in source_refs
    assert any(table == "annotation_drafts" for table, _row_id in source_refs)
    assert any(table == "evidence_anchors" for table, _row_id in source_refs)
    assert any(table == "validation_snapshots" for table, _row_id in source_refs)
    assert any(table == "curation_submissions" for table, _row_id in source_refs)
    assert any(table == "curation_action_log" for table, _row_id in source_refs)


def test_migration_is_idempotent_and_repairs_missing_candidate_projection_refs(db_session):
    _document, _extraction_result, _session_row, candidate = _create_legacy_session(db_session)

    first_summary = _run_migration(db_session)
    assert first_summary.migrated_envelopes == 1
    history_count = db_session.scalar(select(func.count()).select_from(DomainEnvelopeHistory))
    projection_count = db_session.scalar(
        select(func.count()).select_from(DomainEnvelopeProjectionIndex)
    )

    second_summary = _run_migration(db_session)

    assert second_summary.migrated_envelopes == 0
    assert second_summary.linked_candidate_projection_refs == 0
    assert second_summary.skipped_already_migrated_sources == 2
    assert db_session.scalar(select(func.count()).select_from(DomainEnvelopeHistory)) == history_count
    assert (
        db_session.scalar(select(func.count()).select_from(DomainEnvelopeProjectionIndex))
        == projection_count
    )

    candidate.envelope_id = None
    candidate.object_id = None
    candidate.envelope_revision = None
    db_session.commit()

    repair_summary = _run_migration(db_session)

    assert repair_summary.migrated_envelopes == 0
    assert repair_summary.linked_candidate_projection_refs == 1
    db_session.refresh(candidate)
    assert candidate.envelope_id is not None
    assert candidate.object_id == f"legacy-curation-candidate:{candidate.id}"
    assert db_session.scalar(select(func.count()).select_from(DomainEnvelopeHistory)) == history_count


def test_candidate_with_unprovenanced_projection_ref_reports_blocker(db_session):
    _document, _extraction_result, _session_row, candidate = _create_legacy_session(db_session)
    candidate.envelope_id = "external-envelope"
    candidate.object_id = "external-object"
    candidate.envelope_revision = 1
    db_session.commit()

    summary = _run_migration(db_session)

    assert summary.migrated_envelopes == 0
    assert summary.blocker_count == 1
    assert summary.blockers[0].source_table == "curation_candidates"
    assert "no legacy migration provenance" in summary.blockers[0].reason
    assert db_session.scalar(select(func.count()).select_from(DomainEnvelopeModel)) == 0


def test_orphan_extraction_result_migrates_as_standalone_envelope(db_session):
    document = _create_document(db_session)
    extraction_result = _create_extraction_result(db_session, document_id=document.id)

    summary = _run_migration(db_session)

    assert summary.blocker_count == 0
    assert summary.inspected_sessions == 0
    assert summary.inspected_extraction_results == 1
    assert summary.migrated_envelopes == 1

    envelope = db_session.scalar(select(DomainEnvelopeModel))
    assert envelope is not None
    assert envelope.document_id == document.id
    assert envelope.envelope_json["objects"][0]["object_type"] == LEGACY_EXTRACTION_OBJECT_TYPE
    assert envelope.envelope_json["objects"][0]["payload"]["extraction_result_id"] == str(
        extraction_result.id
    )

    second_summary = _run_migration(db_session)
    assert second_summary.migrated_envelopes == 0
    assert second_summary.skipped_already_migrated_sources == 1

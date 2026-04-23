"""Unit tests for curation workspace session-service helpers."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.lib.curation_adapters.reference import REFERENCE_ADAPTER_KEY
from src.lib.curation_workspace.export_adapters import DEFAULT_JSON_BUNDLE_TARGET_KEY
from src.lib.curation_workspace.submission_adapters import NoOpSubmissionAdapter
from src.lib.curation_workspace import session_service as module
from src.lib.curation_workspace.models import (
    CurationActionLogEntry as SessionActionLogModel,
    CurationCandidate,
    CurationDraft as DraftModel,
    CurationEvidenceRecord as EvidenceRecordModel,
    CurationExtractionResultRecord as ExtractionResultModel,
    CurationReviewSession as ReviewSessionModel,
    CurationSubmissionRecord as SubmissionModel,
    CurationValidationSnapshot as ValidationSnapshotModel,
)
from src.models.sql.database import Base
from src.models.sql.pdf_document import PDFDocument
from src.schemas.curation_workspace import (
    CurationActionType,
    CurationActorType,
    CurationCandidateAction,
    CurationCandidateValidationRequest,
    CurationCandidateDecisionRequest,
    CurationCandidateSource,
    CurationCandidateStatus,
    CurationEvidenceSource,
    CurationExtractionSourceKind,
    CurationFlowRunListRequest,
    CurationFlowRunSessionsRequest,
    CurationManualCandidateCreateRequest,
    CurationSessionFilters,
    CurationSessionListRequest,
    CurationSessionSortField,
    CurationSessionStatus,
    CurationSortDirection,
    CurationSubmissionExecuteRequest,
    CurationSubmissionPreviewRequest,
    CurationSubmissionRetryRequest,
    CurationSubmissionStatus,
    CurationValidationScope,
    CurationValidationSnapshotState,
    FieldValidationResult,
    FieldValidationStatus,
    SubmissionMode,
    SubmissionPayloadContract,
)


@compiles(PostgresUUID, "sqlite")
def _compile_pg_uuid_for_sqlite(_type, _compiler, **_kwargs):
    return "CHAR(36)"


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


TEST_TABLES = [
    PDFDocument.__table__,
    ReviewSessionModel.__table__,
    ExtractionResultModel.__table__,
    CurationCandidate.__table__,
    EvidenceRecordModel.__table__,
    DraftModel.__table__,
    SubmissionModel.__table__,
    ValidationSnapshotModel.__table__,
    SessionActionLogModel.__table__,
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
    return datetime(2026, 3, 21, 15, 30, tzinfo=timezone.utc)


def _create_document(db_session):
    now = _now()
    document = PDFDocument(
        id=uuid4(),
        filename="paper.pdf",
        title="Paper Title",
        file_path="/tmp/paper.pdf",
        file_hash="a" * 64,
        file_size=1024,
        page_count=5,
        upload_timestamp=now,
        last_accessed=now,
        status="processed",
    )
    db_session.add(document)
    db_session.commit()
    return document


def _create_extraction_result(
    db_session,
    *,
    document_id: str,
    label: str,
    origin_session_id: str = "chat-session-1",
    flow_run_id: str = "flow-1",
    candidate_count: int = 1,
    metadata: dict | None = None,
) -> ExtractionResultModel:
    record = ExtractionResultModel(
        id=uuid4(),
        document_id=UUID(document_id),
        adapter_key="reference_adapter",
        agent_key="curation_prep",
        source_kind=CurationExtractionSourceKind.CHAT,
        origin_session_id=origin_session_id,
        trace_id=f"trace-{label}",
        flow_run_id=flow_run_id,
        user_id="user-1",
        candidate_count=candidate_count,
        conversation_summary=f"Prep summary for {label}.",
        payload_json={
            "candidates": [],
            "run_metadata": {
                "model_name": "placeholder",
                "token_usage": {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                },
                "processing_notes": [],
                "warnings": [],
            },
        },
        extraction_metadata=metadata or {"final_run_metadata": {"model_name": "gpt-5.4-nano"}},
        created_at=_now(),
    )
    db_session.add(record)
    db_session.commit()
    return record


def _create_session_for_extraction(
    db_session,
    *,
    document_id: str,
    extraction_result_id: UUID,
) -> ReviewSessionModel:
    now = _now()
    session_row = ReviewSessionModel(
        id=uuid4(),
        status=CurationSessionStatus.NEW,
        adapter_key="reference_adapter",
        profile_key="primary",
        document_id=UUID(document_id),
        flow_run_id="flow-1",
        session_version=1,
        notes="Prepared session.",
        tags=["triage"],
        total_candidates=1,
        reviewed_candidates=0,
        pending_candidates=1,
        accepted_candidates=0,
        rejected_candidates=0,
        manual_candidates=0,
        warnings=[],
        prepared_at=now,
        created_at=now,
        updated_at=now,
    )
    db_session.add(session_row)
    db_session.flush()
    db_session.add(
        CurationCandidate(
            id=uuid4(),
            session_id=session_row.id,
            source=CurationCandidateSource.EXTRACTED,
            status=CurationCandidateStatus.PENDING,
            order=0,
            adapter_key="reference_adapter",
            profile_key="primary",
            display_label="Candidate",
            extraction_result_id=extraction_result_id,
            candidate_metadata={},
            normalized_payload={"entity": {"label": "Example"}},
            created_at=now,
            updated_at=now,
        )
    )
    db_session.commit()
    return session_row


def _create_review_session(
    db_session,
    *,
    document_id: str,
    flow_run_id: str | None,
    status: CurationSessionStatus,
    prepared_at: datetime,
    last_worked_at: datetime | None,
    reviewed_candidates: int,
    pending_candidates: int,
) -> ReviewSessionModel:
    session_row = ReviewSessionModel(
        id=uuid4(),
        status=status,
        adapter_key="reference_adapter",
        profile_key="primary",
        document_id=UUID(document_id),
        flow_run_id=flow_run_id,
        session_version=1,
        notes="Prepared session.",
        tags=["triage"],
        total_candidates=max(reviewed_candidates + pending_candidates, 1),
        reviewed_candidates=reviewed_candidates,
        pending_candidates=pending_candidates,
        accepted_candidates=0,
        rejected_candidates=0,
        manual_candidates=0,
        warnings=[],
        prepared_at=prepared_at,
        last_worked_at=last_worked_at,
        submitted_at=last_worked_at if status == CurationSessionStatus.SUBMITTED else None,
        created_at=prepared_at,
        updated_at=prepared_at,
    )
    db_session.add(session_row)
    db_session.commit()
    return session_row


def _build_manual_candidate_request(
    *,
    session_id: str,
    source: CurationCandidateSource = CurationCandidateSource.MANUAL,
) -> CurationManualCandidateCreateRequest:
    timestamp = _now()

    return CurationManualCandidateCreateRequest(
        session_id=session_id,
        adapter_key="reference_adapter",
        source=source,
        display_label="Manual candidate",
        draft={
            "draft_id": "draft-temp-1",
            "candidate_id": "candidate-temp-1",
            "adapter_key": "reference_adapter",
            "version": 1,
            "title": "Manual candidate",
            "fields": [
                {
                    "field_key": "field_a",
                    "label": "Field A",
                    "value": "value alpha",
                    "seed_value": None,
                    "field_type": "string",
                    "group_key": "group_one",
                    "group_label": "Group One",
                    "order": 0,
                    "required": True,
                    "read_only": False,
                    "dirty": False,
                    "stale_validation": False,
                    "evidence_anchor_ids": [],
                    "metadata": {},
                },
                {
                    "field_key": "field_b",
                    "label": "Field B",
                    "value": None,
                    "seed_value": None,
                    "field_type": "string",
                    "group_key": "group_two",
                    "group_label": "Group Two",
                    "order": 1,
                    "required": False,
                    "read_only": False,
                    "dirty": False,
                    "stale_validation": False,
                    "evidence_anchor_ids": [],
                    "metadata": {},
                },
            ],
            "created_at": timestamp,
            "updated_at": timestamp,
            "metadata": {},
        },
        evidence_anchors=[
            {
                "anchor_id": "anchor-temp-1",
                "candidate_id": "candidate-temp-1",
                "source": "manual",
                "field_keys": ["field_a"],
                "field_group_keys": ["group_one"],
                "is_primary": False,
                "anchor": {
                    "anchor_kind": "snippet",
                    "locator_quality": "exact_quote",
                    "supports_decision": "supports",
                    "snippet_text": "Quoted support text",
                    "sentence_text": "Quoted support text",
                    "normalized_text": None,
                    "viewer_search_text": "Quoted support text",
                    "viewer_highlightable": True,
                    "page_number": 4,
                    "page_label": None,
                    "section_title": "Results",
                    "subsection_title": None,
                    "figure_reference": None,
                    "table_reference": None,
                    "chunk_ids": [],
                },
                "created_at": timestamp,
                "updated_at": timestamp,
                "warnings": [],
            }
        ],
    )


def _create_decision_session(
    db_session,
    *,
    first_candidate_status: CurationCandidateStatus = CurationCandidateStatus.PENDING,
    with_manual_evidence: bool = False,
    with_existing_action_log: bool = False,
):
    document = _create_document(db_session)
    now = _now()
    session_row = ReviewSessionModel(
        id=uuid4(),
        status=CurationSessionStatus.NEW,
        adapter_key="reference_adapter",
        profile_key="primary",
        document_id=document.id,
        session_version=1,
        total_candidates=2,
        reviewed_candidates=0 if first_candidate_status == CurationCandidateStatus.PENDING else 1,
        pending_candidates=2 if first_candidate_status == CurationCandidateStatus.PENDING else 1,
        accepted_candidates=1 if first_candidate_status == CurationCandidateStatus.ACCEPTED else 0,
        rejected_candidates=1 if first_candidate_status == CurationCandidateStatus.REJECTED else 0,
        manual_candidates=0,
        warnings=[],
        prepared_at=now,
        created_at=now,
        updated_at=now,
    )
    db_session.add(session_row)
    db_session.flush()

    first_candidate = CurationCandidate(
        id=uuid4(),
        session_id=session_row.id,
        source=CurationCandidateSource.EXTRACTED,
        status=first_candidate_status,
        order=0,
        adapter_key="reference_adapter",
        profile_key="primary",
        display_label="Candidate one",
        candidate_metadata={},
        normalized_payload={"field_a": "seed-1"},
        created_at=now,
        updated_at=now,
    )
    second_candidate = CurationCandidate(
        id=uuid4(),
        session_id=session_row.id,
        source=CurationCandidateSource.EXTRACTED,
        status=CurationCandidateStatus.PENDING,
        order=1,
        adapter_key="reference_adapter",
        profile_key="primary",
        display_label="Candidate two",
        candidate_metadata={},
        normalized_payload={"field_a": "seed-2"},
        created_at=now,
        updated_at=now,
    )
    db_session.add_all([first_candidate, second_candidate])
    db_session.flush()

    first_draft = DraftModel(
        id=uuid4(),
        candidate_id=first_candidate.id,
        adapter_key="reference_adapter",
        version=2,
        title="Candidate one draft",
        fields=[
            {
                "field_key": "field_a",
                "label": "Field A",
                "value": "edited-value",
                "seed_value": "seed-1",
                "order": 0,
                "required": True,
                "read_only": False,
                "dirty": True,
                "stale_validation": True,
                "evidence_anchor_ids": [],
                "metadata": {},
            }
        ],
        notes="Curator note",
        created_at=now,
        updated_at=now,
        last_saved_at=now,
        draft_metadata={},
    )
    second_draft = DraftModel(
        id=uuid4(),
        candidate_id=second_candidate.id,
        adapter_key="reference_adapter",
        version=1,
        title="Candidate two draft",
        fields=[
            {
                "field_key": "field_a",
                "label": "Field A",
                "value": "seed-2",
                "seed_value": "seed-2",
                "order": 0,
                "required": True,
                "read_only": False,
                "dirty": False,
                "stale_validation": False,
                "evidence_anchor_ids": [],
                "metadata": {},
            }
        ],
        notes=None,
        created_at=now,
        updated_at=now,
        draft_metadata={},
    )
    db_session.add_all([first_draft, second_draft])

    manual_evidence = None
    if with_manual_evidence:
        manual_evidence = EvidenceRecordModel(
            id=uuid4(),
            candidate_id=first_candidate.id,
            source=CurationEvidenceSource.MANUAL,
            field_keys=["field_a"],
            field_group_keys=["group_a"],
            is_primary=True,
            anchor={"snippet_text": "Manual evidence"},
            warnings=[],
            created_at=now,
            updated_at=now,
        )
        db_session.add(manual_evidence)
        first_draft.fields[0]["evidence_anchor_ids"] = [str(manual_evidence.id)]

    existing_action_log = None
    if with_existing_action_log:
        existing_action_log = SessionActionLogModel(
            id=uuid4(),
            session_id=session_row.id,
            candidate_id=first_candidate.id,
            draft_id=first_draft.id,
            action_type=CurationActionType.CANDIDATE_UPDATED,
            actor_type=CurationActorType.USER,
            occurred_at=now.replace(minute=25),
            message="Existing edit log",
        )
        db_session.add(existing_action_log)

    session_row.current_candidate_id = first_candidate.id
    db_session.commit()

    return {
        "session_id": str(session_row.id),
        "first_candidate_id": str(first_candidate.id),
        "second_candidate_id": str(second_candidate.id),
        "first_draft_id": str(first_draft.id),
        "manual_evidence_id": str(manual_evidence.id) if manual_evidence is not None else None,
        "existing_action_log_id": str(existing_action_log.id) if existing_action_log is not None else None,
    }


def test_find_reusable_prepared_session_returns_only_matching_extraction_result(db_session):
    document = _create_document(db_session)
    document_id = str(document.id)
    extraction_alpha = _create_extraction_result(
        db_session,
        document_id=document_id,
        label="alpha",
    )
    extraction_beta = _create_extraction_result(
        db_session,
        document_id=document_id,
        label="beta",
    )
    session_alpha = _create_session_for_extraction(
        db_session,
        document_id=document_id,
        extraction_result_id=extraction_alpha.id,
    )

    reusable = module.find_reusable_prepared_session(
        db_session,
        document_id=document_id,
        adapter_key="reference_adapter",
        flow_run_id="flow-1",
        prep_extraction_result_id=str(extraction_beta.id),
    )

    assert reusable is None

    matching = module.find_reusable_prepared_session(
        db_session,
        document_id=document_id,
        adapter_key="reference_adapter",
        flow_run_id="flow-1",
        prep_extraction_result_id=str(extraction_alpha.id),
    )

    assert matching is not None
    assert matching.session_id == str(session_alpha.id)


def test_create_manual_candidate_persists_candidate_updates_session_and_logs_action(db_session):
    document = _create_document(db_session)
    extraction_result = _create_extraction_result(
        db_session,
        document_id=str(document.id),
        label="alpha",
    )
    session_row = _create_session_for_extraction(
        db_session,
        document_id=str(document.id),
        extraction_result_id=extraction_result.id,
    )

    response = module.create_manual_candidate(
        db_session,
        str(session_row.id),
        _build_manual_candidate_request(session_id=str(session_row.id)),
        actor_claims={"sub": "user-1", "email": "user-1@example.org"},
    )

    assert response.candidate.source == CurationCandidateSource.MANUAL
    assert response.candidate.status == CurationCandidateStatus.PENDING
    assert response.candidate.order == 1
    assert response.candidate.display_label == "Manual candidate"
    assert response.candidate.draft.title == "Manual candidate"
    assert response.candidate.draft.fields[0].seed_value == "value alpha"
    assert response.candidate.draft.fields[0].dirty is False
    assert response.candidate.evidence_anchors[0].field_keys == ["field_a"]
    assert response.session.current_candidate_id == response.candidate.candidate_id
    assert response.session.progress.total_candidates == 2
    assert response.session.progress.pending_candidates == 2
    assert response.session.progress.manual_candidates == 1
    assert response.action_log_entry.action_type == CurationActionType.CANDIDATE_CREATED
    assert response.action_log_entry.candidate_id == response.candidate.candidate_id
    assert response.action_log_entry.draft_id == response.candidate.draft.draft_id
    assert response.action_log_entry.changed_field_keys == ["field_a", "field_b"]
    assert response.action_log_entry.evidence_anchor_ids == [
        response.candidate.evidence_anchors[0].anchor_id
    ]

    persisted_candidates = db_session.scalars(
        select(CurationCandidate)
        .where(CurationCandidate.session_id == session_row.id)
        .order_by(CurationCandidate.order)
    ).all()
    assert len(persisted_candidates) == 2
    assert persisted_candidates[-1].source == CurationCandidateSource.MANUAL

    refreshed_session = db_session.get(ReviewSessionModel, session_row.id)
    assert refreshed_session is not None
    assert refreshed_session.current_candidate_id == UUID(response.candidate.candidate_id)

    action_log_rows = db_session.scalars(
        select(SessionActionLogModel)
        .where(SessionActionLogModel.session_id == session_row.id)
        .where(SessionActionLogModel.action_type == CurationActionType.CANDIDATE_CREATED)
    ).all()
    assert len(action_log_rows) == 1
    assert action_log_rows[0].new_candidate_status == CurationCandidateStatus.PENDING


def test_create_manual_candidate_rejects_non_manual_source(db_session):
    document = _create_document(db_session)
    extraction_result = _create_extraction_result(
        db_session,
        document_id=str(document.id),
        label="alpha",
    )
    session_row = _create_session_for_extraction(
        db_session,
        document_id=str(document.id),
        extraction_result_id=extraction_result.id,
    )

    with pytest.raises(module.HTTPException) as exc:
        module.create_manual_candidate(
            db_session,
            str(session_row.id),
            _build_manual_candidate_request(
                session_id=str(session_row.id),
                source=CurationCandidateSource.IMPORTED,
            ),
            actor_claims={"sub": "user-1"},
        )

    assert exc.value.status_code == 400
    assert exc.value.detail == "Manual candidate creation only supports source=manual"


def test_get_session_workspace_includes_backend_entity_tags(db_session):
    document = _create_document(db_session)
    now = _now()
    session_row = ReviewSessionModel(
        id=uuid4(),
        status=CurationSessionStatus.IN_PROGRESS,
        adapter_key="reference_adapter",
        profile_key="primary",
        document_id=document.id,
        session_version=1,
        total_candidates=1,
        reviewed_candidates=0,
        pending_candidates=1,
        accepted_candidates=0,
        rejected_candidates=0,
        manual_candidates=0,
        warnings=[],
        prepared_at=now,
        created_at=now,
        updated_at=now,
    )
    db_session.add(session_row)
    db_session.flush()

    candidate = CurationCandidate(
        id=uuid4(),
        session_id=session_row.id,
        source=CurationCandidateSource.EXTRACTED,
        status=CurationCandidateStatus.PENDING,
        order=0,
        adapter_key="reference_adapter",
        profile_key="primary",
        display_label="Gene candidate",
        candidate_metadata={},
        normalized_payload={},
        created_at=now,
        updated_at=now,
    )
    db_session.add(candidate)
    db_session.flush()

    db_session.add(
        DraftModel(
            id=uuid4(),
            candidate_id=candidate.id,
            adapter_key="reference_adapter",
            version=1,
            title="Gene draft",
            fields=[
                {
                    "field_key": "gene_symbol",
                    "label": "Gene symbol",
                    "value": "APOE",
                    "seed_value": "APOE",
                    "order": 0,
                    "required": True,
                    "read_only": False,
                    "dirty": False,
                    "stale_validation": False,
                    "evidence_anchor_ids": [],
                    "validation_result": {
                        "status": "validated",
                        "resolver": "agr_db",
                        "candidate_matches": [
                            {
                                "label": "APOE",
                                "identifier": "HGNC:613",
                            }
                        ],
                        "warnings": [],
                    },
                    "metadata": {},
                },
                {
                    "field_key": "entity_type",
                    "label": "Entity type",
                    "value": "ATP:0000005",
                    "seed_value": "ATP:0000005",
                    "order": 1,
                    "required": True,
                    "read_only": False,
                    "dirty": False,
                    "stale_validation": False,
                    "evidence_anchor_ids": [],
                    "validation_result": None,
                    "metadata": {},
                }
            ],
            notes="Gene note",
            created_at=now,
            updated_at=now,
            draft_metadata={
                "entity_tag": {
                    "entity_field_key": "gene_symbol",
                    "entity_type_field_key": "entity_type",
                },
            },
        )
    )
    db_session.add(
        EvidenceRecordModel(
            id=uuid4(),
            candidate_id=candidate.id,
            source=CurationEvidenceSource.EXTRACTED,
            field_keys=["gene_symbol"],
            field_group_keys=["primary"],
            is_primary=True,
            anchor={
                "anchor_kind": "snippet",
                "locator_quality": "exact_quote",
                "supports_decision": "supports",
                "sentence_text": "APOE evidence sentence",
                "snippet_text": "APOE evidence sentence",
                "page_number": 3,
                "section_title": "Results",
                "chunk_ids": ["chunk-1"],
            },
            warnings=[],
            created_at=now,
            updated_at=now,
        )
    )
    session_row.current_candidate_id = candidate.id
    db_session.commit()

    response = module.get_session_workspace(db_session, str(session_row.id))

    assert response.workspace.entity_tags[0].tag_id == str(candidate.id)
    assert response.workspace.entity_tags[0].entity_name == "APOE"
    assert response.workspace.entity_tags[0].entity_type == "ATP:0000005"
    assert response.workspace.entity_tags[0].db_status == "validated"
    assert response.workspace.entity_tags[0].db_entity_id == "HGNC:613"
    assert response.workspace.entity_tags[0].source == "ai"
    assert response.workspace.entity_tags[0].evidence is not None
    assert response.workspace.entity_tags[0].evidence.sentence_text == "APOE evidence sentence"


def test_get_session_workspace_rejects_entity_tag_candidates_missing_entity_type(db_session):
    document = _create_document(db_session)
    now = _now()
    session_row = ReviewSessionModel(
        id=uuid4(),
        status=CurationSessionStatus.IN_PROGRESS,
        adapter_key="reference_adapter",
        profile_key="primary",
        document_id=document.id,
        session_version=1,
        total_candidates=1,
        reviewed_candidates=0,
        pending_candidates=1,
        accepted_candidates=0,
        rejected_candidates=0,
        manual_candidates=0,
        warnings=[],
        prepared_at=now,
        created_at=now,
        updated_at=now,
    )
    db_session.add(session_row)
    db_session.flush()

    candidate = CurationCandidate(
        id=uuid4(),
        session_id=session_row.id,
        source=CurationCandidateSource.EXTRACTED,
        status=CurationCandidateStatus.PENDING,
        order=0,
        adapter_key="reference_adapter",
        profile_key="primary",
        display_label="Gene candidate",
        candidate_metadata={},
        normalized_payload={},
        created_at=now,
        updated_at=now,
    )
    db_session.add(candidate)
    db_session.flush()

    db_session.add(
        DraftModel(
            id=uuid4(),
            candidate_id=candidate.id,
            adapter_key="reference_adapter",
            version=1,
            title="Gene draft",
            fields=[
                {
                    "field_key": "gene_symbol",
                    "label": "Gene symbol",
                    "value": "APOE",
                    "seed_value": "APOE",
                    "order": 0,
                    "required": True,
                    "read_only": False,
                    "dirty": False,
                    "stale_validation": False,
                    "evidence_anchor_ids": [],
                    "validation_result": {
                        "status": "validated",
                        "resolver": "agr_db",
                        "candidate_matches": [
                            {
                                "label": "APOE",
                                "identifier": "HGNC:613",
                            }
                        ],
                        "warnings": [],
                    },
                    "metadata": {},
                }
            ],
            notes="Gene note",
            created_at=now,
            updated_at=now,
            draft_metadata={},
        )
    )
    session_row.current_candidate_id = candidate.id
    db_session.commit()

    with pytest.raises(module.HTTPException) as exc:
        module.get_session_workspace(db_session, str(session_row.id))

    assert exc.value.status_code == 500
    assert "missing an entity type" in exc.value.detail


def test_get_session_detail_returns_empty_adapter_metadata_for_zero_candidate_sessions(db_session):
    document = _create_document(db_session)
    extraction_result = _create_extraction_result(
        db_session,
        document_id=str(document.id),
        label="alpha",
        candidate_count=0,
    )
    session_row = ReviewSessionModel(
        id=uuid4(),
        status=CurationSessionStatus.NEW,
        adapter_key="reference_adapter",
        profile_key="primary",
        document_id=UUID(str(document.id)),
        flow_run_id=extraction_result.flow_run_id,
        session_version=1,
        notes="Prepared empty session.",
        tags=[],
        total_candidates=0,
        reviewed_candidates=0,
        pending_candidates=0,
        accepted_candidates=0,
        rejected_candidates=0,
        manual_candidates=0,
        warnings=[],
        prepared_at=_now(),
        created_at=_now(),
        updated_at=_now(),
    )
    db_session.add(session_row)
    db_session.commit()

    response = module.get_session_detail(db_session, str(session_row.id))

    assert response.progress.total_candidates == 0
    assert response.adapter.metadata == {}


def test_list_sessions_filters_by_origin_session_id_via_candidate_extractions(db_session):
    document = _create_document(db_session)
    document_id = str(document.id)
    extraction_chat_one = _create_extraction_result(
        db_session,
        document_id=document_id,
        label="alpha",
        origin_session_id="chat-session-1",
    )
    extraction_chat_two = _create_extraction_result(
        db_session,
        document_id=document_id,
        label="beta",
        origin_session_id="chat-session-2",
    )
    session_one = _create_session_for_extraction(
        db_session,
        document_id=document_id,
        extraction_result_id=extraction_chat_one.id,
    )
    _create_session_for_extraction(
        db_session,
        document_id=document_id,
        extraction_result_id=extraction_chat_two.id,
    )

    response = module.list_sessions(
        db_session,
        CurationSessionListRequest(
            filters=CurationSessionFilters(origin_session_id="chat-session-1"),
            sort_by=CurationSessionSortField.PREPARED_AT,
            sort_direction=CurationSortDirection.DESC,
            page=1,
            page_size=25,
        ),
    )

    assert [session.session_id for session in response.sessions] == [str(session_one.id)]


def test_list_flow_runs_returns_aggregated_summaries(db_session):
    document = _create_document(db_session)
    document_id = str(document.id)
    now = _now()

    _create_review_session(
        db_session,
        document_id=document_id,
        flow_run_id="flow-alpha",
        status=CurationSessionStatus.NEW,
        prepared_at=now,
        last_worked_at=now,
        reviewed_candidates=0,
        pending_candidates=2,
    )
    _create_review_session(
        db_session,
        document_id=document_id,
        flow_run_id="flow-alpha",
        status=CurationSessionStatus.SUBMITTED,
        prepared_at=now.replace(hour=14),
        last_worked_at=now.replace(hour=16),
        reviewed_candidates=2,
        pending_candidates=0,
    )
    _create_review_session(
        db_session,
        document_id=document_id,
        flow_run_id="flow-beta",
        status=CurationSessionStatus.IN_PROGRESS,
        prepared_at=now.replace(day=20),
        last_worked_at=now.replace(day=20, hour=13),
        reviewed_candidates=1,
        pending_candidates=1,
    )
    _create_review_session(
        db_session,
        document_id=document_id,
        flow_run_id=None,
        status=CurationSessionStatus.NEW,
        prepared_at=now.replace(day=19),
        last_worked_at=None,
        reviewed_candidates=0,
        pending_candidates=1,
    )

    response = module.list_flow_runs(
        db_session,
        CurationFlowRunListRequest(filters=CurationSessionFilters()),
    )

    assert [flow_run.flow_run_id for flow_run in response.flow_runs] == [
        "flow-alpha",
        "flow-beta",
    ]
    assert response.flow_runs[0].session_count == 2
    assert response.flow_runs[0].reviewed_count == 1
    assert response.flow_runs[0].pending_count == 1
    assert response.flow_runs[0].submitted_count == 1
    assert response.flow_runs[0].last_activity_at == now.replace(hour=16).replace(tzinfo=None)
    assert response.flow_runs[1].session_count == 1


def test_list_flow_run_sessions_returns_paginated_summaries(db_session):
    document = _create_document(db_session)
    document_id = str(document.id)
    now = _now()
    newest_session = _create_review_session(
        db_session,
        document_id=document_id,
        flow_run_id="flow-alpha",
        status=CurationSessionStatus.IN_PROGRESS,
        prepared_at=now.replace(hour=18),
        last_worked_at=now.replace(hour=19),
        reviewed_candidates=1,
        pending_candidates=1,
    )
    _create_review_session(
        db_session,
        document_id=document_id,
        flow_run_id="flow-alpha",
        status=CurationSessionStatus.NEW,
        prepared_at=now.replace(hour=14),
        last_worked_at=now.replace(hour=15),
        reviewed_candidates=0,
        pending_candidates=2,
    )
    _create_review_session(
        db_session,
        document_id=document_id,
        flow_run_id="flow-beta",
        status=CurationSessionStatus.SUBMITTED,
        prepared_at=now.replace(day=20),
        last_worked_at=now.replace(day=20, hour=13),
        reviewed_candidates=2,
        pending_candidates=0,
    )

    response = module.list_flow_run_sessions(
        db_session,
        CurationFlowRunSessionsRequest(
            flow_run_id="flow-alpha",
            page=1,
            page_size=1,
        ),
    )

    assert response.flow_run.flow_run_id == "flow-alpha"
    assert response.flow_run.session_count == 2
    assert response.flow_run.reviewed_count == 1
    assert response.flow_run.pending_count == 2
    assert response.page_info.model_dump() == {
        "page": 1,
        "page_size": 1,
        "total_items": 2,
        "total_pages": 2,
        "has_next_page": True,
        "has_previous_page": False,
    }
    assert [session.session_id for session in response.sessions] == [str(newest_session.id)]


def test_list_flow_run_sessions_reuses_flow_run_summary_count_for_page_info(
    db_session,
    monkeypatch,
):
    document = _create_document(db_session)
    document_id = str(document.id)
    now = _now()
    newest_session = _create_review_session(
        db_session,
        document_id=document_id,
        flow_run_id="flow-alpha",
        status=CurationSessionStatus.IN_PROGRESS,
        prepared_at=now.replace(hour=18),
        last_worked_at=now.replace(hour=19),
        reviewed_candidates=1,
        pending_candidates=1,
    )
    _create_review_session(
        db_session,
        document_id=document_id,
        flow_run_id="flow-alpha",
        status=CurationSessionStatus.NEW,
        prepared_at=now.replace(hour=14),
        last_worked_at=now.replace(hour=15),
        reviewed_candidates=0,
        pending_candidates=2,
    )

    captured_total_items: list[int | None] = []
    original_list_session_summaries = module._list_session_summaries

    def wrapped_list_session_summaries(*args, **kwargs):
        captured_total_items.append(kwargs.get("total_items"))
        return original_list_session_summaries(*args, **kwargs)

    monkeypatch.setattr(module, "_list_session_summaries", wrapped_list_session_summaries)

    response = module.list_flow_run_sessions(
        db_session,
        CurationFlowRunSessionsRequest(
            flow_run_id="flow-alpha",
            page=1,
            page_size=1,
        ),
    )

    assert captured_total_items == [response.flow_run.session_count]
    assert response.flow_run.session_count == 2
    assert response.page_info.total_items == 2
    assert [session.session_id for session in response.sessions] == [str(newest_session.id)]


def test_list_flow_run_sessions_raises_not_found_for_unknown_group(db_session):
    document = _create_document(db_session)
    document_id = str(document.id)

    _create_review_session(
        db_session,
        document_id=document_id,
        flow_run_id="flow-alpha",
        status=CurationSessionStatus.NEW,
        prepared_at=_now(),
        last_worked_at=_now(),
        reviewed_candidates=0,
        pending_candidates=1,
    )

    with pytest.raises(module.HTTPException) as exc:
        module.list_flow_run_sessions(
            db_session,
            CurationFlowRunSessionsRequest(flow_run_id="flow-missing"),
        )

    assert exc.value.status_code == 404
    assert exc.value.detail == "Flow run flow-missing not found"


def test_decide_candidate_accepts_and_advances_to_next_pending(db_session):
    seeded = _create_decision_session(db_session)

    response = module.decide_candidate(
        db_session,
        seeded["first_candidate_id"],
        CurationCandidateDecisionRequest(
            session_id=seeded["session_id"],
            candidate_id=seeded["first_candidate_id"],
            action=CurationCandidateAction.ACCEPT,
            advance_queue=True,
        ),
        {
            "sub": "user-1",
            "email": "user-1@example.org",
            "name": "Curator One",
        },
    )

    assert response.candidate.status == CurationCandidateStatus.ACCEPTED
    assert response.session.status == CurationSessionStatus.IN_PROGRESS
    assert response.session.current_candidate_id == seeded["second_candidate_id"]
    assert response.next_candidate_id == seeded["second_candidate_id"]
    assert response.session.progress.model_dump() == {
        "total_candidates": 2,
        "reviewed_candidates": 1,
        "pending_candidates": 1,
        "accepted_candidates": 1,
        "rejected_candidates": 0,
        "manual_candidates": 0,
    }
    assert response.action_log_entry.action_type == CurationActionType.CANDIDATE_ACCEPTED
    assert response.action_log_entry.previous_candidate_status == CurationCandidateStatus.PENDING
    assert response.action_log_entry.new_candidate_status == CurationCandidateStatus.ACCEPTED


def test_delete_candidate_removes_candidate_children_and_updates_session(db_session):
    seeded = _create_decision_session(
        db_session,
        with_manual_evidence=True,
        with_existing_action_log=True,
    )
    now = _now()

    db_session.add_all([
        ValidationSnapshotModel(
            id=uuid4(),
            scope=CurationValidationScope.SESSION,
            session_id=UUID(seeded["session_id"]),
            candidate_id=None,
            adapter_key="reference_adapter",
            state=CurationValidationSnapshotState.COMPLETED,
            field_results={},
            summary={
                "state": "completed",
                "counts": {
                    "validated": 1,
                    "ambiguous": 0,
                    "not_found": 0,
                    "invalid_format": 0,
                    "conflict": 0,
                    "skipped": 0,
                    "overridden": 0,
                },
                "warnings": [],
                "stale_field_keys": [],
            },
            warnings=[],
            requested_at=now,
            completed_at=now,
        ),
        ValidationSnapshotModel(
            id=uuid4(),
            scope=CurationValidationScope.CANDIDATE,
            session_id=UUID(seeded["session_id"]),
            candidate_id=UUID(seeded["first_candidate_id"]),
            adapter_key="reference_adapter",
            state=CurationValidationSnapshotState.COMPLETED,
            field_results={},
            summary={
                "state": "completed",
                "counts": {
                    "validated": 1,
                    "ambiguous": 0,
                    "not_found": 0,
                    "invalid_format": 0,
                    "conflict": 0,
                    "skipped": 0,
                    "overridden": 0,
                },
                "warnings": [],
                "stale_field_keys": [],
            },
            warnings=[],
            requested_at=now,
            completed_at=now,
        ),
    ])
    db_session.commit()

    response = module.delete_candidate(
        db_session,
        seeded["session_id"],
        seeded["first_candidate_id"],
        actor_claims={
            "sub": "user-1",
            "email": "user-1@example.org",
            "name": "Curator One",
        },
    )

    assert response.deleted_candidate_id == seeded["first_candidate_id"]
    assert response.session.current_candidate_id == seeded["second_candidate_id"]
    assert response.session.progress.model_dump() == {
        "total_candidates": 1,
        "reviewed_candidates": 0,
        "pending_candidates": 1,
        "accepted_candidates": 0,
        "rejected_candidates": 0,
        "manual_candidates": 0,
    }
    assert response.action_log_entry.action_type == CurationActionType.CANDIDATE_DELETED
    assert response.action_log_entry.previous_candidate_status == CurationCandidateStatus.PENDING
    assert response.action_log_entry.candidate_id is None
    assert response.action_log_entry.draft_id is None
    assert response.action_log_entry.metadata["deleted_candidate_id"] == seeded["first_candidate_id"]
    assert response.action_log_entry.metadata["next_candidate_id"] == seeded["second_candidate_id"]

    assert db_session.get(CurationCandidate, UUID(seeded["first_candidate_id"])) is None
    assert db_session.get(DraftModel, UUID(seeded["first_draft_id"])) is None

    remaining_evidence = db_session.scalars(select(EvidenceRecordModel)).all()
    assert remaining_evidence == []

    remaining_snapshots = db_session.scalars(select(ValidationSnapshotModel)).all()
    assert remaining_snapshots == []

    action_logs = (
        db_session.query(SessionActionLogModel)
        .filter(SessionActionLogModel.session_id == UUID(seeded["session_id"]))
        .order_by(SessionActionLogModel.occurred_at.asc(), SessionActionLogModel.id.asc())
        .all()
    )

    assert len(action_logs) == 1
    assert action_logs[0].action_type == CurationActionType.CANDIDATE_DELETED
    assert action_logs[0].candidate_id is None


def test_decide_candidate_reset_reverts_draft_and_keeps_existing_audit_entries(db_session):
    seeded = _create_decision_session(
        db_session,
        first_candidate_status=CurationCandidateStatus.ACCEPTED,
        with_manual_evidence=True,
        with_existing_action_log=True,
    )

    response = module.decide_candidate(
        db_session,
        seeded["first_candidate_id"],
        CurationCandidateDecisionRequest(
            session_id=seeded["session_id"],
            candidate_id=seeded["first_candidate_id"],
            action=CurationCandidateAction.RESET,
            advance_queue=False,
        ),
        {
            "sub": "user-1",
            "email": "user-1@example.org",
        },
    )

    assert response.candidate.status == CurationCandidateStatus.PENDING
    assert response.session.current_candidate_id == seeded["first_candidate_id"]
    assert response.next_candidate_id is None
    assert response.candidate.draft.fields[0].value == "seed-1"
    assert response.candidate.draft.fields[0].dirty is False
    assert response.candidate.draft.fields[0].stale_validation is False
    assert response.candidate.draft.fields[0].evidence_anchor_ids == []
    assert response.candidate.draft.notes is None
    assert response.candidate.evidence_anchors == []
    assert response.action_log_entry.action_type == CurationActionType.CANDIDATE_RESET
    assert response.action_log_entry.previous_candidate_status == CurationCandidateStatus.ACCEPTED
    assert response.action_log_entry.new_candidate_status == CurationCandidateStatus.PENDING
    assert response.action_log_entry.evidence_anchor_ids == [seeded["manual_evidence_id"]]

    action_logs = (
        db_session.query(SessionActionLogModel)
        .filter(SessionActionLogModel.session_id == UUID(seeded["session_id"]))
        .order_by(SessionActionLogModel.occurred_at.asc(), SessionActionLogModel.id.asc())
        .all()
    )

    assert len(action_logs) == 2
    assert str(action_logs[0].id) == seeded["existing_action_log_id"]
    assert action_logs[0].action_type == CurationActionType.CANDIDATE_UPDATED
    assert action_logs[1].action_type == CurationActionType.CANDIDATE_RESET


def test_validate_candidate_only_refreshes_requested_field_subset(db_session):
    document = _create_document(db_session)
    session_row = _create_review_session(
        db_session,
        document_id=str(document.id),
        flow_run_id="flow-alpha",
        status=CurationSessionStatus.IN_PROGRESS,
        prepared_at=_now(),
        last_worked_at=_now(),
        reviewed_candidates=0,
        pending_candidates=1,
    )
    candidate = CurationCandidate(
        id=uuid4(),
        session_id=session_row.id,
        source=CurationCandidateSource.EXTRACTED,
        status=CurationCandidateStatus.PENDING,
        order=0,
        adapter_key="test",
        profile_key="primary",
        display_label="Candidate",
        normalized_payload={},
        candidate_metadata={},
        created_at=_now(),
        updated_at=_now(),
    )
    db_session.add(candidate)
    db_session.flush()
    draft = DraftModel(
        id=uuid4(),
        candidate_id=candidate.id,
        adapter_key="test",
        version=1,
        title="Candidate draft",
        summary="Candidate summary",
        fields=[
            {
                "field_key": "field_a",
                "label": "Field A",
                "value": "Alpha",
                "seed_value": "Alpha",
                "order": 0,
                "required": True,
                "read_only": False,
                "dirty": False,
                "stale_validation": True,
                "evidence_anchor_ids": [],
                "metadata": {},
            },
            {
                "field_key": "field_b",
                "label": "Field B",
                "value": "Beta",
                "seed_value": "Beta",
                "order": 1,
                "required": False,
                "read_only": False,
                "dirty": False,
                "stale_validation": True,
                "evidence_anchor_ids": [],
                "validation_result": {
                    "status": "ambiguous",
                    "resolver": "fixture",
                    "candidate_matches": [],
                    "warnings": ["Needs follow-up"],
                },
                "metadata": {},
            },
        ],
        notes="Test draft",
        created_at=_now(),
        updated_at=_now(),
        draft_metadata={},
    )
    db_session.add(draft)
    db_session.commit()

    response = module.validate_candidate(
        db_session,
        candidate.id,
        CurationCandidateValidationRequest(
            session_id=str(session_row.id),
            candidate_id=str(candidate.id),
            field_keys=["field_a"],
        ),
    )

    fields_by_key = {
        field.field_key: field
        for field in response.candidate.draft.fields
    }
    assert fields_by_key["field_a"].stale_validation is False
    assert fields_by_key["field_a"].validation_result is not None
    assert fields_by_key["field_a"].validation_result.status == "skipped"
    assert fields_by_key["field_b"].stale_validation is True
    assert fields_by_key["field_b"].validation_result is not None
    assert fields_by_key["field_b"].validation_result.status == "ambiguous"
    assert response.validation_snapshot.summary.stale_field_keys == ["field_b"]
    assert response.validation_snapshot.summary.counts.skipped == 1
    assert response.validation_snapshot.summary.counts.ambiguous == 1
    assert response.validation_snapshot.field_results["field_b"].status == "ambiguous"
    assert response.candidate.validation is not None
    assert response.candidate.validation.stale_field_keys == ["field_b"]


def test_submission_preview_builds_preview_payload_and_candidate_readiness(db_session):
    seeded = _create_decision_session(
        db_session,
        first_candidate_status=CurationCandidateStatus.ACCEPTED,
    )

    response = module.submission_preview(
        db_session,
        seeded["session_id"],
        CurationSubmissionPreviewRequest(
            session_id=seeded["session_id"],
            mode=SubmissionMode.PREVIEW,
        ),
    )

    readiness_by_candidate = {
        readiness.candidate_id: readiness
        for readiness in response.submission.readiness
    }

    assert response.submission.status == CurationSubmissionStatus.PREVIEW_READY
    assert response.submission.target_key == "reference_adapter.default"
    assert response.session_validation is not None
    assert readiness_by_candidate[seeded["first_candidate_id"]].ready is True
    assert readiness_by_candidate[seeded["first_candidate_id"]].blocking_reasons == []
    assert readiness_by_candidate[seeded["second_candidate_id"]].ready is False
    assert readiness_by_candidate[seeded["second_candidate_id"]].blocking_reasons == [
        "Candidate is still pending curator review."
    ]
    assert response.submission.payload is not None
    assert response.submission.payload.target_key == "reference_adapter.default"
    assert response.submission.payload.payload_json is not None
    assert response.submission.payload.payload_json["candidate_count"] == 1
    assert response.submission.payload.payload_json["candidates"][0]["candidate_id"] == (
        seeded["first_candidate_id"]
    )
    assert response.submission.payload.candidate_ids == [seeded["first_candidate_id"]]
    assert response.submission.payload.payload_text is None


def test_submission_preview_routes_payload_generation_through_submission_adapter(
    db_session,
    monkeypatch,
):
    seeded = _create_decision_session(
        db_session,
        first_candidate_status=CurationCandidateStatus.ACCEPTED,
    )
    captured: dict[str, object] = {}

    class StubSubmissionAdapter:
        adapter_key = "reference_adapter"
        supported_submission_modes = (SubmissionMode.PREVIEW,)
        supported_target_keys = ("adapter_owned_preview_target",)

        def build_submission_payload(self, *, mode, target_key, payload_context):
            captured["mode"] = mode
            captured["target_key"] = target_key
            captured["payload_context"] = payload_context
            return SubmissionPayloadContract(
                mode=mode,
                target_key=target_key,
                adapter_key=self.adapter_key,
                candidate_ids=list(payload_context["candidate_ids"]),
                payload_json={"adapter_owned": True},
            )

    monkeypatch.setattr(
        module,
        "_resolve_submission_domain_adapter",
        lambda adapter_key: StubSubmissionAdapter(),
    )

    response = module.submission_preview(
        db_session,
        seeded["session_id"],
        CurationSubmissionPreviewRequest(
            session_id=seeded["session_id"],
            mode=SubmissionMode.PREVIEW,
        ),
    )

    assert captured["mode"] == SubmissionMode.PREVIEW
    assert captured["target_key"] == "adapter_owned_preview_target"
    assert captured["payload_context"]["candidate_ids"] == [seeded["first_candidate_id"]]
    assert captured["payload_context"]["candidate_count"] == 1
    assert response.submission.payload is not None
    assert response.submission.payload.payload_json == {"adapter_owned": True}


def test_submission_validation_blocking_reason_ignores_warning_only_fields():
    field = module.CurationDraftFieldSchema.model_validate(
        {
            "field_key": "identifiers.pmid",
            "label": "PMID",
            "value": None,
            "seed_value": None,
            "order": 0,
            "required": False,
            "read_only": False,
            "dirty": False,
            "stale_validation": False,
            "evidence_anchor_ids": [],
            "metadata": {
                "validation": {
                    "rules": ["pmid_format"],
                    "severity": "warning",
                }
            },
        }
    )
    validation_result = FieldValidationResult(
        status=FieldValidationStatus.INVALID_FORMAT,
        warnings=["PMID is optional."],
    )

    assert module._submission_validation_blocking_reason(field, validation_result) is None


def test_submission_preview_handles_candidates_without_matching_validation_snapshots(
    db_session,
    monkeypatch,
):
    seeded = _create_decision_session(
        db_session,
        first_candidate_status=CurationCandidateStatus.ACCEPTED,
    )

    monkeypatch.setattr(
        module,
        "validate_session",
        lambda *_args, **_kwargs: SimpleNamespace(
            candidate_validations=[],
            session_validation=None,
        ),
    )

    response = module.submission_preview(
        db_session,
        seeded["session_id"],
        CurationSubmissionPreviewRequest(
            session_id=seeded["session_id"],
            mode=SubmissionMode.PREVIEW,
            target_key="review_export_bundle",
            include_payload=False,
        ),
    )

    readiness_by_candidate = {
        readiness.candidate_id: readiness
        for readiness in response.submission.readiness
    }

    assert readiness_by_candidate[seeded["first_candidate_id"]].ready is True
    assert readiness_by_candidate[seeded["first_candidate_id"]].blocking_reasons == []
    assert readiness_by_candidate[seeded["second_candidate_id"]].ready is False
    assert readiness_by_candidate[seeded["second_candidate_id"]].blocking_reasons == [
        "Candidate is still pending curator review."
    ]
    assert response.submission.payload is None
    assert response.session_validation is None


def test_submission_preview_builds_export_payload_when_ready_candidates_are_filtered(
    db_session,
):
    seeded = _create_decision_session(
        db_session,
        first_candidate_status=CurationCandidateStatus.ACCEPTED,
    )
    first_candidate = db_session.get(CurationCandidate, UUID(seeded["first_candidate_id"]))
    assert first_candidate is not None
    assert first_candidate.draft is not None

    first_candidate.draft.fields = [
        {
            "field_key": "field_a",
            "label": "Field A",
            "value": None,
            "seed_value": None,
            "order": 0,
            "required": True,
            "read_only": False,
            "dirty": False,
            "stale_validation": True,
            "evidence_anchor_ids": [],
            "metadata": {},
        }
    ]
    db_session.add(first_candidate.draft)
    db_session.commit()

    response = module.submission_preview(
        db_session,
        seeded["session_id"],
        CurationSubmissionPreviewRequest(
            session_id=seeded["session_id"],
            mode=SubmissionMode.EXPORT,
            target_key="review_export_bundle",
        ),
    )

    readiness_by_candidate = {
        readiness.candidate_id: readiness
        for readiness in response.submission.readiness
    }

    assert response.submission.status == CurationSubmissionStatus.EXPORT_READY
    assert readiness_by_candidate[seeded["first_candidate_id"]].ready is False
    assert readiness_by_candidate[seeded["first_candidate_id"]].blocking_reasons == [
        "Field A is empty or invalid."
    ]
    assert response.submission.payload is not None
    assert response.submission.payload.candidate_ids == []
    assert response.submission.payload.payload_json is not None
    assert response.submission.payload.payload_json["candidate_count"] == 0
    assert response.submission.payload.payload_text is not None
    assert response.submission.payload.content_type == "application/json"
    assert (
        response.submission.payload.filename
        == f"reference_adapter-{seeded['session_id']}-export-bundle.json"
    )
    assert response.submission.payload.warnings == [
        "No accepted candidates are ready for submission."
    ]


def test_submission_preview_rejects_unknown_candidate_ids(db_session):
    seeded = _create_decision_session(
        db_session,
        first_candidate_status=CurationCandidateStatus.ACCEPTED,
    )

    with pytest.raises(module.HTTPException) as exc:
        module.submission_preview(
            db_session,
            seeded["session_id"],
            CurationSubmissionPreviewRequest(
                session_id=seeded["session_id"],
                mode=SubmissionMode.PREVIEW,
                target_key="review_export_bundle",
                candidate_ids=[str(uuid4())],
            ),
        )

    assert exc.value.status_code == 400
    assert "Unknown candidate(s) for session" in exc.value.detail


def test_submission_adapter_registry_is_built_lazily_and_cached(monkeypatch):
    module._submission_adapter_registry.cache_clear()

    build_calls = []

    class StubRegistry:
        def require(self, target_key):
            return {"transport_key": target_key}

    def _build_registry():
        build_calls.append("built")
        return StubRegistry()

    monkeypatch.setattr(module, "build_default_submission_adapter_registry", _build_registry)

    try:
        first = module._resolve_submission_transport_adapter("reference_target")
        second = module._resolve_submission_transport_adapter("reference_target")
    finally:
        module._submission_adapter_registry.cache_clear()

    assert first == {"transport_key": "reference_target"}
    assert second == {"transport_key": "reference_target"}
    assert build_calls == ["built"]


def test_resolve_submission_transport_adapter_sanitizes_missing_target(monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger=module.logger.name)
    module._submission_adapter_registry.cache_clear()

    class StubRegistry:
        def require(self, target_key):
            raise KeyError(f"Submission target '{target_key}' is missing")

    monkeypatch.setattr(
        module,
        "build_default_submission_adapter_registry",
        lambda: StubRegistry(),
    )

    try:
        with pytest.raises(module.HTTPException) as exc:
            module._resolve_submission_transport_adapter("missing_target")
    finally:
        module._submission_adapter_registry.cache_clear()

    assert exc.value.status_code == 400
    assert exc.value.detail == "Submission target is not configured"
    assert "missing_target" not in str(exc.value.detail)
    assert "missing_target" in caplog.text


def test_build_submission_execute_payload_sanitizes_adapter_errors(caplog, monkeypatch):
    caplog.set_level(logging.WARNING, logger=module.logger.name)

    class StubExportAdapter:
        def build_submission_payload(self, *, mode, target_key, payload_context):
            raise ValueError("Payload builder exploded while preparing submission output.")

    db = SimpleNamespace(get=lambda *_args, **_kwargs: None)
    session_row = SimpleNamespace(
        id=uuid4(),
        document_id="document-1",
        adapter_key=REFERENCE_ADAPTER_KEY,
    )

    monkeypatch.setattr(module, "_resolve_export_adapter", lambda _adapter_key: StubExportAdapter())

    with pytest.raises(module.HTTPException) as exc:
        module._build_submission_execute_payload(
            db=db,
            session_row=session_row,
            mode=SubmissionMode.DIRECT_SUBMIT,
            target_key="target-1",
            ready_candidates=[],
            session_validation=None,
            adapter_key=REFERENCE_ADAPTER_KEY,
        )

    assert exc.value.status_code == 400
    assert exc.value.detail == "Submission payload could not be built"
    assert "payload builder exploded" not in str(exc.value.detail).lower()
    assert "payload builder exploded" in caplog.text.lower()


def test_execute_submission_persists_submission_updates_session_and_logs_action(db_session):
    seeded = _create_decision_session(
        db_session,
        first_candidate_status=CurationCandidateStatus.ACCEPTED,
    )
    session_row = db_session.get(ReviewSessionModel, UUID(seeded["session_id"]))
    assert session_row is not None
    session_row.adapter_key = REFERENCE_ADAPTER_KEY
    db_session.add(session_row)
    db_session.commit()

    response = module.execute_submission(
        db_session,
        seeded["session_id"],
        CurationSubmissionExecuteRequest(
            session_id=seeded["session_id"],
            target_key=DEFAULT_JSON_BUNDLE_TARGET_KEY,
        ),
        actor_claims={"sub": "user-1", "email": "user-1@example.org"},
    )

    assert response.submission.status == CurationSubmissionStatus.ACCEPTED
    assert response.submission.target_key == DEFAULT_JSON_BUNDLE_TARGET_KEY
    assert response.submission.external_reference == f"noop:{DEFAULT_JSON_BUNDLE_TARGET_KEY}:1"
    assert response.submission.payload is not None
    assert response.submission.payload.mode == SubmissionMode.DIRECT_SUBMIT
    assert response.submission.payload.target_key == DEFAULT_JSON_BUNDLE_TARGET_KEY
    assert response.submission.payload.candidate_ids == [seeded["first_candidate_id"]]
    assert response.submission.payload.payload_json is not None
    assert response.submission.payload.payload_json["candidate_count"] == 1
    assert response.submission.payload.payload_text is not None
    assert response.submission.payload.content_type == "application/json"
    assert response.submission.payload.filename is not None
    assert response.session.status == CurationSessionStatus.SUBMITTED
    assert response.session.submitted_at is not None
    assert response.session.latest_submission is not None
    assert response.session.latest_submission.status == CurationSubmissionStatus.ACCEPTED
    assert response.action_log_entry.action_type == CurationActionType.SUBMISSION_EXECUTED
    assert response.action_log_entry.new_session_status == CurationSessionStatus.SUBMITTED
    assert response.action_log_entry.metadata["submitted_candidate_count"] == 1

    persisted_submission = db_session.scalars(
        select(SubmissionModel).where(SubmissionModel.session_id == UUID(seeded["session_id"]))
    ).one()
    assert persisted_submission.status == CurationSubmissionStatus.ACCEPTED
    assert persisted_submission.external_reference == f"noop:{DEFAULT_JSON_BUNDLE_TARGET_KEY}:1"
    assert persisted_submission.payload is not None
    assert persisted_submission.payload["candidate_ids"] == [seeded["first_candidate_id"]]
    assert persisted_submission.payload["payload_json"]["candidate_count"] == 1
    assert persisted_submission.payload["payload_text"] is not None
    assert persisted_submission.payload["content_type"] == "application/json"
    assert persisted_submission.payload["filename"] is not None

    reloaded_submission = module._submission_record(persisted_submission)
    assert reloaded_submission.payload is not None
    assert reloaded_submission.payload.candidate_ids == [seeded["first_candidate_id"]]
    assert reloaded_submission.payload.payload_json is not None
    assert reloaded_submission.payload.payload_json["candidate_count"] == 1
    assert reloaded_submission.payload.payload_text is not None
    assert reloaded_submission.payload.content_type == "application/json"
    assert reloaded_submission.payload.filename == persisted_submission.payload["filename"]

    refreshed_session = db_session.get(ReviewSessionModel, UUID(seeded["session_id"]))
    assert refreshed_session is not None
    assert refreshed_session.status == CurationSessionStatus.SUBMITTED
    assert refreshed_session.submitted_at is not None

    session_detail = module.get_session_detail(db_session, seeded["session_id"])
    assert session_detail.latest_submission is not None
    assert session_detail.latest_submission.payload is not None
    assert session_detail.latest_submission.payload.candidate_ids == [seeded["first_candidate_id"]]
    assert session_detail.latest_submission.payload.payload_text is not None
    assert session_detail.latest_submission.payload.content_type == "application/json"
    assert session_detail.latest_submission.payload.filename is not None


def test_execute_submission_preserves_payload_warnings_across_reload(db_session, monkeypatch):
    seeded = _create_decision_session(
        db_session,
        first_candidate_status=CurationCandidateStatus.ACCEPTED,
    )
    session_row = db_session.get(ReviewSessionModel, UUID(seeded["session_id"]))
    assert session_row is not None
    session_row.adapter_key = REFERENCE_ADAPTER_KEY
    db_session.add(session_row)
    db_session.commit()

    payload_warning = "Payload warning"
    transport_warning = "Transport warning"

    monkeypatch.setattr(
        module,
        "_build_submission_execute_payload",
        lambda **_kwargs: SubmissionPayloadContract(
            mode=SubmissionMode.DIRECT_SUBMIT,
            target_key=DEFAULT_JSON_BUNDLE_TARGET_KEY,
            adapter_key=REFERENCE_ADAPTER_KEY,
            candidate_ids=[seeded["first_candidate_id"]],
            payload_json={"candidate_count": 1},
            payload_text='{"candidate_count": 1}',
            content_type="application/json",
            filename="submission.json",
            warnings=[payload_warning],
        ),
    )
    monkeypatch.setattr(
        module,
        "_resolve_submission_transport_adapter",
        lambda _target_key: NoOpSubmissionAdapter(
            target_key=DEFAULT_JSON_BUNDLE_TARGET_KEY,
            warnings=[transport_warning],
        ),
    )

    response = module.execute_submission(
        db_session,
        seeded["session_id"],
        CurationSubmissionExecuteRequest(
            session_id=seeded["session_id"],
            target_key=DEFAULT_JSON_BUNDLE_TARGET_KEY,
        ),
        actor_claims={"sub": "user-1", "email": "user-1@example.org"},
    )

    assert response.submission.payload is not None
    assert response.submission.payload.warnings == [payload_warning]
    assert response.submission.warnings == [payload_warning, transport_warning]

    persisted_submission = db_session.scalars(
        select(SubmissionModel).where(SubmissionModel.session_id == UUID(seeded["session_id"]))
    ).one()
    assert persisted_submission.payload is not None
    assert persisted_submission.payload["warnings"] == [payload_warning]
    assert persisted_submission.warnings == [payload_warning, transport_warning]

    reloaded_submission = module._submission_record(persisted_submission)
    assert reloaded_submission.payload is not None
    assert reloaded_submission.payload.warnings == [payload_warning]
    assert reloaded_submission.warnings == [payload_warning, transport_warning]

    session_detail = module.get_session_detail(db_session, seeded["session_id"])
    assert session_detail.latest_submission is not None
    assert session_detail.latest_submission.payload is not None
    assert session_detail.latest_submission.payload.warnings == [payload_warning]
    assert session_detail.latest_submission.warnings == [payload_warning, transport_warning]


def test_execute_submission_persists_validation_errors_without_marking_session_submitted(
    db_session,
    monkeypatch,
):
    seeded = _create_decision_session(
        db_session,
        first_candidate_status=CurationCandidateStatus.ACCEPTED,
    )
    session_row = db_session.get(ReviewSessionModel, UUID(seeded["session_id"]))
    assert session_row is not None
    session_row.adapter_key = REFERENCE_ADAPTER_KEY
    db_session.add(session_row)
    db_session.commit()

    class StubSubmissionAdapter:
        transport_key = "stub_submission"

        def submit(self, *, payload):
            assert payload.mode == SubmissionMode.DIRECT_SUBMIT
            return {
                "status": CurationSubmissionStatus.VALIDATION_ERRORS,
                "response_message": "Downstream validation rejected the payload.",
                "validation_errors": ["Field A is empty."],
            }

    monkeypatch.setattr(
        module,
        "_resolve_submission_transport_adapter",
        lambda _target_key: StubSubmissionAdapter(),
    )

    response = module.execute_submission(
        db_session,
        seeded["session_id"],
        CurationSubmissionExecuteRequest(
            session_id=seeded["session_id"],
            target_key=DEFAULT_JSON_BUNDLE_TARGET_KEY,
        ),
        actor_claims={"sub": "user-1"},
    )

    assert response.submission.status == CurationSubmissionStatus.VALIDATION_ERRORS
    assert response.submission.validation_errors == ["Field A is empty."]
    assert response.session.status == CurationSessionStatus.NEW
    assert response.session.submitted_at is None
    assert response.action_log_entry.new_session_status is None

    persisted_submission = db_session.scalars(
        select(SubmissionModel).where(SubmissionModel.session_id == UUID(seeded["session_id"]))
    ).one()
    assert persisted_submission.status == CurationSubmissionStatus.VALIDATION_ERRORS
    assert persisted_submission.validation_errors == ["Field A is empty."]


def test_execute_submission_normalizes_transport_errors_to_failed_submission_record(
    db_session,
    monkeypatch,
):
    seeded = _create_decision_session(
        db_session,
        first_candidate_status=CurationCandidateStatus.ACCEPTED,
    )
    session_row = db_session.get(ReviewSessionModel, UUID(seeded["session_id"]))
    assert session_row is not None
    session_row.adapter_key = REFERENCE_ADAPTER_KEY
    db_session.add(session_row)
    db_session.commit()

    class ExplodingSubmissionAdapter:
        transport_key = "exploding_submission"

        def submit(self, *, payload):
            assert payload.target_key == DEFAULT_JSON_BUNDLE_TARGET_KEY
            raise RuntimeError("timeout talking to downstream submitter")

    monkeypatch.setattr(
        module,
        "_resolve_submission_transport_adapter",
        lambda _target_key: ExplodingSubmissionAdapter(),
    )

    logged = {}

    def _capture_exception(message, *args):
        logged["message"] = message
        logged["args"] = args

    monkeypatch.setattr(module.logger, "exception", _capture_exception)

    response = module.execute_submission(
        db_session,
        seeded["session_id"],
        CurationSubmissionExecuteRequest(
            session_id=seeded["session_id"],
            target_key=DEFAULT_JSON_BUNDLE_TARGET_KEY,
        ),
        actor_claims={"sub": "user-1"},
    )

    assert response.submission.status == CurationSubmissionStatus.FAILED
    assert "timeout talking to downstream submitter" in (response.submission.response_message or "")
    assert response.session.status == CurationSessionStatus.NEW
    assert response.session.submitted_at is None
    assert logged["message"] == (
        "Submission transport adapter '%s' failed for session '%s' and target '%s'"
    )
    assert logged["args"] == (
        "exploding_submission",
        seeded["session_id"],
        DEFAULT_JSON_BUNDLE_TARGET_KEY,
    )

    persisted_submission = db_session.scalars(
        select(SubmissionModel).where(SubmissionModel.session_id == UUID(seeded["session_id"]))
    ).one()
    assert persisted_submission.status == CurationSubmissionStatus.FAILED
    assert "timeout talking to downstream submitter" in (persisted_submission.response_message or "")


def test_retry_submission_creates_new_submission_row_and_logs_retry_action(db_session):
    seeded = _create_decision_session(
        db_session,
        first_candidate_status=CurationCandidateStatus.ACCEPTED,
    )
    session_row = db_session.get(ReviewSessionModel, UUID(seeded["session_id"]))
    assert session_row is not None
    session_row.adapter_key = REFERENCE_ADAPTER_KEY
    db_session.add(session_row)
    db_session.flush()

    original_submission = SubmissionModel(
        id=uuid4(),
        session_id=session_row.id,
        adapter_key=REFERENCE_ADAPTER_KEY,
        mode=SubmissionMode.DIRECT_SUBMIT,
        target_key=DEFAULT_JSON_BUNDLE_TARGET_KEY,
        status=CurationSubmissionStatus.FAILED,
        readiness=[
            {
                "candidate_id": seeded["first_candidate_id"],
                "ready": True,
                "blocking_reasons": [],
                "warnings": [],
            }
        ],
        payload=module._serialize_submission_payload_contract(
            SubmissionPayloadContract(
                mode=SubmissionMode.DIRECT_SUBMIT,
                target_key=DEFAULT_JSON_BUNDLE_TARGET_KEY,
                adapter_key=REFERENCE_ADAPTER_KEY,
                candidate_ids=[seeded["first_candidate_id"]],
                payload_json={"candidate_count": 1},
                payload_text='{"candidate_count": 1}',
                content_type="application/json",
                filename="submission.json",
            )
        ),
        response_message="Initial submit failed.",
        requested_at=_now(),
        completed_at=_now(),
    )
    db_session.add(original_submission)
    db_session.commit()

    response = module.retry_submission(
        db_session,
        seeded["session_id"],
        str(original_submission.id),
        CurationSubmissionRetryRequest(
            submission_id=str(original_submission.id),
            reason="Retry after transient downstream failure.",
        ),
        actor_claims={"sub": "user-1", "email": "user-1@example.org"},
    )

    assert response.submission.submission_id != str(original_submission.id)
    assert response.submission.status == CurationSubmissionStatus.ACCEPTED
    assert response.submission.adapter_key == REFERENCE_ADAPTER_KEY
    assert response.submission.target_key == DEFAULT_JSON_BUNDLE_TARGET_KEY
    assert response.submission.external_reference == f"noop:{DEFAULT_JSON_BUNDLE_TARGET_KEY}:1"
    assert response.submission.payload is not None
    assert response.submission.payload.candidate_ids == [seeded["first_candidate_id"]]
    assert response.action_log_entry.action_type == CurationActionType.SUBMISSION_RETRIED
    assert response.action_log_entry.new_session_status == CurationSessionStatus.SUBMITTED
    assert response.action_log_entry.metadata["original_submission_id"] == str(original_submission.id)
    assert response.action_log_entry.metadata["retry_reason"] == (
        "Retry after transient downstream failure."
    )

    persisted_submissions = db_session.scalars(
        select(SubmissionModel)
        .where(SubmissionModel.session_id == UUID(seeded["session_id"]))
        .order_by(SubmissionModel.requested_at.asc(), SubmissionModel.id.asc())
    ).all()
    assert len(persisted_submissions) == 2
    assert persisted_submissions[0].id == original_submission.id
    assert persisted_submissions[0].status == CurationSubmissionStatus.FAILED
    assert persisted_submissions[1].status == CurationSubmissionStatus.ACCEPTED
    assert persisted_submissions[1].adapter_key == REFERENCE_ADAPTER_KEY

    action_log_rows = db_session.scalars(
        select(SessionActionLogModel)
        .where(SessionActionLogModel.session_id == UUID(seeded["session_id"]))
        .where(SessionActionLogModel.action_type == CurationActionType.SUBMISSION_RETRIED)
    ).all()
    assert len(action_log_rows) == 1
    assert action_log_rows[0].action_metadata["original_submission_id"] == str(original_submission.id)


def test_retry_submission_rejects_non_failed_original_submission(db_session):
    seeded = _create_decision_session(
        db_session,
        first_candidate_status=CurationCandidateStatus.ACCEPTED,
    )
    session_row = db_session.get(ReviewSessionModel, UUID(seeded["session_id"]))
    assert session_row is not None
    session_row.adapter_key = REFERENCE_ADAPTER_KEY
    db_session.add(session_row)
    db_session.flush()

    original_submission = SubmissionModel(
        id=uuid4(),
        session_id=session_row.id,
        adapter_key=REFERENCE_ADAPTER_KEY,
        mode=SubmissionMode.DIRECT_SUBMIT,
        target_key=DEFAULT_JSON_BUNDLE_TARGET_KEY,
        status=CurationSubmissionStatus.ACCEPTED,
        readiness=[
            {
                "candidate_id": seeded["first_candidate_id"],
                "ready": True,
                "blocking_reasons": [],
                "warnings": [],
            }
        ],
        payload=module._serialize_submission_payload_contract(
            SubmissionPayloadContract(
                mode=SubmissionMode.DIRECT_SUBMIT,
                target_key=DEFAULT_JSON_BUNDLE_TARGET_KEY,
                adapter_key=REFERENCE_ADAPTER_KEY,
                candidate_ids=[seeded["first_candidate_id"]],
                payload_json={"candidate_count": 1},
                payload_text='{"candidate_count": 1}',
                content_type="application/json",
                filename="submission.json",
            )
        ),
        response_message="Initial submit succeeded.",
        requested_at=_now(),
        completed_at=_now(),
    )
    db_session.add(original_submission)
    db_session.commit()

    with pytest.raises(module.HTTPException) as exc:
        module.retry_submission(
            db_session,
            seeded["session_id"],
            str(original_submission.id),
            CurationSubmissionRetryRequest(
                submission_id=str(original_submission.id),
                reason="Retry should be rejected for accepted submissions.",
            ),
            actor_claims={"sub": "user-1", "email": "user-1@example.org"},
        )

    assert exc.value.status_code == 400
    assert exc.value.detail == "Only failed submissions may be retried"

    persisted_submissions = db_session.scalars(
        select(SubmissionModel)
        .where(SubmissionModel.session_id == UUID(seeded["session_id"]))
        .order_by(SubmissionModel.requested_at.asc(), SubmissionModel.id.asc())
    ).all()
    assert len(persisted_submissions) == 1
    assert persisted_submissions[0].id == original_submission.id
    assert persisted_submissions[0].status == CurationSubmissionStatus.ACCEPTED

    action_log_rows = db_session.scalars(
        select(SessionActionLogModel)
        .where(SessionActionLogModel.session_id == UUID(seeded["session_id"]))
        .where(SessionActionLogModel.action_type == CurationActionType.SUBMISSION_RETRIED)
    ).all()
    assert action_log_rows == []


def test_get_submission_returns_single_submission_history_record(db_session):
    seeded = _create_decision_session(
        db_session,
        first_candidate_status=CurationCandidateStatus.ACCEPTED,
    )
    submission_row = SubmissionModel(
        id=uuid4(),
        session_id=UUID(seeded["session_id"]),
        adapter_key="reference_adapter",
        mode=SubmissionMode.PREVIEW,
        target_key="reference_adapter.default",
        status=CurationSubmissionStatus.PREVIEW_READY,
        readiness=[
            {
                "candidate_id": seeded["first_candidate_id"],
                "ready": True,
                "blocking_reasons": [],
                "warnings": [],
            }
        ],
        payload={"ok": True},
        response_message="Preview available.",
        requested_at=_now(),
        completed_at=_now(),
    )
    db_session.add(submission_row)
    db_session.commit()

    response = module.get_submission(
        db_session,
        seeded["session_id"],
        str(submission_row.id),
    )

    assert response.submission.submission_id == str(submission_row.id)
    assert response.submission.session_id == seeded["session_id"]
    assert response.submission.status == CurationSubmissionStatus.PREVIEW_READY
    assert response.submission.payload is not None
    assert response.submission.payload.payload_json == {"ok": True}
    assert response.submission.response_message == "Preview available."

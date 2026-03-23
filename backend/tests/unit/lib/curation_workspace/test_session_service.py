"""Unit tests for curation workspace session-service helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

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
    CurationCandidateSource,
    CurationCandidateDecisionRequest,
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
    domain_key: str,
    origin_session_id: str = "chat-session-1",
    flow_run_id: str = "flow-1",
    candidate_count: int = 1,
    metadata: dict | None = None,
) -> ExtractionResultModel:
    record = ExtractionResultModel(
        id=uuid4(),
        document_id=UUID(document_id),
        adapter_key="reference_adapter",
        profile_key="primary",
        domain_key=domain_key,
        agent_key="curation_prep",
        source_kind=CurationExtractionSourceKind.CHAT,
        origin_session_id=origin_session_id,
        trace_id=f"trace-{domain_key}",
        flow_run_id=flow_run_id,
        user_id="user-1",
        candidate_count=candidate_count,
        conversation_summary=f"Prep summary for {domain_key}.",
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
        extraction_metadata=metadata or {"final_run_metadata": {"model_name": "gpt-5-mini"}},
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
        profile_key="primary",
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
                    "pdfx_markdown_offset_start": None,
                    "pdfx_markdown_offset_end": None,
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
        domain_key="alpha",
    )
    extraction_beta = _create_extraction_result(
        db_session,
        document_id=document_id,
        domain_key="beta",
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
        profile_key="primary",
        flow_run_id="flow-1",
        prep_extraction_result_id=str(extraction_beta.id),
    )

    assert reusable is None

    matching = module.find_reusable_prepared_session(
        db_session,
        document_id=document_id,
        adapter_key="reference_adapter",
        profile_key="primary",
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
        domain_key="alpha",
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
    assert response.candidate.profile_key == "primary"
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
        domain_key="alpha",
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


def test_get_session_detail_exposes_adapter_template_fields_for_zero_candidate_sessions(db_session):
    document = _create_document(db_session)
    extraction_result = _create_extraction_result(
        db_session,
        document_id=str(document.id),
        domain_key="alpha",
        candidate_count=0,
        metadata={
            "final_run_metadata": {"model_name": "gpt-5-mini"},
            "adapter_metadata": [
                {
                    "adapter_key": "reference_adapter",
                    "profile_key": "primary",
                    "required_field_keys": ["field_a", "field_b"],
                    "field_hints": [
                        {
                            "field_key": "field_a",
                            "required": True,
                            "label": "Field A",
                            "value_type": "string",
                            "description": "Primary field",
                            "controlled_vocabulary": ["alpha"],
                            "normalization_hints": ["Use canonical value."],
                        }
                    ],
                    "notes": ["Adapter-owned field hints persisted from prep."],
                }
            ],
        },
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
    assert response.adapter.metadata["manual_template_source"] == "prep_adapter_metadata"
    assert response.adapter.metadata["manual_draft_fields"] == [
        {
            "field_key": "field_a",
            "label": "Field A",
            "value": None,
            "seed_value": None,
            "field_type": "string",
            "group_key": None,
            "group_label": None,
            "order": 0,
            "required": True,
            "read_only": False,
            "dirty": False,
            "stale_validation": False,
            "evidence_anchor_ids": [],
            "metadata": {
                "description": "Primary field",
                "controlled_vocabulary": ["alpha"],
                "normalization_hints": ["Use canonical value."],
            },
        },
        {
            "field_key": "field_b",
            "label": "Field B",
            "value": None,
            "seed_value": None,
            "field_type": None,
            "group_key": None,
            "group_label": None,
            "order": 1,
            "required": True,
            "read_only": False,
            "dirty": False,
            "stale_validation": False,
            "evidence_anchor_ids": [],
            "metadata": {},
        },
    ]


def test_list_sessions_filters_by_origin_session_id_via_candidate_extractions(db_session):
    document = _create_document(db_session)
    document_id = str(document.id)
    extraction_chat_one = _create_extraction_result(
        db_session,
        document_id=document_id,
        domain_key="alpha",
        origin_session_id="chat-session-1",
    )
    extraction_chat_two = _create_extraction_result(
        db_session,
        document_id=document_id,
        domain_key="beta",
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

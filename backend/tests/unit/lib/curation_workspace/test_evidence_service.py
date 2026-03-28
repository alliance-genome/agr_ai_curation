"""Unit tests for curation workspace evidence write services."""

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

from src.lib.curation_workspace import evidence_service as module
from src.lib.curation_workspace.session_service import PreparedEvidenceRecordInput
from src.lib.curation_workspace.models import (
    CurationActionLogEntry as SessionActionLogModel,
    CurationCandidate as CandidateModel,
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
    CurationCandidateSource,
    CurationCandidateStatus,
    CurationEvidenceRecomputeRequest,
    CurationEvidenceResolveRequest,
    CurationEvidenceSource,
    CurationExtractionSourceKind,
    CurationManualEvidenceCreateRequest,
    CurationSessionStatus,
    EvidenceAnchor,
    EvidenceAnchorKind,
    EvidenceLocatorQuality,
    EvidenceSupportsDecision,
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
    CandidateModel.__table__,
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


def _anchor(
    *,
    snippet_text: str = "Example quote.",
    locator_quality: EvidenceLocatorQuality = EvidenceLocatorQuality.EXACT_QUOTE,
    page_number: int | None = 2,
) -> EvidenceAnchor:
    return EvidenceAnchor(
        anchor_kind=EvidenceAnchorKind.SNIPPET,
        locator_quality=locator_quality,
        supports_decision=EvidenceSupportsDecision.SUPPORTS,
        snippet_text=snippet_text,
        sentence_text=snippet_text,
        normalized_text=None,
        viewer_search_text=None,
        pdfx_markdown_offset_start=None,
        pdfx_markdown_offset_end=None,
        page_number=page_number,
        page_label=None,
        section_title="Results" if page_number is not None else None,
        subsection_title=None,
        figure_reference=None,
        table_reference=None,
        chunk_ids=[],
    )


def _create_document(db_session) -> PDFDocument:
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
    document_id: UUID,
    user_id: str = "owner-1",
) -> ExtractionResultModel:
    record = ExtractionResultModel(
        id=uuid4(),
        document_id=document_id,
        adapter_key="reference_adapter",
        profile_key="primary",
        domain_key="gene",
        agent_key="curation_prep",
        source_kind=CurationExtractionSourceKind.CHAT,
        origin_session_id="chat-session-1",
        trace_id="trace-1",
        flow_run_id="flow-1",
        user_id=user_id,
        candidate_count=1,
        conversation_summary="Prep summary.",
        payload_json={"candidates": []},
        extraction_metadata={},
        created_at=_now(),
    )
    db_session.add(record)
    db_session.commit()
    return record


def _create_session(
    db_session,
    *,
    document_id: UUID,
    total_candidates: int,
) -> ReviewSessionModel:
    now = _now()
    session = ReviewSessionModel(
        id=uuid4(),
        status=CurationSessionStatus.IN_PROGRESS,
        adapter_key="reference_adapter",
        profile_key="primary",
        document_id=document_id,
        flow_run_id="flow-1",
        session_version=1,
        notes="Evidence review in progress.",
        tags=[],
        total_candidates=total_candidates,
        reviewed_candidates=0,
        pending_candidates=total_candidates,
        accepted_candidates=0,
        rejected_candidates=0,
        manual_candidates=0,
        warnings=[],
        prepared_at=now,
        created_at=now,
        updated_at=now,
    )
    db_session.add(session)
    db_session.commit()
    return session


def _create_candidate(
    db_session,
    *,
    session: ReviewSessionModel,
    extraction_result_id: UUID | None,
    source: CurationCandidateSource,
    order: int,
    field_key: str = "gene.symbol",
) -> tuple[CandidateModel, DraftModel]:
    now = _now()
    candidate = CandidateModel(
        id=uuid4(),
        session_id=session.id,
        source=source,
        status=CurationCandidateStatus.PENDING,
        order=order,
        adapter_key="reference_adapter",
        profile_key="primary",
        display_label=f"Candidate {order + 1}",
        secondary_label=None,
        conversation_summary="Candidate summary.",
        extraction_result_id=extraction_result_id,
        normalized_payload={"gene": {"symbol": f"GENE{order + 1}"}},
        candidate_metadata={},
        created_at=now,
        updated_at=now,
    )
    db_session.add(candidate)
    db_session.flush()

    draft = DraftModel(
        id=uuid4(),
        candidate_id=candidate.id,
        adapter_key=candidate.adapter_key,
        version=1,
        title=f"Draft {order + 1}",
        summary=None,
        fields=[
            {
                "field_key": field_key,
                "label": "Gene Symbol",
                "value": f"GENE{order + 1}",
                "seed_value": f"GENE{order + 1}",
                "field_type": "string",
                "group_key": "gene",
                "group_label": "Gene",
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
    db_session.add(draft)
    db_session.commit()
    return candidate, draft


def _add_evidence(
    db_session,
    *,
    candidate: CandidateModel,
    draft: DraftModel,
    source: CurationEvidenceSource,
    field_key: str = "gene.symbol",
    is_primary: bool = True,
    anchor: EvidenceAnchor | None = None,
) -> EvidenceRecordModel:
    now = _now()
    evidence = EvidenceRecordModel(
        id=uuid4(),
        candidate_id=candidate.id,
        source=source,
        field_keys=[field_key],
        field_group_keys=[],
        is_primary=is_primary,
        anchor=(anchor or _anchor()).model_dump(mode="json"),
        warnings=[],
        created_at=now,
        updated_at=now,
    )
    db_session.add(evidence)
    db_session.flush()

    draft.fields = [
        {
            **field_payload,
            "evidence_anchor_ids": [str(evidence.id)]
            if field_payload.get("field_key") == field_key
            else list(field_payload.get("evidence_anchor_ids") or []),
        }
        for field_payload in draft.fields
    ]
    draft.updated_at = now
    db_session.add(draft)
    db_session.commit()
    return evidence


def test_create_manual_evidence_persists_record_updates_draft_and_logs_action(db_session):
    document = _create_document(db_session)
    extraction_result = _create_extraction_result(db_session, document_id=document.id)
    session = _create_session(db_session, document_id=document.id, total_candidates=1)
    candidate, _draft = _create_candidate(
        db_session,
        session=session,
        extraction_result_id=extraction_result.id,
        source=CurationCandidateSource.EXTRACTED,
        order=0,
    )

    response = module.create_manual_evidence(
        CurationManualEvidenceCreateRequest(
            session_id=str(session.id),
            candidate_id=str(candidate.id),
            field_keys=["gene.symbol"],
            field_group_keys=["gene"],
            anchor=_anchor(snippet_text="Manual curator quote."),
            is_primary=True,
        ),
        actor_claims={"sub": "user-1", "email": "user-1@example.org"},
        db=db_session,
    )

    assert response.evidence_record.source is CurationEvidenceSource.MANUAL
    assert response.candidate.candidate_id == str(candidate.id)
    assert response.candidate.evidence_summary is not None
    assert response.candidate.evidence_summary.total_anchor_count == 1
    assert response.candidate.draft.fields[0].evidence_anchor_ids == [
        response.evidence_record.anchor_id
    ]
    assert response.action_log_entry.action_type is CurationActionType.EVIDENCE_MANUAL_ADDED
    assert response.action_log_entry.evidence_anchor_ids == [response.evidence_record.anchor_id]

    stored_evidence = db_session.scalars(select(EvidenceRecordModel)).one()
    assert stored_evidence.source is CurationEvidenceSource.MANUAL
    stored_action = db_session.scalars(select(SessionActionLogModel)).one()
    assert stored_action.action_type is CurationActionType.EVIDENCE_MANUAL_ADDED


def test_resolve_evidence_replaces_matching_record_in_place(db_session, monkeypatch):
    document = _create_document(db_session)
    extraction_result = _create_extraction_result(db_session, document_id=document.id)
    session = _create_session(db_session, document_id=document.id, total_candidates=1)
    candidate, draft = _create_candidate(
        db_session,
        session=session,
        extraction_result_id=extraction_result.id,
        source=CurationCandidateSource.EXTRACTED,
        order=0,
    )
    existing_evidence = _add_evidence(
        db_session,
        candidate=candidate,
        draft=draft,
        source=CurationEvidenceSource.EXTRACTED,
        anchor=_anchor(snippet_text="Original extracted quote."),
    )

    monkeypatch.setattr(
        module,
        "_resolve_anchor_against_document",
        lambda *_args, **_kwargs: (
            _anchor(
                snippet_text="Resolved replacement quote.",
                locator_quality=EvidenceLocatorQuality.NORMALIZED_QUOTE,
            ),
            ["resolved against refreshed PDFX markdown"],
        ),
    )

    response = module.resolve_evidence(
        CurationEvidenceResolveRequest(
            session_id=str(session.id),
            candidate_id=str(candidate.id),
            field_key="gene.symbol",
            anchor=_anchor(snippet_text="Resolve me."),
            replace_existing=True,
        ),
        current_user_id="curator-1",
        db=db_session,
    )

    assert response.evidence_record.anchor_id == str(existing_evidence.id)
    assert response.evidence_record.source is CurationEvidenceSource.RECOMPUTED
    assert response.evidence_record.anchor.locator_quality is EvidenceLocatorQuality.NORMALIZED_QUOTE
    assert response.evidence_record.warnings == ["resolved against refreshed PDFX markdown"]
    assert response.candidate.draft.fields[0].evidence_anchor_ids == [str(existing_evidence.id)]
    assert db_session.scalars(select(SessionActionLogModel)).all() == []

    stored_evidence = db_session.get(EvidenceRecordModel, existing_evidence.id)
    assert stored_evidence is not None
    assert stored_evidence.source is CurationEvidenceSource.RECOMPUTED


def test_resolve_anchor_against_document_uses_public_resolver_surface(db_session, monkeypatch):
    document = _create_document(db_session)
    extraction_result = _create_extraction_result(db_session, document_id=document.id)

    monkeypatch.setattr(
        "src.lib.curation_workspace.evidence_resolver.DeterministicEvidenceAnchorResolver._safe_resolve_user_id",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("private resolver helper should not be called")
        ),
    )
    monkeypatch.setattr(
        "src.lib.curation_workspace.evidence_resolver.DeterministicEvidenceAnchorResolver._prepare_document",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("private resolver helper should not be called")
        ),
    )
    monkeypatch.setattr(
        "src.lib.curation_workspace.evidence_resolver.DeterministicEvidenceAnchorResolver._resolve_evidence_record",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("private resolver helper should not be called")
        ),
    )

    def _resolve(self, candidate, *, normalized_candidate, context):
        assert candidate.adapter_key == "reference_adapter"
        assert normalized_candidate.normalized_payload["gene"]["symbol"] == "Example quote."
        assert context.document_id == str(document.id)
        assert context.prep_extraction_result_id == str(extraction_result.id)
        return [
            PreparedEvidenceRecordInput(
                source=CurationEvidenceSource.RECOMPUTED,
                field_keys=["gene.symbol"],
                anchor=_anchor(
                    snippet_text="Resolved via public resolver API.",
                    locator_quality=EvidenceLocatorQuality.NORMALIZED_QUOTE,
                ).model_dump(mode="json"),
                warnings=["resolved via public resolver API"],
            )
        ]

    monkeypatch.setattr(
        "src.lib.curation_workspace.evidence_resolver.DeterministicEvidenceAnchorResolver.resolve",
        _resolve,
    )

    resolved_anchor, warnings = module._resolve_anchor_against_document(
        db_session,
        document_id=str(document.id),
        anchor=_anchor(snippet_text="Example quote."),
        adapter_key="reference_adapter",
        profile_key="primary",
        field_path="gene.symbol",
        current_user_id="curator-1",
        prep_extraction_result_id=str(extraction_result.id),
    )

    assert resolved_anchor.locator_quality is EvidenceLocatorQuality.NORMALIZED_QUOTE
    assert resolved_anchor.snippet_text == "Resolved via public resolver API."
    assert warnings == ["resolved via public resolver API"]


def test_recompute_evidence_updates_all_selected_records_including_manual(
    db_session,
    monkeypatch,
):
    document = _create_document(db_session)
    extraction_result = _create_extraction_result(db_session, document_id=document.id)
    session = _create_session(db_session, document_id=document.id, total_candidates=2)
    extracted_candidate, extracted_draft = _create_candidate(
        db_session,
        session=session,
        extraction_result_id=extraction_result.id,
        source=CurationCandidateSource.EXTRACTED,
        order=0,
    )
    manual_candidate, manual_draft = _create_candidate(
        db_session,
        session=session,
        extraction_result_id=None,
        source=CurationCandidateSource.MANUAL,
        order=1,
    )
    extracted_evidence = _add_evidence(
        db_session,
        candidate=extracted_candidate,
        draft=extracted_draft,
        source=CurationEvidenceSource.EXTRACTED,
        anchor=_anchor(snippet_text="Extracted quote."),
    )
    manual_evidence = _add_evidence(
        db_session,
        candidate=manual_candidate,
        draft=manual_draft,
        source=CurationEvidenceSource.MANUAL,
        anchor=_anchor(snippet_text="Manual quote."),
    )

    monkeypatch.setattr(
        module,
        "_resolve_anchor_against_document",
        lambda *_args, **_kwargs: (
            _anchor(
                snippet_text="Recomputed quote.",
                locator_quality=EvidenceLocatorQuality.NORMALIZED_QUOTE,
            ),
            ["recomputed"],
        ),
    )

    response = module.recompute_evidence(
        CurationEvidenceRecomputeRequest(
            session_id=str(session.id),
            candidate_ids=[str(extracted_candidate.id), str(manual_candidate.id)],
            force=False,
        ),
        current_user_id="curator-1",
        actor_claims={"sub": "curator-1", "email": "curator-1@example.org"},
        db=db_session,
    )

    assert [record.anchor_id for record in response.updated_evidence_records] == [
        str(extracted_evidence.id),
        str(manual_evidence.id),
    ]
    assert all(
        record.source is CurationEvidenceSource.RECOMPUTED
        for record in response.updated_evidence_records
    )
    assert response.action_log_entry.action_type is CurationActionType.EVIDENCE_RECOMPUTED
    assert response.action_log_entry.metadata["updated_count"] == 2

    refreshed_extracted = db_session.get(EvidenceRecordModel, extracted_evidence.id)
    refreshed_manual = db_session.get(EvidenceRecordModel, manual_evidence.id)
    assert refreshed_extracted is not None
    assert refreshed_manual is not None
    assert refreshed_extracted.source is CurationEvidenceSource.RECOMPUTED
    assert refreshed_manual.source is CurationEvidenceSource.RECOMPUTED

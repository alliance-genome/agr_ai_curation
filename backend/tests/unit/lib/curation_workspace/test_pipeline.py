"""Unit tests for the deterministic post-agent curation pipeline."""

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

from src.lib.curation_adapters.reference import (
    REFERENCE_ADAPTER_KEY,
    REFERENCE_VALIDATION_PLAN_KEY,
)
from src.lib.curation_adapters.structured_payload import (
    StructuredPayloadCandidateNormalizer,
)
from src.lib.curation_workspace import pipeline as module
from src.lib.curation_workspace import session_service
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
from src.schemas.curation_prep import CurationPrepAgentOutput, CurationPrepCandidate
from src.schemas.curation_workspace import (
    CurationCandidateSource,
    CurationCandidateStatus,
    CurationEvidenceSource,
    CurationExtractionSourceKind,
    CurationActionType,
    CurationSessionStatus,
    CurationValidationCounts,
    CurationValidationScope,
    CurationValidationSnapshotState,
    CurationValidationSummary,
    FieldValidationStatus,
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
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)
    session = SessionLocal()

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


def _make_prep_output(*, candidate_count: int = 1) -> CurationPrepAgentOutput:
    candidates = []
    for index in range(candidate_count):
        gene_symbol = f"GENE{index + 1}"
        disease_label = f"Disease {index + 1}"
        candidates.append(
            {
                "adapter_key": "disease",
                "payload": {
                    "gene": {"symbol": gene_symbol},
                    "condition": {"label": disease_label},
                    "evidence": {"score": 0.8 + (index * 0.05)},
                },
                "evidence_records": [
                    {
                        "evidence_record_id": f"evidence-{index + 1}",
                        "source": "extracted",
                        "extraction_result_id": "prep-extract",
                        "field_paths": ["gene.symbol"],
                        "anchor": {
                            "anchor_kind": "snippet",
                            "locator_quality": "exact_quote",
                            "supports_decision": "supports",
                            "snippet_text": f"{gene_symbol} was linked to the reported phenotype.",
                            "sentence_text": f"{gene_symbol} was linked to the reported phenotype.",
                            "normalized_text": None,
                            "viewer_search_text": f"{gene_symbol} was linked to the reported phenotype.",
                            "page_number": 3,
                            "page_label": None,
                            "section_title": "Results",
                            "subsection_title": "Association",
                            "figure_reference": None,
                            "table_reference": None,
                            "chunk_ids": [f"chunk-{index + 1}"],
                        },
                        "notes": ["The paper names the gene directly in the finding."],
                    }
                ],
                "conversation_context_summary": f"Conversation narrowed to {gene_symbol}.",
            }
        )

    return CurationPrepAgentOutput.model_validate(
        {
            "candidates": candidates,
            "run_metadata": {
                "model_name": "gpt-5.4-nano",
                "token_usage": {
                    "input_tokens": 120,
                    "output_tokens": 45,
                    "total_tokens": 165,
                },
                "processing_notes": ["Prep agent completed candidate extraction."],
                "warnings": ["Prep run warning."],
            },
        }
    )


def _persist_matching_prep_result(
    db_session,
    *,
    document_id: str,
    prep_output: CurationPrepAgentOutput,
    adapter_key: str = "disease",
):
    now = _now()
    record = ExtractionResultModel(
        id=uuid4(),
        document_id=UUID(str(document_id)),
        adapter_key=adapter_key,
        agent_key="curation_prep",
        source_kind=CurationExtractionSourceKind.CHAT,
        origin_session_id="chat-session-1",
        trace_id="trace-1",
        flow_run_id="flow-1",
        user_id="user-1",
        candidate_count=len(prep_output.candidates),
        conversation_summary="Prep conversation summary.",
        payload_json={
            "candidates": prep_output.model_dump(mode="json")["candidates"],
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
        extraction_metadata={
            "final_run_metadata": prep_output.run_metadata.model_dump(mode="json"),
        },
        created_at=now,
    )
    db_session.add(record)
    db_session.commit()
    return record


def _make_request(prep_output: CurationPrepAgentOutput, *, document_id: str, review_session_id: str | None = None):
    return module.PostCurationPipelineRequest(
        prep_output=prep_output,
        document_id=document_id,
        source_kind=CurationExtractionSourceKind.CHAT,
        adapter_key="disease",
        flow_run_id="flow-1",
        origin_session_id="chat-session-1",
        trace_id="trace-1",
        user_id="user-1",
        notes="Pipeline bootstrap.",
        tags=("wave-5",),
        prepared_at=_now(),
        review_session_id=review_session_id,
    )


def _make_reference_prep_output() -> CurationPrepAgentOutput:
    return CurationPrepAgentOutput.model_validate(
        {
            "candidates": [
                {
                    "adapter_key": REFERENCE_ADAPTER_KEY,
                    "payload": {
                        "citation": {
                            "title": "  Adapter-owned reference scaffold in practice  ",
                            "authors": ["Ada Lovelace", " Grace Hopper "],
                            "journal": "  Journal of Adapter Boundaries  ",
                            "publication_year": "2025",
                        },
                        "identifiers": {
                            "doi": " DOI:10.1000/Reference-1 ",
                        },
                    },
                    "evidence_records": [
                        {
                            "evidence_record_id": "reference-evidence-title",
                            "source": "extracted",
                            "extraction_result_id": "prep-extract-reference",
                            "field_paths": ["citation.title"],
                            "anchor": {
                                "anchor_kind": "snippet",
                                "locator_quality": "exact_quote",
                                "supports_decision": "supports",
                                "snippet_text": "Adapter-owned reference scaffold in practice",
                                "sentence_text": "Adapter-owned reference scaffold in practice",
                                "normalized_text": None,
                                "viewer_search_text": "Adapter-owned reference scaffold in practice",
                                "page_number": 2,
                                "page_label": None,
                                "section_title": "Results",
                                "subsection_title": None,
                                "figure_reference": None,
                                "table_reference": None,
                                "chunk_ids": ["chunk-reference-title"],
                            },
                            "notes": ["The title is quoted directly from the manuscript."],
                        },
                        {
                            "evidence_record_id": "reference-evidence-doi",
                            "source": "extracted",
                            "extraction_result_id": "prep-extract-reference",
                            "field_paths": ["identifiers.doi"],
                            "anchor": {
                                "anchor_kind": "snippet",
                                "locator_quality": "exact_quote",
                                "supports_decision": "supports",
                                "snippet_text": "doi:10.1000/Reference-1",
                                "sentence_text": "doi:10.1000/Reference-1",
                                "normalized_text": None,
                                "viewer_search_text": "doi:10.1000/Reference-1",
                                "page_number": 4,
                                "page_label": None,
                                "section_title": "References",
                                "subsection_title": None,
                                "figure_reference": None,
                                "table_reference": None,
                                "chunk_ids": ["chunk-reference-doi"],
                            },
                            "notes": ["The DOI is present in the reference block."],
                        },
                    ],
                    "conversation_context_summary": (
                        "Conversation narrowed the workspace to a single supporting reference."
                    ),
                }
            ],
            "run_metadata": {
                "model_name": "gpt-5.4-nano",
                "token_usage": {
                    "input_tokens": 90,
                    "output_tokens": 35,
                    "total_tokens": 125,
                },
                "processing_notes": ["Reference candidate normalized by the adapter scaffold."],
                "warnings": [],
            },
        }
    )


def _make_reference_request(prep_output: CurationPrepAgentOutput, *, document_id: str):
    return module.PostCurationPipelineRequest(
        prep_output=prep_output,
        document_id=document_id,
        source_kind=CurationExtractionSourceKind.CHAT,
        adapter_key=REFERENCE_ADAPTER_KEY,
        flow_run_id="flow-1",
        origin_session_id="chat-session-1",
        trace_id="trace-1",
        user_id="user-1",
        notes="Reference adapter pipeline bootstrap.",
        tags=("wave-11",),
        prepared_at=_now(),
    )


def _passthrough_dependencies() -> module.PostCurationPipelineDependencies:
    return module.PostCurationPipelineDependencies(
        evidence_resolver=module.PassthroughEvidenceAnchorResolver(),
    )


def test_default_evidence_resolver_resolves_against_document():
    resolver = module._default_evidence_resolver()

    assert isinstance(resolver, module.DeterministicEvidenceAnchorResolver)
    assert resolver._resolve_against_document is True


def test_default_pipeline_dependencies_enable_document_backed_evidence_resolution():
    dependencies = module.PostCurationPipelineDependencies()

    assert isinstance(dependencies.evidence_resolver, module.DeterministicEvidenceAnchorResolver)
    assert dependencies.evidence_resolver._resolve_against_document is True


def test_execute_post_curation_pipeline_default_dependencies_use_document_resolver_with_current_user(
    db_session,
    monkeypatch,
):
    document = _create_document(db_session)
    prep_output = _make_prep_output()
    _persist_matching_prep_result(
        db_session,
        document_id=str(document.id),
        prep_output=prep_output,
    )

    observed: dict[str, object] = {}

    class _SpyResolver:
        def __init__(self, *args, **kwargs):
            observed["kwargs"] = kwargs

        def resolve(self, candidate, *, normalized_candidate, context):
            observed["current_user_id"] = context.current_user_id
            source_record = candidate.evidence_records[0]
            return [
                module.PreparedEvidenceRecordInput(
                    source=CurationEvidenceSource.EXTRACTED,
                    field_keys=list(source_record.field_paths),
                    field_group_keys=[],
                    is_primary=True,
                    anchor=source_record.anchor.model_dump(mode="json"),
                    warnings=[],
                )
            ]

    monkeypatch.setattr(module, "DeterministicEvidenceAnchorResolver", _SpyResolver)

    result = module.execute_post_curation_pipeline(
        _make_request(prep_output, document_id=str(document.id)),
        db=db_session,
    )

    assert result.status is module.PipelineRunStatus.COMPLETED
    assert observed["kwargs"] == {"resolve_against_document": True}
    assert observed["current_user_id"] == "user-1"


def test_structured_payload_candidate_normalizer_builds_payload_and_draft_fields():
    prep_output = _make_prep_output()
    candidate: CurationPrepCandidate = prep_output.candidates[0]

    normalized = StructuredPayloadCandidateNormalizer().normalize(
        candidate.payload,
        prep_candidate=candidate,
        context=module.CandidateNormalizationContext(
            document_id=str(uuid4()),
            adapter_key="disease",
            prep_extraction_result_id="prep-result-1",
            candidate_index=0,
        ),
    )

    assert normalized.normalized_payload == {
        "gene": {"symbol": "GENE1"},
        "condition": {"label": "Disease 1"},
        "evidence": {"score": 0.8},
    }
    assert normalized.display_label == "GENE1"
    assert normalized.secondary_label == "Disease 1"
    assert normalized.draft_fields[0].field_key == "gene.symbol"
    assert normalized.draft_fields[0].label == "Gene / Symbol"


def test_execute_post_curation_pipeline_creates_session_candidates_and_validation(db_session):
    document = _create_document(db_session)
    prep_output = _make_prep_output()
    prep_record = _persist_matching_prep_result(
        db_session,
        document_id=str(document.id),
        prep_output=prep_output,
    )

    result = module.execute_post_curation_pipeline(
        _make_request(prep_output, document_id=str(document.id)),
        db=db_session,
        dependencies=_passthrough_dependencies(),
    )

    assert result.status is module.PipelineRunStatus.COMPLETED
    assert result.session_id is not None
    assert result.created is True
    assert result.prep_extraction_result_id == str(prep_record.id)

    session_row = db_session.scalars(
        select(ReviewSessionModel).where(ReviewSessionModel.id == UUID(result.session_id))
    ).one()
    assert session_row.status is CurationSessionStatus.NEW
    assert session_row.total_candidates == 1
    assert session_row.pending_candidates == 1
    assert session_row.current_candidate_id is not None
    assert session_row.warnings == [
        "Prep run warning.",
        "Deterministic structural validation completed; downstream adapter validation is pending.",
    ]

    candidate_row = db_session.scalars(
        select(CurationCandidate).where(CurationCandidate.session_id == session_row.id)
    ).one()
    assert candidate_row.source is CurationCandidateSource.EXTRACTED
    assert candidate_row.status is CurationCandidateStatus.PENDING
    assert candidate_row.extraction_result_id == prep_record.id
    assert candidate_row.normalized_payload["gene"]["symbol"] == "GENE1"
    assert candidate_row.candidate_metadata["prep_evidence_records"][0]["field_paths"] == ["gene.symbol"]
    assert candidate_row.candidate_metadata["evidence_summary"] == {
        "total_anchor_count": 1,
        "resolved_anchor_count": 1,
        "viewer_highlightable_anchor_count": 1,
        "quality_counts": {
            "exact_quote": 1,
            "normalized_quote": 0,
            "section_only": 0,
            "page_only": 0,
            "document_only": 0,
            "unresolved": 0,
        },
        "degraded": False,
        "warnings": [],
    }

    evidence_row = db_session.scalars(
        select(EvidenceRecordModel).where(EvidenceRecordModel.candidate_id == candidate_row.id)
    ).one()
    assert evidence_row.field_keys == ["gene.symbol"]
    assert evidence_row.anchor["chunk_ids"] == ["chunk-1"]
    assert evidence_row.anchor["viewer_highlightable"] is True

    draft_row = db_session.scalars(
        select(DraftModel).where(DraftModel.candidate_id == candidate_row.id)
    ).one()
    assert "normalized_payload" not in candidate_row.candidate_metadata
    assert draft_row.fields[0]["field_key"] == "gene.symbol"
    assert draft_row.fields[0]["validation_result"]["status"] == FieldValidationStatus.SKIPPED.value
    assert draft_row.fields[0]["evidence_anchor_ids"] == [str(evidence_row.id)]
    assert "normalized_payload" not in draft_row.draft_metadata

    session_snapshots = db_session.scalars(
        select(ValidationSnapshotModel).where(
            ValidationSnapshotModel.session_id == session_row.id,
            ValidationSnapshotModel.candidate_id.is_(None),
        )
    ).all()
    candidate_snapshots = db_session.scalars(
        select(ValidationSnapshotModel).where(
            ValidationSnapshotModel.session_id == session_row.id,
            ValidationSnapshotModel.candidate_id == candidate_row.id,
        )
    ).all()
    assert len(session_snapshots) == 1
    assert len(candidate_snapshots) == 1
    session_snapshot = session_snapshots[0]
    candidate_snapshot = candidate_snapshots[0]
    assert session_snapshot.state is CurationValidationSnapshotState.COMPLETED
    assert candidate_snapshot.state is CurationValidationSnapshotState.COMPLETED
    assert candidate_snapshot.summary["counts"]["skipped"] == 3

    action_log_entries = db_session.scalars(
        select(SessionActionLogModel).where(
            SessionActionLogModel.session_id == session_row.id,
        )
    ).all()
    assert len(action_log_entries) == 2
    assert {entry.action_type for entry in action_log_entries} == {
        CurationActionType.SESSION_CREATED,
        CurationActionType.VALIDATION_COMPLETED,
    }


def test_execute_post_curation_pipeline_registers_reference_adapter_and_persists_adapter_owned_layout(
    db_session,
):
    document = _create_document(db_session)
    prep_output = _make_reference_prep_output()
    prep_record = _persist_matching_prep_result(
        db_session,
        document_id=str(document.id),
        prep_output=prep_output,
        adapter_key=REFERENCE_ADAPTER_KEY,
    )

    result = module.execute_post_curation_pipeline(
        _make_reference_request(prep_output, document_id=str(document.id)),
        db=db_session,
        dependencies=module.PostCurationPipelineDependencies(
            evidence_resolver=module.PassthroughEvidenceAnchorResolver(),
        ),
    )

    assert result.status is module.PipelineRunStatus.COMPLETED
    assert result.prep_extraction_result_id == str(prep_record.id)

    draft_row = db_session.scalars(select(DraftModel)).one()
    assert [field["field_key"] for field in draft_row.fields] == [
        "citation.title",
        "citation.authors",
        "citation.journal",
        "citation.publication_year",
        "citation.reference_type",
        "identifiers.doi",
        "identifiers.pmid",
    ]
    assert [field["group_key"] for field in draft_row.fields[:5]] == [
        "citation_details",
        "citation_details",
        "citation_details",
        "citation_details",
        "citation_details",
    ]
    assert draft_row.fields[1]["value"] == ["Ada Lovelace", "Grace Hopper"]
    assert draft_row.fields[1]["metadata"]["widget"] == "reference_author_list"
    assert draft_row.fields[1]["metadata"]["validation"]["plan_key"] == REFERENCE_VALIDATION_PLAN_KEY
    assert draft_row.fields[4]["value"] == "journal_article"
    assert draft_row.fields[4]["metadata"]["default_applied"] is True
    assert draft_row.fields[5]["value"] == "10.1000/reference-1"
    assert draft_row.fields[5]["evidence_anchor_ids"]

    candidate_row = db_session.scalars(select(CurationCandidate)).one()
    assert candidate_row.normalized_payload == {
        "citation": {
            "title": "Adapter-owned reference scaffold in practice",
            "authors": ["Ada Lovelace", "Grace Hopper"],
            "journal": "Journal of Adapter Boundaries",
            "publication_year": 2025,
            "reference_type": "journal_article",
        },
        "identifiers": {
            "doi": "10.1000/reference-1",
            "pmid": None,
        },
    }
    assert candidate_row.candidate_metadata["reference_adapter"]["adapter_key"] == REFERENCE_ADAPTER_KEY

    workspace = session_service.get_session_workspace(db_session, result.session_id)
    assert workspace.workspace.session.adapter.adapter_key == REFERENCE_ADAPTER_KEY
    assert workspace.workspace.session.adapter.display_label == "Reference Adapter"
    assert workspace.workspace.entity_tags == []


def test_execute_post_curation_pipeline_updates_existing_unreviewed_session(db_session):
    document = _create_document(db_session)
    prep_output = _make_prep_output()
    prep_record = _persist_matching_prep_result(
        db_session,
        document_id=str(document.id),
        prep_output=prep_output,
    )
    now = _now()

    existing_session = ReviewSessionModel(
        id=uuid4(),
        document_id=document.id,
        adapter_key="disease",
        profile_key="primary",
        status=CurationSessionStatus.NEW,
        session_version=1,
        total_candidates=1,
        reviewed_candidates=0,
        pending_candidates=1,
        accepted_candidates=0,
        rejected_candidates=0,
        manual_candidates=0,
        prepared_at=now,
        created_at=now,
        updated_at=now,
    )
    db_session.add(existing_session)
    db_session.flush()

    old_candidate = CurationCandidate(
        id=uuid4(),
        session_id=existing_session.id,
        source=CurationCandidateSource.EXTRACTED,
        status=CurationCandidateStatus.PENDING,
        order=0,
        adapter_key="disease",
        profile_key="primary",
        display_label="Old candidate",
        conversation_summary="Old candidate",
        extraction_result_id=prep_record.id,
        normalized_payload={"old": True},
        candidate_metadata={},
        created_at=now,
        updated_at=now,
    )
    db_session.add(old_candidate)
    db_session.flush()

    db_session.add(
        DraftModel(
            id=uuid4(),
            candidate_id=old_candidate.id,
            adapter_key="disease",
            fields=[],
            created_at=now,
            updated_at=now,
            draft_metadata={},
        )
    )
    db_session.add(
        ValidationSnapshotModel(
            id=uuid4(),
            scope=CurationValidationScope.SESSION,
            session_id=existing_session.id,
            candidate_id=None,
            adapter_key="disease",
            state=CurationValidationSnapshotState.COMPLETED,
            field_results={},
            summary=CurationValidationSummary(
                state=CurationValidationSnapshotState.COMPLETED,
                counts=CurationValidationCounts(),
                last_validated_at=now,
                warnings=[],
            ).model_dump(mode="json"),
            warnings=[],
            requested_at=now,
            completed_at=now,
        )
    )
    db_session.commit()

    result = module.execute_post_curation_pipeline(
        _make_request(
            prep_output,
            document_id=str(document.id),
            review_session_id=str(existing_session.id),
        ),
        db=db_session,
        dependencies=_passthrough_dependencies(),
    )

    assert result.session_id == str(existing_session.id)
    assert result.created is False

    db_session.refresh(existing_session)
    remaining_candidates = db_session.scalars(
        select(CurationCandidate).where(CurationCandidate.session_id == existing_session.id)
    ).all()
    assert len(remaining_candidates) == 1
    assert remaining_candidates[0].id != old_candidate.id
    assert remaining_candidates[0].normalized_payload["gene"]["symbol"] == "GENE1"
    assert "normalized_payload" not in remaining_candidates[0].candidate_metadata
    assert existing_session.session_version == 2

    remaining_snapshots = db_session.scalars(
        select(ValidationSnapshotModel).where(ValidationSnapshotModel.session_id == existing_session.id)
    ).all()
    assert len(remaining_snapshots) == 2
    assert db_session.scalars(
        select(DraftModel).where(DraftModel.candidate_id == old_candidate.id)
    ).all() == []


def test_execute_post_curation_pipeline_requires_persisted_prep_result(db_session):
    document = _create_document(db_session)
    prep_output = _make_prep_output()

    with pytest.raises(LookupError, match="Unable to verify the persisted curation prep extraction result"):
        module.execute_post_curation_pipeline(
            _make_request(prep_output, document_id=str(document.id)),
            db=db_session,
            dependencies=_passthrough_dependencies(),
        )


def test_execute_post_curation_pipeline_with_owned_session_commits_results(db_session, monkeypatch):
    document = _create_document(db_session)
    prep_output = _make_prep_output()
    _persist_matching_prep_result(
        db_session,
        document_id=str(document.id),
        prep_output=prep_output,
    )

    engine = db_session.get_bind()
    session_factory = sessionmaker(
        bind=engine,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )
    monkeypatch.setattr(module, "SessionLocal", session_factory)

    result = module.execute_post_curation_pipeline(
        _make_request(prep_output, document_id=str(document.id)),
        dependencies=_passthrough_dependencies(),
    )

    verification_session = session_factory()
    try:
        persisted_session = verification_session.scalars(
            select(ReviewSessionModel).where(ReviewSessionModel.id == UUID(result.session_id))
        ).one()
        assert persisted_session.total_candidates == 1
    finally:
        verification_session.close()


def test_execute_post_curation_pipeline_with_external_session_leaves_commit_to_caller(db_session):
    document = _create_document(db_session)
    prep_output = _make_prep_output()
    _persist_matching_prep_result(
        db_session,
        document_id=str(document.id),
        prep_output=prep_output,
    )

    result = module.execute_post_curation_pipeline(
        _make_request(prep_output, document_id=str(document.id)),
        db=db_session,
        dependencies=_passthrough_dependencies(),
    )

    pending_session = db_session.scalars(
        select(ReviewSessionModel).where(ReviewSessionModel.id == UUID(result.session_id))
    ).one()
    assert pending_session.total_candidates == 1

    db_session.rollback()

    verification_session = sessionmaker(
        bind=db_session.get_bind(),
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )()
    try:
        assert (
            verification_session.scalars(
                select(ReviewSessionModel).where(ReviewSessionModel.id == UUID(result.session_id))
            ).first()
            is None
        )
    finally:
        verification_session.close()


@pytest.mark.asyncio
async def test_run_post_curation_pipeline_runs_sync_for_small_sets(monkeypatch):
    prep_output = _make_prep_output()
    expected = module.PostCurationPipelineResult(
        status=module.PipelineRunStatus.COMPLETED,
        execution_mode=module.PipelineExecutionMode.SYNC,
        candidate_count=1,
        session_id="session-1",
        created=True,
        prep_extraction_result_id="prep-result-1",
    )

    monkeypatch.setattr(module, "execute_post_curation_pipeline", lambda *args, **kwargs: expected)

    result = await module.run_post_curation_pipeline(
        module.PostCurationPipelineRequest(
            prep_output=prep_output,
            document_id=str(uuid4()),
            source_kind=CurationExtractionSourceKind.CHAT,
            adapter_key="disease",
            async_candidate_threshold=5,
        )
    )

    assert result == expected


@pytest.mark.asyncio
async def test_run_post_curation_pipeline_schedules_async_for_large_sets():
    prep_output = _make_prep_output(candidate_count=2)

    class _FakeScheduler:
        def __init__(self):
            self.calls = []

        def schedule(self, task, *, task_name: str):
            self.calls.append({"task": task, "task_name": task_name})
            return f"scheduled::{task_name}"

    scheduler = _FakeScheduler()
    dependencies = module.PostCurationPipelineDependencies(task_scheduler=scheduler)

    result = await module.run_post_curation_pipeline(
        module.PostCurationPipelineRequest(
            prep_output=prep_output,
            document_id=str(uuid4()),
            source_kind=CurationExtractionSourceKind.CHAT,
            adapter_key="disease",
            async_candidate_threshold=1,
        ),
        dependencies=dependencies,
    )

    assert result.status is module.PipelineRunStatus.SCHEDULED
    assert result.execution_mode is module.PipelineExecutionMode.ASYNC
    assert result.task_name is not None
    assert scheduler.calls[0]["task_name"].startswith("curation-post-agent-pipeline:")

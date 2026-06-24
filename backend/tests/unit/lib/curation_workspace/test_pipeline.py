"""Unit tests for the deterministic post-agent curation pipeline."""

from __future__ import annotations

import inspect
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.lib.curation_adapters.structured_payload import (
    StructuredPayloadCandidateNormalizer,
)
from src.lib.curation_workspace import pipeline as module
from src.lib.curation_workspace.models import (
    CurationActionLogEntry as SessionActionLogModel,
    CurationCandidate,
    CurationDraft as DraftModel,
    CurationEvidenceRecord as EvidenceRecordModel,
    CurationExtractionResultRecord as ExtractionResultModel,
    CurationReviewSession as ReviewSessionModel,
    CurationSubmissionRecord as SubmissionModel,
    CurationValidationSnapshot as ValidationSnapshotModel,
    DomainEnvelopeModel,
    DomainEnvelopeHistory,
    DomainEnvelopeObject,
    DomainEnvelopeProjectionIndex,
    DomainValidationFinding,
)
from src.lib.domain_envelopes.persistence import (
    DomainEnvelopeCheckpointRequest,
    write_domain_envelope_checkpoint,
)
from src.lib.domain_packs.loader import load_domain_pack_metadata
from src.lib.domain_packs.registry import LoadedDomainPack
from src.models.sql.database import Base
from src.models.sql.pdf_document import PDFDocument
from src.schemas.curation_prep import CurationPrepAgentOutput, CurationPrepCandidate
from src.schemas.curation_workspace import (
    CurationCandidateSource,
    CurationCandidateStatus,
    CurationExtractionSourceKind,
    CurationActionType,
    CurationSessionStatus,
    CurationValidationCounts,
    CurationValidationScope,
    CurationValidationSnapshotState,
    CurationValidationSummary,
    DomainEnvelopeReviewRow,
    DomainEnvelopeReviewRowsResponse,
    DomainEnvelopeReviewRowSummaryField,
    FieldValidationStatus,
)
from src.schemas.domain_envelope import CuratableObjectEnvelope, DomainEnvelope


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
    DomainEnvelopeModel.__table__,
    DomainEnvelopeObject.__table__,
    DomainValidationFinding.__table__,
    DomainEnvelopeHistory.__table__,
    DomainEnvelopeProjectionIndex.__table__,
    CurationCandidate.__table__,
    EvidenceRecordModel.__table__,
    DraftModel.__table__,
    SubmissionModel.__table__,
    ValidationSnapshotModel.__table__,
    SessionActionLogModel.__table__,
]


_REAL_REFRESH_DOMAIN_ENVELOPE_VALIDATION_FOR_REF = (
    module._refresh_domain_envelope_validation_for_ref
)
_REAL_ENVELOPE_FIELD_RESULTS_FOR_CANDIDATE = module._envelope_field_results_for_candidate


def test_envelope_refresh_uses_structural_checks_and_active_dispatch():
    source = inspect.getsource(_REAL_REFRESH_DOMAIN_ENVELOPE_VALIDATION_FOR_REF)

    assert "run_domain_envelope_structural_checks" in source
    assert "resolve_curation_domain_envelope_validator_by_id" in source
    assert "dispatch_active_validator_bindings" in source
    assert "run_validation_supervisor" not in source


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
    return _make_envelope_prep_output(review_row_count=candidate_count)


def _make_legacy_candidate() -> CurationPrepCandidate:
    gene_symbol = "GENE1"
    return CurationPrepAgentOutput.model_validate(
        {
            "candidates": [
                {
                    "adapter_key": "disease",
                    "payload": {
                        "gene": {"symbol": gene_symbol},
                        "condition": {"label": "Disease 1"},
                        "evidence": {"score": 0.8},
                    },
                    "evidence_records": [
                        {
                            "evidence_record_id": "evidence-1",
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
                                "chunk_ids": ["chunk-1"],
                            },
                            "notes": ["The paper names the gene directly in the finding."],
                        }
                    ],
                    "conversation_context_summary": f"Conversation narrowed to {gene_symbol}.",
                }
            ],
            "run_metadata": {
                "model_name": "gpt-5.4-mini",
                "token_usage": {
                    "input_tokens": 120,
                    "output_tokens": 45,
                    "total_tokens": 165,
                },
                "processing_notes": ["Prep agent completed candidate extraction."],
                "warnings": ["Prep run warning."],
            },
        }
    ).candidates[0]


def _make_envelope_prep_output(*, review_row_count: int = 1) -> CurationPrepAgentOutput:
    return CurationPrepAgentOutput.model_validate(
        {
            "envelope_refs": [
                {
                    "envelope_id": f"env-review-{review_row_count}",
                    "envelope_revision": 4,
                    "source_extraction_result_id": "extract-domain-1",
                    "domain_pack_id": "fixture.pack",
                    "review_row_count": review_row_count,
                }
            ],
            "review_row_count": review_row_count,
            "run_metadata": {
                "model_name": "deterministic_programmatic_mapper_v1",
                "token_usage": {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                },
                "processing_notes": ["Envelope refs selected."],
                "warnings": [],
            },
        }
    )


@pytest.fixture(autouse=True)
def _stub_domain_envelope_review_row_materializer(monkeypatch):
    def _fake_materialize(_db, envelope_id, *, revision=None, materializer=None):
        row_count = int(str(envelope_id).rsplit("-", 1)[-1])
        envelope_revision = revision or 1
        rows = []
        for index in range(row_count):
            row_number = index + 1
            rows.append(
                DomainEnvelopeReviewRow(
                    envelope_id=envelope_id,
                    object_id=f"object-{row_number}",
                    envelope_revision=envelope_revision,
                    domain_pack_id="fixture.pack",
                    domain_pack_version="0.1.0",
                    object_type="GeneAssertion",
                    object_role="curatable_unit",
                    status="pending",
                    validation_state="clear",
                    projection_type="workspace_review_row",
                    projection_key=f"object-{row_number}",
                    display_label=f"GENE{row_number}",
                    secondary_label=f"Disease {row_number}",
                    summary_fields=[
                        DomainEnvelopeReviewRowSummaryField(
                            field_path="gene.symbol",
                            label="Gene symbol",
                            value=f"GENE{row_number}",
                            field_type="string",
                        ),
                        DomainEnvelopeReviewRowSummaryField(
                            field_path="condition.label",
                            label="Condition label",
                            value=f"Disease {row_number}",
                            field_type="string",
                        ),
                        DomainEnvelopeReviewRowSummaryField(
                            field_path="evidence.score",
                            label="Evidence score",
                            value=0.8 + (index * 0.05),
                            field_type="number",
                        ),
                    ],
                )
            )
        return DomainEnvelopeReviewRowsResponse(
            envelope_id=envelope_id,
            envelope_revision=envelope_revision,
            row_count=len(rows),
            rows=rows,
        )

    monkeypatch.setattr(
        module,
        "materialize_persisted_envelope_review_rows",
        _fake_materialize,
    )
    monkeypatch.setattr(
        module,
        "_refresh_domain_envelope_validation_for_ref",
        lambda _db, envelope_ref, *, runtime_context=None: envelope_ref.envelope_revision,
    )
    monkeypatch.setattr(
        module,
        "_envelope_field_results_for_candidate",
        lambda _db, _candidate: ({}, []),
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
        candidate_count=prep_output.review_row_count,
        conversation_summary="Prep conversation summary.",
        payload_json=prep_output.model_dump(mode="json"),
        extraction_metadata={
            "final_run_metadata": prep_output.run_metadata.model_dump(mode="json"),
        },
        created_at=now,
    )
    db_session.add(record)
    db_session.commit()
    return record


def test_draft_fields_use_summary_fields_only_when_workspace_metadata_is_absent():
    review_row = DomainEnvelopeReviewRow(
        envelope_id="env-review-1",
        object_id="object-1",
        envelope_revision=1,
        domain_pack_id="fixture.pack",
        domain_pack_version="0.1.0",
        object_type="GeneAssertion",
        object_role="curatable_unit",
        status="pending",
        validation_state="clear",
        projection_type="workspace_review_row",
        projection_key="object-1",
        display_label="ABC-1",
        summary_fields=[
            DomainEnvelopeReviewRowSummaryField(
                field_path="gene.symbol",
                label="Gene symbol",
                value="ABC-1",
                field_type="string",
            )
        ],
    )

    fields = module._draft_fields_from_review_row(review_row)

    assert [field.field_key for field in fields] == ["gene.symbol"]
    assert fields[0].group_key == "gene"
    assert fields[0].group_label == "Gene"


def test_draft_fields_do_not_fall_back_to_summary_fields_when_workspace_fields_empty():
    review_row = DomainEnvelopeReviewRow(
        envelope_id="env-review-1",
        object_id="object-1",
        envelope_revision=1,
        domain_pack_id="fixture.pack",
        domain_pack_version="0.1.0",
        object_type="GeneAssertion",
        object_role="curatable_unit",
        status="pending",
        validation_state="clear",
        projection_type="workspace_review_row",
        projection_key="object-1",
        display_label="ABC-1",
        metadata={"workspace_fields": []},
        summary_fields=[
            DomainEnvelopeReviewRowSummaryField(
                field_path="gene.symbol",
                label="Gene symbol",
                value="ABC-1",
                field_type="string",
            )
        ],
    )

    assert module._draft_fields_from_review_row(review_row) == []


def test_draft_fields_require_workspace_group_metadata_for_workspace_fields():
    review_row = DomainEnvelopeReviewRow(
        envelope_id="env-review-1",
        object_id="object-1",
        envelope_revision=1,
        domain_pack_id="fixture.pack",
        domain_pack_version="0.1.0",
        object_type="GeneAssertion",
        object_role="curatable_unit",
        status="pending",
        validation_state="clear",
        projection_type="workspace_review_row",
        projection_key="object-1",
        display_label="ABC-1",
        metadata={
            "workspace_fields": [
                {
                    "field_path": "gene.symbol",
                    "label": "Gene symbol",
                    "value": "ABC-1",
                    "field_type": "string",
                    "metadata": {"workspace_order": 0},
                }
            ]
        },
        summary_fields=[
            DomainEnvelopeReviewRowSummaryField(
                field_path="gene.symbol",
                label="Gene symbol",
                value="ABC-1",
                field_type="string",
            )
        ],
    )

    with pytest.raises(ValueError, match="metadata\\.workspace_group"):
        module._draft_fields_from_review_row(review_row)


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


def test_pipeline_dependencies_configure_async_scheduler_only():
    dependencies = module.PostCurationPipelineDependencies()

    assert isinstance(dependencies.task_scheduler, module.AsyncioPipelineTaskScheduler)


def test_structured_payload_candidate_normalizer_builds_payload_and_draft_fields():
    candidate = _make_legacy_candidate()

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
        "Domain-envelope review-row projection validation completed from persisted envelope fields.",
    ]

    candidate_row = db_session.scalars(
        select(CurationCandidate).where(CurationCandidate.session_id == session_row.id)
    ).one()
    assert candidate_row.source is CurationCandidateSource.EXTRACTED
    assert candidate_row.status is CurationCandidateStatus.PENDING
    assert candidate_row.extraction_result_id == prep_record.id
    assert candidate_row.envelope_id == "env-review-1"
    assert candidate_row.object_id == "object-1"
    assert candidate_row.envelope_revision == 4
    assert candidate_row.normalized_payload == {}
    assert candidate_row.candidate_metadata["semantic_source"] == "domain_envelope.extracted_objects"
    assert candidate_row.candidate_metadata["projection_key"] == "object-1"

    evidence_rows = db_session.scalars(
        select(EvidenceRecordModel).where(EvidenceRecordModel.candidate_id == candidate_row.id)
    ).all()
    assert evidence_rows == []

    draft_row = db_session.scalars(
        select(DraftModel).where(DraftModel.candidate_id == candidate_row.id)
    ).one()
    assert "normalized_payload" not in candidate_row.candidate_metadata
    assert draft_row.fields[0]["field_key"] == "gene.symbol"
    assert draft_row.fields[0]["validation_result"]["status"] == FieldValidationStatus.SKIPPED.value
    assert draft_row.fields[0]["evidence_anchor_ids"] == []
    assert "normalized_payload" not in draft_row.draft_metadata
    assert draft_row.draft_metadata["semantic_source"] == "domain_envelope.extracted_objects"

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


def test_execute_post_curation_pipeline_refreshes_envelope_validation_before_materializing(
    db_session,
    monkeypatch,
    tmp_path,
):
    document = _create_document(db_session)
    metadata_path = tmp_path / "domain_pack.yaml"
    metadata_path.write_text(
        """
pack_id: fixture.pack
display_name: Fixture Pack
version: 0.1.0
metadata_api_version: 1.0.0
status: active
model_definitions:
  - model_id: GeneAssertionPayload
    display_name: Gene assertion payload
object_definitions:
  - object_type: GeneAssertion
    display_name: Gene assertion
    model_ref: GeneAssertionPayload
    metadata:
      workspace_display:
        primary_label_field: gene.symbol
        summary_fields:
          - gene.identifier
          - gene.symbol
    fields:
      - field_path: gene.identifier
        field_type: string
        required: true
      - field_path: gene.symbol
        field_type: string
        required: true
metadata:
  validator_bindings:
    active:
      - binding_id: fixture.identifier_prefix
        validator_agent:
          package_id: fixture.pack
          agent_id: identifier_prefix_validator
        applies_to:
          domain_pack_id: fixture.pack
          object_types:
            - GeneAssertion
          field_paths:
            - gene.identifier
        input_fields:
          identifier:
            source: payload
            path: gene.identifier
        expected_result_fields:
          identifier: gene.identifier
        required: true
        blocking: true
""".strip(),
        encoding="utf-8",
    )
    metadata = load_domain_pack_metadata(metadata_path)
    loaded_pack = LoadedDomainPack(
        pack_id=metadata.pack_id,
        display_name=metadata.display_name,
        version=metadata.version,
        pack_path=tmp_path,
        metadata_path=metadata_path,
        metadata=metadata,
    )
    monkeypatch.setattr(
        module,
        "_refresh_domain_envelope_validation_for_ref",
        _REAL_REFRESH_DOMAIN_ENVELOPE_VALIDATION_FOR_REF,
    )
    monkeypatch.setattr(
        module,
        "_envelope_field_results_for_candidate",
        _REAL_ENVELOPE_FIELD_RESULTS_FOR_CANDIDATE,
    )
    monkeypatch.setattr(
        module,
        "resolve_curation_domain_pack_by_id",
        lambda domain_pack_id: loaded_pack if domain_pack_id == "fixture.pack" else None,
    )
    real_dispatch = module.dispatch_active_validator_bindings
    dispatch_calls = []

    def _fake_validator_dispatch(
        envelope,
        domain_pack,
        *,
        registry=None,
        source_envelope_revision=None,
        runtime_context=None,
    ):
        assert source_envelope_revision == 1

        def _runner(request, *, binding):
            dispatch_calls.append(
                {
                    "request": request,
                    "max_tool_calls": binding.max_tool_calls,
                }
            )
            return {
                "status": "unresolved",
                "request_id": request.request_id,
                "validator_binding_id": request.validator_binding_id,
                "validator_agent": request.validator_agent.model_dump(mode="json"),
                "target": request.target.model_dump(mode="json"),
                "resolved_values": {},
                "resolved_objects": [],
                "missing_expected_fields": ["identifier"],
                "candidates": [],
                "lookup_attempts": [
                    {
                        "provider": "fixture",
                        "method": "identifier_prefix",
                        "query": dict(request.selected_inputs),
                        "result_count": 0,
                        "outcome": "not_found",
                    }
                ],
                "curator_message": "Identifier prefix was not resolved.",
                "explanation": "Fixture validator could not resolve the identifier.",
            }

        return real_dispatch(
            envelope,
            domain_pack,
            registry=registry,
            runner=_runner,
            source_envelope_revision=source_envelope_revision,
            runtime_context=runtime_context,
        )

    monkeypatch.setattr(
        module,
        "dispatch_active_validator_bindings",
        _fake_validator_dispatch,
    )

    envelope = DomainEnvelope(
        envelope_id="env-validation-1",
        domain_pack_id="fixture.pack",
        extracted_objects=[
            CuratableObjectEnvelope(
                object_type="GeneAssertion",
                pending_ref_id="object-1",
                payload={
                    "gene": {
                        "identifier": "BAD:0001",
                        "symbol": "ABC-1",
                    }
                },
            )
        ],
    )
    checkpoint = write_domain_envelope_checkpoint(
        db_session,
        DomainEnvelopeCheckpointRequest(
            project_key="fixture",
            envelope=envelope,
            expected_revision=0,
            document_id=document.id,
        ),
    )
    assert checkpoint.revision == 1

    prep_output = CurationPrepAgentOutput.model_validate(
        {
            "envelope_refs": [
                {
                    "envelope_id": "env-validation-1",
                    "envelope_revision": 1,
                    "source_extraction_result_id": "extract-domain-1",
                    "domain_pack_id": "fixture.pack",
                    "review_row_count": 1,
                }
            ],
            "review_row_count": 1,
            "run_metadata": {
                "model_name": "deterministic_programmatic_mapper_v1",
                "token_usage": {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                },
                "processing_notes": ["Envelope refs selected."],
                "warnings": [],
            },
        }
    )
    _persist_matching_prep_result(
        db_session,
        document_id=str(document.id),
        prep_output=prep_output,
    )

    def _fake_materialize(db, envelope_id, *, revision=None, materializer=None):
        assert envelope_id == "env-validation-1"
        assert revision == 2
        envelope_row = db.get(DomainEnvelopeModel, envelope_id)
        return DomainEnvelopeReviewRowsResponse(
            envelope_id=envelope_id,
            envelope_revision=revision,
            row_count=1,
            rows=[
                DomainEnvelopeReviewRow(
                    envelope_id=envelope_id,
                    object_id="object-1",
                    envelope_revision=envelope_row.revision,
                    domain_pack_id="fixture.pack",
                    domain_pack_version="0.1.0",
                    object_type="GeneAssertion",
                    object_role="curatable_unit",
                    status="pending",
                    validation_state="blocked",
                    projection_type="workspace_review_row",
                    projection_key="object-1",
                    display_label="ABC-1",
                    summary_fields=[
                        DomainEnvelopeReviewRowSummaryField(
                            field_path="gene.identifier",
                            label="Gene ID",
                            value="BAD:0001",
                            field_type="string",
                        ),
                        DomainEnvelopeReviewRowSummaryField(
                            field_path="gene.symbol",
                            label="Gene symbol",
                            value="ABC-1",
                            field_type="string",
                        ),
                    ],
                )
            ],
        )

    monkeypatch.setattr(module, "materialize_persisted_envelope_review_rows", _fake_materialize)

    result = module.execute_post_curation_pipeline(
        _make_request(prep_output, document_id=str(document.id)),
        db=db_session,
    )

    envelope_row = db_session.get(DomainEnvelopeModel, "env-validation-1")
    assert envelope_row.revision == 2
    assert len(dispatch_calls) == 1
    assert dispatch_calls[0]["request"].selected_inputs == {
        "identifier": "BAD:0001",
    }
    assert dispatch_calls[0]["request"].target.input_values == (
        dispatch_calls[0]["request"].selected_inputs
    )
    assert envelope_row.envelope_json["validation_findings"][0]["code"] == (
        "domain_pack.validator_unresolved"
    )
    assert envelope_row.envelope_json["validation_findings"][0]["details"][
        "validation_request"
    ]["target"]["input_values"] == {
        "identifier": "BAD:0001",
    }
    assert envelope_row.envelope_json["validation_findings"][0]["details"][
        "validation_metadata"
    ]["source_envelope_revision"] == 1
    indexed_findings = db_session.scalars(select(DomainValidationFinding)).all()
    assert len(indexed_findings) == 1
    assert indexed_findings[0].field_path == "gene.identifier"

    candidate_row = db_session.scalars(
        select(CurationCandidate).where(CurationCandidate.session_id == UUID(result.session_id))
    ).one()
    assert candidate_row.envelope_revision == 2
    draft_row = db_session.scalars(
        select(DraftModel).where(DraftModel.candidate_id == candidate_row.id)
    ).one()
    fields_by_key = {field["field_key"]: field for field in draft_row.fields}
    assert fields_by_key["gene.identifier"]["validation_result"]["status"] == (
        FieldValidationStatus.NOT_FOUND.value
    )
    assert fields_by_key["gene.symbol"]["validation_result"]["status"] == (
        FieldValidationStatus.SKIPPED.value
    )

    candidate_snapshot = db_session.scalars(
        select(ValidationSnapshotModel).where(
            ValidationSnapshotModel.candidate_id == candidate_row.id,
        )
    ).one()
    assert candidate_snapshot.summary["counts"]["not_found"] == 1
    assert candidate_snapshot.summary["counts"]["skipped"] == 1


def test_execute_post_curation_pipeline_persists_domain_envelope_projection_ref_from_envelope_row(
    db_session,
):
    document = _create_document(db_session)
    prep_output = _make_envelope_prep_output()
    _persist_matching_prep_result(
        db_session,
        document_id=str(document.id),
        prep_output=prep_output,
    )

    result = module.execute_post_curation_pipeline(
        _make_request(prep_output, document_id=str(document.id)),
        db=db_session,
    )

    candidate_row = db_session.scalars(
        select(CurationCandidate).where(CurationCandidate.session_id == UUID(result.session_id))
    ).one()
    assert candidate_row.envelope_id == "env-review-1"
    assert candidate_row.object_id == "object-1"
    assert candidate_row.envelope_revision == 4


def test_execute_post_curation_pipeline_materializes_envelope_rows_without_normalizer(
    db_session,
    monkeypatch,
):
    document = _create_document(db_session)
    prep_output = _make_envelope_prep_output()
    prep_record = _persist_matching_prep_result(
        db_session,
        document_id=str(document.id),
        prep_output=prep_output,
    )

    def _fake_materialize(db, envelope_id, *, revision=None, materializer=None):
        assert db is db_session
        assert envelope_id == "env-review-1"
        assert revision == 4
        return DomainEnvelopeReviewRowsResponse(
            envelope_id="env-review-1",
            envelope_revision=4,
            row_count=1,
            rows=[
                DomainEnvelopeReviewRow(
                    envelope_id="env-review-1",
                    object_id="object-1",
                    envelope_revision=4,
                    domain_pack_id="fixture.pack",
                    domain_pack_version="0.1.0",
                    object_type="GeneAssertion",
                    object_role="curatable_unit",
                    status="pending",
                    validation_state="clear",
                    projection_type="workspace_review_row",
                    projection_key="object-1",
                    display_label="ABC-1",
                    secondary_label="Condition A",
                    schema_provider="json-schema",
                    schema_ref={"schema_id": "fixture.schema.json"},
                    object_model_ref={"provider_refs": {"schema": "fixture"}},
                    model_field_ref={
                        "domain_pack_fields": {
                            "gene.symbol": {"provider_refs": {"slot": "symbol"}}
                        }
                    },
                    metadata={
                        "payload_path": "extracted_objects[0].payload",
                        "evidence_record_ids": ["evidence-1"],
                        "metadata_refs": [
                            {
                                "metadata_path": "evidence_records[0]",
                                "role": "verified_evidence",
                            }
                        ],
                        "workspace_fields": [
                            {
                                "field_path": "gene.symbol",
                                "label": "Gene symbol",
                                "value": "ABC-1",
                                "field_type": "string",
                                "metadata": {
                                    "required": True,
                                    "read_only": False,
                                    "materializes_to_field_paths": [
                                        "experiment.entity_assayed.symbol"
                                    ],
                                    "workspace_group": {
                                        "id": "subject",
                                        "label": "Subject",
                                        "order": 0,
                                        "field_order": 0,
                                    },
                                    "workspace_order": 0,
                                },
                            },
                            {
                                "field_path": "gene.identifier",
                                "label": "Gene identifier",
                                "value": None,
                                "field_type": "string",
                                "metadata": {
                                    "required": False,
                                    "read_only": True,
                                    "workspace_group": {
                                        "id": "subject",
                                        "label": "Subject",
                                        "order": 0,
                                        "field_order": 1,
                                    },
                                    "workspace_order": 1,
                                },
                            },
                        ],
                    },
                    summary_fields=[
                        DomainEnvelopeReviewRowSummaryField(
                            field_path="gene.symbol",
                            label="Gene symbol",
                            value="ABC-1",
                            field_type="string",
                        )
                    ],
                )
            ],
        )

    monkeypatch.setattr(
        module,
        "materialize_persisted_envelope_review_rows",
        _fake_materialize,
    )

    result = module.execute_post_curation_pipeline(
        _make_request(prep_output, document_id=str(document.id)),
        db=db_session,
    )

    assert result.status is module.PipelineRunStatus.COMPLETED
    assert result.candidate_count == 1
    assert result.prep_extraction_result_id == str(prep_record.id)

    candidate_row = db_session.scalars(select(CurationCandidate)).one()
    assert candidate_row.envelope_id == "env-review-1"
    assert candidate_row.object_id == "object-1"
    assert candidate_row.envelope_revision == 4
    assert candidate_row.normalized_payload == {}
    assert candidate_row.candidate_metadata["semantic_source"] == "domain_envelope.extracted_objects"
    assert candidate_row.candidate_metadata["object_type"] == "GeneAssertion"
    assert candidate_row.candidate_metadata["schema_provider"] == "json-schema"
    assert candidate_row.candidate_metadata["schema_ref"] == {
        "schema_id": "fixture.schema.json"
    }
    assert candidate_row.candidate_metadata["object_model_ref"] == {
        "provider_refs": {"schema": "fixture"}
    }
    assert candidate_row.candidate_metadata["model_field_ref"] == {
        "domain_pack_fields": {
            "gene.symbol": {"provider_refs": {"slot": "symbol"}}
        }
    }
    assert candidate_row.candidate_metadata["review_row_metadata"] == {
        "payload_path": "extracted_objects[0].payload",
        "evidence_record_ids": ["evidence-1"],
        "metadata_refs": [
            {
                "metadata_path": "evidence_records[0]",
                "role": "verified_evidence",
            }
        ],
        "workspace_fields": [
            {
                "field_path": "gene.symbol",
                "label": "Gene symbol",
                "value": "ABC-1",
                "field_type": "string",
                "metadata": {
                    "required": True,
                    "read_only": False,
                    "materializes_to_field_paths": [
                        "experiment.entity_assayed.symbol"
                    ],
                    "workspace_group": {
                        "id": "subject",
                        "label": "Subject",
                        "order": 0,
                        "field_order": 0,
                    },
                    "workspace_order": 0,
                },
            },
            {
                "field_path": "gene.identifier",
                "label": "Gene identifier",
                "value": None,
                "field_type": "string",
                "metadata": {
                    "required": False,
                    "read_only": True,
                    "workspace_group": {
                        "id": "subject",
                        "label": "Subject",
                        "order": 0,
                        "field_order": 1,
                    },
                    "workspace_order": 1,
                },
            },
        ],
    }

    draft_row = db_session.scalars(select(DraftModel)).one()
    assert draft_row.fields[0]["field_key"] == "gene.symbol"
    assert draft_row.fields[0]["value"] == "ABC-1"
    assert draft_row.fields[0]["group_key"] == "subject"
    assert draft_row.fields[0]["group_label"] == "Subject"
    assert draft_row.fields[0]["required"] is True
    assert draft_row.fields[0]["read_only"] is False
    assert draft_row.fields[0]["metadata"]["materializes_to_field_paths"] == [
        "experiment.entity_assayed.symbol"
    ]
    assert draft_row.fields[1]["field_key"] == "gene.identifier"
    assert draft_row.fields[1]["group_key"] == "subject"
    assert draft_row.fields[1]["read_only"] is True
    assert draft_row.draft_metadata["projection_ref"] == {
        "envelope_id": "env-review-1",
        "object_id": "object-1",
        "envelope_revision": 4,
    }


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
    )

    assert result.session_id == str(existing_session.id)
    assert result.created is False

    db_session.refresh(existing_session)
    remaining_candidates = db_session.scalars(
        select(CurationCandidate).where(CurationCandidate.session_id == existing_session.id)
    ).all()
    assert len(remaining_candidates) == 1
    assert remaining_candidates[0].id != old_candidate.id
    assert remaining_candidates[0].normalized_payload == {}
    assert remaining_candidates[0].envelope_id == "env-review-1"
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

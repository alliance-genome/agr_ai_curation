"""Unit tests for domain-envelope workspace evidence and validation projections."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from fastapi import HTTPException
import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.lib.curation_workspace import session_service
from src.lib.curation_workspace.validation_runtime import (
    domain_envelope_field_validation_results,
)
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
)
from src.lib.domain_packs.materialization import (
    project_evidence_anchor_projections,
    project_validation_summary_projections,
)
from src.models.sql.database import Base
from src.models.sql.pdf_document import PDFDocument
from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    DomainEnvelope,
    DomainEnvelopeStatus,
    FieldRef,
    ObjectRef,
    ValidationFinding,
    ValidationFindingSeverity,
    ValidationFindingStatus,
)
from src.schemas.curation_workspace import (
    CurationCandidateSource,
    CurationCandidateStatus,
    CurationSessionStatus,
    DomainEnvelopeValidationStatus,
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
    DomainEnvelopeModel.__table__,
    CurationCandidate.__table__,
    EvidenceRecordModel.__table__,
    DraftModel.__table__,
    SubmissionModel.__table__,
    ValidationSnapshotModel.__table__,
    SessionActionLogModel.__table__,
]


def _now() -> datetime:
    return datetime(2026, 5, 10, 12, 30, tzinfo=timezone.utc)


def _db_session():
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


def _object_ref() -> ObjectRef:
    return ObjectRef(object_id="gene-1", object_type="GeneAssertion")


def _field_ref(field_path: str) -> FieldRef:
    return FieldRef(object_ref=_object_ref(), field_path=field_path)


def _envelope() -> DomainEnvelope:
    return DomainEnvelope(
        envelope_id="env-1",
        domain_pack_id="fixture-pack",
        objects=[
            CuratableObjectEnvelope(
                object_type="GeneAssertion",
                object_id="gene-1",
                payload={
                    "gene": {"symbol": "abc-1", "curie": "TEST:Gene00000001"},
                    "evidence": {"score": 0.95},
                },
                evidence_record_ids=["evidence-1"],
            )
        ],
        validation_findings=[
            ValidationFinding(
                severity=ValidationFindingSeverity.WARNING,
                code="fixture.symbol_warning",
                message="Gene symbol should be reviewed.",
                field_ref=_field_ref("gene.symbol"),
                details={"validation_metadata": {"binding_state": "active"}},
            ),
            ValidationFinding(
                severity=ValidationFindingSeverity.INFO,
                code="domain_pack.validator_binding_under_development",
                message="Symbol validator is under development.",
                field_ref=_field_ref("gene.curie"),
                details={
                    "validation_metadata": {
                        "binding_state": "under_development",
                        "validator_binding_id": "fixture.future_symbol_lookup",
                    },
                    "lookup_attempts": [{"lookup_status": "under_development"}],
                },
            ),
            ValidationFinding(
                severity=ValidationFindingSeverity.BLOCKER,
                code="fixture.export_validator_blocked",
                message="Export validator reported a blocking failure.",
                field_ref=_field_ref("evidence.score"),
                details={
                    "failure_classification": "blocked",
                },
            ),
            ValidationFinding(
                severity=ValidationFindingSeverity.WARNING,
                code="domain_pack.validator_unresolved",
                message="Ontology lookup was unresolved.",
                object_ref=_object_ref(),
                details={"failure_classification": "not_found"},
            ),
            ValidationFinding(
                severity=ValidationFindingSeverity.INFO,
                code="fixture.resolved_note",
                message="Previous finding was resolved.",
                field_ref=_field_ref("gene.label"),
                status=ValidationFindingStatus.RESOLVED,
            ),
        ],
        metadata={
            "evidence_records": [
                {
                    "evidence_record_id": "evidence-1",
                    "verified_quote": "abc-1 was detected in the tested sample.",
                    "page": 7,
                    "section": "Results",
                    "subsection": "Expression",
                    "chunk_id": "chunk-7",
                    "document_id": "document-from-evidence",
                    "field_paths": ["gene.symbol"],
                }
            ]
        },
    )


def test_evidence_anchor_projection_preserves_navigation_context():
    projections = project_evidence_anchor_projections(
        _envelope(),
        envelope_revision=3,
        document_id="document-from-session",
        object_id="gene-1",
    )

    assert len(projections) == 1
    projection = projections[0]
    assert projection.envelope_id == "env-1"
    assert projection.envelope_revision == 3
    assert projection.object_id == "gene-1"
    assert projection.field_path == "gene.symbol"
    assert projection.evidence_record_id == "evidence-1"
    assert projection.quote == "abc-1 was detected in the tested sample."
    assert projection.page_number == 7
    assert projection.chunk_id == "chunk-7"
    assert projection.chunk_ids == ["chunk-7"]
    assert projection.document_id == "document-from-evidence"
    assert projection.anchor.viewer_search_text == projection.quote


def test_evidence_anchor_projection_reads_nested_extraction_metadata_records():
    envelope = DomainEnvelope(
        envelope_id="env-nested",
        domain_pack_id="fixture-pack",
        objects=[
            CuratableObjectEnvelope(
                object_type="GeneExpressionAnnotation",
                object_id="gene-expression-1",
                payload={"expression_annotation_subject": {"gene_symbol": "Tmem67"}},
                evidence_record_ids=["evidence-nested-1"],
                metadata_refs=[
                    {
                        "metadata_path": "evidence_records[0]",
                        "role": "verified_evidence",
                    }
                ],
            )
        ],
        metadata={
            "source_document_id": "document-from-envelope",
            "extraction_metadata": {
                "evidence_records": [
                    {
                        "evidence_record_id": "evidence-nested-1",
                        "verified_quote": "Tmem67 expression was detected.",
                        "page": 2,
                        "chunk_id": "chunk-2",
                        "field_paths": ["expression_annotation_subject.gene_symbol"],
                    }
                ]
            },
        },
    )

    projections = project_evidence_anchor_projections(
        envelope,
        envelope_revision=4,
        object_id="gene-expression-1",
    )

    assert len(projections) == 1
    projection = projections[0]
    assert projection.envelope_id == "env-nested"
    assert projection.envelope_revision == 4
    assert projection.object_id == "gene-expression-1"
    assert projection.field_path == "expression_annotation_subject.gene_symbol"
    assert projection.evidence_record_id == "evidence-nested-1"
    assert projection.quote == "Tmem67 expression was detected."
    assert projection.page_number == 2
    assert projection.chunk_id == "chunk-2"
    assert projection.chunk_ids == ["chunk-2"]
    assert projection.document_id == "document-from-envelope"


def test_evidence_anchor_projection_resolves_object_metadata_refs():
    envelope = DomainEnvelope(
        envelope_id="env-metadata-ref",
        domain_pack_id="fixture-pack",
        objects=[
            CuratableObjectEnvelope(
                object_type="GeneAssertion",
                object_id="gene-1",
                payload={"gene": {"symbol": "abc-1"}},
                metadata_refs=[
                    {
                        "metadata_path": "evidence_records[0]",
                        "role": "verified_evidence",
                    }
                ],
            )
        ],
        metadata={
            "extraction_metadata": {
                "evidence_records": [
                    {
                        "evidence_record_id": "evidence-from-metadata-ref",
                        "verified_quote": "abc-1 appeared in the source text.",
                        "page": 5,
                        "chunk_id": "chunk-5",
                        "field_path": "gene.symbol",
                    }
                ]
            },
        },
    )

    projections = project_evidence_anchor_projections(
        envelope,
        envelope_revision=2,
        document_id="document-from-session",
        object_id="gene-1",
    )

    assert len(projections) == 1
    projection = projections[0]
    assert projection.evidence_record_id == "evidence-from-metadata-ref"
    assert projection.field_path == "gene.symbol"
    assert projection.quote == "abc-1 appeared in the source text."
    assert projection.page_number == 5
    assert projection.chunk_id == "chunk-5"
    assert projection.document_id == "document-from-session"


def test_evidence_anchor_projection_rejects_invalid_structured_anchor():
    envelope = DomainEnvelope(
        envelope_id="env-invalid-anchor",
        domain_pack_id="fixture-pack",
        objects=[
            CuratableObjectEnvelope(
                object_type="GeneAssertion",
                object_id="gene-1",
                payload={"gene": {"symbol": "abc-1"}},
                evidence_record_ids=["evidence-invalid-anchor"],
            )
        ],
        metadata={
            "evidence_records": [
                {
                    "evidence_record_id": "evidence-invalid-anchor",
                    "verified_quote": "abc-1 appeared in the source text.",
                    "field_path": "gene.symbol",
                    "anchor": {
                        "anchor_kind": "snippet",
                        "locator_quality": "exact_quote",
                        "supports_decision": "supports",
                        "unexpected": "not part of the evidence anchor contract",
                    },
                }
            ],
        },
    )

    with pytest.raises(ValidationError, match="unexpected"):
        project_evidence_anchor_projections(
            envelope,
            envelope_revision=2,
            object_id="gene-1",
        )


def test_validation_summary_projection_groups_states_by_object_and_field():
    summaries = project_validation_summary_projections(
        _envelope(),
        envelope_revision=3,
        object_id="gene-1",
    )
    by_field_path = {summary.field_path: summary for summary in summaries}

    assert by_field_path["gene.symbol"].status is DomainEnvelopeValidationStatus.UNRESOLVED
    assert by_field_path["gene.symbol"].finding_count == 1
    assert by_field_path["gene.symbol"].open_finding_count == 1
    assert by_field_path["gene.curie"].status is DomainEnvelopeValidationStatus.UNDER_DEVELOPMENT
    assert by_field_path["evidence.score"].status is DomainEnvelopeValidationStatus.BLOCKED
    assert by_field_path[None].status is DomainEnvelopeValidationStatus.UNRESOLVED
    assert by_field_path["gene.label"].status is DomainEnvelopeValidationStatus.RESOLVED
    assert all(summary.envelope_revision == 3 for summary in summaries)
    assert all(summary.object_id == "gene-1" for summary in summaries)


def test_validation_summary_projection_keeps_active_blocker_findings_unresolved():
    envelope = DomainEnvelope(
        envelope_id="env-active-blocker",
        domain_pack_id="fixture-pack",
        objects=[
            CuratableObjectEnvelope(
                object_type="GeneAssertion",
                object_id="gene-1",
                payload={"gene": {"symbol": "abc-1"}},
            )
        ],
        validation_findings=[
            ValidationFinding(
                severity=ValidationFindingSeverity.BLOCKER,
                code="domain_pack.required_field_missing",
                message="GeneAssertion.gene.curie is required.",
                field_ref=_field_ref("gene.curie"),
                details={"validation_metadata": {"binding_state": "active"}},
            )
        ],
    )

    summaries = project_validation_summary_projections(
        envelope,
        envelope_revision=5,
        object_id="gene-1",
    )

    assert len(summaries) == 1
    summary = summaries[0]
    assert summary.status is DomainEnvelopeValidationStatus.UNRESOLVED
    assert summary.highest_severity == ValidationFindingSeverity.BLOCKER.value
    assert summary.open_finding_count == 1
    assert summary.findings[0].summary_status is DomainEnvelopeValidationStatus.UNRESOLVED


def test_field_validation_maps_partial_lookup_success_to_conflict():
    envelope = DomainEnvelope(
        envelope_id="env-partial-lookup",
        domain_pack_id="fixture-pack",
        objects=[
            CuratableObjectEnvelope(
                object_type="GeneAssertion",
                object_id="gene-1",
                payload={"gene": {"symbol": "abc-1"}},
            )
        ],
        validation_findings=[
            ValidationFinding(
                severity=ValidationFindingSeverity.ERROR,
                status=ValidationFindingStatus.OPEN,
                code="domain_pack.validator_lookup_projection_missing",
                message=(
                    "Lookup partially succeeded but failed to return declared "
                    "result value 'symbol'."
                ),
                field_ref=_field_ref("gene.symbol"),
                details={
                    "lookup_status": "success",
                    "failure_classification": "missing_expected_result_field",
                },
            )
        ],
    )

    field_results, warnings = domain_envelope_field_validation_results(
        envelope,
        envelope_revision=2,
        object_id="gene-1",
        field_keys=["gene.symbol"],
    )

    assert field_results["gene.symbol"].status is FieldValidationStatus.CONFLICT
    assert warnings == [
        "Lookup partially succeeded but failed to return declared result value 'symbol'."
    ]


def test_workspace_response_includes_domain_envelope_projections():
    session_iter = _db_session()
    db_session = next(session_iter)
    try:
        now = _now()
        document = PDFDocument(
            id=uuid4(),
            filename="paper.pdf",
            title="Projection Paper",
            file_path="/tmp/paper.pdf",
            file_hash="b" * 64,
            file_size=1024,
            page_count=10,
            upload_timestamp=now,
            last_accessed=now,
            status="processed",
        )
        review_session = ReviewSessionModel(
            id=uuid4(),
            status=CurationSessionStatus.NEW,
            adapter_key="gene",
            document_id=document.id,
            total_candidates=1,
            pending_candidates=1,
            prepared_at=now,
            created_at=now,
            updated_at=now,
        )
        envelope_row = DomainEnvelopeModel(
            envelope_id="env-1",
            revision=3,
            project_key="alliance",
            domain_pack_key="fixture-pack",
            domain_pack_version=None,
            status=DomainEnvelopeStatus.EXTRACTED,
            document_id=document.id,
            session_id=review_session.id,
            envelope_json=_envelope().model_dump(mode="json"),
            checkpointed_at=now,
            created_at=now,
            updated_at=now,
        )
        candidate = CurationCandidate(
            id=uuid4(),
            session_id=review_session.id,
            source=CurationCandidateSource.EXTRACTED,
            status=CurationCandidateStatus.PENDING,
            order=0,
            adapter_key="gene",
            display_label="abc-1",
            envelope_id="env-1",
            object_id="gene-1",
            envelope_revision=3,
            normalized_payload={},
            candidate_metadata={},
            created_at=now,
            updated_at=now,
        )
        draft = DraftModel(
            candidate_id=candidate.id,
            adapter_key="gene",
            title="abc-1",
            summary=None,
            fields=[],
            draft_metadata={},
            created_at=now,
            updated_at=now,
        )
        db_session.add_all([document, review_session, envelope_row, candidate, draft])
        db_session.commit()

        response = session_service.get_session_workspace(db_session, str(review_session.id))

        workspace = response.workspace
        candidate_payload = workspace.candidates[0]
        assert len(workspace.evidence_anchor_projections) == 1
        assert len(candidate_payload.evidence_anchor_projections) == 1
        assert workspace.evidence_anchor_projections[0].quote == (
            "abc-1 was detected in the tested sample."
        )
        assert workspace.evidence_anchor_projections[0].envelope_revision == 3
        assert workspace.validation_summary_projections
        assert {
            summary.status
            for summary in workspace.validation_summary_projections
        } >= {
            DomainEnvelopeValidationStatus.UNRESOLVED,
            DomainEnvelopeValidationStatus.UNDER_DEVELOPMENT,
            DomainEnvelopeValidationStatus.BLOCKED,
        }
        assert candidate_payload.projection_ref is not None
        assert candidate_payload.projection_ref.envelope_revision == 3

        candidate.envelope_revision = 4
        db_session.commit()
        with pytest.raises(HTTPException, match="does not match domain envelope revision"):
            session_service.get_session_workspace(db_session, str(review_session.id))
    finally:
        try:
            next(session_iter)
        except StopIteration:
            pass

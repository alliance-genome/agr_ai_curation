"""Integration coverage for batch curation handoff auto-push."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterable
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select

from tests.fixtures.evidence.harness import (
    build_domain_envelope_extraction_payload,
    load_evidence_fixture,
)


TEST_PREFIX = "handoff_it"


@pytest.fixture
def handoff_db(test_db):
    """Create required tables and clean this module's rows before/after tests."""

    _cleanup_handoff_rows(test_db)
    yield test_db
    _cleanup_handoff_rows(test_db)


def _cleanup_handoff_rows(db) -> None:
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
    from src.models.sql.batch import Batch, BatchDocument
    from src.models.sql.curation_flow import CurationFlow
    from src.models.sql.file_output import FileOutput
    from src.models.sql.pdf_document import PDFDocument
    from src.models.sql.user import User

    try:
        doc_ids = list(
            db.scalars(
                select(PDFDocument.id).where(PDFDocument.filename.like(f"{TEST_PREFIX}_%"))
            )
        )
        flow_ids = list(
            db.scalars(
                select(CurationFlow.id).where(CurationFlow.name.like(f"{TEST_PREFIX}_%"))
            )
        )
        session_ids = (
            list(
                db.scalars(
                    select(CurationReviewSession.id).where(
                        (CurationReviewSession.document_id.in_(doc_ids))
                        | (CurationReviewSession.flow_run_id.like(f"{TEST_PREFIX}%"))
                    )
                )
            )
            if doc_ids
            else []
        )
        candidate_ids = (
            list(
                db.scalars(
                    select(CurationCandidate.id).where(CurationCandidate.session_id.in_(session_ids))
                )
            )
            if session_ids
            else []
        )
        envelope_ids = (
            list(
                db.scalars(
                    select(DomainEnvelopeModel.envelope_id).where(
                        (DomainEnvelopeModel.document_id.in_(doc_ids))
                        | (DomainEnvelopeModel.flow_run_id.like(f"{TEST_PREFIX}%"))
                        | (DomainEnvelopeModel.project_key.like(f"{TEST_PREFIX}%"))
                    )
                )
            )
            if doc_ids
            else []
        )
        batch_ids = (
            list(db.scalars(select(Batch.id).where(Batch.flow_id.in_(flow_ids))))
            if flow_ids
            else []
        )

        if session_ids or candidate_ids:
            db.execute(
                delete(CurationActionLogEntry).where(
                    (CurationActionLogEntry.session_id.in_(session_ids or [uuid4()]))
                    | (CurationActionLogEntry.candidate_id.in_(candidate_ids or [uuid4()]))
                )
            )
            db.execute(
                delete(CurationSubmissionRecord).where(
                    CurationSubmissionRecord.session_id.in_(session_ids or [uuid4()])
                )
            )
            db.execute(
                delete(CurationValidationSnapshot).where(
                    (CurationValidationSnapshot.session_id.in_(session_ids or [uuid4()]))
                    | (CurationValidationSnapshot.candidate_id.in_(candidate_ids or [uuid4()]))
                )
            )
            db.execute(
                delete(CurationEvidenceRecord).where(
                    CurationEvidenceRecord.candidate_id.in_(candidate_ids or [uuid4()])
                )
            )
            db.execute(
                delete(CurationDraft).where(CurationDraft.candidate_id.in_(candidate_ids or [uuid4()]))
            )
            db.execute(
                delete(CurationCandidate).where(CurationCandidate.session_id.in_(session_ids))
            )
            if envelope_ids:
                db.execute(
                    delete(DomainValidationFinding).where(
                        DomainValidationFinding.envelope_id.in_(envelope_ids)
                    )
                )
                db.execute(
                    delete(DomainEnvelopeProjectionIndex).where(
                        DomainEnvelopeProjectionIndex.envelope_id.in_(envelope_ids)
                    )
                )
                db.execute(
                    delete(DomainEnvelopeHistory).where(
                        DomainEnvelopeHistory.envelope_id.in_(envelope_ids)
                    )
                )
                db.execute(
                    delete(DomainEnvelopeObject).where(
                        DomainEnvelopeObject.envelope_id.in_(envelope_ids)
                    )
                )
                db.execute(
                    delete(DomainEnvelopeModel).where(
                        DomainEnvelopeModel.envelope_id.in_(envelope_ids)
                    )
                )
            db.execute(delete(CurationReviewSession).where(CurationReviewSession.id.in_(session_ids)))

        if batch_ids:
            db.execute(delete(BatchDocument).where(BatchDocument.batch_id.in_(batch_ids)))
            db.execute(delete(Batch).where(Batch.id.in_(batch_ids)))

        db.execute(delete(FileOutput).where(FileOutput.file_path.like(f"/tmp/{TEST_PREFIX}_%")))

        if doc_ids:
            db.execute(
                delete(CurationExtractionResultRecord).where(
                    CurationExtractionResultRecord.document_id.in_(doc_ids)
                )
            )

        if flow_ids:
            db.execute(delete(CurationFlow).where(CurationFlow.id.in_(flow_ids)))
        if doc_ids:
            db.execute(delete(PDFDocument).where(PDFDocument.id.in_(doc_ids)))
        db.execute(delete(User).where(User.auth_sub.like(f"{TEST_PREFIX}_%")))
        db.commit()
    except Exception:
        db.rollback()
        raise


def _flow_definition(adapter_keys: Iterable[str], *, exit_agent_id: str = "curation_handoff") -> dict:
    nodes: list[dict[str, Any]] = [
        {"id": "pdf", "type": "agent", "data": {"agent_id": "pdf_extraction"}},
    ]
    edges: list[dict[str, str]] = []
    previous = "pdf"
    for index, adapter_key in enumerate(adapter_keys, start=1):
        node_id = f"extract_{index}"
        nodes.append(
            {
                "id": node_id,
                "type": "agent",
                "data": {"agent_id": adapter_key, "agent_display_name": adapter_key},
            }
        )
        edges.append({"id": f"edge_{previous}_{node_id}", "source": previous, "target": node_id})
        previous = node_id

    nodes.append({"id": "exit", "type": "agent", "data": {"agent_id": exit_agent_id}})
    edges.append({"id": f"edge_{previous}_exit", "source": previous, "target": "exit"})

    return {
        "version": "1.0",
        "entry_node_id": "pdf",
        "nodes": nodes,
        "edges": edges,
    }


def _create_user(db, suffix: str):
    from src.models.sql.user import User

    user = User(
        auth_sub=f"{TEST_PREFIX}_runner_{suffix}",
        email=f"{TEST_PREFIX}_{suffix}@example.org",
        display_name=f"Handoff Runner {suffix}",
        is_active=True,
    )
    db.add(user)
    db.flush()
    return user


def _create_document(db, user, suffix: str):
    from src.models.sql.pdf_document import PDFDocument

    document = PDFDocument(
        id=uuid4(),
        filename=f"{TEST_PREFIX}_{suffix}.pdf",
        title=f"Handoff fixture {suffix}",
        file_path=f"/tmp/{TEST_PREFIX}_{suffix}.pdf",
        file_hash=uuid4().hex + uuid4().hex,
        file_size=4096,
        page_count=3,
        status="processed",
        user_id=user.id,
    )
    db.add(document)
    db.flush()
    return document


def _create_flow(db, user, suffix: str, adapter_keys: Iterable[str], *, exit_agent_id: str = "curation_handoff"):
    from src.models.sql.curation_flow import CurationFlow

    flow = CurationFlow(
        user_id=user.id,
        name=f"{TEST_PREFIX}_{suffix}",
        description="integration curation handoff flow",
        flow_definition=_flow_definition(adapter_keys, exit_agent_id=exit_agent_id),
    )
    db.add(flow)
    db.flush()
    return flow


def _create_batch(db, user, flow, document):
    from src.lib.batch.service import BatchService

    batch = BatchService(db).create_batch(
        user_id=user.id,
        flow_id=flow.id,
        document_ids=[document.id],
    )
    db.refresh(batch)
    return batch


def _batch_document(db, batch_id):
    from src.models.sql.batch import BatchDocument

    return db.scalars(
        select(BatchDocument).where(BatchDocument.batch_id == batch_id)
    ).one()


def _review_sessions(db, session_ids: list[str]):
    from src.lib.curation_workspace.models import CurationReviewSession

    return list(
        db.scalars(
            select(CurationReviewSession).where(
                CurationReviewSession.id.in_([UUID(session_id) for session_id in session_ids])
            )
        )
    )


def _candidate_count(db, session_ids: list[str]) -> int:
    from src.lib.curation_workspace.models import CurationCandidate

    return len(
        list(
            db.scalars(
                select(CurationCandidate).where(
                    CurationCandidate.session_id.in_(
                        [UUID(session_id) for session_id in session_ids]
                    )
                )
            )
        )
    )


def _handoff_state_counts(db, document_id: UUID) -> tuple[int, int, int, int]:
    from src.lib.curation_workspace.models import (
        CurationCandidate,
        CurationExtractionResultRecord,
        CurationReviewSession,
        DomainEnvelopeModel,
    )

    extraction_count = len(
        list(
            db.scalars(
                select(CurationExtractionResultRecord).where(
                    CurationExtractionResultRecord.document_id == document_id
                )
            )
        )
    )
    envelope_count = len(
        list(
            db.scalars(
                select(DomainEnvelopeModel).where(
                    DomainEnvelopeModel.document_id == document_id
                )
            )
        )
    )
    session_count = len(
        list(
            db.scalars(
                select(CurationReviewSession).where(
                    CurationReviewSession.document_id == document_id
                )
            )
        )
    )
    candidate_count = len(
        list(
            db.scalars(
                select(CurationCandidate)
                .join(
                    CurationReviewSession,
                    CurationReviewSession.id == CurationCandidate.session_id,
                )
                .where(CurationReviewSession.document_id == document_id)
            )
        )
    )
    return extraction_count, envelope_count, session_count, candidate_count


def _extraction_record(
    *,
    adapter_key: str,
    document_id: str,
    user_id: str,
    session_id: str,
    flow_run_id: str,
):
    from src.schemas.curation_workspace import (
        CurationExtractionResultRecord,
        CurationExtractionSourceKind,
    )

    fixture_name = (
        "tool_verified_gene_expression_paper"
        if adapter_key == "gene_expression"
        else "tool_verified_gene_paper"
    )
    payload = build_domain_envelope_extraction_payload(load_evidence_fixture(fixture_name))
    candidate_count = len(payload.get("curatable_objects") or [])
    extraction_result_id = f"{flow_run_id}:{adapter_key}:source"
    return CurationExtractionResultRecord(
        extraction_result_id=extraction_result_id,
        document_id=document_id,
        adapter_key=adapter_key,
        agent_key=f"{adapter_key}_extractor",
        source_kind=CurationExtractionSourceKind.FLOW,
        origin_session_id=session_id,
        trace_id=f"trace-{adapter_key}",
        flow_run_id=flow_run_id,
        user_id=user_id,
        candidate_count=candidate_count,
        conversation_summary=f"Prepared {adapter_key} fixture for handoff.",
        payload_json=payload,
        created_at=datetime.now(timezone.utc),
        metadata={
            "project_key": f"{TEST_PREFIX}_{adapter_key}",
            "envelope_id": f"{TEST_PREFIX}:{flow_run_id}:{adapter_key}",
        },
    )


@contextmanager
def _patched_handoff_execute_flow(
    adapter_keys: list[str],
    *,
    failure_boundary: str | None = None,
):
    from src.lib.flows.executor import execute_flow as original_execute_flow
    from src.lib.curation_workspace.extraction_results import ExtractionEnvelopeCandidate
    from src.models.sql.database import SessionLocal
    import src.lib.curation_workspace.bootstrap_service as bootstrap_service
    import src.lib.flows.executor as executor

    original_persist = executor.persist_idempotent_extraction_results
    original_materialize = executor.ensure_domain_envelope_materialization
    original_prep = bootstrap_service.run_curation_prep
    original_bootstrap = bootstrap_service.bootstrap_document_session

    def _raise_after(boundary: str, result):
        if failure_boundary == boundary:
            raise RuntimeError(f"injected {boundary} failure")
        return result

    def _persist_with_fault(requests, *, db=None):
        return _raise_after("canonical_insert", original_persist(requests, db=db))

    def _materialize_with_fault(record, *, persist, db=None):
        return _raise_after(
            "materialization",
            original_materialize(record, persist=persist, db=db),
        )

    async def _prep_with_fault(*args, **kwargs):
        return _raise_after("prep", await original_prep(*args, **kwargs))

    async def _bootstrap_with_fault(*args, **kwargs):
        return _raise_after("review_session", await original_bootstrap(*args, **kwargs))

    executor.persist_idempotent_extraction_results = _persist_with_fault
    executor.ensure_domain_envelope_materialization = _materialize_with_fault
    bootstrap_service.run_curation_prep = _prep_with_fault
    bootstrap_service.bootstrap_document_session = _bootstrap_with_fault

    async def _fake_execute_flow(**kwargs):
        document_id = kwargs["document_id"]
        user_id = kwargs["user_id"]
        session_id = kwargs["session_id"]
        flow_run_id = kwargs["flow_run_id"]
        extraction_fixtures = [
            _extraction_record(
                adapter_key=adapter_key,
                document_id=document_id,
                user_id=user_id,
                session_id=session_id,
                flow_run_id=flow_run_id,
            )
            for adapter_key in adapter_keys
        ]
        candidates = [
            ExtractionEnvelopeCandidate(
                agent_key=record.agent_key,
                adapter_key=record.adapter_key,
                payload_json=record.payload_json,
                candidate_count=record.candidate_count,
                conversation_summary=record.conversation_summary,
                metadata=dict(record.metadata),
            )
            for record in extraction_fixtures
        ]
        handoff_db = SessionLocal()
        if failure_boundary == "final_commit":
            handoff_db.commit = lambda: (_ for _ in ()).throw(
                RuntimeError("injected final_commit failure")
            )
        try:
            extraction_results = executor._persist_flow_extraction_candidates(
                candidates=candidates,
                document_id=document_id,
                user_id=user_id,
                session_id=session_id,
                trace_id=f"trace-{adapter_keys[0]}",
                flow_run_id=flow_run_id,
                db=handoff_db,
            )
            handoff = await bootstrap_service.run_flow_curation_handoff(
                extraction_results=extraction_results,
                document_id=document_id,
                runner_user_id=user_id,
                flow_run_id=flow_run_id,
                origin_session_id=session_id,
                conversation_summary="Integration handoff fixture.",
                db=handoff_db,
            )
        finally:
            handoff_db.close()

        yield {
            "type": "CURATION_HANDOFF_READY",
            "details": {
                "review_session_ids": handoff.review_session_ids,
                "adapter_keys": handoff.adapter_keys,
                "document_id": document_id,
            },
        }
        yield {
            "type": "FLOW_FINISHED",
            "data": {
                "status": "completed",
                "review_session_ids": handoff.review_session_ids,
            },
        }

    executor.execute_flow = _fake_execute_flow
    try:
        yield
    finally:
        executor.execute_flow = original_execute_flow
        executor.persist_idempotent_extraction_results = original_persist
        executor.ensure_domain_envelope_materialization = original_materialize
        bootstrap_service.run_curation_prep = original_prep
        bootstrap_service.bootstrap_document_session = original_bootstrap


@contextmanager
def _patched_file_ready_execute_flow(file_id: UUID):
    from src.lib.flows.executor import execute_flow as original_execute_flow

    async def _fake_execute_flow(**_kwargs):
        yield {
            "type": "FILE_READY",
            "details": {
                "download_url": f"/api/weaviate/documents/download/{file_id}",
                "file_id": str(file_id),
                "filename": "handoff-regression.json",
            },
        }
        yield {"type": "FLOW_FINISHED", "data": {"status": "completed"}}

    import src.lib.flows.executor as executor

    executor.execute_flow = _fake_execute_flow
    try:
        yield
    finally:
        executor.execute_flow = original_execute_flow


def _run_batch(batch_id):
    from src.lib.batch.processor import process_batch_task

    process_batch_task(batch_id)


def test_batch_flow_ending_in_curation_handoff_creates_owned_sessions(handoff_db):
    from src.lib.curation_workspace.curation_prep_constants import CURATION_PREP_AGENT_ID
    from src.lib.curation_workspace.domain_envelope_normalization import (
        domain_envelope_from_extraction_result,
    )
    from src.lib.curation_workspace.extraction_results import (
        canonical_extraction_payload_hash,
        list_extraction_results,
    )
    from src.lib.curation_workspace.models import (
        CurationCandidate,
        CurationExtractionResultRecord,
        DomainEnvelopeModel,
    )
    from src.lib.domain_envelopes.persistence import domain_envelope_payload_hash
    from src.lib.batch.validation import validate_flow_for_batch
    from src.models.sql.batch import BatchDocumentStatus
    from src.schemas.curation_workspace import CurationExtractionSourceKind

    suffix = uuid4().hex[:10]
    user = _create_user(handoff_db, suffix)
    document = _create_document(handoff_db, user, suffix)
    flow = _create_flow(handoff_db, user, suffix, ["gene"])
    handoff_db.commit()

    assert validate_flow_for_batch(flow.flow_definition).valid is True

    batch = _create_batch(handoff_db, user, flow, document)
    with _patched_handoff_execute_flow(["gene"]):
        _run_batch(batch.id)

    handoff_db.expire_all()
    batch_doc = _batch_document(handoff_db, batch.id)
    assert batch_doc.status == BatchDocumentStatus.COMPLETED
    assert batch_doc.result_file_path is None
    assert batch_doc.review_session_ids

    sessions = _review_sessions(handoff_db, batch_doc.review_session_ids)
    assert len(sessions) == 1
    assert sessions[0].adapter_key == "gene"
    assert sessions[0].created_by_id == user.auth_sub
    assert sessions[0].assigned_curator_id == user.auth_sub
    assert _candidate_count(handoff_db, batch_doc.review_session_ids) > 0

    source_row = handoff_db.scalars(
        select(CurationExtractionResultRecord).where(
            CurationExtractionResultRecord.document_id == document.id,
            CurationExtractionResultRecord.source_kind == CurationExtractionSourceKind.FLOW,
            CurationExtractionResultRecord.agent_key != CURATION_PREP_AGENT_ID,
        )
    ).one()
    source_schema = next(
        record
        for record in list_extraction_results(
            db=handoff_db,
            document_id=str(document.id),
            source_kind=CurationExtractionSourceKind.FLOW,
        )
        if record.extraction_result_id == str(source_row.id)
    )
    envelope_row = handoff_db.scalars(
        select(DomainEnvelopeModel).where(
            DomainEnvelopeModel.source_extraction_result_id == str(source_row.id)
        )
    ).one()
    candidate_rows = list(
        handoff_db.scalars(
            select(CurationCandidate).where(CurationCandidate.session_id == sessions[0].id)
        )
    )
    assert candidate_rows
    prep_row = handoff_db.get(
        CurationExtractionResultRecord,
        candidate_rows[0].extraction_result_id,
    )

    assert source_row.document_id == document.id
    assert source_row.user_id == user.auth_sub
    assert source_row.flow_run_id == sessions[0].flow_run_id
    assert source_row.adapter_key == sessions[0].adapter_key == "gene"
    assert source_row.payload_hash == canonical_extraction_payload_hash(source_row.payload_json)
    assert envelope_row.document_id == source_row.document_id
    assert envelope_row.flow_run_id == source_row.flow_run_id
    assert envelope_row.adapter_key == source_row.adapter_key
    assert envelope_row.envelope_json["metadata"]["source_extraction_result_id"] == str(
        source_row.id
    )
    assert envelope_row.source_payload_hash == domain_envelope_payload_hash(
        domain_envelope_from_extraction_result(source_schema)
    )
    assert {candidate.envelope_id for candidate in candidate_rows} == {
        envelope_row.envelope_id
    }
    assert prep_row is not None
    assert prep_row.agent_key == CURATION_PREP_AGENT_ID
    assert prep_row.document_id == source_row.document_id
    assert prep_row.user_id == source_row.user_id
    assert prep_row.flow_run_id == source_row.flow_run_id
    assert prep_row.adapter_key == source_row.adapter_key


def test_multi_adapter_flow_creates_one_session_per_adapter(handoff_db):
    from src.lib.batch.service import BatchService
    from src.models.sql.batch import BatchDocumentStatus

    suffix = uuid4().hex[:10]
    user = _create_user(handoff_db, suffix)
    document = _create_document(handoff_db, user, suffix)
    flow = _create_flow(handoff_db, user, suffix, ["gene", "gene_expression"])
    handoff_db.commit()

    batch = _create_batch(handoff_db, user, flow, document)
    with _patched_handoff_execute_flow(["gene", "gene_expression"]):
        _run_batch(batch.id)

    handoff_db.expire_all()
    batch_doc = _batch_document(handoff_db, batch.id)
    assert batch_doc.status == BatchDocumentStatus.COMPLETED
    assert batch_doc.review_session_ids is not None
    assert len(batch_doc.review_session_ids) == 2

    sessions = _review_sessions(handoff_db, batch_doc.review_session_ids)
    assert {session.adapter_key for session in sessions} == {"gene", "gene_expression"}
    assert {session.assigned_curator_id for session in sessions} == {user.auth_sub}
    assert _candidate_count(handoff_db, batch_doc.review_session_ids) >= 2

    batch_service = BatchService(handoff_db)
    persisted_batch = batch_service.get_batch(batch.id, user.id)
    assert persisted_batch is not None
    batch_response = batch_service.batch_to_response(persisted_batch, flow_name=flow.name)
    response_document = batch_response.documents[0]
    assert response_document.review_session_ids == batch_doc.review_session_ids
    assert response_document.adapter_keys == [
        session.adapter_key
        for session_id in batch_doc.review_session_ids
        for session in sessions
        if str(session.id) == session_id
    ]
    assert response_document.extraction_result_ids
    assert response_document.extraction_result_refs == [
        {
            "result_ref": f"extraction-result:{result_id}",
            "extraction_result_id": result_id,
            "adapter_key": ref["adapter_key"],
            "agent_key": ref["agent_key"],
            "candidate_count": ref["candidate_count"],
            "trace_id": ref["trace_id"],
        }
        for result_id, ref in zip(
            response_document.extraction_result_ids,
            response_document.extraction_result_refs,
            strict=True,
        )
    ]
    assert response_document.flow_run_id == str(batch.id)


async def test_canonical_handoff_retry_reuses_all_persisted_state(handoff_db):
    from src.lib.curation_workspace.bootstrap_service import run_flow_curation_handoff
    from src.lib.curation_workspace.domain_envelope_normalization import (
        domain_envelope_from_extraction_result,
    )
    from src.lib.curation_workspace.extraction_results import ExtractionEnvelopeCandidate
    from src.lib.curation_workspace.models import (
        CurationExtractionResultRecord,
        DomainEnvelopeModel,
    )
    from src.lib.flows.executor import _persist_flow_extraction_candidates

    suffix = uuid4().hex[:10]
    user = _create_user(handoff_db, suffix)
    document = _create_document(handoff_db, user, suffix)
    handoff_db.commit()

    flow_run_id = f"{TEST_PREFIX}_retry_{suffix}"
    origin_session_id = f"{TEST_PREFIX}_session_{suffix}"
    fixture = _extraction_record(
        adapter_key="gene",
        document_id=str(document.id),
        user_id=user.auth_sub,
        session_id=origin_session_id,
        flow_run_id=flow_run_id,
    )
    canonical_payload = domain_envelope_from_extraction_result(fixture).model_dump(
        mode="json"
    )
    assert canonical_payload["metadata"]["source_extraction_result_id"].endswith(
        ":source"
    )
    candidate = ExtractionEnvelopeCandidate(
        agent_key=fixture.agent_key,
        adapter_key=fixture.adapter_key,
        payload_json=canonical_payload,
        candidate_count=fixture.candidate_count,
        conversation_summary=fixture.conversation_summary,
        metadata=dict(fixture.metadata),
    )

    async def _run_once():
        source_records = _persist_flow_extraction_candidates(
            candidates=[candidate],
            document_id=str(document.id),
            user_id=user.auth_sub,
            session_id=origin_session_id,
            trace_id="trace-retry",
            flow_run_id=flow_run_id,
            db=handoff_db,
        )
        return await run_flow_curation_handoff(
            extraction_results=source_records,
            document_id=str(document.id),
            runner_user_id=user.auth_sub,
            flow_run_id=flow_run_id,
            origin_session_id=origin_session_id,
            conversation_summary="Retry integration fixture.",
            db=handoff_db,
        )

    first = await _run_once()
    first_counts = _handoff_state_counts(handoff_db, document.id)
    second = await _run_once()
    handoff_db.expire_all()
    second_counts = _handoff_state_counts(handoff_db, document.id)

    assert second.review_session_ids == first.review_session_ids
    assert second.adapter_keys == first.adapter_keys == ["gene"]
    assert second_counts == first_counts
    assert first_counts[0] == 2  # canonical FLOW source plus curation prep output
    assert first_counts[1] == 1
    assert first_counts[2] == 1
    assert first_counts[3] > 0

    source_row = handoff_db.scalars(
        select(CurationExtractionResultRecord).where(
            CurationExtractionResultRecord.document_id == document.id,
            CurationExtractionResultRecord.agent_key == fixture.agent_key,
        )
    ).one()
    envelope_row = handoff_db.scalars(
        select(DomainEnvelopeModel).where(
            DomainEnvelopeModel.source_extraction_result_id == str(source_row.id)
        )
    ).one()
    assert "source_extraction_result_id" not in source_row.payload_json["metadata"]
    assert envelope_row.envelope_json["metadata"][
        "source_extraction_result_id"
    ] == str(source_row.id)


@pytest.mark.parametrize(
    "failure_boundary",
    [
        "canonical_insert",
        "materialization",
        "prep",
        "review_session",
        "final_commit",
    ],
)
def test_handoff_failure_rolls_back_all_ready_state(handoff_db, failure_boundary):
    from src.models.sql.batch import BatchDocumentStatus

    suffix = uuid4().hex[:10]
    user = _create_user(handoff_db, suffix)
    document = _create_document(handoff_db, user, suffix)
    flow = _create_flow(handoff_db, user, suffix, ["gene"])
    handoff_db.commit()

    batch = _create_batch(handoff_db, user, flow, document)
    with _patched_handoff_execute_flow(
        ["gene"],
        failure_boundary=failure_boundary,
    ):
        _run_batch(batch.id)

    handoff_db.expire_all()
    batch_doc = _batch_document(handoff_db, batch.id)
    assert batch_doc.status == BatchDocumentStatus.FAILED
    assert batch_doc.review_session_ids is None
    assert _handoff_state_counts(handoff_db, document.id) == (0, 0, 0, 0)


def test_completed_handoff_batch_is_not_rerun(handoff_db):
    suffix = uuid4().hex[:10]
    user = _create_user(handoff_db, suffix)
    document = _create_document(handoff_db, user, suffix)
    flow = _create_flow(handoff_db, user, suffix, ["gene", "gene_expression"])
    handoff_db.commit()

    batch = _create_batch(handoff_db, user, flow, document)
    with _patched_handoff_execute_flow(["gene", "gene_expression"]):
        _run_batch(batch.id)

    handoff_db.expire_all()
    first_session_ids = list(_batch_document(handoff_db, batch.id).review_session_ids or [])
    assert len(first_session_ids) == 2

    with _patched_handoff_execute_flow(["gene", "gene_expression"]):
        _run_batch(batch.id)

    handoff_db.expire_all()
    second_session_ids = list(_batch_document(handoff_db, batch.id).review_session_ids or [])
    assert second_session_ids == first_session_ids

    sessions = _review_sessions(handoff_db, second_session_ids)
    assert len(sessions) == 2
    assert {session.session_version for session in sessions} == {1}


def test_file_output_batch_flow_still_completes_with_result_file_path(handoff_db):
    from src.models.sql.batch import BatchDocumentStatus
    from src.models.sql.file_output import FileOutput

    suffix = uuid4().hex[:10]
    user = _create_user(handoff_db, suffix)
    document = _create_document(handoff_db, user, suffix)
    flow = _create_flow(handoff_db, user, suffix, ["gene"], exit_agent_id="json_formatter")
    file_output = FileOutput(
        id=uuid4(),
        filename=f"{TEST_PREFIX}_{suffix}.json",
        file_path=f"/tmp/{TEST_PREFIX}_{suffix}.json",
        file_type="json",
        file_size=128,
        file_hash=uuid4().hex + uuid4().hex,
        curator_id=user.auth_sub,
        session_id=f"batch-file-{suffix}",
        trace_id=uuid4().hex,
        agent_name="JSON Formatter",
    )
    handoff_db.add(file_output)
    handoff_db.commit()

    batch = _create_batch(handoff_db, user, flow, document)
    with _patched_file_ready_execute_flow(file_output.id):
        _run_batch(batch.id)

    handoff_db.expire_all()
    batch_doc = _batch_document(handoff_db, batch.id)
    assert batch_doc.status == BatchDocumentStatus.COMPLETED
    assert batch_doc.result_file_path == f"/api/weaviate/documents/download/{file_output.id}"
    assert batch_doc.review_session_ids is None

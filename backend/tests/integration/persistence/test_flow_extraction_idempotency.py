"""Real-Postgres coverage for FLOW extraction persistence idempotency."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Barrier, Lock
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, func, select

from src.lib.curation_workspace import extraction_results as extraction_results_module
from src.lib.curation_workspace.extraction_results import (
    ExtractionResultPayloadMismatchError,
    build_flow_extraction_idempotency_key,
    canonical_extraction_payload_hash,
    persist_idempotent_extraction_results,
)
from src.lib.curation_workspace.models import (
    CurationExtractionResultRecord as ExtractionResultModel,
)
from src.models.sql.database import SessionLocal
from src.models.sql.pdf_document import PDFDocument
from src.schemas.curation_workspace import (
    CurationExtractionPersistenceRequest,
    CurationExtractionSourceKind,
)


BACKEND_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module", autouse=True)
def migrated_database():
    alembic_config = Config(str(BACKEND_ROOT / "alembic.ini"))
    command.upgrade(alembic_config, "head")


def _document(*, title: str) -> PDFDocument:
    document_id = uuid4()
    hex_value = document_id.hex
    return PDFDocument(
        id=document_id,
        filename=f"flow_idempotency_{hex_value}.pdf",
        title=title,
        file_path=f"{document_id}/flow.pdf",
        file_hash=f"{hex_value}{hex_value}",
        file_size=2048,
        page_count=3,
        upload_timestamp=datetime.now(timezone.utc),
        last_accessed=datetime.now(timezone.utc),
        status="processed",
    )


def _request(*, document_id: str, payload: dict) -> CurationExtractionPersistenceRequest:
    candidate_identity = "flow-1:1:ask_gene_extractor_specialist:gene-extractor"
    payload_hash = canonical_extraction_payload_hash(payload)
    idempotency_key = build_flow_extraction_idempotency_key(
        document_id=document_id,
        user_id="curator-1",
        origin_session_id="flow-session-1",
        flow_run_id="flow-run-1",
        adapter_key="gene",
        agent_key="gene-extractor",
        source_kind=CurationExtractionSourceKind.FLOW,
        candidate_identity=candidate_identity,
    )
    return CurationExtractionPersistenceRequest(
        document_id=document_id,
        adapter_key="gene",
        agent_key="gene-extractor",
        source_kind=CurationExtractionSourceKind.FLOW,
        origin_session_id="flow-session-1",
        trace_id="trace-1",
        flow_run_id="flow-run-1",
        user_id="curator-1",
        candidate_count=1,
        payload_json=payload,
        idempotency_key=idempotency_key,
        payload_hash=payload_hash,
        metadata={"flow_step_key": candidate_identity},
    )


def test_flow_persistence_concurrent_writers_converge_without_losing_outer_work(
    monkeypatch,
):
    source_document = _document(title="FLOW source")
    with SessionLocal() as setup_session:
        setup_session.add(source_document)
        setup_session.commit()

    payload = {
        "envelope_id": "flow:flow-run-1:gene-step-1",
        "domain_pack_id": "gene",
        "domain_pack_version": "0.1.0",
        "status": "extracted",
        "extracted_objects": [
            {
                "object_type": "gene_mention_evidence",
                "pending_ref_id": "gene-notch",
                "payload": {"mention": "notch"},
                "evidence_record_ids": [],
            }
        ],
        "validation_findings": [],
        "history": [],
        "metadata": {},
    }
    request = _request(document_id=str(source_document.id), payload=payload)

    initial_lookup_barrier = Barrier(2)
    lookup_lock = Lock()
    absent_lookup_count = 0
    original_lookup = extraction_results_module._load_extraction_result_by_idempotency_key

    def _synchronize_initial_absent_lookups(session, idempotency_key):
        nonlocal absent_lookup_count
        row = original_lookup(session, idempotency_key)
        if row is not None:
            return row
        with lookup_lock:
            absent_lookup_count += 1
            should_wait = absent_lookup_count <= 2
        if should_wait:
            initial_lookup_barrier.wait(timeout=10)
        return None

    monkeypatch.setattr(
        extraction_results_module,
        "_load_extraction_result_by_idempotency_key",
        _synchronize_initial_absent_lookups,
    )

    outer_document_ids = [uuid4(), uuid4()]

    def _write(writer_index: int) -> str:
        with SessionLocal() as session:
            outer_document = _document(title=f"Unrelated writer {writer_index}")
            outer_document.id = outer_document_ids[writer_index]
            session.add(outer_document)
            response = persist_idempotent_extraction_results([request], db=session)[0]
            session.commit()
            return response.extraction_result.extraction_result_id

    with ThreadPoolExecutor(max_workers=2) as executor:
        result_ids = list(executor.map(_write, range(2)))

    with SessionLocal() as verification_session:
        rows = verification_session.scalars(
            select(ExtractionResultModel).where(
                ExtractionResultModel.idempotency_key == request.idempotency_key
            )
        ).all()
        preserved_outer_work = verification_session.scalar(
            select(func.count())
            .select_from(PDFDocument)
            .where(PDFDocument.id.in_(outer_document_ids))
        )

        assert len(rows) == 1
        assert result_ids == [str(rows[0].id), str(rows[0].id)]
        assert preserved_outer_work == 2

        retry = persist_idempotent_extraction_results([request], db=verification_session)
        assert retry[0].extraction_result.extraction_result_id == str(rows[0].id)

        incompatible_payload = {
            **payload,
            "extracted_objects": [
                {
                    **payload["extracted_objects"][0],
                    "payload": {"mention": "wingless"},
                }
            ],
        }
        incompatible = request.model_copy(
            update={
                "payload_json": incompatible_payload,
                "payload_hash": canonical_extraction_payload_hash(
                    incompatible_payload
                ),
            }
        )
        with pytest.raises(
            ExtractionResultPayloadMismatchError,
            match="idempotency payload mismatch",
        ):
            persist_idempotent_extraction_results(
                [incompatible],
                db=verification_session,
            )

        verification_session.rollback()
        verification_session.execute(
            delete(ExtractionResultModel).where(
                ExtractionResultModel.idempotency_key == request.idempotency_key
            )
        )
        verification_session.execute(
            delete(PDFDocument).where(
                PDFDocument.id.in_([source_document.id, *outer_document_ids])
            )
        )
        verification_session.commit()

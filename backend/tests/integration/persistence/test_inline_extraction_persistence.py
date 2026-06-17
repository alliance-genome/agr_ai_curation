"""Real-DB integration coverage for inline validated extraction persistence.

These tests exercise ``persist_inline_validated_extraction_result`` against the
isolated ``docker-compose.test.yml`` Postgres, so they prove behavior at the
actual database boundary that the unit tests (which use a fake session) cannot:

- the persistence seam creates a durable row independent of any chat-turn /
  ``RUN_FINISHED`` handling (design Part 6 integration scenario 1);
- idempotency: a second call with identical key material returns the existing
  row, ``created_new=False``, with no second row (design Part 2 / Follow-up 3b);
- the partial unique index ``uq_extraction_results_idempotency_key`` rejects a
  direct duplicate INSERT at the DB boundary (Follow-up 3b -- highest value);
- a non-fatal ``validator_error`` finding survives into the persisted payload
  (Follow-up 3, backend-layer assertion).

Run by path so this directory's isolated conftest does not apply parent autouse
mocks; these tests use real DB sessions only.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from src.lib.curation_workspace.extraction_results import (
    persist_inline_validated_extraction_result,
)
from src.lib.curation_workspace.models import (
    CurationExtractionResultRecord as ExtractionResultModel,
)
from src.models.sql.database import SessionLocal
from src.models.sql.pdf_document import PDFDocument
from src.schemas.curation_workspace import CurationExtractionSourceKind


BACKEND_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module", autouse=True)
def migrated_database():
    """Ensure this branch's idempotency-key migration is applied before tests."""

    alembic_config = Config(str(BACKEND_ROOT / "alembic.ini"))
    command.upgrade(alembic_config, "head")


@pytest.fixture
def db_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def document_id(db_session):
    """Create a real ``pdf_documents`` row to satisfy the extraction-results FK."""

    doc_id = uuid4()
    hex_value = doc_id.hex
    db_session.add(
        PDFDocument(
            id=doc_id,
            filename=f"test_inline_persistence_{hex_value}.pdf",
            title="Inline persistence fixture",
            file_path=f"{doc_id}/inline.pdf",
            file_hash=f"{hex_value}{hex_value}",
            file_size=2048,
            page_count=3,
            upload_timestamp=datetime.now(timezone.utc),
            last_accessed=datetime.now(timezone.utc),
            status="processed",
        )
    )
    db_session.commit()
    try:
        yield str(doc_id)
    finally:
        db_session.rollback()
        db_session.execute(
            delete(ExtractionResultModel).where(
                ExtractionResultModel.document_id == doc_id
            )
        )
        db_session.execute(delete(PDFDocument).where(PDFDocument.id == doc_id))
        db_session.commit()


def _canonical_gene_envelope(*, object_count: int = 1) -> dict:
    """Build a strict canonical domain envelope the inline path accepts.

    ``domain_pack_id="gene"`` resolves to a real pack whose
    ``gene_mention_evidence`` object carries a ``supervisor_manifest`` policy, so
    the same row is later renderable by the manifest/inspect_results path.
    """

    extracted_objects = []
    for index in range(1, object_count + 1):
        extracted_objects.append(
            {
                "object_type": "gene_mention_evidence",
                "object_role": "curatable_unit",
                "pending_ref_id": f"gene-mention-{index}",
                "payload": {
                    "mention": f"gene-{index}",
                    "gene_symbol": f"sym-{index}",
                    "primary_external_id": f"FB:FBgn{index:07d}",
                    "taxon": "NCBITaxon:7227",
                },
                "evidence_record_ids": [f"evidence-{index}"],
            }
        )
    return {
        "envelope_id": f"envelope-{uuid4()}",
        "domain_pack_id": "gene",
        "domain_pack_version": "0.1.0",
        "status": "extracted",
        "extracted_objects": extracted_objects,
        "validation_findings": [],
        "history": [],
        "metadata": {
            "evidence_records": [
                {
                    "evidence_record_id": f"evidence-{index}",
                    "entity": f"gene-{index}",
                    "verified_quote": f"gene-{index} was experimentally analyzed.",
                    "page": index,
                    "section": "Results",
                    "chunk_id": f"chunk-{index}",
                }
                for index in range(1, object_count + 1)
            ]
        },
    }


_BUILDER_FINALIZATION = {
    "builder_run_id": "trace-inline-1",
    "builder_invocation_id": "builder-invocation-inline-1",
    "candidate_ids": ["candidate-1"],
    "source_candidate_ids": ["source-candidate-1"],
}


def _persist(
    db_session,
    document_id,
    *,
    payload,
    origin_session_id,
    trace_id="trace-inline-1",
    builder_finalization=None,
):
    return persist_inline_validated_extraction_result(
        payload_json=payload,
        document_id=document_id,
        agent_key="gene",
        adapter_key="gene",
        tool_name="ask_gene_specialist",
        source_kind=CurationExtractionSourceKind.CHAT,
        origin_session_id=origin_session_id,
        trace_id=trace_id,
        user_id="user-inline-1",
        builder_finalization=builder_finalization or dict(_BUILDER_FINALIZATION),
        db=db_session,
    )


def test_inline_persistence_creates_durable_row_at_db_boundary(db_session, document_id):
    """Scenario 1: the persistence seam writes a real, queryable row.

    The row exists from ``persist_inline_validated_extraction_result`` alone --
    no RUN_FINISHED / chat-turn handling is involved -- proving inline
    persistence is independent of outer-turn completion.
    """

    session_id = f"inline-session-{uuid4()}"
    payload = _canonical_gene_envelope(object_count=2)

    result = _persist(db_session, document_id, payload=payload, origin_session_id=session_id)
    db_session.commit()
    db_session.expire_all()

    assert result.created_new is True
    assert result.result_ref == f"extraction-result:{result.extraction_result_id}"

    rows = db_session.scalars(
        select(ExtractionResultModel).where(
            ExtractionResultModel.origin_session_id == session_id
        )
    ).all()
    assert len(rows) == 1
    row = rows[0]
    assert str(row.id) == result.extraction_result_id
    assert row.source_kind is CurationExtractionSourceKind.CHAT
    assert row.candidate_count == 2
    assert row.idempotency_key == result.idempotency_key
    assert row.payload_hash == result.payload_hash
    assert row.extraction_metadata["persistence_phase"] == "inline_validated_extraction"


def test_inline_persistence_is_idempotent_on_duplicate_call(db_session, document_id):
    """Design Part 2: a duplicate call returns the existing row, not a new one."""

    session_id = f"inline-idem-session-{uuid4()}"
    payload = _canonical_gene_envelope()

    first = _persist(db_session, document_id, payload=payload, origin_session_id=session_id)
    db_session.commit()

    # Re-persist with identical key material (same payload/builder/session/trace).
    second = _persist(
        db_session,
        document_id,
        payload=copy.deepcopy(payload),
        origin_session_id=session_id,
    )
    db_session.commit()
    db_session.expire_all()

    assert first.created_new is True
    assert second.created_new is False
    assert second.extraction_result_id == first.extraction_result_id
    assert second.idempotency_key == first.idempotency_key

    rows = db_session.scalars(
        select(ExtractionResultModel).where(
            ExtractionResultModel.origin_session_id == session_id
        )
    ).all()
    assert len(rows) == 1


def test_partial_unique_index_rejects_direct_duplicate_insert(db_session, document_id):
    """Follow-up 3b (highest value): the DB-level partial unique index holds.

    After one inline-persisted row, a direct second INSERT carrying the same
    ``idempotency_key`` must be rejected by ``uq_extraction_results_idempotency_key``
    -- proving duplicate prevention lives at the database boundary, not just in
    the in-process pre-check.
    """

    session_id = f"inline-uq-session-{uuid4()}"
    payload = _canonical_gene_envelope()

    result = _persist(db_session, document_id, payload=payload, origin_session_id=session_id)
    db_session.commit()

    # Build a fresh row that reuses the SAME idempotency_key. Everything else can
    # differ; only the unique key matters for the constraint.
    from uuid import UUID

    duplicate = ExtractionResultModel(
        document_id=UUID(document_id),
        adapter_key="gene",
        agent_key="gene",
        source_kind=CurationExtractionSourceKind.CHAT,
        origin_session_id=f"{session_id}-other",
        trace_id="trace-inline-other",
        user_id="user-inline-other",
        candidate_count=1,
        payload_json={"envelope_id": "x", "domain_pack_id": "gene", "extracted_objects": []},
        idempotency_key=result.idempotency_key,
        payload_hash="some-other-hash",
        extraction_metadata={},
    )
    db_session.add(duplicate)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()

    db_session.expire_all()
    rows = db_session.scalars(
        select(ExtractionResultModel).where(
            ExtractionResultModel.idempotency_key == result.idempotency_key
        )
    ).all()
    assert len(rows) == 1
    assert str(rows[0].id) == result.extraction_result_id


def test_inline_persistence_retains_non_fatal_validator_finding(db_session, document_id):
    """Follow-up 3: a non-fatal validator_error finding survives into the row.

    This asserts the backend persistence layer (not just the frontend severity
    helper) keeps a non-fatal ``validator_error`` finding in the durable payload.
    """

    session_id = f"inline-finding-session-{uuid4()}"
    payload = _canonical_gene_envelope()
    payload["validation_findings"] = [
        {
            "severity": "warning",
            "status": "open",
            "code": "domain_pack.validator_error",
            "message": "Gene validator could not be run for the target.",
            "object_ref": {
                "pending_ref_id": "gene-mention-1",
                "object_type": "gene_mention_evidence",
            },
            "details": {"fatal": False},
        }
    ]

    result = _persist(db_session, document_id, payload=payload, origin_session_id=session_id)
    db_session.commit()
    db_session.expire_all()

    assert result.created_new is True
    row = db_session.scalar(
        select(ExtractionResultModel).where(
            ExtractionResultModel.origin_session_id == session_id
        )
    )
    assert row is not None
    findings = row.payload_json["validation_findings"]
    assert len(findings) == 1
    finding = findings[0]
    assert finding["code"] == "domain_pack.validator_error"
    assert finding["severity"] == "warning"
    assert finding["details"]["fatal"] is False


def test_inline_persistence_rejects_legacy_row_source(db_session, document_id):
    """Design forward-only rule: the strict inline path refuses legacy envelopes."""

    legacy_payload = {
        "adapter_key": "gene",
        "items": [{"label": "notch"}],
        "raw_mentions": [],
        "exclusions": [],
        "ambiguities": [],
        "run_summary": {"candidate_count": 1, "kept_count": 1},
    }
    with pytest.raises(ValueError, match="strict canonical domain envelope"):
        _persist(
            db_session,
            document_id,
            payload=legacy_payload,
            origin_session_id=f"inline-legacy-session-{uuid4()}",
        )

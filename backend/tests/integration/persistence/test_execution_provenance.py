"""Real extraction/checkpoint/export persistence of server-captured provenance."""

from contextlib import nullcontext
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4

from alembic import command  # pyright: ignore[reportAttributeAccessIssue]
from alembic.config import Config  # pyright: ignore[reportMissingImports]
import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
import yaml

from src.lib.curation_workspace.benchmark_snapshots import (
    canonical_json_bytes,
    create_benchmark_snapshot,
    load_benchmark_snapshot_bytes,
)
from src.lib.curation_workspace.curation_prep_service import ensure_domain_envelope_materialization
from src.lib.curation_workspace.execution_provenance import capture_source_document
from src.lib.curation_workspace.extraction_results import (
    persist_extraction_result,
    persist_inline_validated_extraction_result,
)
from src.lib.curation_workspace.models import CurationReviewSession, DomainEnvelopeModel
from src.lib.domain_envelopes.persistence import (
    DomainEnvelopeCheckpointRequest,
    DomainEnvelopePersistenceError,
    write_domain_envelope_checkpoint,
)
from src.models.sql.database import SessionLocal, engine
from src.models.sql.pdf_document import PDFDocument
from src.schemas.curation_workspace import CurationExtractionPersistenceRequest, CurationExtractionSourceKind
from src.schemas.domain_envelope import DomainEnvelope, HistoryActorType, HistoryEvent, HistoryEventKind
from src.schemas.execution_provenance import ExtractionExecutionContext
from tests.pdf_document_test_support import ensure_test_pdf_owner


BACKEND_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module", autouse=True)
def migrated_database():
    command.upgrade(Config(str(BACKEND_ROOT / "alembic.ini")), "head")


@pytest.fixture
def db_session():
    # No whole-table cleanup: every inserted row belongs to this rolled-back transaction.
    with SessionLocal() as db:
        yield db
        db.rollback()


def _document(db):
    subject = f"execution-provenance-{uuid4()}"
    owner_id = ensure_test_pdf_owner(db, auth_sub=subject)
    document = PDFDocument(
        id=uuid4(), user_id=owner_id, filename="execution-provenance.pdf",
        file_path=f"execution-provenance/{uuid4()}.pdf", file_hash=uuid4().hex * 2,
        file_size=100, page_count=1, status="completed",
        source_provider="fixture_provider", source_provider_reference_curie="PMID:12345",
        source_provider_converted_artifact_id="artifact-original",
        source_converted_artifact_sha256=hashlib.sha256(b"# Original source\r\n").hexdigest(),
    )
    db.add(document)
    db.flush()
    return document, subject


def _fixture_payload():
    path = BACKEND_ROOT.parent / "packages/alliance/domain_packs/gene/fixtures/daf16_builder_pending.yaml"
    payload = yaml.safe_load(path.read_text())["fixtures"][0]["envelope"]
    payload["envelope_id"] = f"execution-provenance-{uuid4()}"
    return payload


def _checkpoint(db, row, envelope, session_id, *, supplied=None):
    return write_domain_envelope_checkpoint(
        db,
        DomainEnvelopeCheckpointRequest(
            project_key=row.project_key, envelope=envelope, expected_revision=row.revision,
            document_id=row.document_id, flow_run_id=row.flow_run_id, session_id=session_id,
            execution_context=supplied,
        ),
    )


@pytest.mark.parametrize("source_kind,historical", [("flow", False), ("flow", True), ("chat", False)])
def test_extraction_checkpoint_revisions_and_export_keep_original_context(db_session, monkeypatch, source_kind, historical):
    db = db_session
    document, subject = _document(db)
    # Exercise the real owner-filtered SQL lookup inside this test transaction.
    monkeypatch.setattr("src.models.sql.database.SessionLocal", lambda: nullcontext(db))
    source = capture_source_document(str(document.id), subject)
    assert source is not None
    original = ExtractionExecutionContext(
        captured_at=datetime.now(timezone.utc), source_kind=source_kind,
        flow_id="fixture-flow" if source_kind == "flow" else None,
        step_id="gene-step", agent_key="gene",
        executed_query="  Extract experimentally relevant genes.\r\nKeep β names.\n",
        document=source,
    )
    forged = original.model_copy(update={"executed_query": "model-authored replacement"})
    payload = _fixture_payload()
    payload["execution_context"] = forged.model_dump(mode="json")
    metadata = {} if historical else {"execution_context": original.model_dump(mode="json")}
    if source_kind == "chat":
        record = persist_inline_validated_extraction_result(
            document_id=str(document.id), agent_key="gene", adapter_key="gene",
            tool_name="ask_gene_specialist", source_kind=CurationExtractionSourceKind.CHAT,
            origin_session_id=str(uuid4()), trace_id=str(uuid4()),
            user_id=subject, payload_json=payload, metadata=metadata, db=db,
        ).extraction_result
    else:
        record = persist_extraction_result(
            CurationExtractionPersistenceRequest(
                document_id=str(document.id), agent_key="gene", adapter_key="gene",
                source_kind=CurationExtractionSourceKind.FLOW, flow_run_id=str(uuid4()),
                user_id=subject, payload_json=payload, metadata=metadata,
            ),
            db=db,
        ).extraction_result
    ref = ensure_domain_envelope_materialization(record, persist=True, db=db)
    row = db.get(DomainEnvelopeModel, ref.envelope_id)
    assert row is not None
    expected = None if historical else original.model_dump(mode="json")
    assert row.envelope_json["execution_context"] == expected

    review = CurationReviewSession(
        id=uuid4(), adapter_key="gene", document_id=document.id, created_by_id=subject,
        prepared_at=datetime.now(timezone.utc),
    )
    db.add(review)
    db.flush()

    # Mutable source metadata must not alter the context already captured for this extraction.
    document.source_provider_reference_curie = "PMID:99999"
    document.source_provider_converted_artifact_id = "artifact-replaced"
    document.source_converted_artifact_sha256 = "f" * 64
    db.flush()
    validator_envelope = DomainEnvelope.model_validate(row.envelope_json).model_copy(
        update={"execution_context": forged},
    )
    _checkpoint(db, row, validator_envelope, review.id)
    assert row.envelope_json["execution_context"] == expected
    first = create_benchmark_snapshot(
        db, session_id=review.id, envelope_id=row.envelope_id,
        expected_revision=row.revision, current_user_id=subject,
    )
    first_bytes = load_benchmark_snapshot_bytes(db, snapshot_id=UUID(first.snapshot_id), current_user_id=subject)

    curator_envelope = DomainEnvelope.model_validate(row.envelope_json)
    curator_envelope = curator_envelope.model_copy(update={
        "execution_context": None,
        "history": [*curator_envelope.history, HistoryEvent(
            event_id=str(uuid4()), event_type=HistoryEventKind.CURATOR_FIELD_PATCH_ACCEPTED,
            actor_type=HistoryActorType.HUMAN, actor_id=subject, message="Curator revised a field",
        )],
    })
    _checkpoint(db, row, curator_envelope, review.id)
    second = create_benchmark_snapshot(
        db, session_id=review.id, envelope_id=row.envelope_id,
        expected_revision=row.revision, current_user_id=subject,
    )
    second_bytes = load_benchmark_snapshot_bytes(db, snapshot_id=UUID(second.snapshot_id), current_user_id=subject)
    exported = json.loads(second_bytes)
    assert exported["curation_state"] == "curator_modified"
    assert exported["envelope"]["execution_context"] == expected
    assert exported["envelope_digest"] == "sha256:" + hashlib.sha256(
        canonical_json_bytes(exported["envelope"])
    ).hexdigest()
    assert load_benchmark_snapshot_bytes(db, snapshot_id=UUID(first.snapshot_id), current_user_id=subject) == first_bytes
    assert json.loads(first_bytes)["curation_state"] == "ai_untouched"

    with pytest.raises(DomainEnvelopePersistenceError, match="cannot be replaced"):
        with db.begin_nested():
            _checkpoint(db, row, DomainEnvelope.model_validate(row.envelope_json), review.id, supplied=forged)


def test_source_lookup_is_owner_scoped_and_custom_upload_stays_unbound(db_session, monkeypatch):
    document, subject = _document(db_session)
    monkeypatch.setattr("src.models.sql.database.SessionLocal", lambda: nullcontext(db_session))
    assert capture_source_document(str(document.id), "another-owner") is None
    assert capture_source_document(str(uuid4()), subject) is None
    document.source_provider = None
    document.source_provider_reference_curie = None
    document.source_provider_converted_artifact_id = None
    document.source_converted_artifact_sha256 = None
    db_session.flush()
    captured = capture_source_document(str(document.id), subject)
    assert captured is not None and captured.document_id == document.id
    assert captured.reference_curie is None and captured.converted_artifact_sha256 is None


def test_converted_artifact_migration_nullable_column_and_digest_constraint(db_session):
    column = next(item for item in inspect(engine).get_columns("pdf_documents") if item["name"] == "source_converted_artifact_sha256")
    assert column["nullable"] is True and column["default"] is None
    document, _ = _document(db_session)
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            document.source_converted_artifact_sha256 = "not-a-digest"
            db_session.flush()

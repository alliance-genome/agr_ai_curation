"""Unit tests for curation workspace bootstrap selection helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.lib.curation_workspace import bootstrap_service as module
from src.lib.curation_workspace.models import CurationExtractionResultRecord as ExtractionResultModel
from src.models.sql.database import Base
from src.models.sql.pdf_document import PDFDocument
from src.schemas.curation_workspace import (
    CurationDocumentBootstrapRequest,
    CurationExtractionSourceKind,
)


@compiles(PostgresUUID, "sqlite")
def _compile_pg_uuid_for_sqlite(_type, _compiler, **_kwargs):
    return "CHAR(36)"


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


TEST_TABLES = [
    PDFDocument.__table__,
    ExtractionResultModel.__table__,
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


def _create_document(db_session) -> PDFDocument:
    now = datetime(2026, 3, 21, 15, 30, tzinfo=timezone.utc)
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
    flow_run_id: str | None,
    origin_session_id: str | None,
    created_at: datetime,
) -> ExtractionResultModel:
    record = ExtractionResultModel(
        id=uuid4(),
        document_id=document_id,
        adapter_key="reference_adapter",
        agent_key="curation_prep",
        source_kind=CurationExtractionSourceKind.CHAT,
        origin_session_id=origin_session_id,
        trace_id=f"trace-{flow_run_id or origin_session_id or 'none'}",
        flow_run_id=flow_run_id,
        user_id="user-1",
        candidate_count=1,
        conversation_summary="Prep summary.",
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
        extraction_metadata={"final_run_metadata": {"model_name": "gpt-5-mini"}},
        created_at=created_at,
    )
    db_session.add(record)
    db_session.commit()
    return record


def test_select_bootstrap_extraction_result_honors_flow_run_id(db_session):
    document = _create_document(db_session)
    older_matching = _create_extraction_result(
        db_session,
        document_id=document.id,
        flow_run_id="flow-1",
        origin_session_id="chat-session-1",
        created_at=datetime(2026, 3, 21, 15, 30, tzinfo=timezone.utc),
    )
    _create_extraction_result(
        db_session,
        document_id=document.id,
        flow_run_id="flow-2",
        origin_session_id="chat-session-2",
        created_at=datetime(2026, 3, 21, 15, 31, tzinfo=timezone.utc),
    )

    selected = module._select_bootstrap_extraction_result(
        db_session,
        document_id=str(document.id),
        request=CurationDocumentBootstrapRequest(flow_run_id="flow-1"),
    )

    assert selected.id == older_matching.id


def test_select_bootstrap_extraction_result_honors_origin_session_id(db_session):
    document = _create_document(db_session)
    older_matching = _create_extraction_result(
        db_session,
        document_id=document.id,
        flow_run_id="flow-1",
        origin_session_id="chat-session-1",
        created_at=datetime(2026, 3, 21, 15, 30, tzinfo=timezone.utc),
    )
    _create_extraction_result(
        db_session,
        document_id=document.id,
        flow_run_id="flow-1",
        origin_session_id="chat-session-2",
        created_at=datetime(2026, 3, 21, 15, 31, tzinfo=timezone.utc),
    )

    selected = module._select_bootstrap_extraction_result(
        db_session,
        document_id=str(document.id),
        request=CurationDocumentBootstrapRequest(origin_session_id="chat-session-1"),
    )

    assert selected.id == older_matching.id


def test_get_document_bootstrap_availability_returns_true_when_matching_result_exists(db_session):
    document = _create_document(db_session)
    _create_extraction_result(
        db_session,
        document_id=document.id,
        flow_run_id="flow-1",
        origin_session_id="chat-session-1",
        created_at=datetime(2026, 3, 21, 15, 30, tzinfo=timezone.utc),
    )

    availability = module.get_document_bootstrap_availability(
        str(document.id),
        CurationDocumentBootstrapRequest(flow_run_id="flow-1"),
        current_user_id="user-1",
        db=db_session,
    )

    assert availability.eligible is True


def test_get_document_bootstrap_availability_returns_false_when_no_matching_result_exists(db_session):
    document = _create_document(db_session)
    _create_extraction_result(
        db_session,
        document_id=document.id,
        flow_run_id="flow-1",
        origin_session_id="chat-session-1",
        created_at=datetime(2026, 3, 21, 15, 30, tzinfo=timezone.utc),
    )

    availability = module.get_document_bootstrap_availability(
        str(document.id),
        CurationDocumentBootstrapRequest(flow_run_id="flow-missing"),
        current_user_id="user-1",
        db=db_session,
    )

    assert availability.eligible is False


def test_get_document_bootstrap_availability_returns_true_when_chat_prep_can_run(monkeypatch):
    monkeypatch.setattr(module, "_require_document", lambda db, document_id: object())

    def _fake_select_bootstrap_extraction_result(db, *, document_id, request):
        raise module.HTTPException(status_code=404, detail="missing")

    captured: dict[str, object] = {}

    def _fake_validate_chat_curation_prep_request(*, session_id, user_id, db, requested_adapter_keys):
        captured["session_id"] = session_id
        captured["user_id"] = user_id
        captured["db"] = db
        captured["requested_adapter_keys"] = list(requested_adapter_keys)
        return (
            SimpleNamespace(
                extraction_results=[
                    SimpleNamespace(document_id="document-1", flow_run_id="flow-1"),
                ]
            ),
            ["gene"],
        )

    monkeypatch.setattr(
        module,
        "_select_bootstrap_extraction_result",
        _fake_select_bootstrap_extraction_result,
    )
    monkeypatch.setattr(
        module,
        "validate_chat_curation_prep_request",
        _fake_validate_chat_curation_prep_request,
    )

    availability = module.get_document_bootstrap_availability(
        "document-1",
        CurationDocumentBootstrapRequest(
            origin_session_id="chat-session-1",
            adapter_key="gene",
        ),
        current_user_id="user-1",
        db=object(),
    )

    assert availability.eligible is True
    assert captured == {
        "session_id": "chat-session-1",
        "user_id": "user-1",
        "db": captured["db"],
        "requested_adapter_keys": ["gene"],
    }


def test_get_document_bootstrap_availability_returns_false_when_chat_prep_document_mismatches(monkeypatch):
    monkeypatch.setattr(module, "_require_document", lambda db, document_id: object())
    monkeypatch.setattr(
        module,
        "_select_bootstrap_extraction_result",
        lambda db, *, document_id, request: (_ for _ in ()).throw(
            module.HTTPException(status_code=404, detail="missing")
        ),
    )
    monkeypatch.setattr(
        module,
        "validate_chat_curation_prep_request",
        lambda **_kwargs: (
            SimpleNamespace(
                extraction_results=[
                    SimpleNamespace(document_id="different-document", flow_run_id="flow-1"),
                ]
            ),
            ["gene"],
        ),
    )

    availability = module.get_document_bootstrap_availability(
        "document-1",
        CurationDocumentBootstrapRequest(
            origin_session_id="chat-session-1",
            adapter_key="gene",
        ),
        current_user_id="user-1",
        db=object(),
    )

    assert availability.eligible is False


def test_get_document_bootstrap_availability_returns_false_when_chat_prep_flow_run_mismatches(monkeypatch):
    monkeypatch.setattr(module, "_require_document", lambda db, document_id: object())
    monkeypatch.setattr(
        module,
        "_select_bootstrap_extraction_result",
        lambda db, *, document_id, request: (_ for _ in ()).throw(
            module.HTTPException(status_code=404, detail="missing")
        ),
    )
    monkeypatch.setattr(
        module,
        "validate_chat_curation_prep_request",
        lambda **_kwargs: (
            SimpleNamespace(
                extraction_results=[
                    SimpleNamespace(document_id="document-1", flow_run_id="flow-1"),
                ]
            ),
            ["gene"],
        ),
    )

    availability = module.get_document_bootstrap_availability(
        "document-1",
        CurationDocumentBootstrapRequest(
            origin_session_id="chat-session-1",
            adapter_key="gene",
            flow_run_id="flow-missing",
        ),
        current_user_id="user-1",
        db=object(),
    )

    assert availability.eligible is False


def test_replayable_prep_output_uses_persisted_final_run_metadata(db_session):
    document = _create_document(db_session)
    extraction_result = _create_extraction_result(
        db_session,
        document_id=document.id,
        flow_run_id="flow-1",
        origin_session_id="chat-session-1",
        created_at=datetime(2026, 3, 21, 15, 30, tzinfo=timezone.utc),
    )
    extraction_result.payload_json = {
        "candidates": [
            {
                "adapter_key": "reference_adapter",
                "payload": {"title": "APOE"},
                "evidence_records": [
                    {
                        "evidence_record_id": "extract-1:candidate:1:evidence:1",
                        "source": "extracted",
                        "extraction_result_id": "extract-1",
                        "field_paths": ["title"],
                        "anchor": {
                            "anchor_kind": "snippet",
                            "locator_quality": "exact_quote",
                            "supports_decision": "supports",
                            "snippet_text": "APOE finding.",
                            "sentence_text": "APOE finding.",
                            "normalized_text": None,
                            "viewer_search_text": "APOE finding.",
                            "viewer_highlightable": False,
                            "pdfx_markdown_offset_start": None,
                            "pdfx_markdown_offset_end": None,
                            "page_number": 2,
                            "page_label": None,
                            "section_title": "Results",
                            "subsection_title": None,
                            "figure_reference": None,
                            "table_reference": None,
                            "chunk_ids": ["chunk-1"],
                        },
                        "notes": [],
                    }
                ],
                "conversation_context_summary": "Prepared from persisted prep output.",
            }
        ],
        "run_metadata": {
            "model_name": "stale-model-name",
            "token_usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
            },
            "processing_notes": [],
            "warnings": [],
        },
    }
    extraction_result.extraction_metadata = {
        "final_run_metadata": {
            "model_name": "deterministic_programmatic_mapper_v1",
            "token_usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
            },
            "processing_notes": ["Persisted prep metadata should win during replay."],
            "warnings": [],
        }
    }
    db_session.add(extraction_result)
    db_session.commit()

    prep_output = module._replayable_prep_output(extraction_result)

    assert len(prep_output.candidates) == 1
    assert prep_output.run_metadata.model_name == "deterministic_programmatic_mapper_v1"
    assert prep_output.run_metadata.processing_notes == [
        "Persisted prep metadata should win during replay."
    ]


@pytest.mark.asyncio
async def test_bootstrap_document_session_commits_persisted_session(monkeypatch):
    class FakeDb:
        def __init__(self):
            self.commit_calls = 0
            self.rollback_calls = 0
            self._in_transaction = True

        def commit(self):
            self.commit_calls += 1
            self._in_transaction = False

        def rollback(self):
            self.rollback_calls += 1
            self._in_transaction = False

        def in_transaction(self):
            return self._in_transaction

    fake_db = FakeDb()
    extraction_result = SimpleNamespace(
        id=uuid4(),
        source_kind=CurationExtractionSourceKind.CHAT,
        flow_run_id=None,
        origin_session_id="chat-session-1",
        trace_id="trace-1",
    )
    reusable_session = None
    pipeline_request: dict[str, object] = {}
    detail_request: dict[str, object] = {}

    monkeypatch.setattr(module, "_require_document", lambda db, document_id: object())
    monkeypatch.setattr(
        module,
        "_select_bootstrap_extraction_result",
        lambda db, *, document_id, request: extraction_result,
    )
    monkeypatch.setattr(module, "_replayable_prep_output", lambda extraction_result: object())
    monkeypatch.setattr(module, "_resolved_adapter_key", lambda extraction_result: "reference_adapter")
    monkeypatch.setattr(module, "find_reusable_prepared_session", lambda *args, **kwargs: reusable_session)

    async def _run_post_curation_pipeline(request, *, db):
        pipeline_request["request"] = request
        pipeline_request["db"] = db
        return SimpleNamespace(session_id="session-123", created=True)

    monkeypatch.setattr(module, "run_post_curation_pipeline", _run_post_curation_pipeline)

    session_payload = {"session_id": "session-123"}

    def _get_session_detail(db, session_id):
        detail_request["db"] = db
        detail_request["session_id"] = session_id
        return session_payload

    monkeypatch.setattr(module, "get_session_detail", _get_session_detail)
    monkeypatch.setattr(
        module,
        "CurationDocumentBootstrapResponse",
        lambda *, created, session: SimpleNamespace(created=created, session=session),
    )

    response = await module.bootstrap_document_session(
        "document-1",
        CurationDocumentBootstrapRequest(origin_session_id="chat-session-1"),
        current_user_id="user-1",
        db=fake_db,
    )

    assert response.created is True
    assert response.session == session_payload
    assert pipeline_request["db"] is fake_db
    assert detail_request == {"db": fake_db, "session_id": "session-123"}
    assert fake_db.commit_calls == 1
    assert fake_db.rollback_calls == 0


@pytest.mark.asyncio
async def test_ensure_bootstrap_extraction_result_runs_chat_prep_when_missing(monkeypatch):
    selection_calls = {"count": 0}
    prepared_record = SimpleNamespace(id=uuid4(), adapter_key="gene")
    captured: dict[str, object] = {}

    def _fake_select_bootstrap_extraction_result(db, *, document_id, request):
        selection_calls["count"] += 1
        if selection_calls["count"] == 1:
            raise module.HTTPException(status_code=404, detail="missing")
        return prepared_record

    async def _fake_run_chat_curation_prep(request, *, user_id, db):
        captured["request"] = request
        captured["user_id"] = user_id
        captured["db"] = db
        return SimpleNamespace(document_id="document-1")

    monkeypatch.setattr(
        module,
        "_select_bootstrap_extraction_result",
        _fake_select_bootstrap_extraction_result,
    )
    monkeypatch.setattr(
        module,
        "validate_chat_curation_prep_request",
        lambda **_kwargs: (
            SimpleNamespace(
                extraction_results=[
                    SimpleNamespace(document_id="document-1", flow_run_id=None),
                ]
            ),
            ["gene"],
        ),
    )
    monkeypatch.setattr(module, "run_chat_curation_prep", _fake_run_chat_curation_prep)

    result = await module._ensure_bootstrap_extraction_result(
        object(),
        document_id="document-1",
        request=CurationDocumentBootstrapRequest(
            origin_session_id="chat-session-1",
            adapter_key="gene",
        ),
        current_user_id="user-1",
    )

    assert result is prepared_record
    assert selection_calls["count"] == 2
    assert captured["request"].session_id == "chat-session-1"
    assert captured["request"].adapter_keys == ["gene"]
    assert captured["user_id"] == "user-1"


@pytest.mark.asyncio
async def test_ensure_bootstrap_extraction_result_does_not_run_chat_prep_when_selectors_mismatch(
    monkeypatch,
):
    monkeypatch.setattr(
        module,
        "_select_bootstrap_extraction_result",
        lambda db, *, document_id, request: (_ for _ in ()).throw(
            module.HTTPException(status_code=404, detail="missing")
        ),
    )
    monkeypatch.setattr(
        module,
        "validate_chat_curation_prep_request",
        lambda **_kwargs: (
            SimpleNamespace(
                extraction_results=[
                    SimpleNamespace(document_id="document-1", flow_run_id="flow-1"),
                ]
            ),
            ["gene"],
        ),
    )
    monkeypatch.setattr(
        module,
        "run_chat_curation_prep",
        lambda *args, **kwargs: pytest.fail("chat prep should not run for mismatched selectors"),
    )

    with pytest.raises(module.HTTPException) as exc:
        await module._ensure_bootstrap_extraction_result(
            object(),
            document_id="document-1",
            request=CurationDocumentBootstrapRequest(
                origin_session_id="chat-session-1",
                adapter_key="gene",
                flow_run_id="flow-missing",
            ),
            current_user_id="user-1",
        )

    assert exc.value.status_code == 404

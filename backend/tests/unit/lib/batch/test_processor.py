"""Unit tests for batch processor document result handling."""

import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import Mock

import pytest

from src.lib.batch import processor
from src.models.sql.batch import BatchDocumentStatus, BatchStatus


def _build_batch_context():
    batch = SimpleNamespace(
        id=uuid4(),
        user_id=7,
        total_documents=1,
        completed_documents=0,
        failed_documents=0,
    )
    batch_doc = SimpleNamespace(
        id=uuid4(),
        document_id=uuid4(),
        position=0,
        status=None,
        result_file_path=None,
        processing_time_ms=None,
        processed_at=None,
        error_message=None,
    )
    flow = SimpleNamespace(name="Gene Expression Batch Flow")
    return batch, batch_doc, flow


def test_batch_processor_marks_failed_when_no_file_ready(monkeypatch):
    db = Mock()
    batch, batch_doc, flow = _build_batch_context()
    published_events = []

    async def _fake_execute_flow_for_document(**_kwargs):
        return None

    monkeypatch.setattr(processor, "_execute_flow_for_document", _fake_execute_flow_for_document)
    monkeypatch.setattr(
        processor,
        "get_batch_broadcaster",
        lambda: SimpleNamespace(
            publish_sync=lambda _batch_id, event: published_events.append(event)
        ),
    )

    with pytest.raises(RuntimeError, match="FILE_READY"):
        processor._process_single_document(
            db=db,
            batch=batch,
            batch_doc=batch_doc,
            flow=flow,
            cognito_sub="auth-sub",
        )

    assert batch_doc.status == BatchDocumentStatus.FAILED
    assert batch.failed_documents == 1
    assert batch.completed_documents == 0
    assert batch_doc.result_file_path is None
    assert published_events == [
        {
            "type": "DOCUMENT_STATUS",
            "batch_id": str(batch.id),
            "document_id": str(batch_doc.document_id),
            "batch_document_id": str(batch_doc.id),
            "position": batch_doc.position,
            "status": BatchDocumentStatus.FAILED.value,
            "result_file_path": None,
            "error_message": "Flow completed without FILE_READY output",
            "processing_time_ms": batch_doc.processing_time_ms,
            "timestamp": batch_doc.processed_at.isoformat(),
        }
    ]


def test_batch_processor_marks_completed_when_file_ready(monkeypatch):
    db = Mock()
    batch, batch_doc, flow = _build_batch_context()

    async def _fake_execute_flow_for_document(**_kwargs):
        return "/api/weaviate/documents/download/abc123"

    monkeypatch.setattr(processor, "_execute_flow_for_document", _fake_execute_flow_for_document)

    processor._process_single_document(
        db=db,
        batch=batch,
        batch_doc=batch_doc,
        flow=flow,
        cognito_sub="auth-sub",
    )

    assert batch_doc.status == BatchDocumentStatus.COMPLETED
    assert batch.completed_documents == 1
    assert batch.failed_documents == 0
    assert batch_doc.result_file_path == "/api/weaviate/documents/download/abc123"


class _DummyScalarResult:
    def __init__(self, batch):
        self._batch = batch

    def first(self):
        return self._batch


class _DummyQuery:
    def __init__(self, db, model):
        self._db = db
        self._model = model

    def filter(self, *_args, **_kwargs):
        return self

    def first(self):
        if self._model is processor.CurationFlow:
            return self._db.flow
        if self._model is processor.User:
            return self._db.user
        if self._model is processor.Batch:
            return self._db.batch
        if self._model is processor.BatchDocument:
            return self._db.batch_doc
        return None


class _DummyDB:
    def __init__(self, batch, batch_doc, flow, user):
        self.batch = batch
        self.batch_doc = batch_doc
        self.flow = flow
        self.user = user
        self.commit_calls = 0
        self.rollback_calls = 0

    def scalars(self, _stmt):
        return _DummyScalarResult(self.batch)

    def query(self, model):
        return _DummyQuery(self, model)

    def refresh(self, _obj):
        return None

    def commit(self):
        self.commit_calls += 1

    def rollback(self):
        self.rollback_calls += 1


def test_process_batch_task_does_not_double_count_failed_documents(monkeypatch):
    batch, batch_doc, _flow = _build_batch_context()
    batch.flow_id = uuid4()
    batch.documents = [batch_doc]
    batch.status = BatchStatus.PENDING
    batch.started_at = None
    batch.completed_at = None

    flow = SimpleNamespace(id=batch.flow_id, name="Batch Flow")
    user = SimpleNamespace(id=batch.user_id, auth_sub="auth-sub")
    db = _DummyDB(batch=batch, batch_doc=batch_doc, flow=flow, user=user)

    @contextmanager
    def _fake_get_db_session():
        yield db

    def _fake_process_single_document(db, batch, batch_doc, flow, cognito_sub):
        batch_doc.status = BatchDocumentStatus.FAILED
        batch_doc.error_message = "Flow completed without FILE_READY output"
        batch_doc.processed_at = datetime.now(timezone.utc)
        batch.failed_documents += 1
        raise RuntimeError("Flow completed without FILE_READY output")

    monkeypatch.setattr(processor, "get_db_session", _fake_get_db_session)
    monkeypatch.setattr(processor, "_process_single_document", _fake_process_single_document)

    processor.process_batch_task(batch.id)

    assert batch.failed_documents == 1
    assert batch_doc.status == BatchDocumentStatus.FAILED


def test_execute_flow_for_document_ignores_file_ready_without_file_id(monkeypatch):
    async def _fake_execute_flow(**_kwargs):
        yield {
            "type": "FILE_READY",
            "details": {"download_url": "/api/weaviate/documents/download/no-file-id"},
        }

    published_events = []
    monkeypatch.setattr(
        processor,
        "get_batch_broadcaster",
        lambda: SimpleNamespace(
            publish_sync=lambda _batch_uuid, event: published_events.append(event)
        ),
    )
    monkeypatch.setattr("src.lib.flows.executor.execute_flow", _fake_execute_flow)
    monkeypatch.setattr("src.lib.context.set_current_user_id", lambda _user_id: None)
    monkeypatch.setattr("src.lib.context.set_current_session_id", lambda _session_id: None)

    result = asyncio.run(
        processor._execute_flow_for_document(
            flow=SimpleNamespace(name="Batch Flow"),
            document_id=str(uuid4()),
            cognito_sub="auth-sub",
            batch_id=str(uuid4()),
            db_user_id=7,
        )
    )

    assert result is None
    assert published_events == []


def test_execute_flow_for_document_passes_batch_id_as_flow_run_id(monkeypatch):
    captured = {}

    async def _fake_execute_flow(**kwargs):
        captured.update(kwargs)
        yield {
            "type": "FILE_READY",
            "details": {
                "download_url": "/api/weaviate/documents/download/file-1",
                "file_id": "c0ffee00-cafe-cafe-cafe-c0ffeec0ffee",
            },
        }

    monkeypatch.setattr(
        processor,
        "get_batch_broadcaster",
        lambda: SimpleNamespace(publish_sync=lambda *_args, **_kwargs: None),
    )
    monkeypatch.setattr("src.lib.flows.executor.execute_flow", _fake_execute_flow)
    monkeypatch.setattr("src.lib.context.set_current_user_id", lambda _user_id: None)
    monkeypatch.setattr("src.lib.context.set_current_session_id", lambda _session_id: None)
    monkeypatch.setattr(processor, "_validate_file_ownership", lambda _file_id, _owner: True)

    batch_id = str(uuid4())
    result = asyncio.run(
        processor._execute_flow_for_document(
            flow=SimpleNamespace(name="Batch Flow"),
            document_id=str(uuid4()),
            cognito_sub="auth-sub",
            batch_id=batch_id,
            db_user_id=7,
        )
    )

    assert result == "/api/weaviate/documents/download/file-1"
    assert captured["flow_run_id"] == batch_id


def test_execute_flow_for_document_does_not_publish_unowned_file_ready(monkeypatch):
    async def _fake_execute_flow(**_kwargs):
        yield {
            "type": "FILE_READY",
            "details": {
                "download_url": "/api/weaviate/documents/download/unowned",
                "file_id": "c0ffee00-cafe-cafe-cafe-c0ffeec0ffee",
            },
        }

    published_events = []
    monkeypatch.setattr(
        processor,
        "get_batch_broadcaster",
        lambda: SimpleNamespace(
            publish_sync=lambda _batch_uuid, event: published_events.append(event)
        ),
    )
    monkeypatch.setattr("src.lib.flows.executor.execute_flow", _fake_execute_flow)
    monkeypatch.setattr("src.lib.context.set_current_user_id", lambda _user_id: None)
    monkeypatch.setattr("src.lib.context.set_current_session_id", lambda _session_id: None)
    monkeypatch.setattr(processor, "_validate_file_ownership", lambda _file_id, _owner: False)

    result = asyncio.run(
        processor._execute_flow_for_document(
            flow=SimpleNamespace(name="Batch Flow"),
            document_id=str(uuid4()),
            cognito_sub="auth-sub",
            batch_id=str(uuid4()),
            db_user_id=7,
        )
    )

    assert result is None
    assert published_events == []


def test_execute_flow_for_document_ignores_malformed_file_ready_details(monkeypatch):
    async def _fake_execute_flow(**_kwargs):
        yield {"type": "FILE_READY", "details": None}
        yield {"type": "SUPERVISOR_COMPLETE", "details": {"ok": True}}

    published_events = []
    monkeypatch.setattr(
        processor,
        "get_batch_broadcaster",
        lambda: SimpleNamespace(
            publish_sync=lambda _batch_uuid, event: published_events.append(event)
        ),
    )
    monkeypatch.setattr("src.lib.flows.executor.execute_flow", _fake_execute_flow)
    monkeypatch.setattr("src.lib.context.set_current_user_id", lambda _user_id: None)
    monkeypatch.setattr("src.lib.context.set_current_session_id", lambda _session_id: None)

    result = asyncio.run(
        processor._execute_flow_for_document(
            flow=SimpleNamespace(name="Batch Flow"),
            document_id=str(uuid4()),
            cognito_sub="auth-sub",
            batch_id=str(uuid4()),
            db_user_id=7,
        )
    )

    assert result is None
    assert len(published_events) == 1
    assert published_events[0]["type"] == "SUPERVISOR_COMPLETE"


def test_execute_flow_for_document_strips_internal_payload_before_publish(monkeypatch):
    async def _fake_execute_flow(**_kwargs):
        yield {
            "type": "TOOL_COMPLETE",
            "data": {"step_id": "s-1"},
            "details": {"toolName": "ask_gene_specialist", "friendlyName": "Gene specialist complete"},
            "internal": {"tool_output": "{\"selected_gene\":\"TP53\"}"},
        }

    published_events = []
    batch_id = str(uuid4())
    document_id = str(uuid4())

    monkeypatch.setattr(
        processor,
        "get_batch_broadcaster",
        lambda: SimpleNamespace(
            publish_sync=lambda _batch_uuid, event: published_events.append(event)
        ),
    )
    monkeypatch.setattr("src.lib.flows.executor.execute_flow", _fake_execute_flow)
    monkeypatch.setattr("src.lib.context.set_current_user_id", lambda _user_id: None)
    monkeypatch.setattr("src.lib.context.set_current_session_id", lambda _session_id: None)

    result = asyncio.run(
        processor._execute_flow_for_document(
            flow=SimpleNamespace(name="Batch Flow"),
            document_id=document_id,
            cognito_sub="auth-sub",
            batch_id=batch_id,
            db_user_id=7,
        )
    )

    assert result is None
    assert len(published_events) == 1
    assert published_events[0]["type"] == "TOOL_COMPLETE"
    assert published_events[0]["batch_id"] == batch_id
    assert published_events[0]["document_id"] == document_id
    assert published_events[0]["step_id"] == "s-1"
    assert "internal" not in published_events[0]


def test_validate_file_ownership_fails_closed_on_session_error(monkeypatch):
    def _raise_session_local():
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(processor, "SessionLocal", _raise_session_local)

    assert processor._validate_file_ownership("file-id", "auth-sub") is False

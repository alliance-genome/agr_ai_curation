"""Runtime unit tests for core document endpoints."""

from datetime import datetime, timezone
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks, HTTPException, UploadFile

from src.api import documents
from src.lib.pdf_jobs.upload_intake_service import (
    UploadIntakeDuplicateError,
    UploadIntakeResult,
    UploadIntakeValidationError,
)
from src.lib.pdf_jobs.upload_execution_service import normalize_pipeline_result
from src.models.document import ProcessingStatus
from src.models.pipeline import PipelineStatus, ProcessingStage
from src.schemas.documents import DocumentUpdateRequest


class _FakeQuery:
    def __init__(self, doc):
        self._doc = doc

    def filter(self, *_args, **_kwargs):
        return self

    def first(self):
        return self._doc


class _FakeExecuteResult:
    def __init__(self, doc):
        self._doc = doc

    def scalars(self):
        return self

    def first(self):
        return self._doc


class _FakeSession:
    def __init__(self, query_doc=None, execute_doc=None):
        self._query_doc = query_doc
        self._execute_doc = execute_doc
        self.closed = False
        self.commits = 0
        self.rollbacks = 0
        self.added = []
        self.deleted = []

    def query(self, _model):
        return _FakeQuery(self._query_doc)

    def execute(self, *_args, **_kwargs):
        return _FakeExecuteResult(self._execute_doc)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def add(self, value):
        self.added.append(value)

    def delete(self, value):
        self.deleted.append(value)

    def close(self):
        self.closed = True


class _BoomPath:
    def resolve(self):
        raise RuntimeError("resolve failed")


def _patch_session_factory(monkeypatch, sessions):
    stack = list(sessions)

    def _factory():
        assert stack, "SessionLocal called more times than expected"
        return stack.pop(0)

    monkeypatch.setattr(documents, "SessionLocal", _factory)


@pytest.mark.asyncio
async def test_verify_document_ownership_returns_document(monkeypatch):
    doc_id = str(uuid4())
    owned_doc = SimpleNamespace(id=doc_id, user_id=10)
    session = _FakeSession(query_doc=owned_doc)
    monkeypatch.setattr(documents, "provision_user", lambda *_args, **_kwargs: SimpleNamespace(id=10))
    monkeypatch.setattr(documents, "principal_from_claims", lambda _claims: SimpleNamespace(subject="user-1"))

    result = documents.verify_document_ownership(session, doc_id, {"sub": "user-1"})
    assert result is owned_doc


@pytest.mark.asyncio
async def test_verify_document_ownership_rejects_cross_user(monkeypatch):
    doc_id = str(uuid4())
    foreign_doc = SimpleNamespace(id=doc_id, user_id=999)
    session = _FakeSession(query_doc=foreign_doc)
    monkeypatch.setattr(documents, "provision_user", lambda *_args, **_kwargs: SimpleNamespace(id=10))
    monkeypatch.setattr(documents, "principal_from_claims", lambda _claims: SimpleNamespace(subject="user-1"))

    with pytest.raises(HTTPException) as exc:
        documents.verify_document_ownership(session, doc_id, {"sub": "user-1"})
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_verify_document_ownership_rejects_invalid_uuid(monkeypatch):
    session = _FakeSession(query_doc=None)
    monkeypatch.setattr(documents, "provision_user", lambda *_args, **_kwargs: SimpleNamespace(id=10))
    monkeypatch.setattr(documents, "principal_from_claims", lambda _claims: SimpleNamespace(subject="user-1"))

    with pytest.raises(HTTPException) as exc:
        documents.verify_document_ownership(session, "not-a-uuid", {"sub": "user-1"})
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_validate_user_file_path_returns_resolved_path(tmp_path):
    user_root = tmp_path / "user-1"
    user_root.mkdir()
    file_path = user_root / "paper.pdf"
    file_path.write_text("ok")

    resolved = documents.validate_user_file_path(file_path, tmp_path, "user-1")
    assert resolved == file_path.resolve()


@pytest.mark.asyncio
async def test_validate_user_file_path_handles_resolve_errors(tmp_path):
    with pytest.raises(HTTPException) as exc:
        documents.validate_user_file_path(_BoomPath(), tmp_path, "user-1")
    assert exc.value.status_code == 500


def test_normalize_pipeline_result_supports_legacy_dict_payload():
    success, cancelled, error = normalize_pipeline_result(
        {"status": "completed", "chunks_created": 0}
    )
    assert success is True
    assert cancelled is False
    assert error is None


def test_normalize_pipeline_result_supports_object_payload():
    payload = SimpleNamespace(success=False, cancelled=True, error="Cancelled by user")
    success, cancelled, error = normalize_pipeline_result(payload)
    assert success is False
    assert cancelled is True
    assert error == "Cancelled by user"


@pytest.mark.asyncio
async def test_get_document_endpoint_returns_document_response(monkeypatch):
    upload_time = datetime.now(timezone.utc)
    pg_doc = SimpleNamespace(filename="paper.pdf", upload_timestamp=upload_time, file_size=123)

    monkeypatch.setattr(documents, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: pg_doc)
    monkeypatch.setattr(documents, "principal_from_claims", lambda _claims: SimpleNamespace(subject="user-1"))
    monkeypatch.setattr(documents, "provision_user", lambda *_args, **_kwargs: SimpleNamespace(id=5))
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(
        documents,
        "get_document",
        lambda *_args, **_kwargs: _async_value({"document": {"processing_status": "pending", "chunk_count": 7}}),
    )
    monkeypatch.setattr(documents, "get_tenant_name", lambda _sub: "tenant-user-1")

    response = await documents.get_document_endpoint("doc-1", {"sub": "user-1"})
    assert response.document_id == "doc-1"
    assert response.user_id == 5
    assert response.status == "PENDING"
    assert response.chunk_count == 7


@pytest.mark.asyncio
async def test_get_document_endpoint_raises_500_on_backend_error(monkeypatch):
    monkeypatch.setattr(documents, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: SimpleNamespace(filename="a", upload_timestamp=datetime.now(timezone.utc), file_size=1))
    monkeypatch.setattr(documents, "principal_from_claims", lambda _claims: SimpleNamespace(subject="user-1"))
    monkeypatch.setattr(documents, "provision_user", lambda *_args, **_kwargs: SimpleNamespace(id=5))

    async def _raise(*_args, **_kwargs):
        raise RuntimeError("weaviate down")

    monkeypatch.setattr(documents, "get_document", _raise)

    with pytest.raises(HTTPException) as exc:
        await documents.get_document_endpoint("doc-1", {"sub": "user-1"})
    assert exc.value.status_code == 500


@pytest.mark.asyncio
async def test_update_document_endpoint_updates_title_and_commits(monkeypatch):
    session = _FakeSession()
    document = SimpleNamespace(title="old")
    monkeypatch.setattr(documents, "SessionLocal", lambda: session)
    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: document)

    response = await documents.update_document_endpoint(
        DocumentUpdateRequest(title="new-title"),
        "doc-1",
        {"sub": "user-1"},
    )

    assert response.document_id == "doc-1"
    assert response.title == "new-title"
    assert document.title == "new-title"
    assert session.commits == 1
    assert session.closed is True


@pytest.mark.asyncio
async def test_update_document_endpoint_rolls_back_on_error(monkeypatch):
    session = _FakeSession()
    monkeypatch.setattr(documents, "SessionLocal", lambda: session)

    def _boom(*_args, **_kwargs):
        raise RuntimeError("db exploded")

    monkeypatch.setattr(documents, "verify_document_ownership", _boom)

    with pytest.raises(HTTPException) as exc:
        await documents.update_document_endpoint(DocumentUpdateRequest(title="x"), "doc-1", {"sub": "user-1"})
    assert exc.value.status_code == 500
    assert session.rollbacks == 1
    assert session.closed is True


@pytest.mark.asyncio
async def test_delete_document_endpoint_returns_success(monkeypatch):
    doc_id = str(uuid4())
    verify_session = _FakeSession()
    cleanup_session = _FakeSession(execute_doc=None)
    _patch_session_factory(monkeypatch, [verify_session, cleanup_session])

    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: SimpleNamespace(id=doc_id, user_id=42))
    monkeypatch.setattr(documents, "get_document", lambda *_args, **_kwargs: _async_value({"document": {"processing_status": "pending"}}))
    monkeypatch.setattr(documents, "delete_document", lambda *_args, **_kwargs: _async_value({"success": True, "chunks_deleted": 3}))
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(documents.pipeline_tracker, "get_pipeline_status", lambda *_args, **_kwargs: _async_value(None))

    result = await documents.delete_document_endpoint(doc_id, {"sub": "user-1"})
    assert result.success is True
    assert result.document_id == doc_id
    assert "3 chunks deleted" in result.message
    assert verify_session.closed is True
    assert cleanup_session.closed is True


@pytest.mark.asyncio
async def test_delete_document_endpoint_blocks_processing_documents(monkeypatch):
    doc_id = str(uuid4())
    verify_session = _FakeSession()
    _patch_session_factory(monkeypatch, [verify_session])

    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: SimpleNamespace(id=doc_id, user_id=42))
    monkeypatch.setattr(
        documents,
        "get_document",
        lambda *_args, **_kwargs: _async_value({"document": {"processing_status": ProcessingStatus.PROCESSING.value}}),
    )
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(documents.pipeline_tracker, "get_pipeline_status", lambda *_args, **_kwargs: _async_value(None))

    with pytest.raises(HTTPException) as exc:
        await documents.delete_document_endpoint(doc_id, {"sub": "user-1"})
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_delete_document_endpoint_blocks_active_pdf_job(monkeypatch):
    doc_id = str(uuid4())
    verify_session = _FakeSession()
    _patch_session_factory(monkeypatch, [verify_session])

    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: SimpleNamespace(id=doc_id, user_id=42))
    monkeypatch.setattr(documents, "get_document", lambda *_args, **_kwargs: _async_value({"document": {"processing_status": "pending"}}))
    monkeypatch.setattr(
        documents.pdf_job_service,
        "get_latest_job_for_document",
        lambda **_kwargs: SimpleNamespace(status="running", current_stage="parsing"),
    )
    monkeypatch.setattr(documents.pipeline_tracker, "get_pipeline_status", lambda *_args, **_kwargs: _async_value(None))

    with pytest.raises(HTTPException) as exc:
        await documents.delete_document_endpoint(doc_id, {"sub": "user-1"})
    assert exc.value.status_code == 409
    assert "job status" in str(exc.value.detail).lower()


@pytest.mark.asyncio
async def test_delete_document_endpoint_allows_reconciled_stale_pdf_job(monkeypatch):
    doc_id = str(uuid4())
    verify_session = _FakeSession()
    cleanup_session = _FakeSession(execute_doc=None)
    _patch_session_factory(monkeypatch, [verify_session, cleanup_session])

    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: SimpleNamespace(id=doc_id, user_id=42))
    monkeypatch.setattr(documents, "get_document", lambda *_args, **_kwargs: _async_value({"document": {"processing_status": "pending"}}))
    monkeypatch.setattr(documents, "delete_document", lambda *_args, **_kwargs: _async_value({"success": True, "chunks_deleted": 0}))
    monkeypatch.setattr(
        documents.pdf_job_service,
        "get_latest_job_for_document",
        lambda **_kwargs: SimpleNamespace(status="failed", current_stage="failed"),
    )
    monkeypatch.setattr(documents.pipeline_tracker, "get_pipeline_status", lambda *_args, **_kwargs: _async_value(None))

    result = await documents.delete_document_endpoint(doc_id, {"sub": "user-1"})
    assert result.success is True


@pytest.mark.asyncio
async def test_delete_document_endpoint_allows_stale_processing_status_when_job_terminal(monkeypatch):
    doc_id = str(uuid4())
    verify_session = _FakeSession()
    cleanup_session = _FakeSession(execute_doc=None)
    _patch_session_factory(monkeypatch, [verify_session, cleanup_session])

    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: SimpleNamespace(id=doc_id, user_id=42))
    monkeypatch.setattr(documents, "get_document", lambda *_args, **_kwargs: _async_value({"document": {"processing_status": "processing"}}))
    monkeypatch.setattr(documents, "delete_document", lambda *_args, **_kwargs: _async_value({"success": True, "chunks_deleted": 0}))
    monkeypatch.setattr(
        documents.pdf_job_service,
        "get_latest_job_for_document",
        lambda **_kwargs: SimpleNamespace(status="failed", current_stage="failed"),
    )
    monkeypatch.setattr(documents.pipeline_tracker, "get_pipeline_status", lambda *_args, **_kwargs: _async_value(None))

    result = await documents.delete_document_endpoint(doc_id, {"sub": "user-1"})
    assert result.success is True


@pytest.mark.asyncio
async def test_delete_document_endpoint_raises_500_when_delete_fails(monkeypatch):
    doc_id = str(uuid4())
    verify_session = _FakeSession()
    _patch_session_factory(monkeypatch, [verify_session])

    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: SimpleNamespace(id=doc_id, user_id=42))
    monkeypatch.setattr(documents, "get_document", lambda *_args, **_kwargs: _async_value({"document": {"processing_status": "pending"}}))
    monkeypatch.setattr(documents, "delete_document", lambda *_args, **_kwargs: _async_value({"success": False, "message": "nope"}))
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(documents.pipeline_tracker, "get_pipeline_status", lambda *_args, **_kwargs: _async_value(None))

    with pytest.raises(HTTPException) as exc:
        await documents.delete_document_endpoint(doc_id, {"sub": "user-1"})
    assert exc.value.status_code == 500


@pytest.mark.asyncio
async def test_status_endpoint_returns_404_when_document_missing(monkeypatch):
    monkeypatch.setattr(documents, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(documents, "principal_from_claims", lambda _claims: SimpleNamespace(subject="user-1"))
    monkeypatch.setattr(documents, "provision_user", lambda *_args, **_kwargs: SimpleNamespace(id=7))
    monkeypatch.setattr(documents, "get_document", lambda *_args, **_kwargs: _async_value(None))

    with pytest.raises(HTTPException) as exc:
        await documents.get_document_processing_status("doc-1", {"sub": "user-1"})
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_status_endpoint_raises_500_on_unexpected_error(monkeypatch):
    monkeypatch.setattr(documents, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(documents, "principal_from_claims", lambda _claims: SimpleNamespace(subject="user-1"))
    monkeypatch.setattr(documents, "provision_user", lambda *_args, **_kwargs: SimpleNamespace(id=7))

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("lookup failed")

    monkeypatch.setattr(documents, "get_document", _boom)

    with pytest.raises(HTTPException) as exc:
        await documents.get_document_processing_status("doc-1", {"sub": "user-1"})
    assert exc.value.status_code == 500


@pytest.mark.asyncio
async def test_upload_document_endpoint_rejects_non_pdf(monkeypatch):
    background_tasks = BackgroundTasks()
    upload = UploadFile(filename="notes.txt", file=BytesIO(b"text"))

    async def _raise_validation(**_kwargs):
        raise UploadIntakeValidationError("File must be a PDF. Got: notes.txt")

    monkeypatch.setattr(documents.upload_intake_service, "intake_upload", _raise_validation)

    with pytest.raises(HTTPException) as exc:
        await documents.upload_document_endpoint(background_tasks, upload, {"sub": "user-1"})
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_upload_document_endpoint_happy_path(monkeypatch):
    background_tasks = BackgroundTasks()
    upload = UploadFile(filename="paper.pdf", file=BytesIO(b"%PDF-1.7"))
    captured = {}

    async def _intake_upload(**kwargs):
        captured.update(kwargs)
        return UploadIntakeResult(
            document_id="doc-1",
            job_id="job-1",
            user_id=99,
            filename="paper.pdf",
            status="PENDING",
            upload_timestamp=datetime.now(timezone.utc),
            processing_started_at=None,
            processing_completed_at=None,
            file_size_bytes=7,
            weaviate_tenant="tenant-user-1",
            chunk_count=None,
            error_message=None,
        )

    monkeypatch.setattr(documents.upload_intake_service, "intake_upload", _intake_upload)
    response = await documents.upload_document_endpoint(background_tasks, upload, {"sub": "user-1"})

    assert response.user_id == 99
    assert response.filename == "paper.pdf"
    assert response.status == "PENDING"
    assert response.weaviate_tenant == "tenant-user-1"
    assert captured["background_tasks"] is background_tasks
    assert captured["file"] is upload
    assert captured["user"] == {"sub": "user-1"}


@pytest.mark.asyncio
async def test_upload_document_endpoint_maps_duplicate_error_to_409(monkeypatch):
    background_tasks = BackgroundTasks()
    upload = UploadFile(filename="paper.pdf", file=BytesIO(b"%PDF-1.7"))

    async def _raise_duplicate(**_kwargs):
        raise UploadIntakeDuplicateError(
            {
                "error": "duplicate_file",
                "message": "already uploaded",
                "existing_document_id": "doc-1",
            }
        )

    monkeypatch.setattr(documents.upload_intake_service, "intake_upload", _raise_duplicate)

    with pytest.raises(HTTPException) as exc:
        await documents.upload_document_endpoint(background_tasks, upload, {"sub": "user-1"})
    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "duplicate_file"


@pytest.mark.asyncio
async def test_stream_document_progress_returns_not_found_event(monkeypatch):
    doc_id = str(uuid4())
    monkeypatch.setenv("PDF_PROCESSING_SSE_POLL_INTERVAL_SECONDS", "1")
    monkeypatch.setenv("PDF_PROCESSING_SSE_TIMEOUT_SECONDS", "1")
    monkeypatch.setattr(documents, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(documents, "principal_from_claims", lambda _claims: SimpleNamespace(subject="user-1"))
    monkeypatch.setattr(documents, "provision_user", lambda *_args, **_kwargs: SimpleNamespace(id=7))
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(documents, "get_document", lambda *_args, **_kwargs: _async_value(None))

    response = await documents.stream_document_progress(doc_id, {"sub": "user-1"})
    payload = await _collect_stream(response)
    assert "Document not found" in payload
    assert doc_id in payload


@pytest.mark.asyncio
async def test_stream_document_progress_emits_final_completed_event(monkeypatch):
    now = datetime.now(timezone.utc)
    doc_id = str(uuid4())

    async def _status(*_args, **_kwargs):
        return PipelineStatus(
            document_id="doc-1",
            current_stage=ProcessingStage.COMPLETED,
            started_at=now,
            updated_at=now,
            progress_percentage=100,
            message="done",
        )

    async def _no_sleep(*_args, **_kwargs):
        return None

    monkeypatch.setenv("PDF_PROCESSING_SSE_POLL_INTERVAL_SECONDS", "1")
    monkeypatch.setenv("PDF_PROCESSING_SSE_TIMEOUT_SECONDS", "5")
    monkeypatch.setattr(documents, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(documents, "principal_from_claims", lambda _claims: SimpleNamespace(subject="user-1"))
    monkeypatch.setattr(documents, "provision_user", lambda *_args, **_kwargs: SimpleNamespace(id=7))
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(documents, "get_document", lambda *_args, **_kwargs: _async_value({"document": {"processing_status": "completed"}}))
    monkeypatch.setattr(documents.pipeline_tracker, "get_pipeline_status", _status)
    monkeypatch.setattr(documents.asyncio, "sleep", _no_sleep)

    response = await documents.stream_document_progress(doc_id, {"sub": "user-1"})
    payload = await _collect_stream(response)
    assert '"stage": "completed"' in payload
    assert '"final": true' in payload


def _async_value(value):
    async def _coro(*_args, **_kwargs):
        return value

    return _coro()


async def _collect_stream(response):
    chunks = []
    async for chunk in response.body_iterator:
        if isinstance(chunk, bytes):
            chunks.append(chunk.decode("utf-8"))
        else:
            chunks.append(str(chunk))
    return "".join(chunks)

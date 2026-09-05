"""Runtime unit tests for core document endpoints."""

import logging
from datetime import datetime, timedelta, timezone
from io import BytesIO
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks, FastAPI, HTTPException, UploadFile
from fastapi.testclient import TestClient
from src.api import documents
from src.lib.pdf_jobs.upload_intake_service import (
    UploadIntakeDuplicateError,
    UploadIntakeProviderDecisionError,
    UploadIntakeResult,
    UploadIntakeValidationError,
)
from src.models.document import ProcessingStatus
from src.models.pipeline import PipelineStatus, ProcessingStage
from src.schemas.documents import DocumentUpdateRequest


@pytest.mark.asyncio
async def test_phantom_cleanup_preserves_benchmark_copy_before_vectors_exist(monkeypatch):
    session = MagicMock()
    session.query.return_value.filter.return_value.first.return_value = SimpleNamespace(id=42)
    document = SimpleNamespace(id=uuid4(), viewer_mode="benchmark_frozen")
    session.query.return_value.filter.return_value.all.return_value = [document]
    monkeypatch.setattr(documents, "SessionLocal", lambda: session)
    connection = MagicMock()
    pdf_collection = MagicMock()
    pdf_collection.query.fetch_objects.return_value = SimpleNamespace(objects=[])
    monkeypatch.setattr("src.lib.weaviate_helpers.get_connection", lambda: connection)
    monkeypatch.setattr("src.lib.weaviate_helpers.get_user_collections", lambda *_: (MagicMock(), pdf_collection))
    assert await documents.cleanup_phantom_documents({"sub": "synthetic-curator"}) == 0
    session.delete.assert_not_called()
    pdf_collection.data.delete_by_id.assert_not_called()


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
        self.rowcount = 0

    def scalars(self):
        return self

    def first(self):
        return self._doc

    def scalar_one_or_none(self):
        return self._doc

    def all(self):
        if self._doc is None:
            return []
        if isinstance(self._doc, list):
            return self._doc
        return [self._doc]


class _FakeSession:
    def __init__(self, query_doc=None, execute_doc=None):
        self._query_doc = query_doc
        self._execute_doc = execute_doc
        self.closed = False
        self.commits = 0
        self.rollbacks = 0
        self.added = []
        self.deleted = []
        self.executed_statements = []

    def query(self, _model):
        return _FakeQuery(self._query_doc)

    def execute(self, *args, **_kwargs):
        self.executed_statements.extend(args)
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


def _patch_indexed_document_status(monkeypatch, status="completed"):
    monkeypatch.setattr(
        documents,
        "get_document",
        lambda *_args, **_kwargs: _async_value(
            {"document": {"processing_status": status}}
        ),
    )


@pytest.mark.asyncio
async def test_verify_document_ownership_returns_document(monkeypatch):
    doc_id = str(uuid4())
    owned_doc = SimpleNamespace(id=doc_id, user_id=10, viewer_mode=None)
    session = _FakeSession(execute_doc=owned_doc)
    monkeypatch.setattr(documents, "provision_user", lambda *_args, **_kwargs: SimpleNamespace(id=10))
    monkeypatch.setattr(documents, "principal_from_claims", lambda _claims: SimpleNamespace(subject="user-1"))

    result = documents.verify_document_ownership(session, doc_id, {"sub": "user-1"})
    assert result is owned_doc


@pytest.mark.asyncio
async def test_verify_document_ownership_can_lock_document_row(monkeypatch):
    doc_id = str(uuid4())
    owned_doc = SimpleNamespace(id=doc_id, user_id=10, viewer_mode=None)
    session = _FakeSession(execute_doc=owned_doc)
    monkeypatch.setattr(
        documents,
        "provision_user",
        lambda *_args, **_kwargs: SimpleNamespace(id=10),
    )
    monkeypatch.setattr(
        documents,
        "principal_from_claims",
        lambda _claims: SimpleNamespace(subject="user-1"),
    )

    result = documents.verify_document_ownership(
        session,
        doc_id,
        {"sub": "user-1"},
        for_update=True,
    )

    assert result is owned_doc
    assert session.executed_statements[0]._for_update_arg is not None


@pytest.mark.asyncio
async def test_verify_document_ownership_rejects_cross_user(monkeypatch):
    doc_id = str(uuid4())
    foreign_doc = SimpleNamespace(id=doc_id, user_id=999, viewer_mode=None)
    session = _FakeSession(execute_doc=foreign_doc)
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
        documents.validate_user_file_path(cast(Any, _BoomPath()), tmp_path, "user-1")
    assert exc.value.status_code == 500


@pytest.mark.asyncio
async def test_list_documents_endpoint_sanitizes_backend_error(monkeypatch, caplog):
    monkeypatch.setattr(documents, "cleanup_phantom_documents", lambda *_args, **_kwargs: _async_value(0))

    async def _raise(*_args, **_kwargs):
        raise RuntimeError("weaviate down")

    monkeypatch.setattr(documents, "list_documents", _raise)
    caplog.set_level(logging.ERROR, logger=documents.logger.name)

    with pytest.raises(HTTPException) as exc:
        await documents.list_documents_endpoint(
            user={"sub": "user-1"},
            page=1,
            page_size=20,
            search=None,
            embedding_status=None,
            sort_by=documents.SortBy.CREATION_DATE,
            sort_order=documents.SortOrder.DESC,
            date_from=None,
            date_to=None,
            min_vector_count=None,
            max_vector_count=None,
        )

    assert exc.value.status_code == 500
    assert exc.value.detail == "Failed to retrieve documents"
    assert "weaviate down" in caplog.text


@pytest.mark.asyncio
async def test_get_document_endpoint_returns_document_response(monkeypatch):
    upload_time = datetime.now(timezone.utc)
    processing_started_at = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)
    processing_completed_at = datetime(2026, 8, 25, 12, 5, tzinfo=timezone.utc)
    pg_doc = SimpleNamespace(
        filename="paper.pdf",
        upload_timestamp=upload_time,
        processing_started_at=processing_started_at,
        processing_completed_at=processing_completed_at,
        file_size=123,
    )

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
    assert response.processing_started_at == processing_started_at
    assert response.processing_completed_at == processing_completed_at


@pytest.mark.asyncio
async def test_get_document_endpoint_raises_500_on_backend_error(monkeypatch, caplog):
    monkeypatch.setattr(documents, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: SimpleNamespace(filename="a", upload_timestamp=datetime.now(timezone.utc), file_size=1))
    monkeypatch.setattr(documents, "principal_from_claims", lambda _claims: SimpleNamespace(subject="user-1"))
    monkeypatch.setattr(documents, "provision_user", lambda *_args, **_kwargs: SimpleNamespace(id=5))

    async def _raise(*_args, **_kwargs):
        raise RuntimeError("weaviate down")

    monkeypatch.setattr(documents, "get_document", _raise)
    caplog.set_level(logging.ERROR, logger=documents.logger.name)

    with pytest.raises(HTTPException) as exc:
        await documents.get_document_endpoint("doc-1", {"sub": "user-1"})
    assert exc.value.status_code == 500
    assert exc.value.detail == "Failed to retrieve document"
    assert "weaviate down" in caplog.text


@pytest.mark.asyncio
async def test_update_document_endpoint_updates_title_and_commits(monkeypatch):
    session = _FakeSession()
    document = SimpleNamespace(title="old", filename="paper.pdf")
    monkeypatch.setattr(documents, "SessionLocal", lambda: session)
    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: document)

    response = await documents.update_document_endpoint(
        DocumentUpdateRequest(title="new-title"),
        "doc-1",
        {"sub": "user-1"},
    )

    assert response.document_id == "doc-1"
    assert response.title == "new-title"
    assert response.filename == "paper.pdf"
    assert document.title == "new-title"
    assert session.commits == 1
    assert session.closed is True


@pytest.mark.asyncio
async def test_update_document_endpoint_rolls_back_on_error(monkeypatch, caplog):
    session = _FakeSession()
    monkeypatch.setattr(documents, "SessionLocal", lambda: session)

    def _boom(*_args, **_kwargs):
        raise RuntimeError("db exploded")

    monkeypatch.setattr(documents, "verify_document_ownership", _boom)
    caplog.set_level(logging.ERROR, logger=documents.logger.name)

    with pytest.raises(HTTPException) as exc:
        await documents.update_document_endpoint(DocumentUpdateRequest(title="x"), "doc-1", {"sub": "user-1"})
    assert exc.value.status_code == 500
    assert exc.value.detail == "Failed to update document"
    assert session.rollbacks == 1
    assert session.closed is True
    assert "Document metadata update failed (RuntimeError)" in caplog.text
    assert "db exploded" not in caplog.text


@pytest.mark.asyncio
async def test_update_document_endpoint_renames_source_and_index(monkeypatch, tmp_path):
    document_id = str(uuid4())
    relative_path = f"user-1/{document_id}/old.pdf"
    source_path = tmp_path / relative_path
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(b"%PDF-1.7")
    document = SimpleNamespace(
        title="Title",
        filename="old.pdf",
        file_path=relative_path,
        status="completed",
        user_id=7,
    )
    session = _FakeSession()
    index_updates = []

    async def _update_index(user_sub, doc_id, filename):
        index_updates.append((user_sub, doc_id, filename))

    monkeypatch.setattr(documents, "SessionLocal", lambda: session)
    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: document)
    monkeypatch.setattr(documents, "get_pdf_storage_path", lambda: tmp_path)
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(documents.pipeline_tracker, "get_pipeline_status", lambda *_args: _async_value(None))
    monkeypatch.setattr(documents, "update_document_filename", _update_index)
    _patch_indexed_document_status(monkeypatch)

    response = await documents.update_document_endpoint(
        DocumentUpdateRequest(title="New title", filename="renamed.pdf"),
        document_id,
        {"sub": "user-1"},
    )

    assert response.document_id == document_id
    assert response.title == "New title"
    assert response.filename == "renamed.pdf"
    assert document.file_path == f"user-1/{document_id}/renamed.pdf"
    assert not source_path.exists()
    assert source_path.with_name("renamed.pdf").read_bytes() == b"%PDF-1.7"
    assert index_updates == [("user-1", document_id, "renamed.pdf")]
    assert session.commits == 1


@pytest.mark.asyncio
async def test_update_document_endpoint_rejects_active_processing_rename(monkeypatch):
    document_id = str(uuid4())
    document = SimpleNamespace(
        title=None,
        filename="old.pdf",
        file_path=f"user-1/{document_id}/old.pdf",
        status="processing",
        user_id=7,
    )
    session = _FakeSession()
    monkeypatch.setattr(documents, "SessionLocal", lambda: session)
    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: document)
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(documents.pipeline_tracker, "get_pipeline_status", lambda *_args: _async_value(None))
    _patch_indexed_document_status(monkeypatch)

    with pytest.raises(HTTPException) as exc:
        await documents.update_document_endpoint(
            DocumentUpdateRequest(filename="renamed.pdf"),
            document_id,
            {"sub": "user-1"},
        )

    assert exc.value.status_code == 409
    assert "processing is active" in exc.value.detail
    assert session.commits == 0


@pytest.mark.asyncio
async def test_update_document_endpoint_rejects_current_indexed_reprocess_with_terminal_prior_job(
    monkeypatch,
):
    document_id = str(uuid4())
    document = SimpleNamespace(
        title=None,
        filename="old.pdf",
        file_path=f"user-1/{document_id}/old.pdf",
        status="completed",
        user_id=7,
    )
    session = _FakeSession()
    ownership_calls = []

    def _verify(*_args, **kwargs):
        ownership_calls.append(kwargs)
        return document

    monkeypatch.setattr(documents, "SessionLocal", lambda: session)
    monkeypatch.setattr(documents, "verify_document_ownership", _verify)
    monkeypatch.setattr(
        documents.pdf_job_service,
        "get_latest_job_for_document",
        lambda **_kwargs: SimpleNamespace(status="completed"),
    )
    monkeypatch.setattr(
        documents.pipeline_tracker,
        "get_pipeline_status",
        lambda *_args: _async_value(None),
    )
    _patch_indexed_document_status(monkeypatch, ProcessingStatus.PROCESSING.value)

    with pytest.raises(HTTPException) as exc:
        await documents.update_document_endpoint(
            DocumentUpdateRequest(filename="renamed.pdf"),
            document_id,
            {"sub": "user-1"},
        )

    assert exc.value.status_code == 409
    assert "processing is active" in exc.value.detail
    assert session.commits == 0
    assert ownership_calls == [{"for_update": True}]


@pytest.mark.asyncio
async def test_update_document_endpoint_rejects_filename_collision(monkeypatch, tmp_path):
    document_id = str(uuid4())
    source_path = tmp_path / "user-1" / document_id / "old.pdf"
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(b"old")
    source_path.with_name("taken.pdf").write_bytes(b"taken")
    document = SimpleNamespace(
        title=None,
        filename="old.pdf",
        file_path=str(source_path.relative_to(tmp_path)),
        status="completed",
        user_id=7,
    )
    session = _FakeSession()
    monkeypatch.setattr(documents, "SessionLocal", lambda: session)
    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: document)
    monkeypatch.setattr(documents, "get_pdf_storage_path", lambda: tmp_path)
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(documents.pipeline_tracker, "get_pipeline_status", lambda *_args: _async_value(None))
    _patch_indexed_document_status(monkeypatch)

    with pytest.raises(HTTPException) as exc:
        await documents.update_document_endpoint(
            DocumentUpdateRequest(filename="taken.pdf"),
            document_id,
            {"sub": "user-1"},
        )

    assert exc.value.status_code == 409
    assert source_path.read_bytes() == b"old"
    assert source_path.with_name("taken.pdf").read_bytes() == b"taken"


@pytest.mark.asyncio
async def test_update_document_endpoint_rejects_database_path_collision(monkeypatch, tmp_path):
    document_id = str(uuid4())
    source_path = tmp_path / "user-1" / document_id / "old.pdf"
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(b"old")
    document = SimpleNamespace(
        title=None,
        filename="old.pdf",
        file_path=str(source_path.relative_to(tmp_path)),
        status="completed",
        user_id=7,
    )
    session = _FakeSession(execute_doc=uuid4())
    monkeypatch.setattr(documents, "SessionLocal", lambda: session)
    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: document)
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(documents.pipeline_tracker, "get_pipeline_status", lambda *_args: _async_value(None))
    _patch_indexed_document_status(monkeypatch)

    with pytest.raises(HTTPException) as exc:
        await documents.update_document_endpoint(
            DocumentUpdateRequest(filename="taken.pdf"),
            document_id,
            {"sub": "user-1"},
        )

    assert exc.value.status_code == 409
    assert source_path.read_bytes() == b"old"
    assert not source_path.with_name("taken.pdf").exists()


@pytest.mark.asyncio
async def test_update_document_endpoint_restores_source_when_index_update_fails(monkeypatch, tmp_path):
    document_id = str(uuid4())
    source_path = tmp_path / "user-1" / document_id / "old.pdf"
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(b"old")
    document = SimpleNamespace(
        title=None,
        filename="old.pdf",
        file_path=str(source_path.relative_to(tmp_path)),
        status="completed",
        user_id=7,
    )
    session = _FakeSession()
    index_updates = []

    async def _fail_index(_user_sub, _doc_id, filename):
        index_updates.append(filename)
        if filename == "renamed.pdf":
            raise RuntimeError("index details")

    monkeypatch.setattr(documents, "SessionLocal", lambda: session)
    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: document)
    monkeypatch.setattr(documents, "get_pdf_storage_path", lambda: tmp_path)
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(documents.pipeline_tracker, "get_pipeline_status", lambda *_args: _async_value(None))
    monkeypatch.setattr(documents, "update_document_filename", _fail_index)
    _patch_indexed_document_status(monkeypatch)

    with pytest.raises(HTTPException) as exc:
        await documents.update_document_endpoint(
            DocumentUpdateRequest(filename="renamed.pdf"),
            document_id,
            {"sub": "user-1"},
        )

    assert exc.value.status_code == 500
    assert source_path.read_bytes() == b"old"
    assert not source_path.with_name("renamed.pdf").exists()
    assert document.filename == "old.pdf"
    assert index_updates == ["renamed.pdf"]
    assert session.rollbacks == 1


@pytest.mark.asyncio
async def test_update_document_endpoint_restores_index_and_source_when_commit_fails(monkeypatch, tmp_path):
    document_id = str(uuid4())
    source_path = tmp_path / "user-1" / document_id / "old.pdf"
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(b"old")
    document = SimpleNamespace(
        title=None,
        filename="old.pdf",
        file_path=str(source_path.relative_to(tmp_path)),
        status="completed",
        user_id=7,
    )
    session = _FakeSession()
    index_updates = []

    def _fail_commit():
        raise RuntimeError("commit details")

    async def _update_index(_user_sub, _doc_id, filename):
        index_updates.append(filename)

    session.commit = _fail_commit
    monkeypatch.setattr(documents, "SessionLocal", lambda: session)
    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: document)
    monkeypatch.setattr(documents, "get_pdf_storage_path", lambda: tmp_path)
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(documents.pipeline_tracker, "get_pipeline_status", lambda *_args: _async_value(None))
    monkeypatch.setattr(documents, "update_document_filename", _update_index)
    _patch_indexed_document_status(monkeypatch)

    with pytest.raises(HTTPException) as exc:
        await documents.update_document_endpoint(
            DocumentUpdateRequest(filename="renamed.pdf"),
            document_id,
            {"sub": "user-1"},
        )

    assert exc.value.status_code == 500
    assert index_updates == ["renamed.pdf", "old.pdf"]
    assert source_path.read_bytes() == b"old"
    assert not source_path.with_name("renamed.pdf").exists()
    assert session.rollbacks == 1


@pytest.mark.asyncio
async def test_update_document_endpoint_completes_forward_when_index_rollback_fails(
    monkeypatch,
    tmp_path,
):
    document_id = str(uuid4())
    source_path = tmp_path / "user-1" / document_id / "old.pdf"
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(b"old")
    new_path = source_path.with_name("renamed.pdf")
    document = SimpleNamespace(
        title=None,
        filename="old.pdf",
        file_path=str(source_path.relative_to(tmp_path)),
        status="completed",
        user_id=7,
    )
    session = _FakeSession()
    index_updates = []
    reported = []
    commit_attempts = 0

    def _fail_first_commit():
        nonlocal commit_attempts
        commit_attempts += 1
        if commit_attempts == 1:
            raise RuntimeError("initial commit failed")
        session.commits += 1

    async def _update_index(_user_sub, _doc_id, filename):
        index_updates.append(filename)
        if filename == "old.pdf":
            raise RuntimeError("index rollback failed")

    def _record_report(exc, **kwargs):
        reported.append((exc, kwargs))
        return True

    session.commit = _fail_first_commit
    monkeypatch.setattr(documents, "SessionLocal", lambda: session)
    monkeypatch.setattr(
        documents,
        "verify_document_ownership",
        lambda *_args, **_kwargs: document,
    )
    monkeypatch.setattr(documents, "get_pdf_storage_path", lambda: tmp_path)
    monkeypatch.setattr(
        documents.pdf_job_service,
        "get_latest_job_for_document",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        documents.pipeline_tracker,
        "get_pipeline_status",
        lambda *_args: _async_value(None),
    )
    monkeypatch.setattr(documents, "update_document_filename", _update_index)
    monkeypatch.setattr(documents, "report_runtime_exception", _record_report)
    _patch_indexed_document_status(monkeypatch)

    with pytest.raises(HTTPException) as exc:
        await documents.update_document_endpoint(
            DocumentUpdateRequest(filename="renamed.pdf"),
            document_id,
            {"sub": "user-1"},
        )

    assert exc.value.status_code == 500
    assert index_updates == ["renamed.pdf", "old.pdf"]
    assert document.filename == "renamed.pdf"
    assert document.file_path == str(new_path.relative_to(tmp_path))
    assert new_path.read_bytes() == b"old"
    assert not source_path.exists()
    assert commit_attempts == 2
    assert session.commits == 1
    assert [item[1]["operation"] for item in reported] == [
        "filename_index_rollback_failed"
    ]
    assert isinstance(reported[0][0], documents._DocumentMetadataUpdateError)
    assert reported[0][0].__cause__ is None
    assert reported[0][0].__context__ is None


@pytest.mark.asyncio
async def test_update_document_endpoint_sanitizes_failed_forward_recovery(
    monkeypatch,
    tmp_path,
    caplog,
):
    document_id = str(uuid4())
    source_path = tmp_path / "user-1" / document_id / "old.pdf"
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(b"old")
    new_path = source_path.with_name("renamed.pdf")
    document = SimpleNamespace(
        title=None,
        filename="old.pdf",
        file_path=str(source_path.relative_to(tmp_path)),
        status="completed",
        user_id=7,
    )
    session = _FakeSession()
    index_updates = []
    reported = []
    commit_attempts = 0
    sensitive_text = "INSERT ... params: SECRET-CURATOR-TEXT"

    def _fail_commits():
        nonlocal commit_attempts
        commit_attempts += 1
        if commit_attempts == 1:
            raise RuntimeError("initial commit failed")
        raise RuntimeError(sensitive_text)

    async def _update_index(_user_sub, _doc_id, filename):
        index_updates.append(filename)
        if filename == "old.pdf":
            raise RuntimeError("index rollback failed")

    def _record_report(exc, **kwargs):
        reported.append((exc, kwargs))
        return True

    session.commit = _fail_commits
    monkeypatch.setattr(documents, "SessionLocal", lambda: session)
    monkeypatch.setattr(
        documents,
        "verify_document_ownership",
        lambda *_args, **_kwargs: document,
    )
    monkeypatch.setattr(documents, "get_pdf_storage_path", lambda: tmp_path)
    monkeypatch.setattr(
        documents.pdf_job_service,
        "get_latest_job_for_document",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        documents.pipeline_tracker,
        "get_pipeline_status",
        lambda *_args: _async_value(None),
    )
    monkeypatch.setattr(documents, "update_document_filename", _update_index)
    monkeypatch.setattr(documents, "report_runtime_exception", _record_report)
    _patch_indexed_document_status(monkeypatch)
    caplog.set_level(logging.ERROR, logger=documents.logger.name)

    with pytest.raises(HTTPException) as exc:
        await documents.update_document_endpoint(
            DocumentUpdateRequest(filename="renamed.pdf"),
            document_id,
            {"sub": "user-1"},
        )

    assert exc.value.status_code == 500
    assert exc.value.detail == "Failed to update document"
    assert index_updates == ["renamed.pdf", "old.pdf"]
    assert document.filename == "renamed.pdf"
    assert document.file_path == str(new_path.relative_to(tmp_path))
    assert source_path.read_bytes() == b"old"
    assert new_path.read_bytes() == b"old"
    assert commit_attempts == 2
    assert session.rollbacks == 2

    recovery_reports = [
        item
        for item in reported
        if item[1]["operation"] == "filename_forward_recovery_failed"
    ]
    assert len(recovery_reports) == 1
    reported_exc = recovery_reports[0][0]
    assert isinstance(reported_exc, documents._DocumentMetadataUpdateError)
    assert reported_exc.__traceback__ is not None
    assert reported_exc.__cause__ is None
    assert reported_exc.__context__ is None
    assert sensitive_text not in str(reported_exc)
    assert sensitive_text not in caplog.text


@pytest.mark.asyncio
async def test_delete_document_endpoint_returns_success(monkeypatch):
    doc_id = str(uuid4())
    verify_session = _FakeSession()
    snapshot_session = _FakeSession(execute_doc=None)
    cleanup_session = _FakeSession(execute_doc=None)
    _patch_session_factory(monkeypatch, [verify_session, snapshot_session, cleanup_session])

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
    snapshot_session = _FakeSession(execute_doc=None)
    _patch_session_factory(monkeypatch, [verify_session, snapshot_session])

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
    snapshot_session = _FakeSession(execute_doc=None)
    cleanup_session = _FakeSession(execute_doc=None)
    _patch_session_factory(monkeypatch, [verify_session, snapshot_session, cleanup_session])

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
async def test_delete_document_endpoint_allows_stale_active_weaviate_status_when_job_terminal(
    monkeypatch,
):
    doc_id = str(uuid4())
    verify_session = _FakeSession()
    snapshot_session = _FakeSession(execute_doc=None)
    cleanup_session = _FakeSession(execute_doc=None)
    _patch_session_factory(monkeypatch, [verify_session, snapshot_session, cleanup_session])

    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: SimpleNamespace(id=doc_id, user_id=42))
    monkeypatch.setattr(documents, "get_document", lambda *_args, **_kwargs: _async_value({"document": {"processing_status": "processing"}}))
    monkeypatch.setattr(
        documents.pdf_job_service,
        "get_latest_job_for_document",
        lambda **_kwargs: SimpleNamespace(status="failed", current_stage="failed"),
    )
    monkeypatch.setattr(documents.pipeline_tracker, "get_pipeline_status", lambda *_args, **_kwargs: _async_value(None))
    monkeypatch.setattr(
        documents,
        "delete_document",
        lambda *_args, **_kwargs: _async_value(
            {"success": True, "chunks_deleted": 0}
        ),
    )

    result = await documents.delete_document_endpoint(doc_id, {"sub": "user-1"})

    assert result.success is True


@pytest.mark.asyncio
async def test_delete_document_endpoint_allows_old_active_tracker_after_terminal_job(
    monkeypatch,
):
    doc_id = str(uuid4())
    now = datetime.now(timezone.utc)
    verify_session = _FakeSession()
    snapshot_session = _FakeSession(execute_doc=None)
    cleanup_session = _FakeSession(execute_doc=None)
    _patch_session_factory(
        monkeypatch,
        [verify_session, snapshot_session, cleanup_session],
    )

    monkeypatch.setattr(
        documents,
        "verify_document_ownership",
        lambda *_args, **_kwargs: SimpleNamespace(id=doc_id, user_id=42),
    )
    monkeypatch.setattr(
        documents,
        "get_document",
        lambda *_args, **_kwargs: _async_value(
            {"document": {"processing_status": "failed"}}
        ),
    )
    monkeypatch.setattr(
        documents,
        "delete_document",
        lambda *_args, **_kwargs: _async_value(
            {"success": True, "chunks_deleted": 0}
        ),
    )
    monkeypatch.setattr(
        documents.pdf_job_service,
        "get_latest_job_for_document",
        lambda **_kwargs: SimpleNamespace(
            status="failed",
            current_stage="failed",
            completed_at=now,
            updated_at=now,
        ),
    )
    stale_pipeline = PipelineStatus(
        document_id=doc_id,
        current_stage=ProcessingStage.PARSING,
        started_at=now - timedelta(minutes=1),
        updated_at=now - timedelta(seconds=30),
        progress_percentage=10,
    )
    monkeypatch.setattr(
        documents.pipeline_tracker,
        "get_pipeline_status",
        lambda *_args, **_kwargs: _async_value(stale_pipeline),
    )

    result = await documents.delete_document_endpoint(doc_id, {"sub": "user-1"})

    assert result.success is True


@pytest.mark.asyncio
async def test_delete_document_endpoint_blocks_newer_upload_tracker_after_terminal_job(
    monkeypatch,
):
    doc_id = str(uuid4())
    now = datetime.now(timezone.utc)
    verify_session = _FakeSession()
    _patch_session_factory(monkeypatch, [verify_session])

    monkeypatch.setattr(
        documents,
        "verify_document_ownership",
        lambda *_args, **_kwargs: SimpleNamespace(id=doc_id, user_id=42),
    )
    monkeypatch.setattr(
        documents,
        "get_document",
        lambda *_args, **_kwargs: _async_value(
            {"document": {"processing_status": "failed"}}
        ),
    )
    monkeypatch.setattr(
        documents.pdf_job_service,
        "get_latest_job_for_document",
        lambda **_kwargs: SimpleNamespace(
            status="failed",
            current_stage="failed",
            completed_at=now - timedelta(seconds=30),
            updated_at=now - timedelta(seconds=30),
        ),
    )
    newer_pipeline = PipelineStatus(
        document_id=doc_id,
        current_stage=ProcessingStage.UPLOAD,
        started_at=now - timedelta(seconds=10),
        updated_at=now,
        progress_percentage=10,
    )
    monkeypatch.setattr(
        documents.pipeline_tracker,
        "get_pipeline_status",
        lambda *_args, **_kwargs: _async_value(newer_pipeline),
    )

    with pytest.raises(HTTPException) as exc:
        await documents.delete_document_endpoint(doc_id, {"sub": "user-1"})

    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_delete_document_endpoint_allows_stale_postgres_only_document_cleanup(monkeypatch):
    doc_id = str(uuid4())
    verify_session = _FakeSession()
    cleanup_doc = SimpleNamespace(
        id=doc_id,
        user_id=42,
        file_path=None,
        pdfx_json_path=None,
        processed_json_path=None,
    )
    snapshot_session = _FakeSession(execute_doc=cleanup_doc)
    cleanup_session = _FakeSession(execute_doc=cleanup_doc)
    _patch_session_factory(monkeypatch, [verify_session, snapshot_session, cleanup_session])

    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: SimpleNamespace(id=doc_id, user_id=42))

    async def _missing_document(*_args, **_kwargs):
        raise ValueError(f"Document {doc_id} not found")

    monkeypatch.setattr(documents, "get_document", _missing_document)
    monkeypatch.setattr(
        documents,
        "delete_document",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("delete_document should not run")),
    )
    monkeypatch.setattr(
        documents.pdf_job_service,
        "get_latest_job_for_document",
        lambda **_kwargs: SimpleNamespace(status="completed", current_stage="completed"),
    )
    monkeypatch.setattr(documents.pipeline_tracker, "get_pipeline_status", lambda *_args, **_kwargs: _async_value(None))

    result = await documents.delete_document_endpoint(doc_id, {"sub": "user-1"})

    assert result.success is True
    assert result.document_id == doc_id
    assert "0 chunks deleted" in result.message
    assert cleanup_session.deleted == [cleanup_doc]
    assert cleanup_session.commits == 1


@pytest.mark.asyncio
async def test_delete_document_endpoint_removes_text_only_source_markdown(monkeypatch, tmp_path):
    doc_id = str(uuid4())
    source_dir = tmp_path / "user-1" / "source_markdown"
    source_dir.mkdir(parents=True)
    source_markdown = source_dir / f"{doc_id}.md"
    source_markdown.write_text("# Provider Markdown\n", encoding="utf-8")
    provider_placeholder_dir = tmp_path / "document_sources" / "abc_literature"
    provider_placeholder_dir.mkdir(parents=True)
    provider_placeholder = provider_placeholder_dir / f"{doc_id}-4672234.md"
    provider_placeholder.write_text("placeholder", encoding="utf-8")

    verify_session = _FakeSession()
    cleanup_doc = SimpleNamespace(
        id=doc_id,
        user_id=42,
        file_path=f"document_sources/abc_literature/{doc_id}-4672234.md",
        pdfx_json_path=None,
        processed_json_path=None,
        source_markdown_path=f"user-1/source_markdown/{doc_id}.md",
        viewer_mode="text_only",
    )
    snapshot_session = _FakeSession(execute_doc=cleanup_doc)
    cleanup_session = _FakeSession(execute_doc=cleanup_doc)
    _patch_session_factory(monkeypatch, [verify_session, snapshot_session, cleanup_session])

    monkeypatch.setattr(documents, "get_pdf_storage_path", lambda: str(tmp_path))
    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: SimpleNamespace(id=doc_id, user_id=42))
    monkeypatch.setattr(documents, "get_document", lambda *_args, **_kwargs: _async_value({"document": {"processing_status": "pending"}}))
    monkeypatch.setattr(documents, "delete_document", lambda *_args, **_kwargs: _async_value({"success": True, "chunks_deleted": 0}))
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(documents.pipeline_tracker, "get_pipeline_status", lambda *_args, **_kwargs: _async_value(None))

    result = await documents.delete_document_endpoint(doc_id, {"sub": "user-1"})

    assert result.success is True
    assert not source_markdown.exists()
    assert provider_placeholder.exists()
    assert cleanup_session.deleted == [cleanup_doc]
    assert cleanup_session.commits == 1


@pytest.mark.asyncio
async def test_delete_document_endpoint_uses_snapshot_when_helper_removes_sql_row(monkeypatch, tmp_path):
    doc_id = str(uuid4())
    pdf_dir = tmp_path / "user-1" / doc_id
    pdf_dir.mkdir(parents=True)
    pdf_file = pdf_dir / "paper.pdf"
    pdf_file.write_bytes(b"%PDF-1.7\n")
    source_dir = tmp_path / "user-1" / "source_markdown"
    source_dir.mkdir(parents=True)
    source_markdown = source_dir / f"{doc_id}.md"
    source_markdown.write_text("# Provider Markdown\n", encoding="utf-8")

    verify_session = _FakeSession()
    snapshot_doc = SimpleNamespace(
        id=doc_id,
        user_id=42,
        file_path=f"user-1/{doc_id}/paper.pdf",
        pdfx_json_path=None,
        processed_json_path=None,
        source_markdown_path=f"user-1/source_markdown/{doc_id}.md",
        viewer_mode="local_pdf",
    )
    snapshot_session = _FakeSession(execute_doc=snapshot_doc)
    cleanup_session = _FakeSession(execute_doc=None)
    _patch_session_factory(monkeypatch, [verify_session, snapshot_session, cleanup_session])

    monkeypatch.setattr(documents, "get_pdf_storage_path", lambda: str(tmp_path))
    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: SimpleNamespace(id=doc_id, user_id=42))
    monkeypatch.setattr(documents, "get_document", lambda *_args, **_kwargs: _async_value({"document": {"processing_status": "pending"}}))
    monkeypatch.setattr(documents, "delete_document", lambda *_args, **_kwargs: _async_value({"success": True, "chunks_deleted": 2}))
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(documents.pipeline_tracker, "get_pipeline_status", lambda *_args, **_kwargs: _async_value(None))

    result = await documents.delete_document_endpoint(doc_id, {"sub": "user-1"})

    assert result.success is True
    assert "2 chunks deleted" in result.message
    assert not pdf_dir.exists()
    assert not source_markdown.exists()
    assert cleanup_session.deleted == []
    assert cleanup_session.commits == 1


@pytest.mark.asyncio
async def test_delete_document_endpoint_raises_when_cleanup_fails(monkeypatch, tmp_path, caplog):
    doc_id = str(uuid4())
    verify_session = _FakeSession()
    cleanup_doc = SimpleNamespace(
        id=doc_id,
        user_id=42,
        file_path=None,
        pdfx_json_path=None,
        processed_json_path=None,
        source_markdown_path=f"user-1/source_markdown/{doc_id}.md",
        viewer_mode="text_only",
    )
    snapshot_session = _FakeSession(execute_doc=cleanup_doc)
    cleanup_session = _FakeSession(execute_doc=cleanup_doc)
    _patch_session_factory(monkeypatch, [verify_session, snapshot_session, cleanup_session])

    monkeypatch.setattr(documents, "get_pdf_storage_path", lambda: str(tmp_path))
    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: SimpleNamespace(id=doc_id, user_id=42))
    monkeypatch.setattr(documents, "get_document", lambda *_args, **_kwargs: _async_value({"document": {"processing_status": "pending"}}))
    monkeypatch.setattr(documents, "delete_document", lambda *_args, **_kwargs: _async_value({"success": True, "chunks_deleted": 0}))
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(documents.pipeline_tracker, "get_pipeline_status", lambda *_args, **_kwargs: _async_value(None))

    def _raise_cleanup(**kwargs):
        if kwargs.get("relative_path"):
            raise RuntimeError("source cleanup failed")

    monkeypatch.setattr(documents, "_unlink_user_storage_file_if_present", _raise_cleanup)
    caplog.set_level(logging.ERROR, logger=documents.logger.name)

    with pytest.raises(HTTPException) as exc:
        await documents.delete_document_endpoint(doc_id, {"sub": "user-1"})

    assert exc.value.status_code == 500
    assert exc.value.detail == "Failed to delete document"
    assert cleanup_session.rollbacks == 1
    assert cleanup_session.deleted == []
    assert "source cleanup failed" in caplog.text


@pytest.mark.asyncio
async def test_delete_document_endpoint_blocks_stale_postgres_only_document_with_active_job(monkeypatch):
    doc_id = str(uuid4())
    verify_session = _FakeSession()
    snapshot_session = _FakeSession(execute_doc=None)
    _patch_session_factory(monkeypatch, [verify_session, snapshot_session])

    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: SimpleNamespace(id=doc_id, user_id=42))

    async def _missing_document(*_args, **_kwargs):
        raise ValueError(f"Document {doc_id} not found")

    monkeypatch.setattr(documents, "get_document", _missing_document)
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
async def test_delete_document_endpoint_raises_500_when_delete_fails(monkeypatch):
    doc_id = str(uuid4())
    verify_session = _FakeSession()
    snapshot_session = _FakeSession(execute_doc=None)
    _patch_session_factory(monkeypatch, [verify_session, snapshot_session])

    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: SimpleNamespace(id=doc_id, user_id=42))
    monkeypatch.setattr(documents, "get_document", lambda *_args, **_kwargs: _async_value({"document": {"processing_status": "pending"}}))
    monkeypatch.setattr(documents, "delete_document", lambda *_args, **_kwargs: _async_value({"success": False, "message": "nope"}))
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(documents.pipeline_tracker, "get_pipeline_status", lambda *_args, **_kwargs: _async_value(None))

    with pytest.raises(HTTPException) as exc:
        await documents.delete_document_endpoint(doc_id, {"sub": "user-1"})
    assert exc.value.status_code == 500
    assert exc.value.detail == "nope"


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
async def test_status_endpoint_raises_500_on_unexpected_error(monkeypatch, caplog):
    monkeypatch.setattr(documents, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(documents, "principal_from_claims", lambda _claims: SimpleNamespace(subject="user-1"))
    monkeypatch.setattr(documents, "provision_user", lambda *_args, **_kwargs: SimpleNamespace(id=7))

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("lookup failed")

    monkeypatch.setattr(documents, "get_document", _boom)
    caplog.set_level(logging.ERROR, logger=documents.logger.name)

    with pytest.raises(HTTPException) as exc:
        await documents.get_document_processing_status("doc-1", {"sub": "user-1"})
    assert exc.value.status_code == 500
    assert exc.value.detail == "Failed to get document status"
    assert "lookup failed" in caplog.text


@pytest.mark.asyncio
async def test_upload_document_endpoint_rejects_non_pdf(monkeypatch, caplog):
    background_tasks = BackgroundTasks()
    request = SimpleNamespace(cookies={})
    upload = UploadFile(filename="notes.txt", file=BytesIO(b"text"))

    async def _raise_validation(**_kwargs):
        raise UploadIntakeValidationError("File must be a PDF. Got: notes.txt")

    monkeypatch.setattr(documents.upload_intake_service, "intake_upload", _raise_validation)
    caplog.set_level(logging.WARNING, logger=documents.logger.name)

    with pytest.raises(HTTPException) as exc:
        await documents.upload_document_endpoint(
            background_tasks,
            request,  # type: ignore[arg-type]
            upload,
            {"sub": "user-1"},
        )
    assert exc.value.status_code == 400
    assert exc.value.detail == "Invalid document upload request"
    assert "File must be a PDF. Got: notes.txt" in caplog.text


@pytest.mark.asyncio
async def test_upload_document_endpoint_reports_oversized_pdf_page_count(monkeypatch):
    background_tasks = BackgroundTasks()
    request = SimpleNamespace(cookies={})
    upload = UploadFile(filename="paper.pdf", file=BytesIO(b"%PDF-1.7"))
    detail = {
        "error": "pdf_page_count_exceeded",
        "message": "PDF page count (301) exceeds the configured maximum (300).",
        "actual_page_count": 301,
        "max_page_count": 300,
    }

    async def _raise_validation(**_kwargs):
        raise UploadIntakeValidationError(detail["message"], client_detail=detail)

    monkeypatch.setattr(documents.upload_intake_service, "intake_upload", _raise_validation)

    with pytest.raises(HTTPException) as exc:
        await documents.upload_document_endpoint(
            background_tasks,
            request,  # type: ignore[arg-type]
            upload,
            {"sub": "user-1"},
        )

    assert exc.value.status_code == 400
    assert exc.value.detail == detail


@pytest.mark.asyncio
async def test_upload_document_endpoint_happy_path(monkeypatch):
    background_tasks = BackgroundTasks()
    request = SimpleNamespace(cookies={"auth_token": "curator-token"})
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
    response = await documents.upload_document_endpoint(
        background_tasks,
        request,  # type: ignore[arg-type]
        upload,
        {"sub": "user-1", "groups": ["MGICurator"]},
    )

    assert response.user_id == 99
    assert response.filename == "paper.pdf"
    assert response.title is None
    assert response.status == "PENDING"
    assert response.weaviate_tenant == "tenant-user-1"
    assert captured["background_tasks"] is background_tasks
    assert captured["file"] is upload
    assert captured["user"] == {"sub": "user-1", "groups": ["MGICurator"]}
    assert captured["document_source_context"].authorized_group_ids == ("MGI",)
    assert captured["document_source_context"].curator_token == "curator-token"


def test_upload_document_route_builds_document_source_request_context(monkeypatch):
    monkeypatch.setenv("TESTING_API_KEY", "contract-test-key")
    monkeypatch.setenv("TESTING_API_KEY_GROUPS", "MGIStaff")
    captured = {}

    async def _intake_upload(**kwargs):
        captured.update(kwargs)
        return UploadIntakeResult(
            document_id="doc-route-context",
            job_id="job-route-context",
            user_id=123,
            filename="research_paper.pdf",
            status="PENDING",
            upload_timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
            processing_started_at=None,
            processing_completed_at=None,
            file_size_bytes=1234,
            weaviate_tenant="tenant-route-context",
            chunk_count=None,
            error_message=None,
        )

    monkeypatch.setattr(documents.upload_intake_service, "intake_upload", _intake_upload)
    app = FastAPI()
    app.include_router(documents.router)
    client = TestClient(app)
    client.cookies.set("auth_token", "browser-cookie-token")

    response = client.post(
        "/weaviate/documents/upload",
        headers={"X-API-Key": "contract-test-key"},
        files={"file": ("research_paper.pdf", BytesIO(b"%PDF-1.7"), "application/pdf")},
    )

    assert response.status_code == 201
    context = captured["document_source_context"]
    assert context.provider_groups == ("MGIStaff",)
    assert context.authorized_group_ids == ("MGI",)
    assert context.curator_token is None
    assert captured["file"].filename == "research_paper.pdf"


@pytest.mark.asyncio
async def test_upload_document_endpoint_maps_duplicate_error_to_409(monkeypatch):
    background_tasks = BackgroundTasks()
    request = SimpleNamespace(cookies={})
    upload = UploadFile(filename="paper.pdf", file=BytesIO(b"%PDF-1.7"))

    async def _raise_duplicate(**_kwargs):
        raise UploadIntakeDuplicateError(
            {
                "error": "duplicate_file",
                "message": "already uploaded",
                "existing_document_id": "doc-1",
                "existing_filename": "8385804.pdf",
            }
        )

    monkeypatch.setattr(documents.upload_intake_service, "intake_upload", _raise_duplicate)

    with pytest.raises(HTTPException) as exc:
        await documents.upload_document_endpoint(
            background_tasks,
            request,  # type: ignore[arg-type]
            upload,
            {"sub": "user-1"},
    )
    assert exc.value.status_code == 409
    detail = cast(dict[str, Any], exc.value.detail)
    assert isinstance(detail, dict)
    assert detail["error"] == "duplicate_file"
    assert detail["existing_document_id"] == "doc-1"
    assert detail["existing_filename"] == "8385804.pdf"


@pytest.mark.asyncio
async def test_upload_document_endpoint_maps_provider_decision_error(monkeypatch):
    background_tasks = BackgroundTasks()
    request = SimpleNamespace(cookies={})
    upload = UploadFile(filename="paper.pdf", file=BytesIO(b"%PDF-1.7"))

    async def _raise_provider_decision(**_kwargs):
        raise UploadIntakeProviderDecisionError(
            status_code=403,
            detail={
                "error": "document_source_access_denied",
                "message": "No matching source document is accessible to this curator.",
                "provider": "fake_provider",
                "status": "access_denied",
            },
        )

    monkeypatch.setattr(documents.upload_intake_service, "intake_upload", _raise_provider_decision)

    with pytest.raises(HTTPException) as exc:
        await documents.upload_document_endpoint(
            background_tasks,
            request,  # type: ignore[arg-type]
            upload,
            {"sub": "user-1"},
        )

    assert exc.value.status_code == 403
    detail = cast(dict[str, Any], exc.value.detail)
    assert detail["error"] == "document_source_access_denied"
    assert detail["provider"] == "fake_provider"


@pytest.mark.asyncio
async def test_upload_document_endpoint_sanitizes_unexpected_error(monkeypatch, caplog):
    background_tasks = BackgroundTasks()
    request = SimpleNamespace(cookies={})
    upload = UploadFile(filename="paper.pdf", file=BytesIO(b"%PDF-1.7"))

    async def _raise_unexpected(**_kwargs):
        raise RuntimeError("storage backend unavailable")

    monkeypatch.setattr(documents.upload_intake_service, "intake_upload", _raise_unexpected)
    caplog.set_level(logging.ERROR, logger=documents.logger.name)

    with pytest.raises(HTTPException) as exc:
        await documents.upload_document_endpoint(
            background_tasks,
            request,  # type: ignore[arg-type]
            upload,
            {"sub": "user-1"},
        )

    assert exc.value.status_code == 500
    assert exc.value.detail == "Failed to upload document"
    assert "storage backend unavailable" in caplog.text


@pytest.mark.asyncio
async def test_upload_fails_with_typed_503_before_intake_when_dev_curator_unavailable(
    monkeypatch,
):
    async def _unavailable(**_kwargs):
        raise documents.DevCuratorCredentialUnavailable("sanitized unavailable")

    async def _must_not_intake(**_kwargs):
        pytest.fail("upload intake must not begin without dev curator credentials")

    monkeypatch.setattr(documents, "build_document_source_request_context", _unavailable)
    monkeypatch.setattr(documents.upload_intake_service, "intake_upload", _must_not_intake)

    with pytest.raises(HTTPException) as exc_info:
        await documents.upload_document_endpoint(
            BackgroundTasks(),
            SimpleNamespace(cookies={}),  # type: ignore[arg-type]
            UploadFile(filename="paper.pdf", file=BytesIO(b"%PDF-1.7")),
            {"sub": "dev-user-123"},
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == {
        "error": "document_source_curator_token_unavailable",
        "message": "Document-source curator authentication is unavailable.",
        "suggestion": "Try again later or contact support if this persists.",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("route_kind", ["import", "resolve"])
async def test_identifier_routes_fail_before_service_when_dev_curator_unavailable(
    monkeypatch,
    route_kind,
):
    async def _unavailable(**_kwargs):
        raise documents.DevCuratorCredentialUnavailable("sanitized unavailable")

    async def _must_not_run(**_kwargs):
        pytest.fail("identifier service must not begin without dev curator credentials")

    monkeypatch.setattr(documents, "external_document_source_import_enabled", lambda: True)
    monkeypatch.setattr(documents, "build_document_source_request_context", _unavailable)
    monkeypatch.setattr(documents.identifier_import_service, "import_identifiers", _must_not_run)
    monkeypatch.setattr(documents.identifier_import_service, "resolve_identifiers", _must_not_run)
    payload = documents.DocumentSourceIdentifierImportRequest(identifiers="PMID:1")

    with pytest.raises(HTTPException) as exc_info:
        if route_kind == "import":
            await documents.import_documents_by_source_identifiers(
                payload,
                BackgroundTasks(),
                SimpleNamespace(cookies={}),  # type: ignore[arg-type]
                {"sub": "dev-user-123"},
            )
        else:
            await documents.resolve_documents_by_source_identifiers(
                payload,
                SimpleNamespace(cookies={}),  # type: ignore[arg-type]
                {"sub": "dev-user-123"},
            )

    assert exc_info.value.status_code == 503
    detail = cast(dict[str, Any], exc_info.value.detail)
    assert detail["error"] == "document_source_curator_token_unavailable"


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


@pytest.mark.asyncio
async def test_stream_document_progress_sanitizes_stream_errors(monkeypatch, caplog):
    doc_id = str(uuid4())

    async def _status(*_args, **_kwargs):
        raise RuntimeError("progress backend unavailable")

    monkeypatch.setenv("PDF_PROCESSING_SSE_POLL_INTERVAL_SECONDS", "1")
    monkeypatch.setenv("PDF_PROCESSING_SSE_TIMEOUT_SECONDS", "5")
    monkeypatch.setattr(documents, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(documents, "principal_from_claims", lambda _claims: SimpleNamespace(subject="user-1"))
    monkeypatch.setattr(documents, "provision_user", lambda *_args, **_kwargs: SimpleNamespace(id=7))
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(
        documents,
        "get_document",
        lambda *_args, **_kwargs: _async_value({"document": {"processing_status": "processing"}}),
    )
    monkeypatch.setattr(documents.pipeline_tracker, "get_pipeline_status", _status)
    caplog.set_level(logging.ERROR, logger=documents.logger.name)

    response = await documents.stream_document_progress(doc_id, {"sub": "user-1"})
    payload = await _collect_stream(response)

    assert '"error": "Failed to stream document progress"' in payload
    assert "progress backend unavailable" not in payload
    assert "progress backend unavailable" in caplog.text


@pytest.mark.asyncio
async def test_stream_document_progress_prefers_terminal_cancelled_job_snapshot(monkeypatch):
    now = datetime.now(timezone.utc)
    doc_id = str(uuid4())

    async def _status(*_args, **_kwargs):
        return PipelineStatus(
            document_id=doc_id,
            current_stage=ProcessingStage.COMPLETED,
            started_at=now,
            updated_at=now,
            progress_percentage=100,
            message="stale completion",
        )

    monkeypatch.setenv("PDF_PROCESSING_SSE_POLL_INTERVAL_SECONDS", "1")
    monkeypatch.setenv("PDF_PROCESSING_SSE_TIMEOUT_SECONDS", "5")
    monkeypatch.setattr(documents, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(documents, "principal_from_claims", lambda _claims: SimpleNamespace(subject="user-1"))
    monkeypatch.setattr(documents, "provision_user", lambda *_args, **_kwargs: SimpleNamespace(id=7))
    monkeypatch.setattr(
        documents.pdf_job_service,
        "get_latest_job_for_document",
        lambda **_kwargs: SimpleNamespace(
            status="cancelled",
            current_stage="cancelled",
            progress_percentage=64,
            message="Cancelled by user",
            updated_at=now,
        ),
    )
    monkeypatch.setattr(documents, "get_document", lambda *_args, **_kwargs: _async_value({"document": {"processing_status": "processing"}}))
    monkeypatch.setattr(documents.pipeline_tracker, "get_pipeline_status", _status)

    response = await documents.stream_document_progress(doc_id, {"sub": "user-1"})
    payload = await _collect_stream(response)
    assert '"source": "job"' in payload
    assert '"stage": "failed"' in payload
    assert "Cancelled by user" in payload
    assert '"final": true' in payload


@pytest.mark.asyncio
async def test_stream_document_progress_prefers_terminal_cancelled_job_over_stale_pipeline(monkeypatch):
    now = datetime.now(timezone.utc)
    doc_id = str(uuid4())

    async def _status(*_args, **_kwargs):
        return PipelineStatus(
            document_id=doc_id,
            current_stage=ProcessingStage.COMPLETED,
            started_at=now,
            updated_at=now,
            progress_percentage=100,
            message="stale pipeline success",
        )

    monkeypatch.setenv("PDF_PROCESSING_SSE_POLL_INTERVAL_SECONDS", "1")
    monkeypatch.setenv("PDF_PROCESSING_SSE_TIMEOUT_SECONDS", "5")
    monkeypatch.setattr(documents, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(documents, "principal_from_claims", lambda _claims: SimpleNamespace(subject="user-1"))
    monkeypatch.setattr(documents, "provision_user", lambda *_args, **_kwargs: SimpleNamespace(id=7))
    monkeypatch.setattr(
        documents.pdf_job_service,
        "get_latest_job_for_document",
        lambda **_kwargs: SimpleNamespace(
            status="cancelled",
            current_stage="cancelled",
            progress_percentage=61,
            message="Processing cancelled",
            error_message=None,
            updated_at=now,
            started_at=now,
            completed_at=now,
            document_id=doc_id,
        ),
    )
    monkeypatch.setattr(documents, "get_document", lambda *_args, **_kwargs: _async_value({"document": {"processing_status": "processing"}}))
    monkeypatch.setattr(documents.pipeline_tracker, "get_pipeline_status", _status)

    response = await documents.stream_document_progress(doc_id, {"sub": "user-1"})
    payload = await _collect_stream(response)

    assert '"stage": "failed"' in payload
    assert '"source": "job"' in payload
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

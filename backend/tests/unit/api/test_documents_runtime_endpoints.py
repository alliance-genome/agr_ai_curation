"""Runtime unit tests for core document endpoints."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from io import BytesIO
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock
from uuid import UUID, uuid4

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


class _FakeLockConnection:
    """Postgres in miniature for the per-paper delete lock: the lock code itself runs unchanged.

    The unit job has no database; the lock's behaviour against real Postgres (refusal when
    held, release on failure and cancellation, two deletes on one loop) is tested in
    tests/integration/persistence/test_supplement_add_search_index.py.
    """

    held: set[int] = set()
    log: list[str] = []

    def execute(self, statement, params):
        sql = str(statement)
        key = params["key"]
        if "pg_try_advisory_lock" in sql:
            taken = key not in self.held
            self.held.add(key)
            self.log.append("lock")
            return SimpleNamespace(scalar_one=lambda: taken)
        if "pg_advisory_unlock" in sql:
            self.held.discard(key)
            self.log.append("unlock")
            return SimpleNamespace(scalar_one=lambda: True)
        raise AssertionError(f"unexpected lock statement {sql}")

    def commit(self):
        pass

    def close(self):
        pass

    def invalidate(self):
        pass


@pytest.fixture(autouse=True)
def _paper_lock_database(monkeypatch):
    _FakeLockConnection.held = set()
    _FakeLockConnection.log = []
    monkeypatch.setattr(
        "src.models.sql.database.engine", SimpleNamespace(connect=lambda: _FakeLockConnection()), raising=False,
    )
    yield _FakeLockConnection
    assert _FakeLockConnection.held == set(), "a delete left its paper lock held"


_FILE_FIELDS = (
    "file_path", "file_hash", "file_size", "page_count", "pdfx_json_path", "processed_json_path",
    "hierarchy_metadata", "source_provider_source_file_id", "source_provider_converted_artifact_id",
    "source_provider_pdf_artifact_id", "source_md5", "source_converted_artifact_sha256",
    "source_file_class", "source_file_extension", "source_artifact_status", "source_import_status",
    "source_imported_at", "source_payload_path", "source_markdown_path", "source_access_scope",
    "source_access_group_ids",
)
_PAPER_PROVENANCE_FIELDS = (
    "source_provider", "source_provider_reference_id", "source_provider_reference_curie",
    "source_external_ids",
)


def _doc(*, display_mode="local_pdf", document_kind="library", file_status="ready", **values):
    """A paper record whose per-file values live on its main file."""
    file_values = {field: values.pop(field, None) for field in _FILE_FIELDS}
    file_values["display_mode"] = display_mode
    if display_mode == "text":
        file_values["page_count"] = None
    paper_values = {field: values.pop(field, None) for field in _PAPER_PROVENANCE_FIELDS}
    main_file = SimpleNamespace(id="main-file", role="main", status=file_status, **file_values)
    return SimpleNamespace(
        document_kind=document_kind, main_file=main_file, main_file_or_none=main_file, files=[main_file],
        **paper_values, **values,
    )


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

    def _open_lock_session(connection=None):
        if connection is None:
            return None
        assert isinstance(connection, _FakeLockConnection)
        return _factory()

    monkeypatch.setattr(documents, "SessionLocal", _factory)
    monkeypatch.setattr("src.lib.document_files.paper_lock.open_paper_lock_session", _open_lock_session)


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
    owned_doc = _doc(id=doc_id, user_id=10, )
    session = _FakeSession(execute_doc=owned_doc)
    monkeypatch.setattr(documents, "provision_user", lambda *_args, **_kwargs: SimpleNamespace(id=10))
    monkeypatch.setattr(documents, "principal_from_claims", lambda _claims: SimpleNamespace(subject="user-1"))

    result = documents.verify_document_ownership(session, doc_id, {"sub": "user-1"})
    assert result is owned_doc


@pytest.mark.asyncio
async def test_verify_document_ownership_can_lock_document_row(monkeypatch):
    doc_id = str(uuid4())
    owned_doc = _doc(id=doc_id, user_id=10, )
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
    foreign_doc = _doc(id=doc_id, user_id=999, )
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
    pg_doc = _doc(
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
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_paper_job_for_document", lambda **_kwargs: None)
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
    assert response.ready_file_count == 1
    assert response.processing_started_at == processing_started_at
    assert response.processing_completed_at == processing_completed_at


@pytest.mark.asyncio
async def test_get_document_endpoint_raises_500_on_backend_error(monkeypatch, caplog):
    monkeypatch.setattr(documents, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: _doc(filename="a", upload_timestamp=datetime.now(timezone.utc), file_size=1))
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
    document = _doc(
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
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_paper_job_for_document", lambda **_kwargs: None)
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
    assert document.main_file.file_path == f"user-1/{document_id}/renamed.pdf"
    assert not source_path.exists()
    assert source_path.with_name("renamed.pdf").read_bytes() == b"%PDF-1.7"
    assert index_updates == [("user-1", document_id, "renamed.pdf")]
    assert session.commits == 1


@pytest.mark.asyncio
async def test_update_document_endpoint_rejects_active_processing_rename(monkeypatch):
    document_id = str(uuid4())
    document = _doc(
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
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_paper_job_for_document", lambda **_kwargs: None)
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
async def test_update_document_endpoint_rejects_current_indexed_reembed_with_terminal_prior_job(
    monkeypatch,
):
    document_id = str(uuid4())
    document = _doc(
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
        documents.pdf_job_service,
        "get_latest_paper_job_for_document",
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
    assert ownership_calls == [{"for_update": True, "provisioned_user_id": 7}]


@pytest.mark.asyncio
async def test_update_document_endpoint_rejects_filename_collision(monkeypatch, tmp_path):
    document_id = str(uuid4())
    source_path = tmp_path / "user-1" / document_id / "old.pdf"
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(b"old")
    source_path.with_name("taken.pdf").write_bytes(b"taken")
    document = _doc(
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
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_paper_job_for_document", lambda **_kwargs: None)
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
    document = _doc(
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
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_paper_job_for_document", lambda **_kwargs: None)
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
    document = _doc(
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
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_paper_job_for_document", lambda **_kwargs: None)
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
    document = _doc(
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
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_paper_job_for_document", lambda **_kwargs: None)
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
    document = _doc(
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
        documents.pdf_job_service,
        "get_latest_paper_job_for_document",
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
    assert document.main_file.file_path == str(new_path.relative_to(tmp_path))
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
    document = _doc(
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
        documents.pdf_job_service,
        "get_latest_paper_job_for_document",
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
    assert document.main_file.file_path == str(new_path.relative_to(tmp_path))
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
    paper = _doc(id=doc_id, user_id=42)
    snapshot_session = _FakeSession(execute_doc=paper)
    file_session = _FakeSession(execute_doc=paper)
    cleanup_session = _FakeSession(execute_doc=paper)
    _patch_session_factory(monkeypatch, [verify_session, snapshot_session, file_session, cleanup_session])

    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: SimpleNamespace(id=doc_id, user_id=42))
    monkeypatch.setattr(documents, "get_document", lambda *_args, **_kwargs: _async_value({"document": {"processing_status": "pending"}}))
    monkeypatch.setattr(documents, "delete_paper_chunks", lambda *_args, **_kwargs: _async_value({"success": True, "chunks_deleted": 3}))
    monkeypatch.setattr(documents, "delete_paper_object", lambda *_args, **_kwargs: _async_value({"success": True, "deleted": True}))
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_paper_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(documents.pipeline_tracker, "get_pipeline_status", lambda *_args, **_kwargs: _async_value(None))

    result = await documents.delete_document_endpoint(doc_id, {"sub": "user-1"})
    assert result.success is True
    assert result.document_id == doc_id
    assert "3 chunks deleted" in result.message
    assert verify_session.closed is True
    assert file_session.closed is True
    assert cleanup_session.closed is True
    assert file_session.executed_statements[0]._for_update_arg is not None
    assert cleanup_session.executed_statements[0]._for_update_arg is not None


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
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_paper_job_for_document", lambda **_kwargs: None)
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
    monkeypatch.setattr(
        documents.pdf_job_service,
        "get_latest_paper_job_for_document",
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
    paper = _doc(id=doc_id, user_id=42)
    snapshot_session = _FakeSession(execute_doc=paper)
    file_session = _FakeSession(execute_doc=paper)
    cleanup_session = _FakeSession(execute_doc=paper)
    _patch_session_factory(monkeypatch, [verify_session, snapshot_session, file_session, cleanup_session])

    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: SimpleNamespace(id=doc_id, user_id=42))
    monkeypatch.setattr(documents, "get_document", lambda *_args, **_kwargs: _async_value({"document": {"processing_status": "pending"}}))
    monkeypatch.setattr(documents, "delete_paper_chunks", lambda *_args, **_kwargs: _async_value({"success": True, "chunks_deleted": 0}))
    monkeypatch.setattr(documents, "delete_paper_object", lambda *_args, **_kwargs: _async_value({"success": True, "deleted": True}))
    monkeypatch.setattr(
        documents.pdf_job_service,
        "get_latest_job_for_document",
        lambda **_kwargs: SimpleNamespace(status="failed", current_stage="failed"),
    )
    monkeypatch.setattr(
        documents.pdf_job_service,
        "get_latest_paper_job_for_document",
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
    paper = _doc(id=doc_id, user_id=42)
    snapshot_session = _FakeSession(execute_doc=paper)
    file_session = _FakeSession(execute_doc=paper)
    cleanup_session = _FakeSession(execute_doc=paper)
    _patch_session_factory(monkeypatch, [verify_session, snapshot_session, file_session, cleanup_session])

    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: SimpleNamespace(id=doc_id, user_id=42))
    monkeypatch.setattr(documents, "get_document", lambda *_args, **_kwargs: _async_value({"document": {"processing_status": "processing"}}))
    monkeypatch.setattr(
        documents.pdf_job_service,
        "get_latest_job_for_document",
        lambda **_kwargs: SimpleNamespace(status="failed", current_stage="failed"),
    )
    monkeypatch.setattr(
        documents.pdf_job_service,
        "get_latest_paper_job_for_document",
        lambda **_kwargs: SimpleNamespace(status="failed", current_stage="failed"),
    )
    monkeypatch.setattr(documents.pipeline_tracker, "get_pipeline_status", lambda *_args, **_kwargs: _async_value(None))
    monkeypatch.setattr(
        documents,
        "delete_paper_chunks",
        lambda *_args, **_kwargs: _async_value(
            {"success": True, "chunks_deleted": 0}
        ),
    )
    monkeypatch.setattr(
        documents,
        "delete_paper_object",
        lambda *_args, **_kwargs: _async_value(
            {"success": True, "deleted": True}
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
    paper = _doc(id=doc_id, user_id=42)
    snapshot_session = _FakeSession(execute_doc=paper)
    file_session = _FakeSession(execute_doc=paper)
    cleanup_session = _FakeSession(execute_doc=paper)
    _patch_session_factory(
        monkeypatch,
        [verify_session, snapshot_session, file_session, cleanup_session],
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
        "delete_paper_chunks",
        lambda *_args, **_kwargs: _async_value(
            {"success": True, "chunks_deleted": 0}
        ),
    )
    monkeypatch.setattr(
        documents,
        "delete_paper_object",
        lambda *_args, **_kwargs: _async_value(
            {"success": True, "deleted": True}
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
    monkeypatch.setattr(
        documents.pdf_job_service,
        "get_latest_paper_job_for_document",
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
    monkeypatch.setattr(
        documents.pdf_job_service,
        "get_latest_paper_job_for_document",
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
    cleanup_doc = _doc(
        id=doc_id,
        user_id=42,
        file_path=None,
        pdfx_json_path=None,
        processed_json_path=None,
    )
    snapshot_session = _FakeSession(execute_doc=cleanup_doc)
    file_session = _FakeSession(execute_doc=cleanup_doc)
    cleanup_session = _FakeSession(execute_doc=cleanup_doc)
    _patch_session_factory(monkeypatch, [verify_session, snapshot_session, file_session, cleanup_session])

    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: SimpleNamespace(id=doc_id, user_id=42))

    async def _missing_document(*_args, **_kwargs):
        raise ValueError(f"Document {doc_id} not found")

    chunk_wipe_calls = []

    async def _wipe_chunks(user_sub, target_doc_id):
        chunk_wipe_calls.append((user_sub, target_doc_id))
        return {"success": True, "chunks_deleted": 2}

    monkeypatch.setattr(documents, "get_document", _missing_document)
    monkeypatch.setattr(documents, "delete_paper_chunks", _wipe_chunks)
    monkeypatch.setattr(
        documents,
        "delete_paper_object",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("parent object delete should not run when already absent")),
    )
    monkeypatch.setattr(
        documents.pdf_job_service,
        "get_latest_job_for_document",
        lambda **_kwargs: SimpleNamespace(status="completed", current_stage="completed"),
    )
    monkeypatch.setattr(
        documents.pdf_job_service,
        "get_latest_paper_job_for_document",
        lambda **_kwargs: SimpleNamespace(status="completed", current_stage="completed"),
    )
    monkeypatch.setattr(documents.pipeline_tracker, "get_pipeline_status", lambda *_args, **_kwargs: _async_value(None))

    result = await documents.delete_document_endpoint(doc_id, {"sub": "user-1"})

    assert result.success is True
    assert result.document_id == doc_id
    assert chunk_wipe_calls == [("user-1", doc_id)]
    assert "2 chunks deleted" in result.message
    assert cleanup_session.deleted == [cleanup_doc]
    assert cleanup_session.commits == 1


@pytest.mark.asyncio
async def test_delete_document_endpoint_removes_text_source_markdown(monkeypatch, tmp_path):
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
    cleanup_doc = _doc(
        id=doc_id,
        user_id=42,
        file_path=f"document_sources/abc_literature/{doc_id}-4672234.md",
        pdfx_json_path=None,
        processed_json_path=None,
        source_markdown_path=f"user-1/source_markdown/{doc_id}.md",
        display_mode="text",
    )
    snapshot_session = _FakeSession(execute_doc=cleanup_doc)
    file_session = _FakeSession(execute_doc=cleanup_doc)
    cleanup_session = _FakeSession(execute_doc=cleanup_doc)
    _patch_session_factory(monkeypatch, [verify_session, snapshot_session, file_session, cleanup_session])

    monkeypatch.setattr(documents, "get_pdf_storage_path", lambda: str(tmp_path))
    # The shared stored-file helper reads the same setting where it is defined.
    monkeypatch.setattr("src.config.get_pdf_storage_path", lambda: str(tmp_path))
    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: SimpleNamespace(id=doc_id, user_id=42))
    monkeypatch.setattr(documents, "get_document", lambda *_args, **_kwargs: _async_value({"document": {"processing_status": "pending"}}))
    monkeypatch.setattr(documents, "delete_paper_chunks", lambda *_args, **_kwargs: _async_value({"success": True, "chunks_deleted": 0}))
    monkeypatch.setattr(documents, "delete_paper_object", lambda *_args, **_kwargs: _async_value({"success": True, "deleted": True}))
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_paper_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(documents.pipeline_tracker, "get_pipeline_status", lambda *_args, **_kwargs: _async_value(None))

    result = await documents.delete_document_endpoint(doc_id, {"sub": "user-1"})

    assert result.success is True
    assert not source_markdown.exists()
    assert provider_placeholder.exists()
    assert cleanup_session.deleted == [cleanup_doc]
    assert cleanup_session.commits == 1


@pytest.mark.asyncio
async def test_delete_document_endpoint_deletes_sql_row_after_removing_files(monkeypatch, tmp_path):
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
    paper = _doc(
        id=doc_id,
        user_id=42,
        file_path=f"user-1/{doc_id}/paper.pdf",
        pdfx_json_path=None,
        processed_json_path=None,
        source_markdown_path=f"user-1/source_markdown/{doc_id}.md",
    )
    snapshot_session = _FakeSession(execute_doc=paper)
    file_session = _FakeSession(execute_doc=paper)
    cleanup_session = _FakeSession(execute_doc=paper)
    _patch_session_factory(monkeypatch, [verify_session, snapshot_session, file_session, cleanup_session])

    order: list[str] = []

    async def _wipe_chunks(*_args, **_kwargs):
        assert pdf_dir.exists() and source_markdown.exists()
        order.append("chunks")
        return {"success": True, "chunks_deleted": 2}

    async def _delete_parent(*_args, **kwargs):
        assert kwargs == {"allow_missing": True}
        assert not pdf_dir.exists()
        assert not source_markdown.exists()
        assert cleanup_session.commits == 0
        order.append("parent")
        return {"success": True, "deleted": True}

    monkeypatch.setattr(documents, "get_pdf_storage_path", lambda: str(tmp_path))
    # The shared stored-file helper reads the same setting where it is defined.
    monkeypatch.setattr("src.config.get_pdf_storage_path", lambda: str(tmp_path))
    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: SimpleNamespace(id=doc_id, user_id=42))
    monkeypatch.setattr(documents, "get_document", lambda *_args, **_kwargs: _async_value({"document": {"processing_status": "pending"}}))
    monkeypatch.setattr(documents, "delete_paper_chunks", _wipe_chunks)
    monkeypatch.setattr(documents, "delete_paper_object", _delete_parent)
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_paper_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(documents.pipeline_tracker, "get_pipeline_status", lambda *_args, **_kwargs: _async_value(None))

    def delete_after_files(owned_paper):
        assert not pdf_dir.exists()
        assert not source_markdown.exists()
        assert cleanup_session.commits == 0
        order.append("sql_delete")
        cleanup_session.deleted.append(owned_paper)

    monkeypatch.setattr(cleanup_session, "delete", delete_after_files)
    result = await documents.delete_document_endpoint(doc_id, {"sub": "user-1"})

    assert result.success is True
    assert "2 chunks deleted" in result.message
    assert order == ["chunks", "parent", "sql_delete"]
    assert file_session.executed_statements[0]._for_update_arg is not None
    assert cleanup_session.executed_statements[0]._for_update_arg is not None
    assert not pdf_dir.exists()
    assert not source_markdown.exists()
    assert cleanup_session.deleted == [paper]
    assert cleanup_session.commits == 1


@pytest.mark.asyncio
async def test_delete_document_endpoint_raises_when_cleanup_fails(monkeypatch, tmp_path, caplog):
    doc_id = str(uuid4())
    verify_session = _FakeSession()
    cleanup_doc = _doc(
        id=doc_id,
        user_id=42,
        file_path=None,
        pdfx_json_path=None,
        processed_json_path=None,
        source_markdown_path=f"user-1/source_markdown/{doc_id}.md",
        display_mode="text",
    )
    snapshot_session = _FakeSession(execute_doc=cleanup_doc)
    file_session = _FakeSession(execute_doc=cleanup_doc)
    _patch_session_factory(monkeypatch, [verify_session, snapshot_session, file_session])

    monkeypatch.setattr(documents, "get_pdf_storage_path", lambda: str(tmp_path))
    # The shared stored-file helper reads the same setting where it is defined.
    monkeypatch.setattr("src.config.get_pdf_storage_path", lambda: str(tmp_path))
    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: SimpleNamespace(id=doc_id, user_id=42))
    monkeypatch.setattr(documents, "get_document", lambda *_args, **_kwargs: _async_value({"document": {"processing_status": "pending"}}))
    monkeypatch.setattr(documents, "delete_paper_chunks", lambda *_args, **_kwargs: _async_value({"success": True, "chunks_deleted": 0}))
    monkeypatch.setattr(
        documents,
        "delete_paper_object",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("parent object must stay when file cleanup fails")),
    )
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_paper_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(documents.pipeline_tracker, "get_pipeline_status", lambda *_args, **_kwargs: _async_value(None))

    def _raise_cleanup(paths, *, owner_subject):
        if list(paths):
            raise RuntimeError("source cleanup failed")

    monkeypatch.setattr(documents, "remove_stored_files", _raise_cleanup)
    caplog.set_level(logging.WARNING, logger=documents.logger.name)

    with pytest.raises(HTTPException) as exc:
        await documents.delete_document_endpoint(doc_id, {"sub": "user-1"})

    assert exc.value.status_code == 500
    assert exc.value.detail == "Failed to delete document"
    assert file_session.executed_statements[0]._for_update_arg is not None
    assert file_session.rollbacks == 1
    assert file_session.commits == 0
    assert file_session.deleted == []
    assert "Paper delete cleanup failed" in caplog.text
    assert "step=stored_files" in caplog.text
    assert "exception_type=RuntimeError" in caplog.text
    assert "source cleanup failed" not in caplog.text
    assert doc_id not in caplog.text


@pytest.mark.asyncio
async def test_delete_document_endpoint_retains_sql_row_when_parent_object_delete_fails(monkeypatch, tmp_path):
    doc_id = str(uuid4())
    verify_session = _FakeSession()
    paper = _doc(id=doc_id, user_id=42)
    snapshot_session = _FakeSession(execute_doc=paper)
    file_session = _FakeSession(execute_doc=paper)
    _patch_session_factory(monkeypatch, [verify_session, snapshot_session, file_session])

    monkeypatch.setattr(documents, "get_pdf_storage_path", lambda: str(tmp_path))
    monkeypatch.setattr("src.config.get_pdf_storage_path", lambda: str(tmp_path))
    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: SimpleNamespace(id=doc_id, user_id=42))
    monkeypatch.setattr(documents, "get_document", lambda *_args, **_kwargs: _async_value({"document": {"processing_status": "pending"}}))
    monkeypatch.setattr(documents, "delete_paper_chunks", lambda *_args, **_kwargs: _async_value({"success": True, "chunks_deleted": 0}))
    monkeypatch.setattr(documents, "delete_paper_object", lambda *_args, **_kwargs: _async_value({"success": False, "message": "weaviate parent failed"}))
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_paper_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(documents.pipeline_tracker, "get_pipeline_status", lambda *_args, **_kwargs: _async_value(None))

    with pytest.raises(HTTPException) as exc:
        await documents.delete_document_endpoint(doc_id, {"sub": "user-1"})

    assert exc.value.status_code == 500
    assert exc.value.detail == "weaviate parent failed"
    assert file_session.commits == 0
    assert file_session.deleted == []


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
    monkeypatch.setattr(
        documents.pdf_job_service,
        "get_latest_paper_job_for_document",
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
    snapshot_session = _FakeSession(execute_doc=_doc(id=doc_id, user_id=42))
    _patch_session_factory(monkeypatch, [verify_session, snapshot_session])

    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: SimpleNamespace(id=doc_id, user_id=42))
    monkeypatch.setattr(documents, "get_document", lambda *_args, **_kwargs: _async_value({"document": {"processing_status": "pending"}}))
    monkeypatch.setattr(documents, "delete_paper_chunks", lambda *_args, **_kwargs: _async_value({"success": False, "message": "nope"}))
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_paper_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(documents.pipeline_tracker, "get_pipeline_status", lambda *_args, **_kwargs: _async_value(None))

    with pytest.raises(HTTPException) as exc:
        await documents.delete_document_endpoint(doc_id, {"sub": "user-1"})
    assert exc.value.status_code == 500
    assert exc.value.detail == "nope"


@pytest.mark.asyncio
async def test_delete_document_endpoint_cancel_during_chunk_wipe_drains_under_lock(monkeypatch, tmp_path):
    from src.lib.document_files.paper_lock import paper_lock_key

    doc_id = str(uuid4())
    lock_key = paper_lock_key(doc_id)
    pdf_dir = tmp_path / "user-1" / doc_id
    pdf_dir.mkdir(parents=True)
    pdf_file = pdf_dir / "paper.pdf"
    pdf_file.write_bytes(b"%PDF-1.7\n")

    verify_session = _FakeSession()
    paper = _doc(id=doc_id, user_id=42, file_path=f"user-1/{doc_id}/paper.pdf")
    snapshot_session = _FakeSession(execute_doc=paper)
    file_session = _FakeSession(execute_doc=paper)
    cleanup_session = _FakeSession(execute_doc=paper)
    _patch_session_factory(monkeypatch, [verify_session, snapshot_session, file_session, cleanup_session])

    wipe_entered = asyncio.Event()
    resume_wipe = asyncio.Event()
    parent_entered = asyncio.Event()
    resume_parent = asyncio.Event()
    order: list[str] = []

    async def _wipe_chunks(*_args, **_kwargs):
        from src.lib.document_cleanup import mark_destructive_delete_started

        mark_destructive_delete_started()
        wipe_entered.set()
        await resume_wipe.wait()
        assert lock_key in _FakeLockConnection.held
        order.append("chunks")
        return {"success": True, "chunks_deleted": 3}

    async def _delete_parent(*_args, **kwargs):
        assert kwargs == {"allow_missing": True}
        assert not pdf_file.exists()
        assert lock_key in _FakeLockConnection.held
        order.append("files")
        parent_entered.set()
        await resume_parent.wait()
        assert lock_key in _FakeLockConnection.held
        order.append("parent")
        return {"success": True, "deleted": True}

    def _delete_sql(owned_paper):
        assert lock_key in _FakeLockConnection.held
        order.append("sql_delete")
        cleanup_session.deleted.append(owned_paper)

    monkeypatch.setattr(documents, "get_pdf_storage_path", lambda: str(tmp_path))
    monkeypatch.setattr("src.config.get_pdf_storage_path", lambda: str(tmp_path))
    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: SimpleNamespace(id=doc_id, user_id=42))
    monkeypatch.setattr(documents, "get_document", lambda *_args, **_kwargs: _async_value({"document": {"processing_status": "completed"}}))
    monkeypatch.setattr(documents, "delete_paper_chunks", _wipe_chunks)
    monkeypatch.setattr(documents, "delete_paper_object", _delete_parent)
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_paper_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(documents.pipeline_tracker, "get_pipeline_status", lambda *_args, **_kwargs: _async_value(None))
    monkeypatch.setattr(cleanup_session, "delete", _delete_sql)

    request_task = asyncio.create_task(documents.delete_document_endpoint(doc_id, {"sub": "user-1"}))
    await asyncio.wait_for(wipe_entered.wait(), timeout=2)

    request_task.cancel("client disconnected")
    await asyncio.sleep(0)
    request_task.cancel("second cancellation")
    await asyncio.sleep(0)
    assert not request_task.done()
    assert lock_key in _FakeLockConnection.held

    resume_wipe.set()
    await asyncio.wait_for(parent_entered.wait(), timeout=2)
    request_task.cancel("third cancellation during parent delete")
    await asyncio.sleep(0)
    assert not request_task.done()
    assert lock_key in _FakeLockConnection.held

    resume_parent.set()
    with pytest.raises(asyncio.CancelledError, match="client disconnected"):
        await asyncio.wait_for(request_task, timeout=2)

    assert order == ["chunks", "files", "parent", "sql_delete"]
    assert not pdf_file.exists()
    assert cleanup_session.deleted == [paper]
    assert cleanup_session.commits == 1
    assert _FakeLockConnection.held == set()


@pytest.mark.asyncio
async def test_delete_document_endpoint_cancel_before_destructive_write_cancels_promptly(monkeypatch):
    from src.lib.document_files.paper_lock import paper_lock_key

    doc_id = str(uuid4())
    lock_key = paper_lock_key(doc_id)
    verify_session = _FakeSession()
    _patch_session_factory(monkeypatch, [verify_session])

    in_get_document = asyncio.Event()
    wipe_called = False

    async def _slow_get_document(*_args, **_kwargs):
        assert lock_key in _FakeLockConnection.held
        in_get_document.set()
        await asyncio.sleep(5)
        return {"document": {"processing_status": "completed"}}

    async def _unexpected_wipe(*_args, **_kwargs):
        nonlocal wipe_called
        wipe_called = True
        return {"success": True, "chunks_deleted": 0}

    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: SimpleNamespace(id=doc_id, user_id=42))
    monkeypatch.setattr(documents, "get_document", _slow_get_document)
    monkeypatch.setattr(documents, "delete_paper_chunks", _unexpected_wipe)
    monkeypatch.setattr(
        documents,
        "delete_paper_object",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("parent delete must not run")),
    )
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_paper_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(documents.pipeline_tracker, "get_pipeline_status", lambda *_args, **_kwargs: _async_value(None))

    request_task = asyncio.create_task(documents.delete_document_endpoint(doc_id, {"sub": "user-1"}))
    await asyncio.wait_for(in_get_document.wait(), timeout=2)

    request_task.cancel("cancelled before destructive write")
    with pytest.raises(asyncio.CancelledError, match="cancelled before destructive write"):
        await asyncio.wait_for(request_task, timeout=2)

    assert wipe_called is False
    assert _FakeLockConnection.held == set()


@pytest.mark.asyncio
async def test_delete_document_endpoint_cancel_during_incomplete_chunk_wipe_retains_ownership(
    monkeypatch, tmp_path, caplog,
):
    from src.lib.document_files.paper_lock import paper_lock_key

    doc_id = str(uuid4())
    lock_key = paper_lock_key(doc_id)
    pdf_dir = tmp_path / "user-1" / doc_id
    pdf_dir.mkdir(parents=True)
    pdf_file = pdf_dir / "paper.pdf"
    pdf_file.write_bytes(b"%PDF-1.7\n")

    verify_session = _FakeSession()
    paper = _doc(id=doc_id, user_id=42, file_path=f"user-1/{doc_id}/paper.pdf")
    snapshot_session = _FakeSession(execute_doc=paper)
    _patch_session_factory(monkeypatch, [verify_session, snapshot_session])

    wipe_entered = asyncio.Event()
    resume_wipe = asyncio.Event()

    async def _incomplete_wipe(*_args, **_kwargs):
        from src.lib.document_cleanup import mark_destructive_delete_started

        mark_destructive_delete_started()
        wipe_entered.set()
        await resume_wipe.wait()
        assert lock_key in _FakeLockConnection.held
        return {
            "success": False,
            "message": "Document deletion reported as unsuccessful",
            "chunks_deleted": 1,
            "chunks_matched": 2,
            "chunks_failed": 1,
        }

    report = MagicMock()
    monkeypatch.setattr(documents, "report_runtime_exception", report)
    monkeypatch.setattr(documents, "get_pdf_storage_path", lambda: str(tmp_path))
    monkeypatch.setattr("src.config.get_pdf_storage_path", lambda: str(tmp_path))
    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: SimpleNamespace(id=doc_id, user_id=42))
    monkeypatch.setattr(documents, "get_document", lambda *_args, **_kwargs: _async_value({"document": {"processing_status": "completed"}}))
    monkeypatch.setattr(documents, "delete_paper_chunks", _incomplete_wipe)
    monkeypatch.setattr(
        documents,
        "delete_paper_object",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("parent object must stay when chunk wipe is incomplete")),
    )
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_paper_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(documents.pipeline_tracker, "get_pipeline_status", lambda *_args, **_kwargs: _async_value(None))
    caplog.set_level(logging.WARNING, logger=documents.logger.name)

    request_task = asyncio.create_task(documents.delete_document_endpoint(doc_id, {"sub": "user-1"}))
    await asyncio.wait_for(wipe_entered.wait(), timeout=2)
    request_task.cancel("client disconnected")
    await asyncio.sleep(0)
    request_task.cancel("second cancel")
    await asyncio.sleep(0)
    assert not request_task.done()
    assert lock_key in _FakeLockConnection.held

    resume_wipe.set()
    with pytest.raises(asyncio.CancelledError, match="client disconnected"):
        await asyncio.wait_for(request_task, timeout=2)

    assert pdf_file.exists()
    assert _FakeLockConnection.held == set()
    # Incomplete chunk wipes are owned solely by paper_delete_chunk_wipe_incomplete: no second capture.
    report.assert_not_called()
    assert doc_id not in caplog.text
    warnings = [record for record in caplog.records if record.name == documents.logger.name]
    assert warnings
    assert all(getattr(record, "sentry_skip_event", False) for record in warnings)


@pytest.mark.asyncio
async def test_delete_document_endpoint_cancel_during_file_unlink_failure_retains_ownership(
    monkeypatch, tmp_path, caplog,
):
    from src.lib.document_files.paper_lock import paper_lock_key
    from src.lib.observability.sentry import hash_sentry_identifier

    doc_id = str(uuid4())
    lock_key = paper_lock_key(doc_id)
    fake_private_path = f"/private-storage/fake-curator/{doc_id}/secret-paper.pdf"
    verify_session = _FakeSession()
    cleanup_doc = _doc(
        id=doc_id,
        user_id=42,
        file_path=None,
        pdfx_json_path=None,
        processed_json_path=None,
        source_markdown_path=f"user-1/source_markdown/{doc_id}.md",
        display_mode="text",
    )
    snapshot_session = _FakeSession(execute_doc=cleanup_doc)
    file_session = _FakeSession(execute_doc=cleanup_doc)
    _patch_session_factory(monkeypatch, [verify_session, snapshot_session, file_session])

    wipe_entered = asyncio.Event()
    resume_wipe = asyncio.Event()
    unlink_saw_lock = False

    async def _wipe_chunks(*_args, **_kwargs):
        from src.lib.document_cleanup import mark_destructive_delete_started

        mark_destructive_delete_started()
        wipe_entered.set()
        await resume_wipe.wait()
        return {"success": True, "chunks_deleted": 2}

    def _fail_unlink(paths, *, owner_subject):
        nonlocal unlink_saw_lock
        unlink_saw_lock = lock_key in _FakeLockConnection.held
        if list(paths):
            raise PermissionError(f"cannot unlink {fake_private_path}")

    report = MagicMock()
    monkeypatch.setattr(documents, "report_runtime_exception", report)
    monkeypatch.setattr(documents, "get_pdf_storage_path", lambda: str(tmp_path))
    monkeypatch.setattr("src.config.get_pdf_storage_path", lambda: str(tmp_path))
    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: SimpleNamespace(id=doc_id, user_id=42))
    monkeypatch.setattr(documents, "get_document", lambda *_args, **_kwargs: _async_value({"document": {"processing_status": "completed"}}))
    monkeypatch.setattr(documents, "delete_paper_chunks", _wipe_chunks)
    monkeypatch.setattr(documents, "remove_stored_files", _fail_unlink)
    monkeypatch.setattr(
        documents,
        "delete_paper_object",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("parent object must stay when file cleanup fails")),
    )
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_paper_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(documents.pipeline_tracker, "get_pipeline_status", lambda *_args, **_kwargs: _async_value(None))
    caplog.set_level(logging.WARNING, logger=documents.logger.name)

    request_task = asyncio.create_task(documents.delete_document_endpoint(doc_id, {"sub": "user-1"}))
    await asyncio.wait_for(wipe_entered.wait(), timeout=2)
    request_task.cancel("client disconnected")
    await asyncio.sleep(0)
    assert not request_task.done()
    assert lock_key in _FakeLockConnection.held

    resume_wipe.set()
    with pytest.raises(asyncio.CancelledError, match="client disconnected"):
        await asyncio.wait_for(request_task, timeout=2)

    assert unlink_saw_lock is True
    assert file_session.rollbacks == 1
    assert file_session.commits == 0
    assert file_session.deleted == []
    assert _FakeLockConnection.held == set()
    report.assert_called_once()
    (reported_exc,) = report.call_args.args
    assert isinstance(reported_exc, RuntimeError)
    assert str(reported_exc) == "Paper delete failed while draining after cancellation"
    assert reported_exc.__traceback__ is not None
    assert reported_exc.__cause__ is None and reported_exc.__context__ is None
    assert report.call_args.kwargs == {
        "component": "documents",
        "operation": "paper_delete_cancelled_drain_failed",
        "context": {
            "document_id_hash": hash_sentry_identifier(doc_id),
            "step": "stored_files",
            "exception_type": "PermissionError",
        },
        "fingerprint": ["paper_delete_cancelled_drain_failed", "stored_files", "PermissionError"],
        "include_request": False,
    }
    for sensitive in (fake_private_path, doc_id):
        assert sensitive not in repr(report.call_args)
        assert sensitive not in caplog.text
    warnings = [record for record in caplog.records if record.name == documents.logger.name]
    assert warnings
    assert all(getattr(record, "sentry_skip_event", False) for record in warnings)


@pytest.mark.asyncio
async def test_delete_document_endpoint_cancel_during_pool_timeout_emits_one_sanitized_report(
    monkeypatch, tmp_path, caplog,
):
    from sqlalchemy.exc import TimeoutError as PoolTimeoutError
    from src.lib.document_files.paper_lock import paper_lock_key
    from src.lib.observability.sentry import hash_sentry_identifier

    doc_id = str(uuid4())
    lock_key = paper_lock_key(doc_id)
    fake_db_secret = "private-pool-detail-fake-curator-session"
    verify_session = _FakeSession()
    paper = _doc(id=doc_id, user_id=42)
    snapshot_session = _FakeSession(execute_doc=paper)
    file_session = _FakeSession(execute_doc=paper)
    cleanup_session = _FakeSession(execute_doc=paper)
    _patch_session_factory(monkeypatch, [verify_session, snapshot_session, file_session, cleanup_session])

    wipe_entered = asyncio.Event()
    resume_wipe = asyncio.Event()

    async def _wipe_chunks(*_args, **_kwargs):
        from src.lib.document_cleanup import mark_destructive_delete_started

        mark_destructive_delete_started()
        wipe_entered.set()
        await resume_wipe.wait()
        return {"success": True, "chunks_deleted": 1}

    def _pool_timeout_on_commit():
        assert lock_key in _FakeLockConnection.held
        raise PoolTimeoutError(f"QueuePool exhausted for {fake_db_secret} doc={doc_id}")

    report = MagicMock()
    monkeypatch.setattr(documents, "report_runtime_exception", report)
    monkeypatch.setattr(documents, "get_pdf_storage_path", lambda: str(tmp_path))
    monkeypatch.setattr("src.config.get_pdf_storage_path", lambda: str(tmp_path))
    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: SimpleNamespace(id=doc_id, user_id=42))
    monkeypatch.setattr(documents, "get_document", lambda *_args, **_kwargs: _async_value({"document": {"processing_status": "completed"}}))
    monkeypatch.setattr(documents, "delete_paper_chunks", _wipe_chunks)
    monkeypatch.setattr(documents, "delete_paper_object", lambda *_args, **_kwargs: _async_value({"success": True, "deleted": True}))
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_paper_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(documents.pipeline_tracker, "get_pipeline_status", lambda *_args, **_kwargs: _async_value(None))
    monkeypatch.setattr(cleanup_session, "commit", _pool_timeout_on_commit)
    caplog.set_level(logging.WARNING, logger=documents.logger.name)

    request_task = asyncio.create_task(documents.delete_document_endpoint(doc_id, {"sub": "user-1"}))
    await asyncio.wait_for(wipe_entered.wait(), timeout=2)
    request_task.cancel("client disconnected")
    await asyncio.sleep(0)
    assert not request_task.done()

    resume_wipe.set()
    with pytest.raises(asyncio.CancelledError, match="client disconnected"):
        await asyncio.wait_for(request_task, timeout=2)

    assert cleanup_session.rollbacks == 1
    assert _FakeLockConnection.held == set()
    report.assert_called_once()
    (reported_exc,) = report.call_args.args
    assert isinstance(reported_exc, RuntimeError)
    assert reported_exc.__traceback__ is not None
    assert reported_exc.__cause__ is None and reported_exc.__context__ is None
    assert report.call_args.kwargs == {
        "component": "documents",
        "operation": "paper_delete_cancelled_drain_failed",
        "context": {
            "document_id_hash": hash_sentry_identifier(doc_id),
            "step": "sql_cleanup",
            "exception_type": "TimeoutError",
        },
        "fingerprint": ["paper_delete_cancelled_drain_failed", "sql_cleanup", "TimeoutError"],
        "include_request": False,
    }
    for sensitive in (fake_db_secret, doc_id):
        assert sensitive not in repr(report.call_args)
        assert sensitive not in caplog.text
    warnings = [record for record in caplog.records if record.name == documents.logger.name]
    assert warnings
    assert all(getattr(record, "sentry_skip_event", False) for record in warnings)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_point", ["before_task_create", "after_task_create"])
async def test_delete_document_endpoint_cancel_in_pre_submission_scheduling_gap_aborts_before_wipe(
    monkeypatch, tmp_path, cancel_point,
):
    from src.lib import document_cleanup

    doc_id = str(uuid4())
    pdf_dir = tmp_path / "user-1" / doc_id
    pdf_dir.mkdir(parents=True)
    pdf_file = pdf_dir / "paper.pdf"
    pdf_file.write_bytes(b"%PDF-1.7\n")

    verify_session = _FakeSession()
    paper = _doc(id=doc_id, user_id=42, file_path=f"user-1/{doc_id}/paper.pdf")
    snapshot_session = _FakeSession(execute_doc=paper)
    _patch_session_factory(monkeypatch, [verify_session, snapshot_session])

    wipe_called = False
    parent_called = False

    async def _unexpected_wipe(*_args, **_kwargs):
        nonlocal wipe_called
        wipe_called = True
        return {"success": True, "chunks_deleted": 3}

    async def _unexpected_parent(*_args, **_kwargs):
        nonlocal parent_called
        parent_called = True
        return {"success": True, "deleted": True}

    request_task: asyncio.Task[Any] | None = None
    real_drain = document_cleanup.drain_shielded
    real_create_task = document_cleanup.asyncio.create_task

    if cancel_point == "before_task_create":
        async def _cancel_before_drain(coro, **kwargs):
            assert request_task is not None
            request_task.cancel("cancelled right before submission")
            return await real_drain(coro, **kwargs)

        monkeypatch.setattr(documents, "drain_shielded", _cancel_before_drain)
    else:
        def _cancel_in_create_task_gap(coro, *args, **kwargs):
            task = real_create_task(coro, *args, **kwargs)
            if request_task is not None and getattr(getattr(coro, "cr_code", None), "co_name", None) == "_guarded":
                request_task.cancel("cancelled right before submission")
            return task

        monkeypatch.setattr(document_cleanup.asyncio, "create_task", _cancel_in_create_task_gap)

    monkeypatch.setattr(documents, "get_pdf_storage_path", lambda: str(tmp_path))
    monkeypatch.setattr("src.config.get_pdf_storage_path", lambda: str(tmp_path))
    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: SimpleNamespace(id=doc_id, user_id=42))
    monkeypatch.setattr(documents, "get_document", lambda *_args, **_kwargs: _async_value({"document": {"processing_status": "completed"}}))
    monkeypatch.setattr(documents, "delete_paper_chunks", _unexpected_wipe)
    monkeypatch.setattr(documents, "delete_paper_object", _unexpected_parent)
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_paper_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(documents.pipeline_tracker, "get_pipeline_status", lambda *_args, **_kwargs: _async_value(None))

    request_task = real_create_task(documents.delete_document_endpoint(doc_id, {"sub": "user-1"}))
    with pytest.raises(asyncio.CancelledError, match="cancelled right before submission"):
        await asyncio.wait_for(request_task, timeout=2)

    assert wipe_called is False
    assert parent_called is False
    assert pdf_file.exists()
    assert _FakeLockConnection.held == set()


@pytest.mark.asyncio
async def test_delete_document_endpoint_cancel_preserves_cancellation_when_report_callback_fails(
    monkeypatch, tmp_path,
):
    doc_id = str(uuid4())
    verify_session = _FakeSession()
    cleanup_doc = _doc(
        id=doc_id,
        user_id=42,
        file_path=None,
        pdfx_json_path=None,
        processed_json_path=None,
        source_markdown_path=f"user-1/source_markdown/{doc_id}.md",
        display_mode="text",
    )
    snapshot_session = _FakeSession(execute_doc=cleanup_doc)
    file_session = _FakeSession(execute_doc=cleanup_doc)
    _patch_session_factory(monkeypatch, [verify_session, snapshot_session, file_session])

    wipe_entered = asyncio.Event()
    resume_wipe = asyncio.Event()

    async def _wipe_chunks(*_args, **_kwargs):
        from src.lib.document_cleanup import mark_destructive_delete_started

        mark_destructive_delete_started()
        wipe_entered.set()
        await resume_wipe.wait()
        return {"success": True, "chunks_deleted": 1}

    def _fail_unlink(paths, *, owner_subject):
        if list(paths):
            raise PermissionError("fake-unlink-error")

    monkeypatch.setattr(
        documents,
        "report_runtime_exception",
        MagicMock(side_effect=RuntimeError("sentry transport down")),
    )
    monkeypatch.setattr(documents, "get_pdf_storage_path", lambda: str(tmp_path))
    monkeypatch.setattr("src.config.get_pdf_storage_path", lambda: str(tmp_path))
    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: SimpleNamespace(id=doc_id, user_id=42))
    monkeypatch.setattr(documents, "get_document", lambda *_args, **_kwargs: _async_value({"document": {"processing_status": "completed"}}))
    monkeypatch.setattr(documents, "delete_paper_chunks", _wipe_chunks)
    monkeypatch.setattr(documents, "remove_stored_files", _fail_unlink)
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_paper_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(documents.pipeline_tracker, "get_pipeline_status", lambda *_args, **_kwargs: _async_value(None))

    request_task = asyncio.create_task(documents.delete_document_endpoint(doc_id, {"sub": "user-1"}))
    await asyncio.wait_for(wipe_entered.wait(), timeout=2)
    request_task.cancel("client disconnected")
    await asyncio.sleep(0)

    resume_wipe.set()
    with pytest.raises(asyncio.CancelledError, match="client disconnected"):
        await asyncio.wait_for(request_task, timeout=2)

    assert file_session.rollbacks == 1
    assert _FakeLockConnection.held == set()


@pytest.mark.asyncio
async def test_delete_document_endpoint_cancel_during_real_chunk_wipe_checkpoint_aborts_before_submit(
    monkeypatch, tmp_path,
):
    from contextlib import contextmanager
    from src.lib.weaviate_client import documents as weaviate_docs

    doc_id = str(uuid4())
    pdf_dir = tmp_path / "user-1" / doc_id
    pdf_dir.mkdir(parents=True)
    pdf_file = pdf_dir / "paper.pdf"
    pdf_file.write_bytes(b"%PDF-1.7\n")

    verify_session = _FakeSession()
    paper = _doc(id=doc_id, user_id=42, file_path=f"user-1/{doc_id}/paper.pdf")
    snapshot_session = _FakeSession(execute_doc=paper)
    _patch_session_factory(monkeypatch, [verify_session, snapshot_session])

    chunks, papers = MagicMock(), MagicMock()

    @contextmanager
    def _unexpected_session():
        raise AssertionError("Weaviate session must not open when cancelled before submission")
        yield MagicMock()

    connection = MagicMock()
    connection.session.side_effect = _unexpected_session
    monkeypatch.setattr(weaviate_docs, "get_connection", lambda: connection)
    monkeypatch.setattr(weaviate_docs, "get_user_collections", lambda *_: (chunks, papers))

    request_task: asyncio.Task[Any] | None = None
    real_sleep = weaviate_docs.asyncio.sleep
    cancelled_once = False

    async def _cancel_at_helper_checkpoint(delay, *args, **kwargs):
        nonlocal cancelled_once
        if delay == 0 and not cancelled_once and request_task is not None:
            cancelled_once = True
            request_task.cancel("cancelled at helper checkpoint")
        return await real_sleep(delay, *args, **kwargs)

    monkeypatch.setattr(weaviate_docs.asyncio, "sleep", _cancel_at_helper_checkpoint)
    monkeypatch.setattr(documents, "get_pdf_storage_path", lambda: str(tmp_path))
    monkeypatch.setattr("src.config.get_pdf_storage_path", lambda: str(tmp_path))
    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: SimpleNamespace(id=doc_id, user_id=42))
    monkeypatch.setattr(documents, "get_document", lambda *_args, **_kwargs: _async_value({"document": {"processing_status": "completed"}}))
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_paper_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(documents.pipeline_tracker, "get_pipeline_status", lambda *_args, **_kwargs: _async_value(None))

    request_task = asyncio.create_task(documents.delete_document_endpoint(doc_id, {"sub": "user-1"}))
    with pytest.raises(asyncio.CancelledError, match="cancelled at helper checkpoint"):
        await asyncio.wait_for(request_task, timeout=2)

    assert cancelled_once is True
    connection.session.assert_not_called()
    chunks.data.delete_many.assert_not_called()
    papers.data.delete_by_id.assert_not_called()
    assert pdf_file.exists()
    assert _FakeLockConnection.held == set()


@pytest.mark.asyncio
async def test_delete_document_endpoint_cancel_at_chunk_wipe_yield_before_submit_aborts(
    monkeypatch, tmp_path,
):
    """Cancel arriving right at delete_paper_chunks's own yield aborts with zero wipe."""
    from contextlib import contextmanager
    from src.lib.weaviate_client import documents as weaviate_docs

    doc_id = str(uuid4())
    pdf_dir = tmp_path / "user-1" / doc_id
    pdf_dir.mkdir(parents=True)
    pdf_file = pdf_dir / "paper.pdf"
    pdf_file.write_bytes(b"%PDF-1.7\n")

    verify_session = _FakeSession()
    paper = _doc(id=doc_id, user_id=42, file_path=f"user-1/{doc_id}/paper.pdf")
    snapshot_session = _FakeSession(execute_doc=paper)
    _patch_session_factory(monkeypatch, [verify_session, snapshot_session])

    chunks, papers = MagicMock(), MagicMock()

    @contextmanager
    def _unexpected_session(*_args, **_kwargs):
        raise AssertionError("Weaviate session must not open when cancelled at chunk-wipe yield")
        yield MagicMock()

    connection = MagicMock()
    connection.session.side_effect = _unexpected_session
    monkeypatch.setattr(weaviate_docs, "get_connection", lambda: connection)
    monkeypatch.setattr(weaviate_docs, "get_user_collections", lambda *_: (chunks, papers))

    request_task: asyncio.Task[Any] | None = None
    real_sleep = weaviate_docs.asyncio.sleep
    sleep_zero_calls = 0
    cancelled_in_chunk_wipe = False

    async def _cancel_after_chunk_wipe_yield(delay, *args, **kwargs):
        nonlocal sleep_zero_calls, cancelled_in_chunk_wipe
        result = await real_sleep(delay, *args, **kwargs)
        if delay == 0:
            sleep_zero_calls += 1
            # Call 1 is drain_shielded's entry yield; call 2 is delete_paper_chunks's
            # pre-submission yield inside the shielded child task. Cancelling the
            # owning request_task right as call 2 resumes reproduces the exact scheduling
            # order where the child steps before the owner runs task.cancel().
            if sleep_zero_calls == 2 and request_task is not None:
                cancelled_in_chunk_wipe = True
                request_task.cancel("cancelled at chunk-wipe yield")
        return result

    monkeypatch.setattr(weaviate_docs.asyncio, "sleep", _cancel_after_chunk_wipe_yield)
    monkeypatch.setattr(documents, "get_pdf_storage_path", lambda: str(tmp_path))
    monkeypatch.setattr("src.config.get_pdf_storage_path", lambda: str(tmp_path))
    monkeypatch.setattr(
        documents,
        "verify_document_ownership",
        lambda *_args, **_kwargs: SimpleNamespace(id=doc_id, user_id=42),
    )
    monkeypatch.setattr(
        documents,
        "get_document",
        lambda *_args, **_kwargs: _async_value({"document": {"processing_status": "completed"}}),
    )
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_paper_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(documents.pipeline_tracker, "get_pipeline_status", lambda *_args, **_kwargs: _async_value(None))

    request_task = asyncio.create_task(documents.delete_document_endpoint(doc_id, {"sub": "user-1"}))
    with pytest.raises(asyncio.CancelledError, match="cancelled at chunk-wipe yield"):
        await asyncio.wait_for(request_task, timeout=2)

    assert cancelled_in_chunk_wipe is True
    assert sleep_zero_calls == 2
    connection.session.assert_not_called()
    chunks.data.delete_many.assert_not_called()
    papers.data.delete_by_id.assert_not_called()
    assert pdf_file.exists()
    assert _FakeLockConnection.held == set()


@pytest.mark.parametrize(
    "failure_mode",
    [
        "chunk_error",
        "parent_error",
        "parent_reconnect_error",
        "parent_reconnect_cancel_at_yield",
        "stored_files_cancel_before_on_cancel",
        "sql_cleanup_cancel_before_on_cancel",
        "incomplete_wipe",
    ],
)
def test_cancelled_delete_worker_failures_emit_one_sanitized_sentry_event_without_request_url(
    failure_mode,
):
    import subprocess
    import sys
    import textwrap

    script = textwrap.dedent(f"""
        import asyncio
        import logging
        import os
        import tempfile
        import threading
        from contextlib import contextmanager
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        os.environ["SENTRY_CONTENT_REDACTION_ENABLED"] = "false"

        import sentry_sdk
        from sentry_sdk.integrations.logging import LoggingIntegration
        import weaviate

        from src.api import documents
        from src.lib.document_files import paper_lock
        from src.lib.observability.sentry import before_send, hash_sentry_identifier
        from src.lib.weaviate_client.connection import WeaviateConnection
        from src.lib.weaviate_client import documents as weaviate_docs
        import src.models.sql.database as sql_db

        doc_id = "11111111-2222-3333-4444-555555555555"
        secret_detail = "private-weaviate-detail-for-secret-curator"
        events = []

        logging.basicConfig(level=logging.INFO)
        sentry_sdk.init(
            dsn="http://public@example.invalid/1",
            transport=events.append,
            before_send=before_send,
            include_local_variables=False,
            default_integrations=False,
            integrations=[LoggingIntegration(level=logging.INFO, event_level=logging.ERROR)],
        )

        def _attach_request_url(event, _hint):
            event["request"] = {{"url": f"https://example.invalid/weaviate/documents/{{doc_id}}"}}
            return event

        sentry_sdk.get_global_scope().add_event_processor(_attach_request_url)

        class _LockConn:
            def execute(self, statement, params):
                return SimpleNamespace(scalar_one=lambda: True)
            def commit(self):
                pass
            def close(self):
                pass
            def invalidate(self):
                pass

        sql_db.engine = SimpleNamespace(connect=lambda: _LockConn())

        class _Session:
            def __init__(self, doc=None, *, fail_on_delete=False):
                self._doc = doc
                self._fail_on_delete = fail_on_delete
            def execute(self, *_a, **_kw):
                return SimpleNamespace(
                    scalars=lambda: SimpleNamespace(first=lambda: self._doc),
                    scalar_one_or_none=lambda: self._doc,
                )
            def delete(self, _row):
                if self._fail_on_delete:
                    raise RuntimeError(f"{{secret_detail}} {{doc_id}}")
            def commit(self):
                pass
            def rollback(self):
                pass
            def close(self):
                pass

        main_file = SimpleNamespace(
            id="f1", role="main", status="ready", display_mode="local_pdf",
            file_path=None, pdfx_json_path=None, processed_json_path=None, source_markdown_path=None,
        )
        paper = SimpleNamespace(
            id=doc_id, user_id=42, document_kind="library",
            main_file=main_file, main_file_or_none=main_file, files=[main_file],
        )
        sessions = [
            _Session(),
            _Session(paper),
            _Session(paper),
            _Session(paper, fail_on_delete=({failure_mode!r} == "sql_cleanup_cancel_before_on_cancel")),
        ]
        documents.SessionLocal = lambda: sessions.pop(0)
        def _open_lock_session(conn=None):
            if conn is None:
                return None
            assert isinstance(conn, _LockConn)
            return sessions.pop(0)
        paper_lock.open_paper_lock_session = _open_lock_session
        documents.provision_user_async = lambda _p: asyncio.sleep(0, result=SimpleNamespace(id=42))
        documents.verify_document_ownership = lambda *_a, **_kw: SimpleNamespace(id=doc_id, user_id=42)
        documents.get_document = lambda *_a, **_kw: asyncio.sleep(0, result={{"document": {{"processing_status": "completed"}}}})
        documents.pdf_job_service.get_latest_job_for_document = lambda **_kw: None
        documents.pipeline_tracker.get_pipeline_status = lambda *_a, **_kw: asyncio.sleep(0, result=None)
        documents.cleanup_document_curation_dependencies = lambda *_a, **_kw: {{}}
        if {failure_mode!r} == "stored_files_cancel_before_on_cancel":
            def _boom_files(*_a, **_kw):
                raise RuntimeError(f"{{secret_detail}} {{doc_id}}")
            documents.remove_stored_files = _boom_files

        wipe_entered = threading.Event()
        resume_wipe = threading.Event()
        ready_state = {{"ready": True}}
        wipe_state = {{"done": False, "cancelled_at_parent": False, "cancelled_before_on_cancel": False}}
        is_reconnect_mode = {failure_mode!r} in {{"parent_reconnect_error", "parent_reconnect_cancel_at_yield"}}
        is_post_wipe_cancel_mode = {failure_mode!r} in {{
            "parent_reconnect_cancel_at_yield",
            "stored_files_cancel_before_on_cancel",
            "sql_cleanup_cancel_before_on_cancel",
        }}

        if is_reconnect_mode:
            WeaviateConnection._instance = None
            conn = WeaviateConnection(url="http://localhost:8080")
            fake_client = MagicMock()
            fake_client.is_ready.side_effect = lambda: ready_state["ready"]
            conn._client = fake_client

            def _refuse_reconnect(**_kw):
                raise ConnectionRefusedError(f"{{secret_detail}} {{doc_id}}")

            weaviate.connect_to_local = _refuse_reconnect
        else:
            @contextmanager
            def _weaviate_session(*_args, **_kwargs):
                yield MagicMock()

            conn = MagicMock()
            conn.session.side_effect = _weaviate_session

        chunks, papers = MagicMock(), MagicMock()

        def _delete_many(**_kw):
            wipe_entered.set()
            assert resume_wipe.wait(timeout=5)
            if {failure_mode!r} == "chunk_error":
                raise RuntimeError(f"{{secret_detail}} {{doc_id}}")
            if {failure_mode!r} == "incomplete_wipe":
                return SimpleNamespace(successful=1, matches=2, failed=1)
            if is_reconnect_mode:
                ready_state["ready"] = False
            wipe_state["done"] = True
            return SimpleNamespace(successful=2, matches=2, failed=0)

        def _delete_by_id(_doc_id):
            if {failure_mode!r} == "parent_error":
                raise RuntimeError(f"{{secret_detail}} {{doc_id}}")
            return True

        chunks.data.delete_many.side_effect = _delete_many
        papers.data.delete_by_id.side_effect = _delete_by_id
        weaviate_docs.get_connection = lambda: conn
        weaviate_docs.get_user_collections = lambda *_a: (chunks, papers)

        async def _exercise():
            task_holder = []
            if {failure_mode!r} == "parent_reconnect_cancel_at_yield":
                real_sleep = weaviate_docs.asyncio.sleep

                async def _cancel_at_parent_yield(delay, *args, **kwargs):
                    res = await real_sleep(delay, *args, **kwargs)
                    if (
                        delay == 0
                        and wipe_state["done"]
                        and not wipe_state["cancelled_at_parent"]
                        and task_holder
                    ):
                        wipe_state["cancelled_at_parent"] = True
                        task_holder[0].cancel("client disconnected at parent yield")
                    return res

                weaviate_docs.asyncio.sleep = _cancel_at_parent_yield
            elif {failure_mode!r} in {{
                "stored_files_cancel_before_on_cancel",
                "sql_cleanup_cancel_before_on_cancel",
            }}:
                target_worker = (
                    "remove_paper_files"
                    if {failure_mode!r} == "stored_files_cancel_before_on_cancel"
                    else "finish_delete"
                )
                real_run_thread = documents.run_thread_drained

                async def _cancel_before_on_cancel(func, *args, **kwargs):
                    if getattr(func, "__name__", None) == target_worker:
                        try:
                            return await real_run_thread(func, *args, **kwargs)
                        except Exception:
                            # Cancel the owning request task immediately before complete_delete's
                            # except handler runs so owner_task.cancelling() > initial_cancels
                            # while drain_state.cancelled is still False.
                            wipe_state["cancelled_before_on_cancel"] = True
                            task_holder[0].cancel("client disconnected before on_cancel")
                            raise
                    return await real_run_thread(func, *args, **kwargs)

                documents.run_thread_drained = _cancel_before_on_cancel

            with tempfile.TemporaryDirectory() as tmp:
                documents.get_pdf_storage_path = lambda: tmp
                task = asyncio.create_task(documents.delete_document_endpoint(doc_id, {{"sub": "user-1"}}))
                task_holder.append(task)
                try:
                    assert await asyncio.to_thread(wipe_entered.wait, 5)
                    if not is_post_wipe_cancel_mode:
                        task.cancel("client disconnected")
                        await asyncio.sleep(0.02)
                finally:
                    resume_wipe.set()
                try:
                    await asyncio.wait_for(task, timeout=5)
                except asyncio.CancelledError:
                    pass
                else:
                    raise AssertionError("expected CancelledError")

        asyncio.run(_exercise())

        if {failure_mode!r} == "parent_reconnect_cancel_at_yield":
            assert wipe_state["cancelled_at_parent"] is True
        if {failure_mode!r} in {{
            "stored_files_cancel_before_on_cancel",
            "sql_cleanup_cancel_before_on_cancel",
        }}:
            assert wipe_state["cancelled_before_on_cancel"] is True

        assert len(events) == 1, f"expected 1 Sentry event, got {{len(events)}}: {{events!r}}"
        event = events[0]
        assert "request" not in event, f"request leaked into Sentry event: {{event.get('request')!r}}"
        assert doc_id not in repr(event), f"raw doc_id leaked: {{event!r}}"
        assert secret_detail not in repr(event), f"secret leaked: {{event!r}}"
        runtime_ctx = event["contexts"]["runtime_exception"]
        if {failure_mode!r} == "incomplete_wipe":
            assert runtime_ctx["component"] == "weaviate_client"
            assert runtime_ctx["operation"] == "paper_delete_chunk_wipe_incomplete"
        else:
            step_by_mode = {{
                "chunk_error": "chunk_wipe",
                "parent_error": "parent_object",
                "parent_reconnect_error": "parent_object",
                "parent_reconnect_cancel_at_yield": "parent_object",
                "stored_files_cancel_before_on_cancel": "stored_files",
                "sql_cleanup_cancel_before_on_cancel": "sql_cleanup",
            }}
            exc_by_mode = {{
                "chunk_error": "HTTPException",
                "parent_error": "HTTPException",
                "parent_reconnect_error": "ConnectionRefusedError",
                "parent_reconnect_cancel_at_yield": "ConnectionRefusedError",
                "stored_files_cancel_before_on_cancel": "RuntimeError",
                "sql_cleanup_cancel_before_on_cancel": "RuntimeError",
            }}
            assert runtime_ctx == {{
                "component": "documents",
                "operation": "paper_delete_cancelled_drain_failed",
                "document_id_hash": hash_sentry_identifier(doc_id),
                "step": step_by_mode[{failure_mode!r}],
                "exception_type": exc_by_mode[{failure_mode!r}],
            }}
    """)
    result = subprocess.run([sys.executable, "-c", script], text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("failure_step", ["stored_files", "sql_cleanup"])
def test_ordinary_delete_cleanup_failures_emit_one_sanitized_sentry_event_without_request_url(
    failure_step,
):
    """Non-cancelled HTTP stored_files/sql_cleanup failures emit 1 sanitized Sentry event, no request.url, and HTTP 500."""
    import subprocess
    import sys
    import textwrap

    script = textwrap.dedent(f"""
        import asyncio
        from contextlib import contextmanager
        import io
        import logging
        import os
        import tempfile
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        os.environ["SENTRY_CONTENT_REDACTION_ENABLED"] = "false"

        import sentry_sdk
        from sentry_sdk.integrations.logging import LoggingIntegration
        from fastapi import HTTPException

        from src.api import documents
        from src.lib.document_files import paper_lock
        from src.lib.observability.sentry import before_send, hash_sentry_identifier
        from src.lib.weaviate_client import documents as weaviate_docs
        import src.models.sql.database as sql_db

        doc_id = "22222222-3333-4444-5555-666666666666"
        secret_detail = "private-db-constraint-value-secret"
        events = []

        log_buffer = io.StringIO()
        log_handler = logging.StreamHandler(log_buffer)
        log_handler.setLevel(logging.INFO)
        logging.basicConfig(level=logging.INFO, handlers=[log_handler], force=True)
        sentry_sdk.init(
            dsn="http://public@example.invalid/1",
            transport=events.append,
            before_send=before_send,
            include_local_variables=False,
            default_integrations=False,
            integrations=[LoggingIntegration(level=logging.INFO, event_level=logging.ERROR)],
        )

        def _attach_request_url(event, _hint):
            event["request"] = {{"url": f"https://example.invalid/weaviate/documents/{{doc_id}}"}}
            return event

        sentry_sdk.get_global_scope().add_event_processor(_attach_request_url)

        class _LockConn:
            def execute(self, *_a, **_kw):
                return SimpleNamespace(scalar_one=lambda: True)
            def commit(self):
                pass
            def close(self):
                pass
            def invalidate(self):
                pass

        sql_db.engine = SimpleNamespace(connect=lambda: _LockConn())

        class _Session:
            def __init__(self, doc=None, *, fail_on_delete=False):
                self._doc = doc
                self._fail_on_delete = fail_on_delete
            def execute(self, *_a, **_kw):
                return SimpleNamespace(
                    scalars=lambda: SimpleNamespace(first=lambda: self._doc),
                    scalar_one_or_none=lambda: self._doc,
                )
            def delete(self, _row):
                if self._fail_on_delete:
                    raise RuntimeError(f"{{secret_detail}} {{doc_id}}")
            def commit(self):
                pass
            def rollback(self):
                pass
            def close(self):
                pass

        main_file = SimpleNamespace(
            id="f1", role="main", status="ready", display_mode="local_pdf",
            file_path=None, pdfx_json_path=None, processed_json_path=None, source_markdown_path=None,
        )
        paper = SimpleNamespace(
            id=doc_id, user_id=42, document_kind="library",
            main_file=main_file, main_file_or_none=main_file, files=[main_file],
        )
        sessions = [
            _Session(),
            _Session(paper),
            _Session(paper),
            _Session(paper, fail_on_delete=({failure_step!r} == "sql_cleanup")),
        ]
        documents.SessionLocal = lambda: sessions.pop(0)
        def _open_lock_session(conn=None):
            if conn is None:
                return None
            assert isinstance(conn, _LockConn)
            return sessions.pop(0)
        paper_lock.open_paper_lock_session = _open_lock_session
        documents.provision_user_async = lambda _p: asyncio.sleep(0, result=SimpleNamespace(id=42))
        documents.verify_document_ownership = lambda *_a, **_kw: SimpleNamespace(id=doc_id, user_id=42)
        documents.get_document = lambda *_a, **_kw: asyncio.sleep(0, result={{"document": {{"processing_status": "completed"}}}})
        documents.pdf_job_service.get_latest_job_for_document = lambda **_kw: None
        documents.pipeline_tracker.get_pipeline_status = lambda *_a, **_kw: asyncio.sleep(0, result=None)
        documents.cleanup_document_curation_dependencies = lambda *_a, **_kw: {{}}
        if {failure_step!r} == "stored_files":
            def _boom_files(*_a, **_kw):
                raise RuntimeError(f"{{secret_detail}} {{doc_id}}")
            documents.remove_stored_files = _boom_files

        @contextmanager
        def _weaviate_session(*_a, **_kw):
            yield MagicMock()

        conn = MagicMock()
        conn.session.side_effect = _weaviate_session
        chunks, papers = MagicMock(), MagicMock()
        chunks.data.delete_many.return_value = SimpleNamespace(successful=2, matches=2, failed=0)
        papers.data.delete_by_id.return_value = True
        weaviate_docs.get_connection = lambda: conn
        weaviate_docs.get_user_collections = lambda *_a: (chunks, papers)

        async def _exercise():
            with tempfile.TemporaryDirectory() as tmp:
                documents.get_pdf_storage_path = lambda: tmp
                try:
                    await documents.delete_document_endpoint(doc_id, {{"sub": "user-1"}})
                except HTTPException as exc:
                    assert exc.status_code == 500
                    assert exc.detail == "Failed to delete document"
                else:
                    raise AssertionError("expected HTTPException 500")

        asyncio.run(_exercise())

        assert len(events) == 1, f"expected 1 Sentry event, got {{len(events)}}: {{events!r}}"
        event = events[0]
        assert "request" not in event, f"request leaked: {{event.get('request')!r}}"
        assert doc_id not in repr(event), f"raw doc_id leaked: {{event!r}}"
        assert secret_detail not in repr(event), f"secret leaked: {{event!r}}"
        assert event["contexts"]["runtime_exception"] == {{
            "component": "documents",
            "operation": "paper_delete_cleanup_failed",
            "document_id_hash": hash_sentry_identifier(doc_id),
            "step": {failure_step!r},
            "exception_type": "RuntimeError",
        }}
        log_output = log_buffer.getvalue()
        assert "Paper delete cleanup failed" in log_output
        assert f"step={failure_step}" in log_output
        assert doc_id not in log_output, f"raw doc_id leaked into logs: {{log_output!r}}"
        assert secret_detail not in log_output, f"secret leaked into logs: {{log_output!r}}"
    """)
    result = subprocess.run([sys.executable, "-c", script], text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr


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
async def test_upload_fails_with_typed_503_before_intake_when_dev_reader_unavailable(
    monkeypatch,
):
    async def _unavailable(**_kwargs):
        raise documents.DevelopmentReaderUnavailable("sanitized unavailable")

    async def _must_not_intake(**_kwargs):
        pytest.fail("upload intake must not begin without the development reader")

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
        "message": "Document-source authentication is unavailable.",
        "suggestion": "Try again later or contact support if this persists.",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("route_kind", ["import", "resolve"])
async def test_identifier_routes_fail_before_service_when_dev_reader_unavailable(
    monkeypatch,
    route_kind,
):
    async def _unavailable(**_kwargs):
        raise documents.DevelopmentReaderUnavailable("sanitized unavailable")

    async def _must_not_run(**_kwargs):
        pytest.fail("identifier service must not begin without the development reader")

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
    reporter = MagicMock()
    monkeypatch.setattr(documents, "report_runtime_exception", reporter)
    doc_id = str(uuid4())
    monkeypatch.setenv("PDF_PROCESSING_SSE_POLL_INTERVAL_SECONDS", "1")
    monkeypatch.setenv("PDF_PROCESSING_SSE_TIMEOUT_SECONDS", "1")
    monkeypatch.setattr(documents, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(documents, "principal_from_claims", lambda _claims: SimpleNamespace(subject="user-1"))
    monkeypatch.setattr(documents, "provision_user", lambda *_args, **_kwargs: SimpleNamespace(id=7))
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_paper_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(documents, "get_document", lambda *_args, **_kwargs: _async_value(None))

    response = await documents.stream_document_progress(doc_id, {"sub": "user-1"})
    payload = await _collect_stream(response)
    assert "Document not found" in payload
    assert doc_id in payload

    reporter.assert_not_called()


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
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_paper_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(documents, "get_document", lambda *_args, **_kwargs: _async_value({"document": {"processing_status": "completed"}}))
    monkeypatch.setattr(documents.pipeline_tracker, "get_pipeline_status", _status)
    monkeypatch.setattr(documents.asyncio, "sleep", _no_sleep)

    response = await documents.stream_document_progress(doc_id, {"sub": "user-1"})
    payload = await _collect_stream(response)
    assert '"stage": "completed"' in payload
    assert '"final": true' in payload


@pytest.mark.asyncio
@pytest.mark.parametrize("reporting_fails", [False, True])
async def test_stream_document_progress_sanitizes_stream_errors(monkeypatch, caplog, reporting_fails):
    doc_id = str(uuid4())
    reporter = MagicMock(side_effect=RuntimeError("private reporting failure") if reporting_fails else None)
    monkeypatch.setattr(documents, "report_runtime_exception", reporter)

    async def _status(*_args, **_kwargs):
        raise RuntimeError("progress backend unavailable")

    monkeypatch.setenv("PDF_PROCESSING_SSE_POLL_INTERVAL_SECONDS", "1")
    monkeypatch.setenv("PDF_PROCESSING_SSE_TIMEOUT_SECONDS", "5")
    monkeypatch.setattr(documents, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(documents, "principal_from_claims", lambda _claims: SimpleNamespace(subject="user-1"))
    monkeypatch.setattr(documents, "provision_user", lambda *_args, **_kwargs: SimpleNamespace(id=7))
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_paper_job_for_document", lambda **_kwargs: None)
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

    reporter.assert_called_once()
    reported = reporter.call_args.args[0]
    assert str(reported) == "Document progress stream failed"
    assert reported.__traceback__ is not None
    assert reported.__context__ is None
    assert reported.__cause__ is None
    assert reporter.call_args.kwargs == {
        "component": "documents",
        "operation": "stream_document_progress",
        "tags": {"phase": "progress_stream"},
        "context": {"exception_type": "RuntimeError"},
    }
    assert "private reporting failure" not in payload
    assert "private reporting failure" not in caplog.text
    assert all(getattr(record, "sentry_skip_event", False) for record in caplog.records)


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
    monkeypatch.setattr(
        documents.pdf_job_service,
        "get_latest_paper_job_for_document",
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
    monkeypatch.setattr(
        documents.pdf_job_service,
        "get_latest_paper_job_for_document",
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

@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["truncated", "empty", "vector_error", "invalid_date", "invalid_count"])
async def test_listing_never_reconciles_incomplete_inventory(monkeypatch, scenario):
    """Exercise the real endpoint/library with >1,000 durable SQL records."""
    from src.lib.weaviate_client import documents as library

    durable = [
        _doc(id=uuid4(),  file_path="paper/source.pdf")
        for _ in range(1001)
    ]
    durable.append(_doc(id=uuid4(), document_kind="benchmark_frozen", display_mode="text"))
    session = MagicMock()
    session.query.return_value.filter.return_value.first.return_value = SimpleNamespace(id=42)
    session.query.return_value.filter.return_value.all.return_value = durable
    session.execute.return_value.scalar_one_or_none.return_value = SimpleNamespace(id=42)
    session.execute.return_value.scalars.return_value.all.return_value = []
    session.scalars.return_value = [durable[-1].id]

    def get_db():
        try:
            yield session
        finally:
            session.close()

    connection = MagicMock()
    collection = MagicMock()
    collection.query.fetch_objects.return_value = SimpleNamespace(
        objects=[SimpleNamespace(uuid=durable[0].id, properties={})] if scenario == "truncated" else []
    )
    collection.aggregate.over_all.return_value = SimpleNamespace(total_count=1 if scenario == "truncated" else 0)
    if scenario == "vector_error":
        collection.query.fetch_objects.side_effect = RuntimeError("synthetic vector outage")

    monkeypatch.setattr(documents, "SessionLocal", lambda: session)
    monkeypatch.setattr(library, "get_db", get_db)
    monkeypatch.setattr(library, "get_connection", lambda: connection)
    monkeypatch.setattr(library, "get_user_collections", lambda *_: (collection, collection))
    # These also catch an accidental reintroduction of the former cleanup path.
    monkeypatch.setattr("src.lib.weaviate_helpers.get_connection", lambda: connection)
    monkeypatch.setattr("src.lib.weaviate_helpers.get_user_collections", lambda *_: (collection, collection))
    cleanup = MagicMock()
    remove_tree = MagicMock()
    unlink = MagicMock()
    monkeypatch.setattr(documents, "cleanup_document_curation_dependencies", cleanup)
    monkeypatch.setattr(documents.shutil, "rmtree", remove_tree)
    monkeypatch.setattr(documents.FilePath, "unlink", unlink)

    kwargs: dict[str, Any] = dict(
        user={"sub": "synthetic-curator"}, page=2, page_size=20,
        search=None, embedding_status=None, sort_by=documents.SortBy.CREATION_DATE,
        sort_order=documents.SortOrder.DESC, date_from=None, date_to=None,
        min_vector_count=None, max_vector_count=None,
    )
    if scenario == "invalid_date":
        kwargs.update(date_from=datetime(2026, 2, 1), date_to=datetime(2026, 1, 1))
    if scenario == "invalid_count":
        kwargs.update(min_vector_count=2, max_vector_count=1)
    if scenario in {"vector_error", "invalid_date", "invalid_count"}:
        with pytest.raises(HTTPException) as exc:
            await documents.list_documents_endpoint(**kwargs)
        assert exc.value.status_code == (500 if scenario == "vector_error" else 400)
    else:
        result = await documents.list_documents_endpoint(**kwargs)
        assert result.documents == []
        assert result.total == (1 if scenario == "truncated" else 0)
        assert result.limit == 20
        assert result.offset == 20

    session.delete.assert_not_called()
    session.commit.assert_not_called()
    session.query.assert_not_called()  # No full SQL inventory.
    cleanup.assert_not_called()
    remove_tree.assert_not_called()
    unlink.assert_not_called()
    assert collection.data.mock_calls == []
    if scenario.startswith("invalid"):
        session.execute.assert_not_called()
        collection.query.fetch_objects.assert_not_called()
        connection.session.assert_not_called()
    else:
        collection.query.fetch_objects.assert_called_once()
        assert collection.query.fetch_objects.call_args.kwargs["limit"] == 20
        assert collection.query.fetch_objects.call_args.kwargs["offset"] == 20
        assert session.close.call_count == (1 if scenario == "vector_error" else 2)
        assert collection.aggregate.over_all.call_count == (0 if scenario == "vector_error" else 1)


@pytest.fixture(autouse=True)
def async_provisioning_boundary(monkeypatch):
    # These endpoint tests use a fake SQL session; real retirement is covered
    # in PostgreSQL persistence tests.
    monkeypatch.setattr("src.lib.validation_history.store.retire_document_history", lambda *args: 0)
    monkeypatch.setattr(documents, "provision_user", lambda *_args: SimpleNamespace(id=7))
    async def provision(principal):
        return documents.provision_user(None, principal)
    monkeypatch.setattr(documents, "provision_user_async", provision)


@pytest.mark.asyncio
async def test_progress_stream_default_includes_queue_allowance(monkeypatch):
    elapsed = [0.0]
    monkeypatch.setenv("PDF_EXTRACTION_TIMEOUT", "2")
    monkeypatch.setenv("PDF_EXTRACTION_QUEUE_TIMEOUT_SECONDS", "3")
    monkeypatch.setenv("PDF_PROCESSING_SSE_POLL_INTERVAL_SECONDS", "1")
    monkeypatch.delenv("PDF_PROCESSING_SSE_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setattr(documents, "time", SimpleNamespace(monotonic=lambda: elapsed[0]))
    async def advance(seconds):
        elapsed[0] += seconds
    monkeypatch.setattr(documents.asyncio, "sleep", advance)
    monkeypatch.setattr(documents, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(documents, "principal_from_claims", lambda _claims: SimpleNamespace(subject="user-1"))
    monkeypatch.setattr(documents, "provision_user", lambda *_args, **_kwargs: SimpleNamespace(id=7))
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_paper_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(documents, "get_document", lambda *_args, **_kwargs: _async_value({"document": {"processing_status": "processing"}}))
    monkeypatch.setattr(documents.pipeline_tracker, "get_pipeline_status", lambda *_args: _async_value(None))
    response = await documents.stream_document_progress(str(uuid4()), {"sub": "user-1"})
    payload = await _collect_stream(response)
    assert elapsed[0] == 5
    assert '"stage": "timeout"' in payload


def _supplements_only_document(document_id: str):
    from tests.pdf_document_test_support import new_supplements_only_paper

    return new_supplements_only_paper(
        id=UUID(document_id), user_id=7, filename="old-name.pdf", title="Title", status="completed",
        document_kind="library",
    )


@pytest.mark.asyncio
async def test_renaming_a_paper_without_a_main_file_changes_its_name_and_index_only(monkeypatch, tmp_path):
    document_id = str(uuid4())
    document = _supplements_only_document(document_id)
    session = _FakeSession()
    index_updates = []

    async def _update_index(user_sub, doc_id, filename):
        index_updates.append((user_sub, doc_id, filename))

    monkeypatch.setattr(documents, "SessionLocal", lambda: session)
    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: document)
    monkeypatch.setattr(documents, "get_pdf_storage_path", lambda: tmp_path)
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_paper_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(documents.pipeline_tracker, "get_pipeline_status", lambda *_args: _async_value(None))
    monkeypatch.setattr(documents, "update_document_filename", _update_index)
    _patch_indexed_document_status(monkeypatch)

    response = await documents.update_document_endpoint(
        DocumentUpdateRequest(title="New title", filename="new-name.pdf"),
        document_id,
        {"sub": "user-1"},
    )

    assert (response.filename, response.title) == ("new-name.pdf", "New title")
    assert document.filename == "new-name.pdf"
    assert [file_row.display_name for file_row in document.files] == ["Table_S1.xlsx"]
    assert index_updates == [("user-1", document_id, "new-name.pdf")]
    assert session.commits == 1
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_a_failed_rename_of_a_paper_without_a_main_file_puts_the_index_name_back(monkeypatch, tmp_path):
    document_id = str(uuid4())
    document = _supplements_only_document(document_id)
    session = _FakeSession()
    index_updates = []

    def _failing_commit():
        raise RuntimeError("db exploded")

    session.commit = _failing_commit

    async def _update_index(user_sub, doc_id, filename):
        index_updates.append(filename)

    monkeypatch.setattr(documents, "SessionLocal", lambda: session)
    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: document)
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_paper_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(documents.pipeline_tracker, "get_pipeline_status", lambda *_args: _async_value(None))
    monkeypatch.setattr(documents, "update_document_filename", _update_index)
    _patch_indexed_document_status(monkeypatch)

    with pytest.raises(HTTPException) as exc:
        await documents.update_document_endpoint(
            DocumentUpdateRequest(filename="new-name.pdf"), document_id, {"sub": "user-1"},
        )

    assert exc.value.status_code == 500
    assert index_updates == ["new-name.pdf", "old-name.pdf"]


@pytest.mark.asyncio
async def test_document_detail_of_a_paper_without_a_main_file_has_no_file_size(monkeypatch):
    document_id = str(uuid4())
    pg_doc = _supplements_only_document(document_id)
    pg_doc.upload_timestamp = datetime.now(timezone.utc)

    monkeypatch.setattr(documents, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: pg_doc)
    monkeypatch.setattr(documents, "principal_from_claims", lambda _claims: SimpleNamespace(subject="user-1"))
    monkeypatch.setattr(documents, "provision_user", lambda *_args, **_kwargs: SimpleNamespace(id=7))
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_paper_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(
        documents,
        "get_document",
        lambda *_args, **_kwargs: _async_value({"document": {"processing_status": "completed", "chunk_count": 3}}),
    )
    monkeypatch.setattr(documents, "get_tenant_name", lambda _sub: "tenant-user-1")

    response = await documents.get_document_endpoint(document_id, {"sub": "user-1"})

    assert response.filename == "old-name.pdf"
    assert response.file_size_bytes is None


def test_only_a_waiting_supplement_stage_is_ended_by_a_delete():
    """KANBAN-1976 / ALL-1460, P7b: a delete ends a stage that only waits; nothing else."""

    from datetime import datetime, timezone

    from src.lib.pdf_jobs.service import is_waiting_supplement_stage
    from src.schemas.pdf_jobs import PdfJobResponse

    def _job(stage, kind, status="running"):
        now = datetime.now(timezone.utc)
        return PdfJobResponse(
            job_id="job-1", document_id="doc-1", user_id=7, status=status, current_stage=stage,
            metadata={"job_kind": kind} if kind else None, created_at=now, updated_at=now,
        )

    assert is_waiting_supplement_stage(_job("waiting_for_supplements", "supplement_wait")) is True
    assert is_waiting_supplement_stage(_job("adding_supplements", "supplement_wait")) is False
    assert is_waiting_supplement_stage(_job("waiting_for_supplements", "supplement_wait", "completed")) is False
    assert is_waiting_supplement_stage(_job("parsing", None)) is False
    assert is_waiting_supplement_stage(_job("waiting_for_supplements", "supplement_add")) is False
    assert is_waiting_supplement_stage(None) is False


def _stage_job(status, stage="waiting_for_supplements"):
    from datetime import datetime, timezone

    from src.schemas.pdf_jobs import PdfJobResponse

    now = datetime.now(timezone.utc)
    return PdfJobResponse(
        job_id="stage-job-1", document_id="doc-1", user_id=42, status=status, current_stage=stage,
        metadata={"job_kind": "supplement_wait"}, created_at=now, updated_at=now,
        completed_at=now if status == "cancelled" else None,
    )


@pytest.mark.asyncio
async def test_a_delete_during_a_supplement_wait_ends_the_wait_and_deletes_the_paper(monkeypatch):
    """KANBAN-1976 / ALL-1460, P7b: the paper stays deletable while its supplements are awaited."""

    doc_id = str(uuid4())
    verify_session = _FakeSession()
    paper = _doc(id=doc_id, user_id=42)
    snapshot_session = _FakeSession(execute_doc=paper)
    file_session = _FakeSession(execute_doc=paper)
    cleanup_session = _FakeSession(execute_doc=paper)
    _patch_session_factory(monkeypatch, [verify_session, snapshot_session, file_session, cleanup_session])
    ended = []
    latest = [_stage_job("running"), _stage_job("cancelled")]

    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: SimpleNamespace(id=doc_id, user_id=42))
    monkeypatch.setattr(documents, "get_document", lambda *_args, **_kwargs: _async_value({"document": {"processing_status": "completed"}}))
    monkeypatch.setattr(documents, "delete_paper_chunks", lambda *_args, **_kwargs: _async_value({"success": True, "chunks_deleted": 3}))
    monkeypatch.setattr(documents, "delete_paper_object", lambda *_args, **_kwargs: _async_value({"success": True, "deleted": True}))
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_job_for_document", lambda **_kwargs: latest.pop(0))
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_paper_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(
        documents.pdf_job_service, "end_supplement_wait_for_delete",
        lambda **kwargs: ended.append(kwargs) or True,
    )
    monkeypatch.setattr(documents.pipeline_tracker, "get_pipeline_status", lambda *_args, **_kwargs: _async_value(None))

    result = await documents.delete_document_endpoint(doc_id, {"sub": "user-1"})

    assert len(ended) == 1
    assert ended[0]["document_id"] == doc_id
    assert ended[0]["user_id"] == 42
    assert isinstance(ended[0]["connection"], _FakeLockConnection)
    assert result.success is True


@pytest.mark.asyncio
async def test_a_delete_while_the_stage_writes_files_is_refused(monkeypatch):
    doc_id = str(uuid4())
    verify_session = _FakeSession()
    _patch_session_factory(monkeypatch, [verify_session])
    ended = []

    monkeypatch.setattr(documents, "verify_document_ownership", lambda *_args, **_kwargs: SimpleNamespace(id=doc_id, user_id=42))
    monkeypatch.setattr(documents, "get_document", lambda *_args, **_kwargs: _async_value({"document": {"processing_status": "completed"}}))
    monkeypatch.setattr(
        documents.pdf_job_service, "get_latest_job_for_document",
        lambda **_kwargs: _stage_job("running", stage="adding_supplements"),
    )
    monkeypatch.setattr(documents.pdf_job_service, "get_latest_paper_job_for_document", lambda **_kwargs: None)
    monkeypatch.setattr(
        documents.pdf_job_service, "end_supplement_wait_for_delete",
        lambda **kwargs: ended.append(kwargs) or True,
    )
    monkeypatch.setattr(documents.pipeline_tracker, "get_pipeline_status", lambda *_args, **_kwargs: _async_value(None))

    with pytest.raises(HTTPException) as exc:
        await documents.delete_document_endpoint(doc_id, {"sub": "user-1"})

    assert exc.value.status_code == 409
    assert ended == []

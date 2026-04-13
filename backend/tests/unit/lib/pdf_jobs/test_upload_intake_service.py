"""Unit tests for upload intake choreography service."""

from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks, UploadFile
from sqlalchemy.exc import IntegrityError

from src.lib.pdf_jobs.upload_intake_service import (
    UploadIntakeDuplicateError,
    UploadIntakeService,
    UploadIntakeValidationError,
)


class _ExecuteResult:
    def __init__(self, row):
        self._row = row
        self.rowcount = 0

    def scalars(self):
        return self

    def first(self):
        return self._row

    def all(self):
        if self._row is None:
            return []
        if isinstance(self._row, list):
            return self._row
        return [self._row]


class _FakeSession:
    def __init__(self, *, execute_row=None, fail_commit_on_call=None, commit_error=None):
        self.execute_row = execute_row
        self.fail_commit_on_call = fail_commit_on_call
        self.commit_error = commit_error
        self.commit_calls = 0
        self.closed = False
        self.rollbacks = 0
        self.added = []
        self.deleted = []

    def execute(self, *_args, **_kwargs):
        return _ExecuteResult(self.execute_row)

    def add(self, row):
        self.added.append(row)

    def delete(self, row):
        self.deleted.append(row)

    def commit(self):
        self.commit_calls += 1
        if self.fail_commit_on_call and self.commit_calls == self.fail_commit_on_call:
            raise self.commit_error

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


class _DispatchRecorder:
    def __init__(self):
        self.calls = []

    async def dispatch_upload_execution(self, *, background_tasks, request):
        self.calls.append({"background_tasks": background_tasks, "request": request})


class _UploadHandler:
    def __init__(self, *, storage_path: Path, checksum: str = "checksum-1"):
        self.storage_path = storage_path
        self.checksum = checksum

    async def save_uploaded_pdf(self, file):
        doc_id = str(uuid4())
        doc_dir = self.storage_path / doc_id
        doc_dir.mkdir(parents=True, exist_ok=True)
        saved_path = doc_dir / file.filename
        saved_path.write_bytes(b"%PDF-1.7")
        document = SimpleNamespace(
            id=doc_id,
            filename=file.filename,
            metadata=SimpleNamespace(checksum=self.checksum, page_count=3),
        )
        return saved_path, document


@pytest.mark.asyncio
async def test_intake_upload_happy_path_creates_job_and_dispatches(tmp_path):
    session = _FakeSession()
    dispatch = _DispatchRecorder()
    create_document_calls = []

    async def _create_document(user_sub, document):
        create_document_calls.append((user_sub, document.id))

    service = UploadIntakeService(
        upload_execution_service=dispatch,
        session_factory=lambda: session,
        storage_path_provider=lambda: tmp_path,
        upload_handler_factory=lambda storage_path: _UploadHandler(storage_path=storage_path),
        principal_from_claims_fn=lambda _claims: SimpleNamespace(subject="user-1"),
        provision_user_fn=lambda *_args, **_kwargs: SimpleNamespace(id=42),
        create_document_fn=_create_document,
        get_document_fn=lambda *_args, **_kwargs: _async_value({"document": {}}),
        delete_document_fn=lambda *_args, **_kwargs: _async_value(None),
        create_job_fn=lambda **_kwargs: SimpleNamespace(job_id="job-1"),
        tenant_name_resolver=lambda _sub: "tenant-user-1",
    )

    result = await service.intake_upload(
        background_tasks=BackgroundTasks(),
        file=UploadFile(filename="paper.pdf", file=BytesIO(b"%PDF-1.7")),
        user={"sub": "user-1"},
    )

    assert result.document_id
    assert result.job_id == "job-1"
    assert result.status == "PENDING"
    assert result.user_id == 42
    assert result.weaviate_tenant == "tenant-user-1"
    assert result.upload_timestamp.tzinfo == timezone.utc
    assert create_document_calls and create_document_calls[0][0] == "user-1"
    assert len(dispatch.calls) == 1
    assert dispatch.calls[0]["request"].job_id == "job-1"
    assert session.commit_calls == 1
    assert session.closed is True


@pytest.mark.asyncio
async def test_intake_upload_rejects_non_pdf_before_side_effects(tmp_path):
    session = _FakeSession()
    dispatch = _DispatchRecorder()

    service = UploadIntakeService(
        upload_execution_service=dispatch,
        session_factory=lambda: session,
        storage_path_provider=lambda: tmp_path,
    )

    with pytest.raises(UploadIntakeValidationError):
        await service.intake_upload(
            background_tasks=BackgroundTasks(),
            file=UploadFile(filename="notes.txt", file=BytesIO(b"text")),
            user={"sub": "user-1"},
        )

    assert session.commit_calls == 0
    assert not dispatch.calls


@pytest.mark.asyncio
async def test_intake_upload_duplicate_keeps_existing_and_cleans_new_artifacts(tmp_path):
    existing = SimpleNamespace(
        id=uuid4(),
        upload_timestamp=datetime(2026, 1, 3, 13, 0, tzinfo=timezone.utc),
    )
    session = _FakeSession(execute_row=existing)
    dispatch = _DispatchRecorder()
    create_job_calls = []

    async def _get_document(_user_sub, _document_id):
        return {"document": {"id": _document_id}}

    service = UploadIntakeService(
        upload_execution_service=dispatch,
        session_factory=lambda: session,
        storage_path_provider=lambda: tmp_path,
        upload_handler_factory=lambda storage_path: _UploadHandler(storage_path=storage_path),
        principal_from_claims_fn=lambda _claims: SimpleNamespace(subject="user-1"),
        provision_user_fn=lambda *_args, **_kwargs: SimpleNamespace(id=42),
        create_document_fn=lambda *_args, **_kwargs: _async_value(None),
        get_document_fn=_get_document,
        delete_document_fn=lambda *_args, **_kwargs: _async_value(None),
        create_job_fn=lambda **kwargs: create_job_calls.append(kwargs) or SimpleNamespace(job_id="job-1"),
        tenant_name_resolver=lambda _sub: "tenant-user-1",
    )

    with pytest.raises(UploadIntakeDuplicateError) as exc:
        await service.intake_upload(
            background_tasks=BackgroundTasks(),
            file=UploadFile(filename="paper.pdf", file=BytesIO(b"%PDF-1.7")),
            user={"sub": "user-1"},
        )

    assert exc.value.detail["error"] == "duplicate_file"
    assert session.deleted == []
    assert create_job_calls == []
    assert dispatch.calls == []
    user_dir = tmp_path / "user-1"
    assert user_dir.exists()
    assert not any(user_dir.iterdir())


@pytest.mark.asyncio
async def test_intake_upload_phantom_duplicate_is_deleted_and_intake_continues(tmp_path):
    existing = SimpleNamespace(
        id=uuid4(),
        upload_timestamp=datetime(2026, 1, 3, 13, 0, tzinfo=timezone.utc),
    )
    session = _FakeSession(execute_row=existing)
    dispatch = _DispatchRecorder()
    cleanup_dependency_calls = []

    async def _raise_not_found(*_args, **_kwargs):
        raise ValueError("missing in weaviate")

    create_document_calls = []

    async def _create_document(user_sub, document):
        create_document_calls.append((user_sub, document.id))

    service = UploadIntakeService(
        upload_execution_service=dispatch,
        session_factory=lambda: session,
        storage_path_provider=lambda: tmp_path,
        upload_handler_factory=lambda storage_path: _UploadHandler(storage_path=storage_path),
        principal_from_claims_fn=lambda _claims: SimpleNamespace(subject="user-1"),
        provision_user_fn=lambda *_args, **_kwargs: SimpleNamespace(id=42),
        create_document_fn=_create_document,
        get_document_fn=_raise_not_found,
        delete_document_fn=lambda *_args, **_kwargs: _async_value(None),
        create_job_fn=lambda **_kwargs: SimpleNamespace(job_id="job-1"),
        tenant_name_resolver=lambda _sub: "tenant-user-1",
        cleanup_document_dependencies_fn=lambda session_arg, document_id: cleanup_dependency_calls.append(
            (session_arg, document_id)
        ),
    )

    result = await service.intake_upload(
        background_tasks=BackgroundTasks(),
        file=UploadFile(filename="paper.pdf", file=BytesIO(b"%PDF-1.7")),
        user={"sub": "user-1"},
    )

    assert result.job_id == "job-1"
    assert session.deleted == [existing]
    assert session.commit_calls == 2
    assert cleanup_dependency_calls == [(session, existing.id)]
    assert create_document_calls and create_document_calls[0][0] == "user-1"


@pytest.mark.asyncio
async def test_intake_upload_integrity_error_compensates_and_returns_duplicate_error(tmp_path):
    session = _FakeSession(
        fail_commit_on_call=1,
        commit_error=IntegrityError("insert", {"id": "doc"}, Exception("duplicate key")),
    )
    dispatch = _DispatchRecorder()
    delete_document_calls = []

    async def _delete_document(user_sub, document_id):
        delete_document_calls.append((user_sub, document_id))

    create_document_calls = []

    async def _create_document(user_sub, document):
        create_document_calls.append((user_sub, document.id))

    service = UploadIntakeService(
        upload_execution_service=dispatch,
        session_factory=lambda: session,
        storage_path_provider=lambda: tmp_path,
        upload_handler_factory=lambda storage_path: _UploadHandler(storage_path=storage_path),
        principal_from_claims_fn=lambda _claims: SimpleNamespace(subject="user-1"),
        provision_user_fn=lambda *_args, **_kwargs: SimpleNamespace(id=42),
        create_document_fn=_create_document,
        get_document_fn=lambda *_args, **_kwargs: _async_value({"document": {}}),
        delete_document_fn=_delete_document,
        create_job_fn=lambda **_kwargs: SimpleNamespace(job_id="job-1"),
        tenant_name_resolver=lambda _sub: "tenant-user-1",
    )

    with pytest.raises(UploadIntakeDuplicateError) as exc:
        await service.intake_upload(
            background_tasks=BackgroundTasks(),
            file=UploadFile(filename="paper.pdf", file=BytesIO(b"%PDF-1.7")),
            user={"sub": "user-1"},
        )

    assert exc.value.detail["error"] == "duplicate_file"
    assert create_document_calls and create_document_calls[0][0] == "user-1"
    assert len(delete_document_calls) == 1
    assert session.rollbacks == 1
    user_dir = tmp_path / "user-1"
    assert user_dir.exists()
    assert not any(user_dir.iterdir())
    assert not dispatch.calls


@pytest.mark.asyncio
async def test_intake_upload_generic_commit_failure_rolls_back_and_cleans_up(tmp_path):
    session = _FakeSession(
        fail_commit_on_call=1,
        commit_error=RuntimeError("database unavailable"),
    )
    dispatch = _DispatchRecorder()
    delete_document_calls = []

    async def _delete_document(user_sub, document_id):
        delete_document_calls.append((user_sub, document_id))

    service = UploadIntakeService(
        upload_execution_service=dispatch,
        session_factory=lambda: session,
        storage_path_provider=lambda: tmp_path,
        upload_handler_factory=lambda storage_path: _UploadHandler(storage_path=storage_path),
        principal_from_claims_fn=lambda _claims: SimpleNamespace(subject="user-1"),
        provision_user_fn=lambda *_args, **_kwargs: SimpleNamespace(id=42),
        create_document_fn=lambda *_args, **_kwargs: _async_value(None),
        get_document_fn=lambda *_args, **_kwargs: _async_value({"document": {}}),
        delete_document_fn=_delete_document,
        create_job_fn=lambda **_kwargs: SimpleNamespace(job_id="job-1"),
        tenant_name_resolver=lambda _sub: "tenant-user-1",
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        await service.intake_upload(
            background_tasks=BackgroundTasks(),
            file=UploadFile(filename="paper.pdf", file=BytesIO(b"%PDF-1.7")),
            user={"sub": "user-1"},
        )

    assert session.rollbacks == 1
    assert len(delete_document_calls) == 1
    user_dir = tmp_path / "user-1"
    assert user_dir.exists()
    assert not any(user_dir.iterdir())
    assert not dispatch.calls


@pytest.mark.asyncio
async def test_intake_upload_runtime_error_rolls_back_and_compensates_without_dispatch(tmp_path):
    session = _FakeSession(
        fail_commit_on_call=1,
        commit_error=RuntimeError("database unavailable"),
    )
    dispatch = _DispatchRecorder()
    delete_document_calls = []
    create_job_calls = []

    async def _delete_document(user_sub, document_id):
        delete_document_calls.append((user_sub, document_id))

    service = UploadIntakeService(
        upload_execution_service=dispatch,
        session_factory=lambda: session,
        storage_path_provider=lambda: tmp_path,
        upload_handler_factory=lambda storage_path: _UploadHandler(storage_path=storage_path),
        principal_from_claims_fn=lambda _claims: SimpleNamespace(subject="user-1"),
        provision_user_fn=lambda *_args, **_kwargs: SimpleNamespace(id=42),
        create_document_fn=lambda *_args, **_kwargs: _async_value(None),
        get_document_fn=lambda *_args, **_kwargs: _async_value({"document": {}}),
        delete_document_fn=_delete_document,
        create_job_fn=lambda **kwargs: create_job_calls.append(kwargs) or SimpleNamespace(job_id="job-1"),
        tenant_name_resolver=lambda _sub: "tenant-user-1",
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        await service.intake_upload(
            background_tasks=BackgroundTasks(),
            file=UploadFile(filename="paper.pdf", file=BytesIO(b"%PDF-1.7")),
            user={"sub": "user-1"},
        )

    assert session.rollbacks == 1
    assert len(delete_document_calls) == 1
    assert delete_document_calls[0][0] == "user-1"
    assert create_job_calls == []
    assert dispatch.calls == []
    user_dir = tmp_path / "user-1"
    assert user_dir.exists()
    assert not any(user_dir.iterdir())


@pytest.mark.asyncio
async def test_intake_upload_runtime_error_preserves_original_exception_when_cleanup_fails(tmp_path):
    session = _FakeSession(
        fail_commit_on_call=1,
        commit_error=RuntimeError("database unavailable"),
    )
    dispatch = _DispatchRecorder()

    async def _delete_document(*_args, **_kwargs):
        raise RuntimeError("cleanup failed")

    service = UploadIntakeService(
        upload_execution_service=dispatch,
        session_factory=lambda: session,
        storage_path_provider=lambda: tmp_path,
        upload_handler_factory=lambda storage_path: _UploadHandler(storage_path=storage_path),
        principal_from_claims_fn=lambda _claims: SimpleNamespace(subject="user-1"),
        provision_user_fn=lambda *_args, **_kwargs: SimpleNamespace(id=42),
        create_document_fn=lambda *_args, **_kwargs: _async_value(None),
        get_document_fn=lambda *_args, **_kwargs: _async_value({"document": {}}),
        delete_document_fn=_delete_document,
        create_job_fn=lambda **_kwargs: SimpleNamespace(job_id="job-1"),
        tenant_name_resolver=lambda _sub: "tenant-user-1",
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        await service.intake_upload(
            background_tasks=BackgroundTasks(),
            file=UploadFile(filename="paper.pdf", file=BytesIO(b"%PDF-1.7")),
            user={"sub": "user-1"},
        )

    assert session.rollbacks == 1
    assert dispatch.calls == []
    user_dir = tmp_path / "user-1"
    assert user_dir.exists()
    assert not any(user_dir.iterdir())


@pytest.mark.asyncio
async def test_intake_upload_non_integrity_persistence_error_rolls_back_and_cleans_artifacts(tmp_path):
    session = _FakeSession()
    dispatch = _DispatchRecorder()
    delete_document_calls = []

    async def _delete_document(user_sub, document_id):
        delete_document_calls.append((user_sub, document_id))

    def _boom_on_add(_row):
        raise RuntimeError("sql write failed")

    session.add = _boom_on_add

    service = UploadIntakeService(
        upload_execution_service=dispatch,
        session_factory=lambda: session,
        storage_path_provider=lambda: tmp_path,
        upload_handler_factory=lambda storage_path: _UploadHandler(storage_path=storage_path),
        principal_from_claims_fn=lambda _claims: SimpleNamespace(subject="user-1"),
        provision_user_fn=lambda *_args, **_kwargs: SimpleNamespace(id=42),
        create_document_fn=lambda *_args, **_kwargs: _async_value(None),
        get_document_fn=lambda *_args, **_kwargs: _async_value({"document": {}}),
        delete_document_fn=_delete_document,
        create_job_fn=lambda **_kwargs: SimpleNamespace(job_id="job-1"),
        tenant_name_resolver=lambda _sub: "tenant-user-1",
    )

    with pytest.raises(RuntimeError, match="sql write failed"):
        await service.intake_upload(
            background_tasks=BackgroundTasks(),
            file=UploadFile(filename="paper.pdf", file=BytesIO(b"%PDF-1.7")),
            user={"sub": "user-1"},
        )

    assert session.rollbacks == 1
    assert len(delete_document_calls) == 1
    assert not dispatch.calls
    user_dir = tmp_path / "user-1"
    assert user_dir.exists()
    assert not any(user_dir.iterdir())


@pytest.mark.asyncio
async def test_intake_upload_job_creation_failure_compensates_sql_weaviate_and_files(tmp_path):
    initial_session = _FakeSession()
    persisted_record = SimpleNamespace(id=uuid4())
    cleanup_session = _FakeSession(execute_row=persisted_record)
    sessions = [initial_session, cleanup_session]
    dispatch = _DispatchRecorder()
    delete_document_calls = []

    async def _delete_document(user_sub, document_id):
        delete_document_calls.append((user_sub, document_id))

    def _session_factory():
        assert sessions, "session_factory called more times than expected"
        return sessions.pop(0)

    service = UploadIntakeService(
        upload_execution_service=dispatch,
        session_factory=_session_factory,
        storage_path_provider=lambda: tmp_path,
        upload_handler_factory=lambda storage_path: _UploadHandler(storage_path=storage_path),
        principal_from_claims_fn=lambda _claims: SimpleNamespace(subject="user-1"),
        provision_user_fn=lambda *_args, **_kwargs: SimpleNamespace(id=42),
        create_document_fn=lambda *_args, **_kwargs: _async_value(None),
        get_document_fn=lambda *_args, **_kwargs: _async_value({"document": {}}),
        delete_document_fn=_delete_document,
        create_job_fn=lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("job row failed")),
        tenant_name_resolver=lambda _sub: "tenant-user-1",
    )

    with pytest.raises(RuntimeError, match="job row failed"):
        await service.intake_upload(
            background_tasks=BackgroundTasks(),
            file=UploadFile(filename="paper.pdf", file=BytesIO(b"%PDF-1.7")),
            user={"sub": "user-1"},
        )

    assert initial_session.commit_calls == 1
    assert cleanup_session.deleted == [persisted_record]
    assert cleanup_session.commit_calls == 1
    assert len(delete_document_calls) == 1
    assert not dispatch.calls
    user_dir = tmp_path / "user-1"
    assert user_dir.exists()
    assert not any(user_dir.iterdir())


def _async_value(value):
    async def _coro(*_args, **_kwargs):
        return value

    return _coro()

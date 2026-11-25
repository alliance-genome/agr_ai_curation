"""Unit tests for upload intake choreography service."""

from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import BackgroundTasks, UploadFile
from pypdf import PdfWriter
from sqlalchemy.exc import IntegrityError
from src.lib.document_sources.access import DocumentSourceRequestContext
from src.lib.document_sources.import_selection import (
    ChecksumImportCandidate,
    ChecksumImportDecision,
    ChecksumImportDecisionStatus,
)
from src.lib.document_sources.models import (
    DocumentSourceConfigError,
    DocumentSourceError,
    DocumentSourceHealth,
    SourceAccessPolicy,
    SourceAccessScope,
    SourceArtifact,
    SourceArtifactFormat,
    SourceArtifactRole,
    SourceArtifactStatus,
    SourceConversionResult,
    SourceConversionStatus,
    SourceReference,
)
from src.lib.observability import runtime
from src.lib.pdf_jobs import upload_intake_service as upload_intake_module
from src.lib.pdf_jobs.upload_intake_service import (
    UploadIntakeDuplicateError,
    UploadIntakeProviderDecisionError,
    UploadIntakeService,
    UploadIntakeValidationError,
)
from src.lib.pdf_limits import (
    MAX_PDF_FILE_SIZE_BYTES,
    MAX_PDF_FILE_SIZE_MB,
    pdf_file_size_limit_message,
)
from src.lib.pipeline.upload import UploadError
from src.lib.document_sources.models import ProviderBearerKind
from tests.unit.lib.intake_compensation_contract import IntakeCompensationContract

FAKE_UPLOAD_MD5 = "f87357c6cdc4f067" + "e19f42aebabc6fb7"


class _FakePaperLockConnection:
    """In-memory PostgreSQL advisory lock simulator for upload compensation tests."""

    held: set[int] = set()

    def execute(self, statement, params):
        sql = str(statement)
        key = params["key"]
        if "pg_try_advisory_lock" in sql:
            taken = key not in self.held
            self.held.add(key)
            return SimpleNamespace(scalar_one=lambda: taken)
        if "pg_advisory_unlock" in sql:
            self.held.discard(key)
            return SimpleNamespace(scalar_one=lambda: True)
        raise AssertionError(f"unexpected lock statement {sql}")

    def commit(self):
        pass

    def close(self):
        pass

    def invalidate(self):
        pass


@pytest.fixture(autouse=True)
def _compensation_lock_and_jobs_fixture(monkeypatch):
    _FakePaperLockConnection.held = set()
    monkeypatch.setattr(
        "src.models.sql.database.engine",
        SimpleNamespace(connect=lambda: _FakePaperLockConnection()),
        raising=False,
    )
    monkeypatch.setattr(
        upload_intake_module.pdf_job_service,
        "get_active_job_for_document",
        lambda **_kwargs: None,
    )

    def _open_lock_session(conn=None):
        if conn is not None:
            assert isinstance(conn, _FakePaperLockConnection)
        return None

    monkeypatch.setattr(upload_intake_module, "open_paper_lock_session", _open_lock_session)
    monkeypatch.setattr(
        "src.lib.document_files.paper_lock.open_paper_lock_session",
        _open_lock_session,
    )
    yield _FakePaperLockConnection
    assert _FakePaperLockConnection.held == set(), "compensation left paper lock held"


def test_provider_decision_error_fields_cover_every_status():
    assert set(upload_intake_module._CHECKSUM_DECISION_ERROR_FIELDS) == set(
        ChecksumImportDecisionStatus
    )


def test_provider_decision_error_uses_registered_presentation_label(monkeypatch):
    monkeypatch.setattr(
        upload_intake_module,
        "get_document_source_provider_metadata",
        lambda provider_id: {"display_label": "Example Literature"}
        if provider_id == "fake_provider"
        else None,
    )

    _, _, message, suggestion = upload_intake_module._provider_decision_error_fields(
        ChecksumImportDecisionStatus.ACCESS_DENIED,
        provider="fake_provider",
    )

    assert message == "No matching source document is accessible to this curator."
    assert suggestion == (
        "Confirm you are signed in with an account authorized by Example Literature."
    )


def test_provider_decision_error_rejects_missing_presentation_label(monkeypatch):
    monkeypatch.setattr(
        upload_intake_module,
        "get_document_source_provider_metadata",
        lambda _provider_id: None,
    )

    with pytest.raises(
        DocumentSourceConfigError,
        match="fake_provider.*no presentation display label",
    ):
        upload_intake_module._provider_decision_error_fields(
            ChecksumImportDecisionStatus.ACCESS_DENIED,
            provider="fake_provider",
        )


def test_provider_decision_error_skips_unused_presentation_label(monkeypatch):
    def _unexpected_metadata_lookup(_provider_id):
        raise AssertionError("provider metadata should not be loaded for generic copy")

    monkeypatch.setattr(
        upload_intake_module,
        "get_document_source_provider_metadata",
        _unexpected_metadata_lookup,
    )

    fields = upload_intake_module._provider_decision_error_fields(
        ChecksumImportDecisionStatus.NO_SOURCE_ARTIFACT,
        provider="fake_provider",
    )

    assert fields == upload_intake_module._CHECKSUM_DECISION_ERROR_FIELDS[
        ChecksumImportDecisionStatus.NO_SOURCE_ARTIFACT
    ]


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
        self.provider_markdown_calls = []
        self.provider_conversion_calls = []

    async def dispatch_upload_execution(self, *, background_tasks, request):
        self.calls.append({"background_tasks": background_tasks, "request": request})

    async def dispatch_provider_markdown_execution(self, *, background_tasks, request):
        self.provider_markdown_calls.append(
            {"background_tasks": background_tasks, "request": request}
        )

    async def dispatch_provider_conversion_execution(self, *, background_tasks, request):
        self.provider_conversion_calls.append(
            {"background_tasks": background_tasks, "request": request}
        )


class _UploadHandler:
    def __init__(
        self,
        *,
        storage_path: Path,
        checksum: str = "checksum-1",
        page_count: int = 3,
    ):
        self.storage_path = storage_path
        self.checksum = checksum
        self.page_count = page_count

    async def save_uploaded_pdf(self, file):
        doc_id = str(uuid4())
        doc_dir = self.storage_path / doc_id
        doc_dir.mkdir(parents=True, exist_ok=True)
        saved_path = doc_dir / file.filename
        saved_path.write_bytes(b"%PDF-1.7")
        document = SimpleNamespace(
            id=doc_id,
            filename=file.filename,
            metadata=SimpleNamespace(checksum=self.checksum, page_count=self.page_count),
        )
        return saved_path, document


class _FakeDocumentSourceProvider:
    provider_id = "fake_provider"

    def __init__(self):
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        await self.aclose()

    async def aclose(self):
        self.closed = True

    async def resolve_reference(
        self,
        identifier: str,
        *,
        request_bearer_token: str | None = None,
    ) -> SourceReference:
        _ = request_bearer_token
        raise NotImplementedError(identifier)

    async def list_artifacts(
        self,
        reference: SourceReference | str,
        *,
        request_bearer_token: str | None = None,
    ) -> list[SourceArtifact]:
        _ = request_bearer_token
        raise NotImplementedError(reference)

    async def find_artifacts_by_checksum(
        self,
        checksum: str,
        *,
        request_bearer_token: str | None = None,
    ) -> list[SourceArtifact]:
        _ = request_bearer_token
        raise NotImplementedError(checksum)

    async def download_artifact(
        self,
        artifact_id: str,
        *,
        request_bearer_token: str | None = None,
    ) -> bytes:
        _ = request_bearer_token
        raise NotImplementedError(artifact_id)

    async def health(self) -> DocumentSourceHealth:
        return DocumentSourceHealth(
            provider=self.provider_id,
            ok=True,
            message="ok",
        )


class _OversizedUploadHandler(_UploadHandler):
    async def save_uploaded_pdf(self, file):
        doc_id = str(uuid4())
        doc_dir = self.storage_path / doc_id
        doc_dir.mkdir(parents=True, exist_ok=True)
        saved_path = doc_dir / file.filename
        with saved_path.open("wb") as file_handle:
            file_handle.write(b"%PDF-1.7\n")
            file_handle.seek(MAX_PDF_FILE_SIZE_BYTES)
            file_handle.write(b"0")
        document = SimpleNamespace(
            id=doc_id,
            filename=file.filename,
            metadata=SimpleNamespace(checksum=self.checksum, page_count=3),
        )
        return saved_path, document


class _ConstraintError(Exception):
    def __init__(self, constraint_name: str, message: str):
        super().__init__(message)
        self.diag = SimpleNamespace(constraint_name=constraint_name)


class _FailingUploadHandler:
    def __init__(self, *_args, **_kwargs):
        pass

    async def save_uploaded_pdf(self, _file):
        raise UploadError(pdf_file_size_limit_message(MAX_PDF_FILE_SIZE_BYTES + 1))


def test_external_document_source_import_enabled_ignores_local_pdf_provider(monkeypatch):
    monkeypatch.setattr(
        upload_intake_module,
        "get_document_source_import_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        upload_intake_module,
        "get_document_source_provider",
        lambda: "LOCAL_PDF",
    )

    assert upload_intake_module.external_document_source_import_enabled() is False

    monkeypatch.setattr(
        upload_intake_module,
        "get_document_source_provider",
        lambda: "abc_literature",
    )

    assert upload_intake_module.external_document_source_import_enabled() is True


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
        provision_user_fn=AsyncMock(return_value=SimpleNamespace(id=42)),
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
async def test_intake_upload_normalizes_existing_user_storage_permissions(tmp_path):
    session = _FakeSession()
    dispatch = _DispatchRecorder()
    user_dir = tmp_path / "user-1"
    user_dir.mkdir(parents=True, exist_ok=True)
    user_dir.chmod(0o755)

    async def _create_document(*_args, **_kwargs):
        return None

    service = UploadIntakeService(
        upload_execution_service=dispatch,
        session_factory=lambda: session,
        storage_path_provider=lambda: tmp_path,
        upload_handler_factory=lambda storage_path: _UploadHandler(storage_path=storage_path),
        principal_from_claims_fn=lambda _claims: SimpleNamespace(subject="user-1"),
        provision_user_fn=AsyncMock(return_value=SimpleNamespace(id=42)),
        create_document_fn=_create_document,
        get_document_fn=lambda *_args, **_kwargs: _async_value({"document": {}}),
        delete_document_fn=lambda *_args, **_kwargs: _async_value(None),
        create_job_fn=lambda **_kwargs: SimpleNamespace(job_id="job-1"),
        tenant_name_resolver=lambda _sub: "tenant-user-1",
    )

    await service.intake_upload(
        background_tasks=BackgroundTasks(),
        file=UploadFile(filename="paper.pdf", file=BytesIO(b"%PDF-1.7")),
        user={"sub": "user-1"},
    )

    assert user_dir.stat().st_mode & 0o777 == 0o777


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
async def test_intake_upload_maps_upload_handler_validation_errors_to_validation_error(tmp_path):
    session = _FakeSession()
    dispatch = _DispatchRecorder()

    service = UploadIntakeService(
        upload_execution_service=dispatch,
        session_factory=lambda: session,
        storage_path_provider=lambda: tmp_path,
        upload_handler_factory=lambda storage_path: _FailingUploadHandler(storage_path=storage_path),
    )

    with pytest.raises(UploadIntakeValidationError, match=f"{MAX_PDF_FILE_SIZE_MB} MB"):
        await service.intake_upload(
            background_tasks=BackgroundTasks(),
            file=UploadFile(filename="paper.pdf", file=BytesIO(b"%PDF-1.7")),
            user={"sub": "user-1"},
        )

    assert session.commit_calls == 0
    assert not dispatch.calls


@pytest.mark.asyncio
async def test_intake_upload_rejects_pdf_larger_than_configured_limit_before_db_work(tmp_path):
    session = _FakeSession()
    dispatch = _DispatchRecorder()
    create_document_calls = []

    async def _create_document(user_sub, document):
        create_document_calls.append((user_sub, document.id))

    service = UploadIntakeService(
        upload_execution_service=dispatch,
        session_factory=lambda: session,
        storage_path_provider=lambda: tmp_path,
        upload_handler_factory=lambda storage_path: _OversizedUploadHandler(storage_path=storage_path),
        principal_from_claims_fn=lambda _claims: SimpleNamespace(subject="user-1"),
        provision_user_fn=AsyncMock(return_value=SimpleNamespace(id=42)),
        create_document_fn=_create_document,
        get_document_fn=lambda *_args, **_kwargs: _async_value({"document": {}}),
        delete_document_fn=lambda *_args, **_kwargs: _async_value(None),
        create_job_fn=lambda **_kwargs: SimpleNamespace(job_id="job-1"),
        tenant_name_resolver=lambda _sub: "tenant-user-1",
    )

    with pytest.raises(UploadIntakeValidationError, match=f"{MAX_PDF_FILE_SIZE_MB} MB"):
        await service.intake_upload(
            background_tasks=BackgroundTasks(),
            file=UploadFile(filename="paper.pdf", file=BytesIO(b"%PDF-1.7")),
            user={"sub": "user-1"},
        )

    assert session.commit_calls == 0
    assert create_document_calls == []
    assert dispatch.calls == []
    user_dir = tmp_path / "user-1"
    assert user_dir.exists()
    assert not any(user_dir.iterdir())


@pytest.mark.asyncio
@pytest.mark.parametrize("page_count", [1, 100, 137, 299, 300])
async def test_intake_upload_accepts_page_counts_through_default_limit(
    tmp_path,
    monkeypatch,
    page_count,
):
    monkeypatch.delenv("PDF_UPLOAD_MAX_PAGE_COUNT", raising=False)
    session = _FakeSession()
    dispatch = _DispatchRecorder()

    service = UploadIntakeService(
        upload_execution_service=dispatch,
        session_factory=lambda: session,
        storage_path_provider=lambda: tmp_path,
        upload_handler_factory=lambda storage_path: _UploadHandler(
            storage_path=storage_path,
            page_count=page_count,
        ),
        principal_from_claims_fn=lambda _claims: SimpleNamespace(subject="user-1"),
        provision_user_fn=AsyncMock(return_value=SimpleNamespace(id=42)),
        create_document_fn=lambda *_args, **_kwargs: _async_value(None),
        get_document_fn=lambda *_args, **_kwargs: _async_value({"document": {}}),
        delete_document_fn=lambda *_args, **_kwargs: _async_value(None),
        create_job_fn=lambda **_kwargs: SimpleNamespace(job_id="job-1"),
        tenant_name_resolver=lambda _sub: "tenant-user-1",
    )

    await service.intake_upload(
        background_tasks=BackgroundTasks(),
        file=UploadFile(filename="paper.pdf", file=BytesIO(b"%PDF-1.7")),
        user={"sub": "user-1"},
    )

    assert session.added[0].main_file.page_count == page_count
    # Writer pin (P9a): a pending local PDF with its real page count. The viewer's
    # per-file routes serve it only once the file is ready.
    assert (session.added[0].main_file.display_mode, session.added[0].main_file.status) == (
        "local_pdf",
        "pending",
    )
    assert session.commit_calls == 1
    assert len(dispatch.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("configured_max", "page_count"),
    [(300, 301), (350, 351)],
)
async def test_intake_upload_rejects_page_count_before_durable_side_effects(
    tmp_path,
    monkeypatch,
    configured_max,
    page_count,
):
    monkeypatch.setenv("PDF_UPLOAD_MAX_PAGE_COUNT", str(configured_max))
    session = _FakeSession()
    dispatch = _DispatchRecorder()
    create_document_calls = []
    create_job_calls = []

    service = UploadIntakeService(
        upload_execution_service=dispatch,
        session_factory=lambda: session,
        storage_path_provider=lambda: tmp_path,
        upload_handler_factory=lambda storage_path: _UploadHandler(
            storage_path=storage_path,
            page_count=page_count,
        ),
        create_document_fn=lambda *_args, **_kwargs: create_document_calls.append(True),
        create_job_fn=lambda **_kwargs: create_job_calls.append(True),
    )

    with pytest.raises(UploadIntakeValidationError) as exc_info:
        await service.intake_upload(
            background_tasks=BackgroundTasks(),
            file=UploadFile(filename="paper.pdf", file=BytesIO(b"%PDF-1.7")),
            user={"sub": "user-1"},
        )

    assert str(page_count) in str(exc_info.value)
    assert str(configured_max) in str(exc_info.value)
    assert exc_info.value.client_detail["actual_page_count"] == page_count
    assert exc_info.value.client_detail["max_page_count"] == configured_max
    assert session.commit_calls == 0
    assert session.added == []
    assert create_document_calls == []
    assert create_job_calls == []
    assert dispatch.calls == []
    user_dir = tmp_path / "user-1"
    assert user_dir.exists()
    assert not any(user_dir.iterdir())


@pytest.mark.asyncio
async def test_intake_upload_accepts_301_pages_with_350_page_override(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("PDF_UPLOAD_MAX_PAGE_COUNT", "350")
    session = _FakeSession()
    dispatch = _DispatchRecorder()

    service = UploadIntakeService(
        upload_execution_service=dispatch,
        session_factory=lambda: session,
        storage_path_provider=lambda: tmp_path,
        upload_handler_factory=lambda storage_path: _UploadHandler(
            storage_path=storage_path,
            page_count=301,
        ),
        principal_from_claims_fn=lambda _claims: SimpleNamespace(subject="user-1"),
        provision_user_fn=AsyncMock(return_value=SimpleNamespace(id=42)),
        create_document_fn=lambda *_args, **_kwargs: _async_value(None),
        get_document_fn=lambda *_args, **_kwargs: _async_value({"document": {}}),
        delete_document_fn=lambda *_args, **_kwargs: _async_value(None),
        create_job_fn=lambda **_kwargs: SimpleNamespace(job_id="job-1"),
        tenant_name_resolver=lambda _sub: "tenant-user-1",
    )

    await service.intake_upload(
        background_tasks=BackgroundTasks(),
        file=UploadFile(filename="paper.pdf", file=BytesIO(b"%PDF-1.7")),
        user={"sub": "user-1"},
    )

    assert session.added[0].main_file.page_count == 301
    assert session.commit_calls == 1


@pytest.mark.asyncio
async def test_default_upload_handler_rejects_real_301_page_pdf_before_session(
    tmp_path,
    monkeypatch,
):
    monkeypatch.delenv("PDF_UPLOAD_MAX_PAGE_COUNT", raising=False)
    pdf_buffer = BytesIO()
    writer = PdfWriter()
    for _ in range(301):
        writer.add_blank_page(width=612, height=792)
    writer.write(pdf_buffer)
    pdf_buffer.seek(0)
    session_factory_calls = []
    dispatch = _DispatchRecorder()

    service = UploadIntakeService(
        upload_execution_service=dispatch,
        session_factory=lambda: session_factory_calls.append(True),
        storage_path_provider=lambda: tmp_path,
    )

    with pytest.raises(UploadIntakeValidationError) as exc_info:
        await service.intake_upload(
            background_tasks=BackgroundTasks(),
            file=UploadFile(filename="paper.pdf", file=pdf_buffer),
            user={"sub": "user-1"},
        )

    assert exc_info.value.client_detail["actual_page_count"] == 301
    assert exc_info.value.client_detail["max_page_count"] == 300
    assert session_factory_calls == []
    assert dispatch.calls == []
    user_dir = tmp_path / "user-1"
    assert user_dir.exists()
    assert not any(user_dir.iterdir())


@pytest.mark.asyncio
async def test_intake_upload_duplicate_keeps_existing_and_cleans_new_artifacts(tmp_path):
    existing = SimpleNamespace(
        id=uuid4(),
        filename="8385804.pdf",
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
        provision_user_fn=AsyncMock(return_value=SimpleNamespace(id=42)),
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
    assert exc.value.detail["existing_document_id"] == str(existing.id)
    assert exc.value.detail["existing_filename"] == "8385804.pdf"
    assert 'as "8385804.pdf"' in exc.value.detail["suggestion"]
    assert session.deleted == []
    assert create_job_calls == []
    assert dispatch.calls == []
    user_dir = tmp_path / "user-1"
    assert user_dir.exists()
    assert not any(user_dir.iterdir())


@pytest.mark.asyncio
async def test_intake_upload_enabled_provider_no_match_falls_back_to_local_pdf_processing(tmp_path):
    session = _FakeSession()
    dispatch = _DispatchRecorder()
    provider = _FakeDocumentSourceProvider()
    create_document_calls = []
    create_job_calls = []
    selector_calls = []

    async def _selector(**kwargs):
        selector_calls.append(kwargs)
        return ChecksumImportDecision(
            status=ChecksumImportDecisionStatus.NO_MATCH,
            provider=kwargs["provider"].provider_id,
            checksum=kwargs["checksum"],
            message="No provider match",
        )

    async def _create_document(user_sub, document):
        create_document_calls.append((user_sub, document.id))

    service = UploadIntakeService(
        upload_execution_service=dispatch,
        session_factory=lambda: session,
        storage_path_provider=lambda: tmp_path,
        upload_handler_factory=lambda storage_path: _UploadHandler(
            storage_path=storage_path,
            checksum="sha256-source",
        ),
        principal_from_claims_fn=lambda _claims: SimpleNamespace(subject="user-1"),
        provision_user_fn=AsyncMock(return_value=SimpleNamespace(id=42)),
        create_document_fn=_create_document,
        get_document_fn=lambda *_args, **_kwargs: _async_value({"document": {}}),
        delete_document_fn=lambda *_args, **_kwargs: _async_value(None),
        create_job_fn=lambda **kwargs: create_job_calls.append(kwargs) or SimpleNamespace(job_id="job-1"),
        tenant_name_resolver=lambda _sub: "tenant-user-1",
        document_source_import_enabled_fn=lambda: True,
        document_source_provider_factory=lambda: provider,
        checksum_import_selector=_selector,
    )

    result = await service.intake_upload(
        background_tasks=BackgroundTasks(),
        file=UploadFile(filename="paper.pdf", file=BytesIO(b"%PDF-1.7")),
        user={"sub": "user-1"},
        document_source_context=DocumentSourceRequestContext(
            bearer_kind=ProviderBearerKind.CURATOR,
            provider_groups=("MGIStaff",),
            authorized_group_ids=("MGI",),
            curator_token="curator-token",
        )
    )

    assert result.job_id == "job-1"
    assert selector_calls == [
        {
            "provider": provider,
            "checksum": FAKE_UPLOAD_MD5,
            "authorized_group_ids": ("MGI",),
            "bearer_kind": ProviderBearerKind.CURATOR,
            "request_bearer_token": "curator-token",
            "allow_conversion_request": True,
        }
    ]
    assert provider.closed is True
    assert create_document_calls and create_document_calls[0][0] == "user-1"
    assert create_job_calls and create_job_calls[0]["filename"] == "paper.pdf"
    assert len(dispatch.calls) == 1
    assert dispatch.calls[0]["request"].job_id == "job-1"
    assert dispatch.provider_markdown_calls == []
    assert len(session.added) == 1
    record = session.added[0]
    assert record.main_file.display_mode == "local_pdf"
    assert record.source_provider is None
    assert session.commit_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("reporting_fails", [False, True])
async def test_checksum_provider_failure_reports_sanitized_503(tmp_path, monkeypatch, caplog, reporting_fails):
    sensitive = "synthetic-private-provider-response"
    provider = _FakeDocumentSourceProvider()
    monkeypatch.setattr(
        provider,
        "find_artifacts_by_checksum",
        AsyncMock(side_effect=DocumentSourceError(sensitive)),
    )
    report = Mock(side_effect=RuntimeError("synthetic-reporting-failure") if reporting_fails else None)
    monkeypatch.setattr(runtime, "report_runtime_exception", report)
    dispatch = _DispatchRecorder()
    service = UploadIntakeService(
        upload_execution_service=dispatch,
        session_factory=_FakeSession,
        storage_path_provider=lambda: tmp_path,
        upload_handler_factory=lambda storage_path: _UploadHandler(storage_path=storage_path),
        provision_user_fn=AsyncMock(return_value=SimpleNamespace(id=42)),
        document_source_import_enabled_fn=lambda: True,
        document_source_provider_factory=lambda: provider,
    )

    with pytest.raises(UploadIntakeProviderDecisionError) as exc:
        await service.intake_upload(
            background_tasks=BackgroundTasks(),
            file=UploadFile(filename="paper.pdf", file=BytesIO(b"%PDF-1.7")),
            user={"sub": "user-1"},
            document_source_context=DocumentSourceRequestContext(
                bearer_kind=ProviderBearerKind.SERVICE,
                provider_groups=(),
                authorized_group_ids=(),
                curator_token="synthetic-reader-token",
            ),
        )

    assert exc.value.status_code == 503
    assert exc.value.detail == {
        "error": "document_source_unavailable",
        "message": "Document-source lookup is unavailable.",
        "suggestion": "Try again later or contact support if this persists.",
    }
    report.assert_called_once()
    (reported,) = report.call_args.args
    assert str(reported) == "Document-source checksum lookup failed"
    assert reported.__traceback__ is not None
    assert reported.__cause__ is None
    assert reported.__context__ is None
    assert report.call_args.kwargs == {
        "component": "upload_intake",
        "operation": "checksum_lookup_failed",
        "context": {"exception_type": "DocumentSourceError"},
    }
    assert sensitive not in caplog.text
    assert provider.closed
    assert not any((tmp_path / "user-1").iterdir())
    assert dispatch.calls == []
    assert dispatch.provider_markdown_calls == []
    assert dispatch.provider_conversion_calls == []


@pytest.mark.asyncio
async def test_intake_checksum_matched_supplement_processes_exact_uploaded_pdf(tmp_path):
    session = _FakeSession()
    dispatch = _DispatchRecorder()
    provider = _FakeDocumentSourceProvider()
    selector_calls = []
    source_artifact = SourceArtifact(
        provider="abc_literature",
        artifact_id="5006666",
        role=SourceArtifactRole.SOURCE_PDF,
        artifact_format=SourceArtifactFormat.PDF,
        status=SourceArtifactStatus.AVAILABLE,
        reference_id="101000001296197",
        reference_curie="AGRKB:101000001296197",
        display_name="FBrf0265453_supplement.pdf",
        md5sum="e6d1d1f9f937451923edb6cb53b51047",
        access_policy=SourceAccessPolicy(scope=SourceAccessScope.GLOBAL),
        metadata={"file_class": "supplement"},
    )

    async def _selector(**kwargs):
        selector_calls.append(kwargs)
        candidate = ChecksumImportCandidate(source_artifact=source_artifact)
        return ChecksumImportDecision(
            status=ChecksumImportDecisionStatus.LOCAL_PDF_REQUIRED,
            provider="abc_literature",
            checksum=kwargs["checksum"],
            selected=candidate,
            candidates=(candidate,),
            source_artifacts=(source_artifact,),
            metadata={"text_source": "local_pdf"},
        )

    service = UploadIntakeService(
        upload_execution_service=dispatch,
        session_factory=lambda: session,
        storage_path_provider=lambda: tmp_path,
        upload_handler_factory=lambda storage_path: _UploadHandler(
            storage_path=storage_path,
            checksum="sha256-source",
        ),
        principal_from_claims_fn=lambda _claims: SimpleNamespace(subject="user-1"),
        provision_user_fn=AsyncMock(return_value=SimpleNamespace(id=42)),
        create_document_fn=lambda *_args, **_kwargs: _async_value(None),
        get_document_fn=lambda *_args, **_kwargs: _async_value({"document": {}}),
        delete_document_fn=lambda *_args, **_kwargs: _async_value(None),
        create_job_fn=lambda **_kwargs: SimpleNamespace(job_id="job-1"),
        tenant_name_resolver=lambda _sub: "tenant-user-1",
        document_source_import_enabled_fn=lambda: True,
        document_source_provider_factory=lambda: provider,
        checksum_import_selector=_selector,
    )

    result = await service.intake_upload(
        background_tasks=BackgroundTasks(),
        file=UploadFile(
            filename="supplement_for_gm85531.pdf",
            file=BytesIO(b"%PDF-1.7"),
        ),
        user={"sub": "user-1"},
        document_source_context=DocumentSourceRequestContext(
            bearer_kind=ProviderBearerKind.CURATOR,
            provider_groups=("FBStaff",),
            authorized_group_ids=("FB",),
            curator_token="curator-token",
        ),
    )

    assert result.job_id == "job-1"
    assert selector_calls[0]["checksum"] == FAKE_UPLOAD_MD5
    assert len(dispatch.calls) == 1
    assert dispatch.calls[0]["request"].file_path.name == "supplement_for_gm85531.pdf"
    assert dispatch.provider_markdown_calls == []
    assert dispatch.provider_conversion_calls == []
    assert len(session.added) == 1
    record = session.added[0]
    assert record.source_provider is None
    assert record.main_file.display_mode == "local_pdf"
    assert session.commit_calls == 1


@pytest.mark.asyncio
async def test_intake_upload_enabled_provider_ready_dispatches_markdown_import(tmp_path):
    session = _FakeSession()
    dispatch = _DispatchRecorder()
    provider = _FakeDocumentSourceProvider()
    create_document_calls = []
    source_artifact = SourceArtifact(
        provider="fake_provider",
        artifact_id="source-pdf-1",
        role=SourceArtifactRole.SOURCE_PDF,
        artifact_format=SourceArtifactFormat.PDF,
        status=SourceArtifactStatus.AVAILABLE,
        reference_id="ref-1",
        reference_curie="AGRKB:101",
        md5sum=FAKE_UPLOAD_MD5,
        access_policy=SourceAccessPolicy(
            scope=SourceAccessScope.RESTRICTED,
            group_ids=("MGI",),
        ),
        metadata={"external_ids": {"doi": "10.123/example"}},
    )
    converted_artifact = SourceArtifact(
        provider="fake_provider",
        artifact_id="markdown-1",
        role=SourceArtifactRole.CONVERTED_TEXT,
        artifact_format=SourceArtifactFormat.MARKDOWN,
        status=SourceArtifactStatus.AVAILABLE,
        reference_id="ref-1",
        reference_curie="AGRKB:101",
        parent_artifact_id="source-pdf-1",
        access_policy=source_artifact.access_policy,
    )

    async def _selector(**kwargs):
        candidate = ChecksumImportCandidate(
            source_artifact=source_artifact,
            converted_artifact=converted_artifact,
        )
        return ChecksumImportDecision(
            status=ChecksumImportDecisionStatus.READY,
            provider=kwargs["provider"].provider_id,
            checksum=kwargs["checksum"],
            selected=candidate,
            candidates=(candidate,),
            source_artifacts=(source_artifact,),
        )

    async def _create_document(user_sub, document):
        create_document_calls.append((user_sub, document))

    service = UploadIntakeService(
        upload_execution_service=dispatch,
        session_factory=lambda: session,
        storage_path_provider=lambda: tmp_path,
        upload_handler_factory=lambda storage_path: _UploadHandler(
            storage_path=storage_path,
            checksum="sha256-source",
        ),
        principal_from_claims_fn=lambda _claims: SimpleNamespace(subject="user-1"),
        provision_user_fn=AsyncMock(return_value=SimpleNamespace(id=42)),
        create_document_fn=_create_document,
        get_document_fn=lambda *_args, **_kwargs: _async_value({"document": {}}),
        delete_document_fn=lambda *_args, **_kwargs: _async_value(None),
        create_job_fn=lambda **_kwargs: SimpleNamespace(job_id="job-ready"),
        tenant_name_resolver=lambda _sub: "tenant-user-1",
        document_source_import_enabled_fn=lambda: True,
        document_source_provider_factory=lambda: provider,
        checksum_import_selector=_selector,
        find_existing_source_document_fn=lambda *_args, **_kwargs: None,
    )

    result = await service.intake_upload(
        background_tasks=BackgroundTasks(),
        file=UploadFile(filename="paper.pdf", file=BytesIO(b"%PDF-1.7")),
        user={"sub": "user-1"},
        document_source_context=DocumentSourceRequestContext(
            bearer_kind=ProviderBearerKind.CURATOR,
            provider_groups=("MGIStaff",),
            authorized_group_ids=("MGI",),
            curator_token="curator-token",
        ),
    )

    assert result.job_id == "job-ready"
    assert create_document_calls[0][0] == "user-1"
    assert create_document_calls[0][1].source_provenance["converted_artifact_id"] == "markdown-1"
    assert len(session.added) == 1
    record = session.added[0]
    assert record.document_kind == "library" and record.next_chunk_index == 0
    assert [file_row.role for file_row in record.files] == ["main"]
    main_file = record.main_file
    assert main_file.display_mode == "local_pdf"
    assert main_file.file_path.endswith("/paper.pdf")
    assert record.source_provider == "fake_provider"
    assert main_file.source_provider_converted_artifact_id == "markdown-1"
    assert main_file.source_import_status == "pending"
    assert main_file.source_access_scope == "restricted"
    assert main_file.source_access_group_ids == ["MGI"]
    assert dispatch.calls == []
    assert len(dispatch.provider_markdown_calls) == 1
    provider_request = dispatch.provider_markdown_calls[0]["request"]
    assert provider_request.converted_artifact_id == "markdown-1"
    assert provider_request.curator_token == "curator-token"
    assert provider_request.source_provenance["source_md5"] == FAKE_UPLOAD_MD5
    assert provider_request.file_path == tmp_path / record.main_file.file_path
    assert provider.closed is True
    user_dir = tmp_path / "user-1"
    assert user_dir.exists()
    assert (tmp_path / record.main_file.file_path).exists()


@pytest.mark.asyncio
async def test_intake_upload_provider_match_with_pending_conversion_does_not_dispatch_pdf_processing(tmp_path):
    session = _FakeSession()
    dispatch = _DispatchRecorder()
    provider = _FakeDocumentSourceProvider()
    provider.provider_id = "fake_provider"
    create_document_calls = []
    source_artifact = SourceArtifact(
        provider="fake_provider",
        artifact_id="source-pdf-1",
        role=SourceArtifactRole.SOURCE_PDF,
        artifact_format=SourceArtifactFormat.PDF,
        status=SourceArtifactStatus.AVAILABLE,
        reference_id="ref-1",
        reference_curie="AGRKB:101",
        md5sum=FAKE_UPLOAD_MD5,
        access_policy=SourceAccessPolicy(
            scope=SourceAccessScope.RESTRICTED,
            group_ids=("MGI",),
        ),
        metadata={"external_ids": {"doi": "10.123/example"}},
    )

    async def _selector(**kwargs):
        candidate = ChecksumImportCandidate(source_artifact=source_artifact)
        return ChecksumImportDecision(
            status=ChecksumImportDecisionStatus.CONVERSION_RUNNING,
            provider=kwargs["provider"].provider_id,
            checksum=kwargs["checksum"],
            selected=candidate,
            candidates=(candidate,),
            source_artifacts=(source_artifact,),
            metadata={"conversion_status": "running", "conversion_job_id": "job-fake"},
        )

    async def _create_document(user_sub, document):
        create_document_calls.append((user_sub, document))

    service = UploadIntakeService(
        upload_execution_service=dispatch,
        session_factory=lambda: session,
        storage_path_provider=lambda: tmp_path,
        upload_handler_factory=lambda storage_path: _UploadHandler(
            storage_path=storage_path,
            checksum="sha256-source",
        ),
        principal_from_claims_fn=lambda _claims: SimpleNamespace(subject="user-1"),
        provision_user_fn=AsyncMock(return_value=SimpleNamespace(id=42)),
        create_document_fn=_create_document,
        get_document_fn=lambda *_args, **_kwargs: _async_value({"document": {}}),
        delete_document_fn=lambda *_args, **_kwargs: _async_value(None),
        create_job_fn=lambda **_kwargs: SimpleNamespace(job_id="job-ready"),
        tenant_name_resolver=lambda _sub: "tenant-user-1",
        document_source_import_enabled_fn=lambda: True,
        document_source_provider_factory=lambda: provider,
        checksum_import_selector=_selector,
        find_existing_source_document_fn=lambda *_args, **_kwargs: None,
    )

    result = await service.intake_upload(
        background_tasks=BackgroundTasks(),
        file=UploadFile(filename="paper.pdf", file=BytesIO(b"%PDF-1.7")),
        user={"sub": "user-1"},
        document_source_context=DocumentSourceRequestContext(
            bearer_kind=ProviderBearerKind.CURATOR,
            provider_groups=("MGIStaff",),
            authorized_group_ids=("MGI",),
            curator_token="curator-token",
        )
    )

    assert result.job_id == "job-ready"
    assert create_document_calls[0][1].source_provenance["provider"] == "fake_provider"
    assert len(session.added) == 1
    record = session.added[0]
    assert record.main_file.display_mode == "local_pdf"
    assert record.source_provider == "fake_provider"
    assert record.main_file.source_import_status == "pending"
    assert dispatch.provider_markdown_calls == []
    assert dispatch.calls == []
    assert len(dispatch.provider_conversion_calls) == 1
    conversion_request = dispatch.provider_conversion_calls[0]["request"]
    assert conversion_request.reference == "AGRKB:101"
    assert conversion_request.source_artifact_id == "source-pdf-1"
    assert conversion_request.curator_token == "curator-token"
    assert conversion_request.file_path == tmp_path / record.main_file.file_path
    assert provider.closed is True
    user_dir = tmp_path / "user-1"
    assert user_dir.exists()
    assert (tmp_path / record.main_file.file_path).exists()


@pytest.mark.asyncio
async def test_intake_upload_provider_source_only_ready_uses_presentation_label(
    tmp_path,
    monkeypatch,
):
    session = _FakeSession()
    dispatch = _DispatchRecorder()
    provider = _FakeDocumentSourceProvider()
    provider.provider_id = "fake_provider"
    monkeypatch.setattr(
        upload_intake_module,
        "get_document_source_provider_metadata",
        lambda provider_id: {"display_label": "Example Literature"}
        if provider_id == "fake_provider"
        else None,
    )
    create_document_calls = []
    source_artifact = SourceArtifact(
        provider="fake_provider",
        artifact_id="source-pdf-1",
        role=SourceArtifactRole.SOURCE_PDF,
        artifact_format=SourceArtifactFormat.PDF,
        status=SourceArtifactStatus.AVAILABLE,
        reference_id="ref-1",
        reference_curie="AGRKB:101",
        md5sum=FAKE_UPLOAD_MD5,
        access_policy=SourceAccessPolicy(
            scope=SourceAccessScope.RESTRICTED,
            group_ids=("MGI",),
        ),
    )

    async def _selector(**kwargs):
        candidate = ChecksumImportCandidate(source_artifact=source_artifact)
        return ChecksumImportDecision(
            status=ChecksumImportDecisionStatus.READY,
            provider=kwargs["provider"].provider_id,
            checksum=kwargs["checksum"],
            selected=candidate,
            candidates=(candidate,),
            source_artifacts=(source_artifact,),
        )

    async def _create_document(user_sub, document):
        create_document_calls.append((user_sub, document))

    service = UploadIntakeService(
        upload_execution_service=dispatch,
        session_factory=lambda: session,
        storage_path_provider=lambda: tmp_path,
        upload_handler_factory=lambda storage_path: _UploadHandler(
            storage_path=storage_path,
            checksum="sha256-source",
        ),
        principal_from_claims_fn=lambda _claims: SimpleNamespace(subject="user-1"),
        provision_user_fn=AsyncMock(return_value=SimpleNamespace(id=42)),
        create_document_fn=_create_document,
        get_document_fn=lambda *_args, **_kwargs: _async_value({"document": {}}),
        delete_document_fn=lambda *_args, **_kwargs: _async_value(None),
        create_job_fn=lambda **_kwargs: SimpleNamespace(job_id="job-ready"),
        tenant_name_resolver=lambda _sub: "tenant-user-1",
        document_source_import_enabled_fn=lambda: True,
        document_source_provider_factory=lambda: provider,
        checksum_import_selector=_selector,
        find_existing_source_document_fn=lambda *_args, **_kwargs: None,
    )

    with pytest.raises(UploadIntakeProviderDecisionError) as exc:
        await service.intake_upload(
            background_tasks=BackgroundTasks(),
            file=UploadFile(filename="paper.pdf", file=BytesIO(b"%PDF-1.7")),
            user={"sub": "user-1"},
            document_source_context=DocumentSourceRequestContext(
                bearer_kind=ProviderBearerKind.CURATOR,
                provider_groups=("MGIStaff",),
                authorized_group_ids=("MGI",),
                curator_token="curator-token",
            ),
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "document_source_no_converted_text"
    assert "Example Literature" in exc.value.detail["message"]
    assert "Example Literature" in exc.value.detail["suggestion"]
    assert "fake_provider" not in exc.value.detail["message"]
    assert create_document_calls == []
    assert session.added == []
    assert dispatch.provider_markdown_calls == []
    assert dispatch.calls == []
    assert provider.closed is True
    user_dir = tmp_path / "user-1"
    assert user_dir.exists()
    assert not any(user_dir.iterdir())


@pytest.mark.asyncio
async def test_intake_upload_enabled_provider_ready_requires_curator_token(tmp_path):
    session = _FakeSession()
    dispatch = _DispatchRecorder()
    provider = _FakeDocumentSourceProvider()
    selector_calls = []
    source_artifact = SourceArtifact(
        provider="fake_provider",
        artifact_id="source-pdf-1",
        role=SourceArtifactRole.SOURCE_PDF,
        artifact_format=SourceArtifactFormat.PDF,
        status=SourceArtifactStatus.AVAILABLE,
        md5sum=FAKE_UPLOAD_MD5,
        access_policy=SourceAccessPolicy(scope=SourceAccessScope.GLOBAL),
    )
    converted_artifact = SourceArtifact(
        provider="fake_provider",
        artifact_id="markdown-1",
        role=SourceArtifactRole.CONVERTED_TEXT,
        artifact_format=SourceArtifactFormat.MARKDOWN,
        status=SourceArtifactStatus.AVAILABLE,
        parent_artifact_id="source-pdf-1",
        access_policy=source_artifact.access_policy,
    )

    async def _selector(**kwargs):
        selector_calls.append(kwargs)
        candidate = ChecksumImportCandidate(
            source_artifact=source_artifact,
            converted_artifact=converted_artifact,
        )
        return ChecksumImportDecision(
            status=ChecksumImportDecisionStatus.READY,
            provider=kwargs["provider"].provider_id,
            checksum=kwargs["checksum"],
            selected=candidate,
        )

    service = UploadIntakeService(
        upload_execution_service=dispatch,
        session_factory=lambda: session,
        storage_path_provider=lambda: tmp_path,
        upload_handler_factory=lambda storage_path: _UploadHandler(
            storage_path=storage_path,
            checksum="sha256-source",
        ),
        principal_from_claims_fn=lambda _claims: SimpleNamespace(subject="user-1"),
        provision_user_fn=AsyncMock(return_value=SimpleNamespace(id=42)),
        create_document_fn=lambda *_args, **_kwargs: _async_value(None),
        get_document_fn=lambda *_args, **_kwargs: _async_value({"document": {}}),
        delete_document_fn=lambda *_args, **_kwargs: _async_value(None),
        create_job_fn=lambda **_kwargs: SimpleNamespace(job_id="job-ready"),
        tenant_name_resolver=lambda _sub: "tenant-user-1",
        document_source_import_enabled_fn=lambda: True,
        document_source_provider_factory=lambda: provider,
        checksum_import_selector=_selector,
    )

    with pytest.raises(UploadIntakeProviderDecisionError) as exc:
        await service.intake_upload(
            background_tasks=BackgroundTasks(),
            file=UploadFile(filename="paper.pdf", file=BytesIO(b"%PDF-1.7")),
            user={"sub": "user-1"},
            document_source_context=DocumentSourceRequestContext(
                bearer_kind=ProviderBearerKind.CURATOR,
                provider_groups=("MGIStaff",),
                authorized_group_ids=("MGI",),
            ),
        )

    assert exc.value.status_code == 503
    assert exc.value.detail["error"] == "document_source_curator_token_unavailable"
    assert selector_calls[0]["allow_conversion_request"] is False
    assert selector_calls[0]["request_bearer_token"] is None
    assert session.added == []
    assert dispatch.calls == []
    assert dispatch.provider_markdown_calls == []
    user_dir = tmp_path / "user-1"
    assert user_dir.exists()
    assert not any(user_dir.iterdir())


@pytest.mark.asyncio
async def test_intake_upload_enabled_provider_duplicate_short_circuits_before_lookup(tmp_path):
    existing = SimpleNamespace(
        id=uuid4(),
        filename="8385804.pdf",
        upload_timestamp=datetime(2026, 1, 3, 13, 0, tzinfo=timezone.utc),
    )
    session = _FakeSession(execute_row=existing)
    dispatch = _DispatchRecorder()
    provider_calls = []
    provider = _FakeDocumentSourceProvider()

    async def _get_document(_user_sub, _document_id):
        return {"document": {"id": _document_id}}

    service = UploadIntakeService(
        upload_execution_service=dispatch,
        session_factory=lambda: session,
        storage_path_provider=lambda: tmp_path,
        upload_handler_factory=lambda storage_path: _UploadHandler(storage_path=storage_path),
        principal_from_claims_fn=lambda _claims: SimpleNamespace(subject="user-1"),
        provision_user_fn=AsyncMock(return_value=SimpleNamespace(id=42)),
        create_document_fn=lambda *_args, **_kwargs: _async_value(None),
        get_document_fn=_get_document,
        delete_document_fn=lambda *_args, **_kwargs: _async_value(None),
        create_job_fn=lambda **_kwargs: SimpleNamespace(job_id="job-1"),
        tenant_name_resolver=lambda _sub: "tenant-user-1",
        document_source_import_enabled_fn=lambda: True,
        document_source_provider_factory=lambda: provider_calls.append("provider") or provider,
    )

    with pytest.raises(UploadIntakeDuplicateError):
        await service.intake_upload(
            background_tasks=BackgroundTasks(),
            file=UploadFile(filename="paper.pdf", file=BytesIO(b"%PDF-1.7")),
            user={"sub": "user-1"},
            document_source_context=DocumentSourceRequestContext(
                bearer_kind=ProviderBearerKind.CURATOR,
                provider_groups=("MGIStaff",),
                authorized_group_ids=("MGI",),
            ),
        )

    assert provider_calls == []
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
        provision_user_fn=AsyncMock(return_value=SimpleNamespace(id=42)),
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
    existing = SimpleNamespace(
        id=uuid4(),
        filename="8385804.pdf",
        upload_timestamp=datetime(2026, 1, 3, 13, 0, tzinfo=timezone.utc),
    )

    class _ConcurrentDuplicateSession(_FakeSession):
        def __init__(self):
            super().__init__(
                fail_commit_on_call=1,
                commit_error=IntegrityError(
                    "insert",
                    {"id": "doc"},
                    Exception("duplicate key"),
                ),
            )
            self.execute_calls = 0

        def execute(self, *_args, **_kwargs):
            self.execute_calls += 1
            return _ExecuteResult(None if self.execute_calls == 1 else existing)

    session = _ConcurrentDuplicateSession()
    dispatch = _DispatchRecorder()
    delete_document_calls = []

    async def _delete_document(user_sub, document_id, **_kwargs):
        delete_document_calls.append((user_sub, document_id))
        return {"success": True}

    create_document_calls = []

    async def _create_document(user_sub, document):
        create_document_calls.append((user_sub, document.id))

    service = UploadIntakeService(
        upload_execution_service=dispatch,
        session_factory=lambda: session,
        storage_path_provider=lambda: tmp_path,
        upload_handler_factory=lambda storage_path: _UploadHandler(storage_path=storage_path),
        principal_from_claims_fn=lambda _claims: SimpleNamespace(subject="user-1"),
        provision_user_fn=AsyncMock(return_value=SimpleNamespace(id=42)),
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
    assert exc.value.detail["existing_document_id"] == str(existing.id)
    assert exc.value.detail["existing_filename"] == "8385804.pdf"
    assert create_document_calls and create_document_calls[0][0] == "user-1"
    assert len(delete_document_calls) == 1
    assert session.rollbacks == 1
    user_dir = tmp_path / "user-1"
    assert user_dir.exists()
    assert not any(user_dir.iterdir())
    assert not dispatch.calls


@pytest.mark.asyncio
async def test_intake_upload_file_size_constraint_error_is_not_misreported_as_limit_error(
    tmp_path,
):
    session = _FakeSession(
        fail_commit_on_call=1,
        commit_error=IntegrityError(
            "insert",
            {"id": "doc"},
            _ConstraintError(
                "ck_pdf_documents_file_size",
                "new row for relation \"pdf_documents\" violates check constraint "
                "\"ck_pdf_documents_file_size\"",
            ),
        ),
    )
    dispatch = _DispatchRecorder()
    delete_document_calls = []

    async def _delete_document(user_sub, document_id, **_kwargs):
        delete_document_calls.append((user_sub, document_id))
        return {"success": True}

    service = UploadIntakeService(
        upload_execution_service=dispatch,
        session_factory=lambda: session,
        storage_path_provider=lambda: tmp_path,
        upload_handler_factory=lambda storage_path: _UploadHandler(storage_path=storage_path),
        principal_from_claims_fn=lambda _claims: SimpleNamespace(subject="user-1"),
        provision_user_fn=AsyncMock(return_value=SimpleNamespace(id=42)),
        create_document_fn=lambda *_args, **_kwargs: _async_value(None),
        get_document_fn=lambda *_args, **_kwargs: _async_value({"document": {}}),
        delete_document_fn=_delete_document,
        create_job_fn=lambda **_kwargs: SimpleNamespace(job_id="job-1"),
        tenant_name_resolver=lambda _sub: "tenant-user-1",
    )

    with pytest.raises(IntegrityError):
        await service.intake_upload(
            background_tasks=BackgroundTasks(),
            file=UploadFile(filename="paper.pdf", file=BytesIO(b"%PDF-1.7")),
            user={"sub": "user-1"},
        )

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

    async def _delete_document(user_sub, document_id, **_kwargs):
        delete_document_calls.append((user_sub, document_id))
        return {"success": True}

    service = UploadIntakeService(
        upload_execution_service=dispatch,
        session_factory=lambda: session,
        storage_path_provider=lambda: tmp_path,
        upload_handler_factory=lambda storage_path: _UploadHandler(storage_path=storage_path),
        principal_from_claims_fn=lambda _claims: SimpleNamespace(subject="user-1"),
        provision_user_fn=AsyncMock(return_value=SimpleNamespace(id=42)),
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

    async def _delete_document(user_sub, document_id, **_kwargs):
        delete_document_calls.append((user_sub, document_id))
        return {"success": True}

    service = UploadIntakeService(
        upload_execution_service=dispatch,
        session_factory=lambda: session,
        storage_path_provider=lambda: tmp_path,
        upload_handler_factory=lambda storage_path: _UploadHandler(storage_path=storage_path),
        principal_from_claims_fn=lambda _claims: SimpleNamespace(subject="user-1"),
        provision_user_fn=AsyncMock(return_value=SimpleNamespace(id=42)),
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
        provision_user_fn=AsyncMock(return_value=SimpleNamespace(id=42)),
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

    async def _delete_document(user_sub, document_id, **_kwargs):
        delete_document_calls.append((user_sub, document_id))
        return {"success": True}

    def _boom_on_add(_row):
        raise RuntimeError("sql write failed")

    session.add = _boom_on_add

    service = UploadIntakeService(
        upload_execution_service=dispatch,
        session_factory=lambda: session,
        storage_path_provider=lambda: tmp_path,
        upload_handler_factory=lambda storage_path: _UploadHandler(storage_path=storage_path),
        principal_from_claims_fn=lambda _claims: SimpleNamespace(subject="user-1"),
        provision_user_fn=AsyncMock(return_value=SimpleNamespace(id=42)),
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
    snapshot_session = _FakeSession(execute_row=persisted_record)
    cleanup_session = _FakeSession(execute_row=persisted_record)
    # 1: persist document, 2: snapshot live SQL files before Weaviate delete, 3: SQL delete fallback
    sessions = [initial_session, snapshot_session, cleanup_session]
    dispatch = _DispatchRecorder()
    delete_document_calls = []

    async def _delete_document(user_sub, document_id, **_kwargs):
        delete_document_calls.append((user_sub, document_id))
        return {"success": True}

    def _session_factory():
        assert sessions, "session_factory called more times than expected"
        return sessions.pop(0)

    service = UploadIntakeService(
        upload_execution_service=dispatch,
        session_factory=_session_factory,
        storage_path_provider=lambda: tmp_path,
        upload_handler_factory=lambda storage_path: _UploadHandler(storage_path=storage_path),
        principal_from_claims_fn=lambda _claims: SimpleNamespace(subject="user-1"),
        provision_user_fn=AsyncMock(return_value=SimpleNamespace(id=42)),
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


@pytest.mark.asyncio
async def test_provisioning_integrity_failure_never_enters_pdf_duplicate_recovery(tmp_path):
    from sqlalchemy.exc import IntegrityError
    failure = IntegrityError('private-user-sql', {}, Exception('unique constraint private-user-constraint'))
    dispatch = _DispatchRecorder()
    service = UploadIntakeService(
        upload_execution_service=dispatch,
        session_factory=lambda: pytest.fail('document Session opened before provisioning'),
        storage_path_provider=lambda: tmp_path,
        upload_handler_factory=lambda storage_path: _UploadHandler(storage_path=storage_path),
        principal_from_claims_fn=lambda _claims: SimpleNamespace(subject='user-1'),
        provision_user_fn=AsyncMock(side_effect=failure),
    )
    with pytest.raises(IntegrityError) as caught:
        await service.intake_upload(
            background_tasks=BackgroundTasks(),
            file=UploadFile(filename='paper.pdf', file=BytesIO(b'%PDF-1.7')),
            user={'sub': 'user-1'},
        )
    assert caught.value is failure
    assert not dispatch.calls
    assert not any((tmp_path / 'user-1').iterdir())



@pytest.mark.asyncio
@pytest.mark.parametrize("request_sent", [True, False], ids=["selector_sent_the_request", "listing_showed_it_running"])
async def test_an_upload_that_waits_for_conversion_records_whether_the_request_was_sent(tmp_path, request_sent):
    """KANBAN-2191 / ALL-1725 (finding 2 of #1137's review): the checksum match sends the
    provider's on-demand request while deciding; the job records it and the wait gets the
    answer, so the wait never sends it a second time."""

    session = _FakeSession()
    dispatch = _DispatchRecorder()
    provider = _FakeDocumentSourceProvider()
    provider.provider_id = "fake_provider"
    created_jobs = []
    source_artifact = SourceArtifact(
        provider="fake_provider", artifact_id="source-pdf-1", role=SourceArtifactRole.SOURCE_PDF,
        artifact_format=SourceArtifactFormat.PDF, status=SourceArtifactStatus.AVAILABLE,
        reference_id="ref-1", reference_curie="AGRKB:101", md5sum=FAKE_UPLOAD_MD5,
        access_policy=SourceAccessPolicy(scope=SourceAccessScope.RESTRICTED, group_ids=("MGI",)),
    )
    from datetime import datetime, timezone

    sent_at = datetime.now(timezone.utc)
    answer = SourceConversionResult(
        provider="fake_provider", status=SourceConversionStatus.RUNNING, reference_curie="AGRKB:101",
        job_id="job-fake",
    )

    async def _selector(**kwargs):
        candidate = ChecksumImportCandidate(source_artifact=source_artifact)
        return ChecksumImportDecision(
            status=ChecksumImportDecisionStatus.CONVERSION_RUNNING, provider="fake_provider",
            checksum=kwargs["checksum"], selected=candidate, candidates=(candidate,),
            source_artifacts=(source_artifact,), conversion_answer=answer if request_sent else None,
            conversion_requested_at=sent_at if request_sent else None,
        )

    def _create_job(**kwargs):
        created_jobs.append(kwargs)
        return SimpleNamespace(job_id="job-ready")

    service = UploadIntakeService(
        upload_execution_service=dispatch,
        session_factory=lambda: session,
        storage_path_provider=lambda: tmp_path,
        upload_handler_factory=lambda storage_path: _UploadHandler(storage_path=storage_path, checksum="sha256-source"),
        principal_from_claims_fn=lambda _claims: SimpleNamespace(subject="user-1"),
        provision_user_fn=AsyncMock(return_value=SimpleNamespace(id=42)),
        create_document_fn=AsyncMock(),
        get_document_fn=lambda *_args, **_kwargs: _async_value({"document": {}}),
        delete_document_fn=lambda *_args, **_kwargs: _async_value(None),
        create_job_fn=_create_job,
        tenant_name_resolver=lambda _sub: "tenant-user-1",
        document_source_import_enabled_fn=lambda: True,
        document_source_provider_factory=lambda: provider,
        checksum_import_selector=_selector,
        find_existing_source_document_fn=lambda *_args, **_kwargs: None,
    )

    await service.intake_upload(
        background_tasks=BackgroundTasks(),
        file=UploadFile(filename="paper.pdf", file=BytesIO(b"%PDF-1.7")),
        user={"sub": "user-1"},
        document_source_context=DocumentSourceRequestContext(
            bearer_kind=ProviderBearerKind.CURATOR, provider_groups=("MGIStaff",),
            authorized_group_ids=("MGI",), curator_token="curator-token",
        ),
    )

    from src.lib.pdf_jobs import service as jobs

    request = dispatch.provider_conversion_calls[0]["request"]
    if request_sent:
        assert created_jobs[0]["metadata"][jobs.CONVERSION_REQUESTS_KEY] == {"on_demand_sent_at": sent_at.isoformat()}
        assert request.conversion_answer is answer
    else:
        assert "metadata" not in created_jobs[0]
        assert request.conversion_answer is None


@pytest.mark.asyncio
async def test_intake_upload_job_creation_compensation_cancelled_during_chunk_wipe_cleans_saved_pdf(
    monkeypatch, tmp_path,
):
    import asyncio
    from contextlib import contextmanager
    import threading
    from unittest.mock import MagicMock

    from src.lib.weaviate_client import documents as weaviate_docs

    wipe_entered = threading.Event()
    resume_wipe = threading.Event()
    events: list[str] = []

    @contextmanager
    def _session(*, caller_reports_failure):
        yield MagicMock()

    connection = MagicMock()
    connection.session.side_effect = _session
    chunks, papers = MagicMock(), MagicMock()

    def _delete_many(**_kwargs):
        wipe_entered.set()
        assert resume_wipe.wait(timeout=5)
        events.append("chunks")
        return SimpleNamespace(successful=0, matches=0, failed=0)

    def _delete_by_id(_doc_id):
        events.append("parent")
        return True

    sql_owner_deleted = False

    def _delete_sql_row(*_args, **_kwargs):
        nonlocal sql_owner_deleted
        sql_owner_deleted = True
        events.append("sql")
        return True

    chunks.data.delete_many.side_effect = _delete_many
    papers.data.delete_by_id.side_effect = _delete_by_id
    monkeypatch.setattr(weaviate_docs, "get_connection", lambda *, caller_reports_failure: connection)
    monkeypatch.setattr(weaviate_docs, "get_user_collections", lambda *_: (chunks, papers))
    monkeypatch.setattr(weaviate_docs, "_delete_sql_row", _delete_sql_row)

    initial_session = _FakeSession()
    persisted_record = SimpleNamespace(id=uuid4())
    snapshot_session = _FakeSession(execute_row=persisted_record)
    cleanup_session = _FakeSession(execute_row=persisted_record)
    sessions = [initial_session, snapshot_session, cleanup_session]

    service = UploadIntakeService(
        upload_execution_service=_DispatchRecorder(),
        session_factory=lambda: sessions.pop(0),
        storage_path_provider=lambda: tmp_path,
        upload_handler_factory=lambda storage_path: _UploadHandler(storage_path=storage_path),
        principal_from_claims_fn=lambda _claims: SimpleNamespace(subject="user-1"),
        provision_user_fn=AsyncMock(return_value=SimpleNamespace(id=42)),
        create_document_fn=lambda *_args, **_kwargs: _async_value(None),
        get_document_fn=lambda *_args, **_kwargs: _async_value({"document": {}}),
        delete_document_fn=weaviate_docs.delete_document,
        create_job_fn=lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("job row failed")),
        tenant_name_resolver=lambda _sub: "tenant-user-1",
    )

    task = asyncio.create_task(
        service.intake_upload(
            background_tasks=BackgroundTasks(),
            file=UploadFile(filename="paper.pdf", file=BytesIO(b"%PDF-1.7")),
            user={"sub": "user-1"},
        )
    )
    try:
        assert await asyncio.to_thread(wipe_entered.wait, 5)
        assert not list(tmp_path.rglob("*.pdf"))
        task.cancel("cancelled during upload compensation")
        await asyncio.sleep(0.02)
        assert not task.done()
    finally:
        resume_wipe.set()

    with pytest.raises(asyncio.CancelledError, match="cancelled during upload compensation"):
        await asyncio.wait_for(task, timeout=5)

    assert events == ["chunks", "parent", "sql"]
    assert sql_owner_deleted is True
    assert not list(tmp_path.rglob("*.pdf"))


@pytest.mark.asyncio
async def test_intake_upload_job_creation_compensation_retains_owner_on_persistent_unlink_failure(
    monkeypatch, tmp_path,
):
    import shutil

    initial_session = _FakeSession()
    persisted_record = SimpleNamespace(id=uuid4())
    snapshot_session = _FakeSession(execute_row=persisted_record)
    cleanup_session = _FakeSession(execute_row=persisted_record)
    sessions = [initial_session, snapshot_session, cleanup_session]
    delete_document_mock = AsyncMock()
    monkeypatch.setattr(shutil, "rmtree", lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError("locked")))

    service = UploadIntakeService(
        upload_execution_service=_DispatchRecorder(),
        session_factory=lambda: sessions.pop(0),
        storage_path_provider=lambda: tmp_path,
        upload_handler_factory=lambda storage_path: _UploadHandler(storage_path=storage_path),
        principal_from_claims_fn=lambda _claims: SimpleNamespace(subject="user-1"),
        provision_user_fn=AsyncMock(return_value=SimpleNamespace(id=42)),
        create_document_fn=lambda *_args, **_kwargs: _async_value(None),
        get_document_fn=lambda *_args, **_kwargs: _async_value({"document": {}}),
        delete_document_fn=delete_document_mock,
        create_job_fn=lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("job row failed")),
        tenant_name_resolver=lambda _sub: "tenant-user-1",
    )

    with pytest.raises(RuntimeError, match="job row failed"):
        await service.intake_upload(
            background_tasks=BackgroundTasks(),
            file=UploadFile(filename="paper.pdf", file=BytesIO(b"%PDF-1.7")),
            user={"sub": "user-1"},
        )

    assert list(tmp_path.rglob("*.pdf"))
    delete_document_mock.assert_not_awaited()
    assert cleanup_session.deleted == []
    assert cleanup_session.commit_calls == 0


@pytest.mark.asyncio
async def test_intake_upload_pre_commit_compensation_retains_weaviate_and_reports_on_persistent_unlink_failure(
    monkeypatch, tmp_path,
):
    import shutil
    from src.lib.document_cleanup import runtime as cleanup_runtime
    from src.lib.observability.sentry import hash_sentry_identifier

    session = _FakeSession(
        fail_commit_on_call=1,
        commit_error=RuntimeError("database unavailable"),
    )
    delete_document_mock = AsyncMock()
    report_mock = Mock()
    monkeypatch.setattr(cleanup_runtime, "report_runtime_exception", report_mock)
    monkeypatch.setattr(
        shutil,
        "rmtree",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError("locked")),
    )

    created_doc_ids: list[str] = []

    async def _create_document(_user_sub, doc):
        created_doc_ids.append(str(doc.id))
        return {"success": True}

    service = UploadIntakeService(
        upload_execution_service=_DispatchRecorder(),
        session_factory=lambda: session,
        storage_path_provider=lambda: tmp_path,
        upload_handler_factory=lambda storage_path: _UploadHandler(storage_path=storage_path),
        principal_from_claims_fn=lambda _claims: SimpleNamespace(subject="user-1"),
        provision_user_fn=AsyncMock(return_value=SimpleNamespace(id=42)),
        create_document_fn=_create_document,
        get_document_fn=lambda *_args, **_kwargs: _async_value({"document": {}}),
        delete_document_fn=delete_document_mock,
        create_job_fn=lambda **_kwargs: SimpleNamespace(job_id="job-1"),
        tenant_name_resolver=lambda _sub: "tenant-user-1",
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        await service.intake_upload(
            background_tasks=BackgroundTasks(),
            file=UploadFile(filename="paper.pdf", file=BytesIO(b"%PDF-1.7")),
            user={"sub": "user-1"},
        )

    assert len(created_doc_ids) == 1
    doc_id = created_doc_ids[0]
    assert list(tmp_path.rglob("*.pdf"))
    delete_document_mock.assert_not_awaited()
    report_mock.assert_called_once()
    (err,) = report_mock.call_args.args
    assert err.__cause__ is None and err.__context__ is None
    assert report_mock.call_args.kwargs == {
        "component": "pdf_jobs",
        "operation": "intake_compensation_file_cleanup_failed",
        "context": {
            "document_id_hash": hash_sentry_identifier(doc_id),
            "sql_committed": False,
            "weaviate_retained": True,
        },
        "fingerprint": ["intake_compensation_file_cleanup_failed", "pdf_jobs", "pre_commit"],
        "include_request": False,
    }
    assert doc_id not in repr(report_mock.call_args)
    assert str(tmp_path) not in repr(report_mock.call_args)


@pytest.mark.asyncio
@pytest.mark.parametrize("competing_mode", ["lock_held", "active_job"])
async def test_intake_upload_job_creation_compensation_skips_when_competing_supplement_add_owns_paper(
    monkeypatch, tmp_path, competing_mode, caplog,
):
    from src.lib.document_files.paper_lock import paper_lock_key

    report = Mock()
    monkeypatch.setattr(runtime, "report_runtime_exception", report)

    initial_session = _FakeSession()
    persisted_record = SimpleNamespace(id=uuid4())
    cleanup_session = _FakeSession(execute_row=persisted_record)
    sessions = [initial_session, cleanup_session]
    delete_document_mock = AsyncMock()
    created_doc_ids: list[str] = []

    async def _create_document(_user_sub, doc):
        created_doc_ids.append(str(doc.id))
        return {"success": True}

    def _fail_create_job(**kwargs):
        doc_id = str(kwargs["document_id"])
        if competing_mode == "lock_held":
            # Simulate a competing supplement add holding the paper advisory lock.
            _FakePaperLockConnection.held.add(paper_lock_key(doc_id))
        raise RuntimeError("job row failed")

    service = UploadIntakeService(
        upload_execution_service=_DispatchRecorder(),
        session_factory=lambda: sessions.pop(0),
        storage_path_provider=lambda: tmp_path,
        upload_handler_factory=lambda storage_path: _UploadHandler(storage_path=storage_path),
        principal_from_claims_fn=lambda _claims: SimpleNamespace(subject="user-1"),
        provision_user_fn=AsyncMock(return_value=SimpleNamespace(id=42)),
        create_document_fn=_create_document,
        get_document_fn=lambda *_args, **_kwargs: _async_value({"document": {}}),
        delete_document_fn=delete_document_mock,
        create_job_fn=_fail_create_job,
        tenant_name_resolver=lambda _sub: "tenant-user-1",
        get_active_job_fn=(
            (lambda **_kwargs: SimpleNamespace(status="pending", metadata={"job_kind": "supplement_add"}))
            if competing_mode == "active_job"
            else (lambda **_kwargs: None)
        ),
    )

    try:
        with pytest.raises(RuntimeError, match="job row failed"):
            await service.intake_upload(
                background_tasks=BackgroundTasks(),
                file=UploadFile(filename="paper.pdf", file=BytesIO(b"%PDF-1.7")),
                user={"sub": "user-1"},
            )
    finally:
        if competing_mode == "lock_held" and created_doc_ids:
            _FakePaperLockConnection.held.discard(paper_lock_key(created_doc_ids[0]))

    # Compensation must never delete files, Weaviate, or SQL underneath a competing add.
    assert list(tmp_path.rglob("*.pdf"))
    delete_document_mock.assert_not_awaited()
    assert cleanup_session.deleted == []
    assert cleanup_session.commit_calls == 0
    report.assert_not_called()
    assert not caplog.records


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled", [False, True])
async def test_intake_upload_job_creation_compensation_retains_owner_on_incomplete_wipe(
    monkeypatch, tmp_path, cancelled,
):
    import asyncio
    from contextlib import contextmanager
    import threading
    from unittest.mock import MagicMock

    from src.lib.weaviate_client import documents as weaviate_docs

    wipe_entered = threading.Event()
    resume_wipe = threading.Event()

    @contextmanager
    def _session(*, caller_reports_failure):
        yield MagicMock()

    connection = MagicMock()
    connection.session.side_effect = _session
    chunks, papers = MagicMock(), MagicMock()

    def _delete_many(**_kwargs):
        wipe_entered.set()
        if cancelled:
            assert resume_wipe.wait(timeout=5)
        return SimpleNamespace(successful=0, matches=1, failed=1)

    chunks.data.delete_many.side_effect = _delete_many
    monkeypatch.setattr(weaviate_docs, "get_connection", lambda *, caller_reports_failure: connection)
    monkeypatch.setattr(weaviate_docs, "get_user_collections", lambda *_: (chunks, papers))
    delete_sql_row = MagicMock()
    monkeypatch.setattr(weaviate_docs, "_delete_sql_row", delete_sql_row)
    monkeypatch.setattr(weaviate_docs.runtime, "report_runtime_exception", MagicMock())

    initial_session = _FakeSession()
    persisted_record = SimpleNamespace(id=uuid4())
    snapshot_session = _FakeSession(execute_row=persisted_record)
    cleanup_session = _FakeSession(execute_row=persisted_record)
    sessions = [initial_session, snapshot_session, cleanup_session]

    service = UploadIntakeService(
        upload_execution_service=_DispatchRecorder(),
        session_factory=lambda: sessions.pop(0),
        storage_path_provider=lambda: tmp_path,
        upload_handler_factory=lambda storage_path: _UploadHandler(storage_path=storage_path),
        principal_from_claims_fn=lambda _claims: SimpleNamespace(subject="user-1"),
        provision_user_fn=AsyncMock(return_value=SimpleNamespace(id=42)),
        create_document_fn=lambda *_args, **_kwargs: _async_value(None),
        get_document_fn=lambda *_args, **_kwargs: _async_value({"document": {}}),
        delete_document_fn=weaviate_docs.delete_document,
        create_job_fn=lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("job row failed")),
        tenant_name_resolver=lambda _sub: "tenant-user-1",
    )

    coro = service.intake_upload(
        background_tasks=BackgroundTasks(),
        file=UploadFile(filename="paper.pdf", file=BytesIO(b"%PDF-1.7")),
        user={"sub": "user-1"},
    )
    if cancelled:
        task = asyncio.create_task(coro)
        try:
            assert await asyncio.to_thread(wipe_entered.wait, 5)
            task.cancel("cancelled during incomplete upload compensation wipe")
            await asyncio.sleep(0.02)
            assert not task.done()
        finally:
            resume_wipe.set()
        with pytest.raises(asyncio.CancelledError, match="cancelled during incomplete upload compensation wipe"):
            await asyncio.wait_for(task, timeout=5)
    else:
        with pytest.raises(RuntimeError, match="job row failed"):
            await coro

    papers.data.delete_by_id.assert_not_called()
    delete_sql_row.assert_not_called()
    assert cleanup_session.deleted == []
    assert cleanup_session.commit_calls == 0


@pytest.mark.asyncio
async def test_intake_upload_pre_commit_compensation_handles_stat_permission_error(monkeypatch, tmp_path):
    from pathlib import Path as RealPath
    from src.lib.document_cleanup import runtime as cleanup_runtime

    session = _FakeSession(
        fail_commit_on_call=1,
        commit_error=RuntimeError("database unavailable"),
    )
    delete_document_mock = AsyncMock()
    report_mock = Mock()
    monkeypatch.setattr(cleanup_runtime, "report_runtime_exception", report_mock)

    orig_exists = RealPath.exists

    def _deny_pdf_exists(self):
        if self.suffix == ".pdf":
            raise PermissionError("cannot stat pdf parent")
        return orig_exists(self)

    monkeypatch.setattr(RealPath, "exists", _deny_pdf_exists)

    service = UploadIntakeService(
        upload_execution_service=_DispatchRecorder(),
        session_factory=lambda: session,
        storage_path_provider=lambda: tmp_path,
        upload_handler_factory=lambda storage_path: _UploadHandler(storage_path=storage_path),
        principal_from_claims_fn=lambda _claims: SimpleNamespace(subject="user-1"),
        provision_user_fn=AsyncMock(return_value=SimpleNamespace(id=42)),
        create_document_fn=AsyncMock(return_value={"success": True}),
        get_document_fn=lambda *_args, **_kwargs: _async_value({"document": {}}),
        delete_document_fn=delete_document_mock,
        create_job_fn=lambda **_kwargs: SimpleNamespace(job_id="job-1"),
        tenant_name_resolver=lambda _sub: "tenant-user-1",
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        await service.intake_upload(
            background_tasks=BackgroundTasks(),
            file=UploadFile(filename="paper.pdf", file=BytesIO(b"%PDF-1.7")),
            user={"sub": "user-1"},
        )

    delete_document_mock.assert_not_awaited()
    report_mock.assert_called_once()
    assert report_mock.call_args.kwargs["operation"] == "intake_compensation_file_cleanup_failed"
    assert report_mock.call_args.kwargs["include_request"] is False


@pytest.mark.asyncio
async def test_intake_upload_committed_compensation_removes_live_renamed_sql_file_snapshot(tmp_path):
    persisted_records: list[object] = []

    class _TrackingSession(_FakeSession):
        def add(self, row):
            super().add(row)
            persisted_records.append(row)

        def execute(self, *_args, **_kwargs):
            return _ExecuteResult(persisted_records[0] if persisted_records else None)

    renamed_rel = "user-1/renamed_upload/renamed_paper.pdf"

    def _rename_and_fail_create_job(**_kwargs):
        # Simulate a concurrent rename moving the PDF and updating live PDFDocumentFile.file_path.
        original_pdfs = list(tmp_path.rglob("*.pdf"))
        assert len(original_pdfs) == 1
        target = tmp_path / renamed_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        original_pdfs[0].replace(target)
        persisted_records[0].files[0].file_path = renamed_rel
        raise RuntimeError("job row failed after rename")

    delete_document_mock = AsyncMock(return_value={"success": True, "postgres_deleted": True})
    service = UploadIntakeService(
        upload_execution_service=_DispatchRecorder(),
        session_factory=lambda: _TrackingSession(),
        storage_path_provider=lambda: tmp_path,
        upload_handler_factory=lambda storage_path: _UploadHandler(storage_path=storage_path),
        principal_from_claims_fn=lambda _claims: SimpleNamespace(subject="user-1"),
        provision_user_fn=AsyncMock(return_value=SimpleNamespace(id=42)),
        create_document_fn=AsyncMock(return_value={"success": True}),
        get_document_fn=lambda *_args, **_kwargs: _async_value({"document": {}}),
        delete_document_fn=delete_document_mock,
        create_job_fn=_rename_and_fail_create_job,
        tenant_name_resolver=lambda _sub: "tenant-user-1",
    )

    with pytest.raises(RuntimeError, match="job row failed after rename"):
        await service.intake_upload(
            background_tasks=BackgroundTasks(),
            file=UploadFile(filename="paper.pdf", file=BytesIO(b"%PDF-1.7")),
            user={"sub": "user-1"},
        )

    assert not (tmp_path / renamed_rel).exists()
    assert not list(tmp_path.rglob("*.pdf"))
    delete_document_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_committed_compensation_cancel_during_file_removal_drains_worker_and_cleanup_before_lock_release(
    monkeypatch, tmp_path,
):
    import asyncio
    import threading
    from src.lib.document_files import paper_lock

    events: list[str] = []
    worker_entered = threading.Event()
    resume_worker = threading.Event()

    orig_release = paper_lock._release

    def _tracked_release(conn, key):
        events.append("lock_unlocked")
        return orig_release(conn, key)

    monkeypatch.setattr(paper_lock, "_release", _tracked_release)

    persisted_records: list[object] = []

    class _TrackingSession(_FakeSession):
        def add(self, row):
            super().add(row)
            persisted_records.append(row)

        def execute(self, *_args, **_kwargs):
            return _ExecuteResult(persisted_records[0] if persisted_records else None)

    async def _delete_document(_user_sub, _doc_id, *, connection=None):
        assert isinstance(connection, _FakePaperLockConnection)
        events.append("weaviate_and_sql_done")
        return {"success": True, "postgres_deleted": True}

    service = UploadIntakeService(
        upload_execution_service=_DispatchRecorder(),
        session_factory=lambda: _TrackingSession(),
        storage_path_provider=lambda: tmp_path,
        upload_handler_factory=lambda storage_path: _UploadHandler(storage_path=storage_path),
        principal_from_claims_fn=lambda _claims: SimpleNamespace(subject="user-1"),
        provision_user_fn=AsyncMock(return_value=SimpleNamespace(id=42)),
        create_document_fn=AsyncMock(return_value={"success": True}),
        get_document_fn=lambda *_args, **_kwargs: _async_value({"document": {}}),
        delete_document_fn=_delete_document,
        create_job_fn=lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("job row failed")),
        tenant_name_resolver=lambda _sub: "tenant-user-1",
    )

    real_remove = service._remove_committed_paper_files

    def _blocking_remove(**kwargs):
        worker_entered.set()
        assert resume_worker.wait(timeout=5)
        result = real_remove(**kwargs)
        events.append("file_removal_done")
        return result

    service._remove_committed_paper_files = _blocking_remove  # type: ignore[method-assign]

    task = asyncio.create_task(
        service.intake_upload(
            background_tasks=BackgroundTasks(),
            file=UploadFile(filename="paper.pdf", file=BytesIO(b"%PDF-1.7")),
            user={"sub": "user-1"},
        )
    )
    try:
        assert await asyncio.to_thread(worker_entered.wait, 5)
        task.cancel("cancelled during file removal worker")
        await asyncio.sleep(0.02)
        assert not task.done()
        assert events == []
    finally:
        resume_worker.set()

    with pytest.raises(asyncio.CancelledError, match="cancelled during file removal worker"):
        await asyncio.wait_for(task, timeout=5)

    assert events == ["file_removal_done", "weaviate_and_sql_done", "lock_unlocked"]
    assert not list(tmp_path.rglob("*.pdf"))
    assert _FakePaperLockConnection.held == set()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_filename",
    ["../escape.pdf", "sub/paper.pdf", "..\\escape.pdf", "/etc/passwd.pdf", "bad\x00.pdf"],
)
async def test_intake_upload_rejects_filename_with_path_traversal_and_separators(
    tmp_path, bad_filename,
):
    handler_called = False

    def _unexpected_handler(_storage_path):
        nonlocal handler_called
        handler_called = True
        raise AssertionError("upload handler must not be called for unsafe filename")

    service = UploadIntakeService(
        upload_execution_service=_DispatchRecorder(),
        session_factory=lambda: _FakeSession(),
        storage_path_provider=lambda: tmp_path,
        upload_handler_factory=_unexpected_handler,
        principal_from_claims_fn=lambda _claims: SimpleNamespace(subject="user-1"),
        provision_user_fn=AsyncMock(return_value=SimpleNamespace(id=42)),
    )

    with pytest.raises(UploadIntakeValidationError, match="path separators|traversal"):
        await service.intake_upload(
            background_tasks=BackgroundTasks(),
            file=UploadFile(filename=bad_filename, file=BytesIO(b"%PDF-1.7")),
            user={"sub": "user-1"},
        )

    assert handler_called is False
    assert not list(tmp_path.rglob("*.pdf"))


@pytest.mark.asyncio
async def test_upload_handler_rejects_traversal_filename_before_writing(tmp_path):
    from src.lib.pipeline.upload import PDFUploadHandler

    user_root = tmp_path / "user-1"
    handler = PDFUploadHandler(storage_path=user_root)
    outside_sentinel = tmp_path / "keep.txt"
    outside_sentinel.write_text("do not delete")

    with pytest.raises(UploadError, match="safe PDF filename"):
        await handler.save_uploaded_pdf(
            UploadFile(filename="../escape.pdf", file=BytesIO(b"%PDF-1.7"))
        )

    assert outside_sentinel.exists()
    assert not list(tmp_path.rglob("*.pdf"))


def test_staged_cleanup_refuses_to_rmtree_outside_expected_paper_directory(tmp_path):
    doc_id = str(uuid4())
    unrelated_dir = tmp_path / "user-1" / "unrelated_dir"
    unrelated_dir.mkdir(parents=True)
    keep_file = unrelated_dir / "keep.txt"
    keep_file.write_text("important")
    outside_pdf = unrelated_dir / "evil.pdf"
    outside_pdf.write_bytes(b"%PDF-1.7")

    removed = UploadIntakeService._cleanup_saved_file_artifacts(
        outside_pdf,
        base_storage=tmp_path,
        user_sub="user-1",
        document_id=doc_id,
    )

    assert removed is False
    assert keep_file.exists()
    assert outside_pdf.exists()


def test_intake_upload_compensation_reports_one_sanitized_sentry_event_when_delete_document_sql_fails():
    import subprocess
    import sys
    import textwrap

    script = textwrap.dedent("""
        import asyncio
        from contextlib import contextmanager
        from io import BytesIO
        import logging
        import os
        from pathlib import Path
        import tempfile
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock
        from uuid import uuid4

        os.environ["SENTRY_CONTENT_REDACTION_ENABLED"] = "false"

        import sentry_sdk
        from sentry_sdk.integrations.logging import LoggingIntegration
        from fastapi import BackgroundTasks, UploadFile

        from src.lib.observability.sentry import before_send, hash_sentry_identifier
        from src.lib.pdf_jobs import upload_intake_service as module
        from src.lib.weaviate_client import documents as weaviate_docs
        import src.models.sql.database as sql_db

        doc_id = "33333333-4444-5555-6666-777777777777"
        secret_detail = "private-postgres-param-for-upload-comp"
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
            event["request"] = {"url": f"https://example.invalid/weaviate/documents/{doc_id}"}
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
        module.pdf_job_service.get_active_job_for_document = lambda **_kw: None
        seen_lock_conns = []
        def _open_lock_session(conn=None):
            if conn is not None:
                assert isinstance(conn, _LockConn)
                seen_lock_conns.append(conn)
            return None
        module.open_paper_lock_session = _open_lock_session

        @contextmanager
        def _w_session(*_a, **_kw):
            yield MagicMock()

        w_conn = MagicMock()
        w_conn.session.side_effect = _w_session
        chunks, papers = MagicMock(), MagicMock()
        chunks.data.delete_many.return_value = SimpleNamespace(successful=0, matches=0, failed=0)
        papers.data.delete_by_id.return_value = True
        weaviate_docs.get_connection = lambda *, caller_reports_failure: w_conn
        weaviate_docs.get_user_collections = lambda *_a: (chunks, papers)

        sql_delete_calls = 0

        def _failing_sql_row(*_a, **_kw):
            global sql_delete_calls
            assert isinstance(_kw.get("connection"), _LockConn)
            sql_delete_calls += 1
            raise RuntimeError(f"{secret_detail} {doc_id}")

        weaviate_docs._delete_sql_row = _failing_sql_row

        class _Session:
            def __init__(self, row=None):
                self._row = row
                self.deleted = []
            def execute(self, *_a, **_kw):
                return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: self._row))
            def add(self, _row):
                pass
            def delete(self, row):
                self.deleted.append(row)
            def commit(self):
                pass
            def rollback(self):
                pass
            def close(self):
                pass

        class _Handler:
            def __init__(self, storage_path):
                self.storage_path = storage_path
            async def save_uploaded_pdf(self, file):
                doc_dir = self.storage_path / doc_id
                doc_dir.mkdir(parents=True, exist_ok=True)
                p = doc_dir / file.filename
                p.write_bytes(b"%PDF-1.7")
                return p, SimpleNamespace(
                    id=doc_id,
                    filename=file.filename,
                    metadata=SimpleNamespace(checksum="chk-1", page_count=1),
                )

        async def _exercise():
            with tempfile.TemporaryDirectory() as tmp:
                persisted = SimpleNamespace(id=doc_id)
                fallback_session = _Session(persisted)
                sessions = [_Session(), _Session(persisted), fallback_session]
                svc = module.UploadIntakeService(
                    upload_execution_service=MagicMock(),
                    session_factory=lambda: sessions.pop(0),
                    storage_path_provider=lambda: Path(tmp),
                    upload_handler_factory=lambda sp: _Handler(sp),
                    principal_from_claims_fn=lambda _c: SimpleNamespace(subject="user-1"),
                    provision_user_fn=AsyncMock(return_value=SimpleNamespace(id=42)),
                    create_document_fn=AsyncMock(return_value={"success": True}),
                    get_document_fn=AsyncMock(return_value=None),
                    delete_document_fn=weaviate_docs.delete_document,
                    create_job_fn=lambda **_kw: (_ for _ in ()).throw(RuntimeError("job row failed")),
                    tenant_name_resolver=lambda _s: "tenant-1",
                )
                try:
                    await svc.intake_upload(
                        background_tasks=BackgroundTasks(),
                        file=UploadFile(filename="paper.pdf", file=BytesIO(b"%PDF-1.7")),
                        user={"sub": "user-1"},
                    )
                except RuntimeError as exc:
                    assert "job row failed" in str(exc)
                else:
                    raise AssertionError("expected RuntimeError")
                assert fallback_session.deleted == [], "must not attempt SQL delete a second time"

        asyncio.run(_exercise())

        assert sql_delete_calls == 1, f"expected 1 SQL delete attempt, got {sql_delete_calls}"
        assert len(events) == 1, f"expected 1 Sentry event, got {len(events)}: {events!r}"
        event = events[0]
        assert "request" not in event, f"request leaked: {event.get('request')!r}"
        assert doc_id not in repr(event), f"raw doc_id leaked: {event!r}"
        assert secret_detail not in repr(event), f"secret leaked: {event!r}"
        assert event["contexts"]["runtime_exception"] == {
            "component": "weaviate_client",
            "operation": "paper_delete_sql_cleanup_failed",
            "document_id_hash": hash_sentry_identifier(doc_id),
            "exception_type": "RuntimeError",
        }
    """)
    result = subprocess.run([sys.executable, "-c", script], text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.asyncio
async def test_committed_compensation_cancel_before_file_removal_submission_aborts_cleanly(
    tmp_path, monkeypatch,
):
    """Cancellation arriving after create_task schedules compensation but before file-worker submission deletes nothing."""
    import asyncio
    from contextlib import asynccontextmanager

    doc_id = str(uuid4())
    staged_dir = tmp_path / "user-1" / doc_id
    staged_dir.mkdir(parents=True)
    staged_pdf = staged_dir / "paper.pdf"
    staged_pdf.write_bytes(b"%PDF-1.7\n")

    lock_events: list[str] = []
    file_removal_calls = 0
    delete_doc_mock = AsyncMock()
    sql_delete_calls = 0

    @asynccontextmanager
    async def _lock(_doc_id):
        lock_events.append("acquired")
        try:
            yield object()
        finally:
            lock_events.append("released")

    service = UploadIntakeService(
        upload_execution_service=_DispatchRecorder(),
        session_factory=lambda: _FakeSession(),
        storage_path_provider=lambda: tmp_path,
        delete_document_fn=delete_doc_mock,
        paper_operation_lock_fn=_lock,
        get_active_job_fn=lambda **_kw: None,
    )

    def _unexpected_file_removal(**_kw):
        nonlocal file_removal_calls
        file_removal_calls += 1
        return True

    def _unexpected_sql_delete(**_kw):
        nonlocal sql_delete_calls
        sql_delete_calls += 1

    monkeypatch.setattr(service, "_remove_committed_paper_files", _unexpected_file_removal)
    monkeypatch.setattr(service, "_delete_committed_sql_row", _unexpected_sql_delete)

    task_holder: list[asyncio.Task] = []
    real_create_task = asyncio.create_task
    cancelled_at_guarded = False

    def _cancel_on_guarded_schedule(coro, *args, **kwargs):
        nonlocal cancelled_at_guarded
        created = real_create_task(coro, *args, **kwargs)
        if getattr(coro, "__name__", None) == "_guarded" and not cancelled_at_guarded and task_holder:
            cancelled_at_guarded = True
            task_holder[0].cancel("cancelled before file-worker submission")
        return created

    monkeypatch.setattr("src.lib.document_cleanup.asyncio.create_task", _cancel_on_guarded_schedule)

    comp_task = real_create_task(
        service._compensate_job_creation_failure(
            user_sub="user-1",
            document_id=doc_id,
            saved_path=staged_pdf,
            weaviate_document_created=True,
        )
    )
    task_holder.append(comp_task)

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(comp_task, timeout=5)

    assert cancelled_at_guarded is True
    assert file_removal_calls == 0
    assert delete_doc_mock.await_count == 0
    assert sql_delete_calls == 0
    assert staged_pdf.exists()
    assert lock_events == ["acquired", "released"]


def test_staged_cleanup_refuses_symlinked_owner_directory_pointing_outside_storage(tmp_path):
    """A symlinked owner directory escaping base_storage must never be traversed or removed."""
    from src.lib.document_files.storage_paths import StoredPathOutsideOwnerStorage, owner_storage_path

    base_storage = tmp_path / "storage"
    base_storage.mkdir()
    outside_target = tmp_path / "outside"
    doc_id = str(uuid4())
    outside_doc_dir = outside_target / doc_id
    outside_doc_dir.mkdir(parents=True)
    outside_pdf = outside_doc_dir / "paper.pdf"
    outside_pdf.write_bytes(b"%PDF-1.7\n")
    outside_keep = outside_doc_dir / "keep.txt"
    outside_keep.write_text("do not delete")

    owner_link = base_storage / "user-1"
    owner_link.symlink_to(outside_target, target_is_directory=True)
    staged_via_link = owner_link / doc_id / "paper.pdf"

    with pytest.raises(StoredPathOutsideOwnerStorage):
        owner_storage_path(staged_via_link, base_storage, "user-1")

    removed = UploadIntakeService._cleanup_saved_file_artifacts(
        staged_via_link,
        base_storage=base_storage,
        user_sub="user-1",
        document_id=doc_id,
    )

    assert removed is False
    assert outside_pdf.exists()
    assert outside_keep.exists()


@pytest.mark.asyncio
async def test_duplicate_upload_staged_cleanup_failure_reports_sanitized_sentry_event(
    tmp_path, monkeypatch,
):
    """Duplicate-paper staged cleanup failure reports once via sanitized Sentry reporter while raising duplicate."""
    from datetime import datetime, timezone
    from src.lib.observability.sentry import hash_sentry_identifier
    from src.lib.pdf_jobs import upload_intake_service as module

    doc_id = str(uuid4())
    existing_doc = SimpleNamespace(
        id=uuid4(),
        filename="existing.pdf",
        status="completed",
        upload_timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    report_mock = Mock(return_value=True)
    monkeypatch.setattr("src.lib.document_cleanup.runtime.report_runtime_exception", report_mock)
    monkeypatch.setattr(module.UploadIntakeService, "_cleanup_saved_file_artifacts", classmethod(lambda cls, *_a, **_kw: False))

    class _FixedIdHandler:
        def __init__(self, storage_path: Path):
            self.storage_path = storage_path

        async def save_uploaded_pdf(self, file):
            doc_dir = self.storage_path / doc_id
            doc_dir.mkdir(parents=True, exist_ok=True)
            path = doc_dir / file.filename
            path.write_bytes(b"%PDF-1.7")
            return path, SimpleNamespace(
                id=doc_id,
                filename=file.filename,
                metadata=SimpleNamespace(checksum="dup-chk-1", page_count=1),
            )

    service = UploadIntakeService(
        upload_execution_service=_DispatchRecorder(),
        session_factory=lambda: _FakeSession(execute_row=existing_doc),
        storage_path_provider=lambda: tmp_path,
        upload_handler_factory=lambda sp: _FixedIdHandler(sp),
        principal_from_claims_fn=lambda _claims: SimpleNamespace(subject="user-1"),
        provision_user_fn=AsyncMock(return_value=SimpleNamespace(id=42)),
        get_document_fn=AsyncMock(return_value={"document": {"id": str(existing_doc.id)}}),
    )

    with pytest.raises(UploadIntakeDuplicateError):
        await service.intake_upload(
            background_tasks=BackgroundTasks(),
            file=UploadFile(filename="paper.pdf", file=BytesIO(b"%PDF-1.7")),
            user={"sub": "user-1"},
        )

    report_mock.assert_called_once()
    assert report_mock.call_args.kwargs == {
        "component": "pdf_jobs",
        "operation": "intake_compensation_file_cleanup_failed",
        "context": {
            "document_id_hash": hash_sentry_identifier(doc_id),
            "sql_committed": False,
            "weaviate_retained": False,
        },
        "fingerprint": ["intake_compensation_file_cleanup_failed", "pdf_jobs", "pre_commit"],
        "include_request": False,
    }


@pytest.mark.asyncio
async def test_provider_duplicate_staged_cleanup_failure_reports_sanitized_sentry_event(
    tmp_path, monkeypatch,
):
    """Provider-matched duplicate staged cleanup failure reports once while preserving UploadIntakeDuplicateError."""
    from datetime import datetime, timezone
    from src.lib.observability.sentry import hash_sentry_identifier
    from src.lib.pdf_jobs import upload_intake_service as module

    doc_id = str(uuid4())
    existing_doc = SimpleNamespace(
        id=uuid4(),
        filename="existing.pdf",
        upload_timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    report_mock = Mock(return_value=True)
    monkeypatch.setattr("src.lib.document_cleanup.runtime.report_runtime_exception", report_mock)
    monkeypatch.setattr(
        module.UploadIntakeService,
        "_cleanup_saved_file_artifacts",
        classmethod(lambda cls, *_a, **_kw: False),
    )

    source_artifact = SourceArtifact(
        provider="abc_literature",
        artifact_id="src-1",
        role=SourceArtifactRole.SOURCE_PDF,
        artifact_format=SourceArtifactFormat.PDF,
        status=SourceArtifactStatus.AVAILABLE,
        reference_id="101000001296197",
        reference_curie="AGRKB:101000001296197",
        display_name="paper.pdf",
        md5sum="d41d8cd98f00b204e9800998ecf8427e",
        access_policy=SourceAccessPolicy(scope=SourceAccessScope.GLOBAL),
    )
    converted_artifact = SourceArtifact(
        provider="abc_literature",
        artifact_id="conv-1",
        role=SourceArtifactRole.CONVERTED_TEXT,
        artifact_format=SourceArtifactFormat.MARKDOWN,
        status=SourceArtifactStatus.AVAILABLE,
        reference_id="101000001296197",
        reference_curie="AGRKB:101000001296197",
        display_name="paper.md",
        access_policy=SourceAccessPolicy(scope=SourceAccessScope.GLOBAL),
    )
    candidate = ChecksumImportCandidate(
        source_artifact=source_artifact,
        converted_artifact=converted_artifact,
    )
    decision = ChecksumImportDecision(
        provider="abc_literature",
        checksum="d41d8cd98f00b204e9800998ecf8427e",
        status=ChecksumImportDecisionStatus.READY,
        selected=candidate,
        candidates=(candidate,),
        source_artifacts=(source_artifact,),
    )

    service = UploadIntakeService(
        upload_execution_service=_DispatchRecorder(),
        session_factory=lambda: _FakeSession(),
        storage_path_provider=lambda: tmp_path,
        document_source_import_enabled_fn=lambda: True,
        document_source_provider_factory=lambda: _FakeDocumentSourceProvider(),
        checksum_import_selector=AsyncMock(return_value=decision),
        find_existing_source_document_fn=lambda *_a, **_kw: existing_doc,
    )

    staged_pdf = tmp_path / "user-1" / doc_id / "paper.pdf"
    staged_pdf.parent.mkdir(parents=True, exist_ok=True)
    staged_pdf.write_bytes(b"%PDF-1.7\n")

    with pytest.raises(UploadIntakeDuplicateError):
        await service._select_provider_checksum_import_or_raise(
            checksum="d41d8cd98f00b204e9800998ecf8427e",
            document_source_context=DocumentSourceRequestContext(
                bearer_kind=ProviderBearerKind.CURATOR,
                provider_groups=("FBStaff",),
                authorized_group_ids=("FB",),
                curator_token="curator-token",
            ),
            saved_path=staged_pdf,
            session=_FakeSession(),
            owner_user_id=42,
            base_storage=tmp_path,
            user_sub="user-1",
            document_id=doc_id,
        )

    report_mock.assert_called_once()
    assert report_mock.call_args.kwargs == {
        "component": "pdf_jobs",
        "operation": "intake_compensation_file_cleanup_failed",
        "context": {
            "document_id_hash": hash_sentry_identifier(doc_id),
            "sql_committed": False,
            "weaviate_retained": False,
        },
        "fingerprint": ["intake_compensation_file_cleanup_failed", "pdf_jobs", "pre_commit"],
        "include_request": False,
    }


class TestIntakeCompensation(IntakeCompensationContract):
    component = "pdf_jobs"

    async def run_intake(self, tmp_path, **dependencies):
        service = UploadIntakeService(
            upload_execution_service=_DispatchRecorder(),
            storage_path_provider=lambda: tmp_path,
            upload_handler_factory=lambda storage_path: _UploadHandler(storage_path=storage_path),
            principal_from_claims_fn=lambda claims: SimpleNamespace(subject=claims["sub"]),
            provision_user_fn=AsyncMock(return_value=SimpleNamespace(id=42)),
            tenant_name_resolver=lambda _sub: "tenant-user-1",
            **dependencies,
        )
        await service.intake_upload(
            background_tasks=BackgroundTasks(),
            file=UploadFile(filename="paper.pdf", file=BytesIO(b"%PDF-1.7")),
            user={"sub": "user-1"},
        )

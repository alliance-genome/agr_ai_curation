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

from src.lib.document_sources.access import DocumentSourceRequestContext
from src.lib.document_sources.import_selection import (
    ChecksumImportCandidate,
    ChecksumImportDecision,
    ChecksumImportDecisionStatus,
)
from src.lib.document_sources.models import (
    DocumentSourceHealth,
    SourceAccessPolicy,
    SourceAccessScope,
    SourceArtifact,
    SourceArtifactFormat,
    SourceArtifactRole,
    SourceArtifactStatus,
    SourceReference,
)
from src.lib.pdf_jobs import upload_intake_service as upload_intake_module
from src.lib.pdf_limits import (
    MAX_PDF_FILE_SIZE_BYTES,
    MAX_PDF_FILE_SIZE_MB,
    pdf_file_size_limit_message,
)
from src.lib.pdf_jobs.upload_intake_service import (
    UploadIntakeDuplicateError,
    UploadIntakeProviderDecisionError,
    UploadIntakeService,
    UploadIntakeValidationError,
)
from src.lib.pipeline.upload import UploadError

FAKE_UPLOAD_MD5 = "f87357c6cdc4f067" + "e19f42aebabc6fb7"


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
        "get_abc_literature_import_enabled",
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
        provision_user_fn=lambda *_args, **_kwargs: SimpleNamespace(id=42),
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
        provision_user_fn=lambda *_args, **_kwargs: SimpleNamespace(id=42),
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
        provision_user_fn=lambda *_args, **_kwargs: SimpleNamespace(id=42),
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
    assert record.viewer_mode is None
    assert record.source_provider is None
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
            mods=("MGI",),
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
        provision_user_fn=lambda *_args, **_kwargs: SimpleNamespace(id=42),
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
    assert record.viewer_mode == "local_pdf"
    assert record.file_path.endswith("/paper.pdf")
    assert record.source_provider == "fake_provider"
    assert record.source_provider_converted_artifact_id == "markdown-1"
    assert record.source_import_status == "pending"
    assert record.source_access_scope == "restricted"
    assert record.source_access_mods == {"mods": ["MGI"]}
    assert dispatch.calls == []
    assert len(dispatch.provider_markdown_calls) == 1
    provider_request = dispatch.provider_markdown_calls[0]["request"]
    assert provider_request.converted_artifact_id == "markdown-1"
    assert provider_request.curator_token == "curator-token"
    assert provider_request.source_provenance["source_md5"] == FAKE_UPLOAD_MD5
    assert provider.closed is True
    user_dir = tmp_path / "user-1"
    assert user_dir.exists()
    assert (tmp_path / record.file_path).exists()


@pytest.mark.asyncio
async def test_intake_upload_provider_match_with_pending_conversion_does_not_dispatch_pdf_processing(tmp_path):
    session = _FakeSession()
    dispatch = _DispatchRecorder()
    provider = _FakeDocumentSourceProvider()
    provider.provider_id = "abc_literature"
    create_document_calls = []
    source_artifact = SourceArtifact(
        provider="abc_literature",
        artifact_id="source-pdf-1",
        role=SourceArtifactRole.SOURCE_PDF,
        artifact_format=SourceArtifactFormat.PDF,
        status=SourceArtifactStatus.AVAILABLE,
        reference_id="ref-1",
        reference_curie="AGRKB:101",
        md5sum=FAKE_UPLOAD_MD5,
        access_policy=SourceAccessPolicy(
            scope=SourceAccessScope.RESTRICTED,
            mods=("MGI",),
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
            metadata={"conversion_status": "running", "conversion_job_id": "job-abc"},
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
        provision_user_fn=lambda *_args, **_kwargs: SimpleNamespace(id=42),
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
            provider_groups=("MGIStaff",),
            authorized_group_ids=("MGI",),
            curator_token="curator-token",
        )
    )

    assert result.job_id == "job-ready"
    assert create_document_calls[0][1].source_provenance["provider"] == "abc_literature"
    assert len(session.added) == 1
    record = session.added[0]
    assert record.viewer_mode == "local_pdf"
    assert record.source_provider == "abc_literature"
    assert record.source_import_status == "pending"
    assert dispatch.provider_markdown_calls == []
    assert dispatch.calls == []
    assert len(dispatch.provider_conversion_calls) == 1
    conversion_request = dispatch.provider_conversion_calls[0]["request"]
    assert conversion_request.reference == "AGRKB:101"
    assert conversion_request.source_artifact_id == "source-pdf-1"
    assert conversion_request.curator_token == "curator-token"
    assert provider.closed is True
    user_dir = tmp_path / "user-1"
    assert user_dir.exists()
    assert (tmp_path / record.file_path).exists()


@pytest.mark.asyncio
async def test_intake_upload_abc_source_only_ready_does_not_dispatch_pdf_processing(tmp_path):
    session = _FakeSession()
    dispatch = _DispatchRecorder()
    provider = _FakeDocumentSourceProvider()
    provider.provider_id = "abc_literature"
    create_document_calls = []
    source_artifact = SourceArtifact(
        provider="abc_literature",
        artifact_id="source-pdf-1",
        role=SourceArtifactRole.SOURCE_PDF,
        artifact_format=SourceArtifactFormat.PDF,
        status=SourceArtifactStatus.AVAILABLE,
        reference_id="ref-1",
        reference_curie="AGRKB:101",
        md5sum=FAKE_UPLOAD_MD5,
        access_policy=SourceAccessPolicy(
            scope=SourceAccessScope.RESTRICTED,
            mods=("MGI",),
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
        provision_user_fn=lambda *_args, **_kwargs: SimpleNamespace(id=42),
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
                provider_groups=("MGIStaff",),
                authorized_group_ids=("MGI",),
                curator_token="curator-token",
            ),
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "document_source_no_converted_text"
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
        provision_user_fn=lambda *_args, **_kwargs: SimpleNamespace(id=42),
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
        provision_user_fn=lambda *_args, **_kwargs: SimpleNamespace(id=42),
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
async def test_intake_upload_file_size_constraint_error_maps_to_validation_error(tmp_path):
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

    with pytest.raises(UploadIntakeValidationError, match=f"{MAX_PDF_FILE_SIZE_MB} MB"):
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

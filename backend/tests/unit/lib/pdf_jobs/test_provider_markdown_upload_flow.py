"""Integration-style tests for provider-backed Markdown upload flow."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks, UploadFile

import src.lib.document_sources.ingestion as ingestion_module
from src.lib.document_sources.access import DocumentSourceRequestContext
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
from src.lib.pdf_jobs import upload_execution_service as execution_module
from src.lib.pdf_jobs.upload_execution_service import (
    ProviderMarkdownExecutionRequest,
    UploadExecutionService,
)
from src.lib.pdf_jobs.upload_intake_service import UploadIntakeService
from src.models.pipeline import ProcessingStage
from src.models.sql.pdf_processing_job import PdfJobStatus

FAKE_UPLOAD_MD5 = "ab2934ecd0f4b164" + "9839207786803998"


class _ExecuteResult:
    def __init__(self, row):
        self._row = row

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
    def __init__(self, *, execute_row=None):
        self.execute_row = execute_row
        self.added = []
        self.commit_calls = 0
        self.closed = False

    def execute(self, *_args, **_kwargs):
        return _ExecuteResult(self.execute_row)

    def add(self, row):
        self.added.append(row)

    def commit(self):
        self.commit_calls += 1

    def rollback(self):
        raise AssertionError("rollback should not be needed on the happy path")

    def close(self):
        self.closed = True


class _UploadHandler:
    def __init__(self, *, storage_path: Path, checksum: str):
        self.storage_path = storage_path
        self.checksum = checksum

    async def save_uploaded_pdf(self, file):
        doc_id = str(uuid4())
        doc_dir = self.storage_path / doc_id
        doc_dir.mkdir(parents=True, exist_ok=True)
        saved_path = doc_dir / file.filename
        saved_path.write_bytes(b"%PDF-1.7\ntransient local bytes")
        document = SimpleNamespace(
            id=doc_id,
            filename=file.filename,
            metadata=SimpleNamespace(checksum=self.checksum, page_count=4),
        )
        return saved_path, document


class _Tracker:
    def __init__(self):
        self.calls = []

    async def track_pipeline_progress(
        self,
        document_id,
        stage,
        progress_percentage=None,
        message=None,
    ):
        self.calls.append(
            {
                "document_id": document_id,
                "stage": stage,
                "progress_percentage": progress_percentage,
                "message": message,
            }
        )
        return None


class _FakeDocumentSourceProvider:
    provider_id = "mock_literature"

    def __init__(
        self,
        *,
        artifacts: list[SourceArtifact] | None = None,
        markdown_payload: bytes = b"",
    ):
        self.artifacts = artifacts or []
        self.markdown_payload = markdown_payload
        self.checksum_lookups = []
        self.downloads = []
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
        raise AssertionError(f"reference resolution should not run: {identifier}")

    async def list_artifacts(
        self,
        reference: SourceReference | str,
        *,
        request_bearer_token: str | None = None,
    ) -> list[SourceArtifact]:
        _ = request_bearer_token
        raise AssertionError(f"artifact listing should not run: {reference}")

    async def find_artifacts_by_checksum(
        self,
        checksum: str,
        *,
        request_bearer_token: str | None = None,
    ) -> list[SourceArtifact]:
        self.checksum_lookups.append(
            {"checksum": checksum, "request_bearer_token": request_bearer_token}
        )
        return self.artifacts

    async def download_artifact(
        self,
        artifact_id: str,
        *,
        request_bearer_token: str | None = None,
    ) -> bytes:
        self.downloads.append(
            {
                "artifact_id": artifact_id,
                "request_bearer_token": request_bearer_token,
            }
        )
        return self.markdown_payload

    async def health(self) -> DocumentSourceHealth:
        return DocumentSourceHealth(
            provider=self.provider_id,
            ok=True,
            message="ok",
        )


@pytest.mark.asyncio
async def test_upload_intake_ready_provider_markdown_runs_generic_ingestion(
    monkeypatch,
    tmp_path,
):
    checksum = FAKE_UPLOAD_MD5
    local_checksum = "sha256-provider-source"
    access_policy = SourceAccessPolicy(
        scope=SourceAccessScope.RESTRICTED,
        mods=("FAKE",),
    )
    source_artifact = SourceArtifact(
        provider="mock_literature",
        artifact_id="source-pdf-55",
        role=SourceArtifactRole.SOURCE_PDF,
        artifact_format=SourceArtifactFormat.PDF,
        status=SourceArtifactStatus.AVAILABLE,
        reference_id="mock-ref-55",
        reference_curie="MOCK:55",
        md5sum=checksum,
        access_policy=access_policy,
        metadata={
            "source_file_id": "source-file-55",
            "external_ids": {
                "doi": "10.5555/provider-flow",
                "token": "must-not-persist",
            },
        },
    )
    converted_artifact = SourceArtifact(
        provider="mock_literature",
        artifact_id="markdown-55",
        role=SourceArtifactRole.CONVERTED_TEXT,
        artifact_format=SourceArtifactFormat.MARKDOWN,
        status=SourceArtifactStatus.AVAILABLE,
        reference_id="mock-ref-55",
        reference_curie="MOCK:55",
        parent_artifact_id="source-pdf-55",
        access_policy=access_policy,
        metadata={
            "file_class": "semantic_text",
            "file_extension": "md",
        },
    )
    lookup_provider = _FakeDocumentSourceProvider(
        artifacts=[source_artifact, converted_artifact],
    )
    markdown = "# Provider Results\n\nSignal from provider converted text.\n"
    download_provider = _FakeDocumentSourceProvider(
        markdown_payload=markdown.encode("utf-8"),
    )
    providers = [lookup_provider, download_provider]

    def _provider_factory():
        assert providers, "provider factory called more often than expected"
        return providers.pop(0)

    def _orchestrator_factory(*_args, **_kwargs):
        raise AssertionError("provider Markdown import must not invoke PDFX")

    tracker = _Tracker()
    execution_service = UploadExecutionService(
        pipeline_tracker=cast(Any, tracker),
        orchestrator_factory=_orchestrator_factory,
        document_source_provider_factory=_provider_factory,
    )

    progress_updates = []
    completed_events = []
    failed_events = []
    ingestion_status_updates = []
    persisted_metadata = []
    stored_chunks = []
    source_markdown_writes = []
    processed_json_writes = []

    async def _require_owned_document(document_id, user_id, owner_user_id):
        assert document_id
        assert user_id == "user-provider"
        assert owner_user_id == 42

    async def _save_source_markdown(*, markdown, document_id, user_id):
        source_markdown_writes.append(
            {
                "markdown": markdown,
                "document_id": document_id,
                "user_id": user_id,
            }
        )
        return f"{user_id}/source_markdown/{document_id}.md"

    async def _save_processed_json(*, elements, document_id, user_id):
        processed_json_writes.append(
            {
                "elements": elements,
                "document_id": document_id,
                "user_id": user_id,
            }
        )
        return f"{user_id}/processed_json/{document_id}.json"

    async def _persist_ingestion_metadata(**kwargs):
        persisted_metadata.append(kwargs)

    async def _sync_sql_document_status(document_id, **kwargs):
        ingestion_status_updates.append({"document_id": document_id, **kwargs})

    async def _resolve_hierarchy(elements):
        return elements, None

    async def _chunk_document(elements, strategy, document_id):
        del strategy
        return [
            {
                "chunk_index": 0,
                "content": elements[0]["text"],
                "metadata": {"document_id": document_id},
            }
        ]

    async def _store_to_weaviate(chunks, document_id, weaviate_client, user_id):
        stored_chunks.append(
            {
                "chunks": chunks,
                "document_id": document_id,
                "weaviate_client": weaviate_client,
                "user_id": user_id,
            }
        )

    monkeypatch.setattr(execution_module, "get_connection", lambda: "weaviate-client")
    monkeypatch.setattr(execution_module.pdf_job_service, "get_job_by_id", lambda **_kwargs: None)
    monkeypatch.setattr(execution_module.pdf_job_service, "is_cancel_requested", lambda **_kwargs: False)
    monkeypatch.setattr(
        execution_module.pdf_job_service,
        "update_progress",
        lambda **kwargs: progress_updates.append(kwargs),
    )
    monkeypatch.setattr(
        execution_module.pdf_job_service,
        "mark_completed",
        lambda **kwargs: completed_events.append(kwargs),
    )
    monkeypatch.setattr(
        execution_module.pdf_job_service,
        "mark_failed",
        lambda **kwargs: failed_events.append(kwargs),
    )
    monkeypatch.setattr(execution_module.pdf_job_service, "mark_cancelled", lambda **_kwargs: None)
    monkeypatch.setattr(
        execution_module,
        "update_document_status",
        lambda *_args, **_kwargs: pytest.fail("document status should not fail-sync on success"),
    )
    monkeypatch.setattr(ingestion_module, "_validate_provider_markdown", lambda _markdown: [])
    monkeypatch.setattr(ingestion_module, "_require_owned_document", _require_owned_document)
    monkeypatch.setattr(ingestion_module, "_save_source_markdown", _save_source_markdown)
    monkeypatch.setattr(ingestion_module, "_save_processed_json", _save_processed_json)
    monkeypatch.setattr(ingestion_module, "_persist_ingestion_metadata", _persist_ingestion_metadata)
    monkeypatch.setattr(ingestion_module, "_store_hierarchy_metadata", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ingestion_module, "_sync_sql_document_status", _sync_sql_document_status)
    monkeypatch.setattr(
        "src.lib.pipeline.pdfx_parser.markdown_to_pipeline_elements",
        lambda text: [{"text": text, "page": 1, "metadata": {}}],
    )
    monkeypatch.setattr(
        "src.lib.pipeline.hierarchy_resolution.resolve_document_hierarchy",
        _resolve_hierarchy,
    )
    monkeypatch.setattr("src.lib.pipeline.chunk.chunk_parsed_document", _chunk_document)
    monkeypatch.setattr("src.lib.pipeline.store.store_to_weaviate", _store_to_weaviate)

    source_duplicate_checks = []
    created_documents = []
    session = _FakeSession()
    service = UploadIntakeService(
        upload_execution_service=execution_service,
        session_factory=lambda: session,
        storage_path_provider=lambda: tmp_path,
        upload_handler_factory=cast(
            Any,
            lambda storage_path: _UploadHandler(
                storage_path=storage_path,
                checksum=local_checksum,
            ),
        ),
        principal_from_claims_fn=lambda _claims: SimpleNamespace(subject="user-provider"),
        provision_user_fn=lambda *_args, **_kwargs: SimpleNamespace(id=42),
        create_document_fn=lambda user_sub, document: _async_value(
            created_documents.append((user_sub, document))
        ),
        get_document_fn=lambda *_args, **_kwargs: _async_value({"document": {}}),
        delete_document_fn=lambda *_args, **_kwargs: _async_value(None),
        create_job_fn=lambda **_kwargs: SimpleNamespace(job_id="job-provider-flow"),
        tenant_name_resolver=lambda _sub: "tenant-user-provider",
        document_source_import_enabled_fn=lambda: True,
        document_source_provider_factory=_provider_factory,
        find_existing_source_document_fn=lambda *_args, **kwargs: source_duplicate_checks.append(kwargs) or None,
    )

    background_tasks = BackgroundTasks()
    result = await service.intake_upload(
        background_tasks=background_tasks,
        file=UploadFile(filename="paper.pdf", file=BytesIO(b"%PDF-1.7")),
        user={"sub": "user-provider"},
        document_source_context=DocumentSourceRequestContext(
            provider_groups=("ExternalCurators",),
            authorized_group_ids=("FAKE",),
            curator_token="curator-token",
        ),
    )

    assert result.job_id == "job-provider-flow"
    assert result.weaviate_tenant == "tenant-user-provider"
    assert lookup_provider.checksum_lookups == [
        {"checksum": checksum, "request_bearer_token": "curator-token"}
    ]
    assert lookup_provider.downloads == []
    assert lookup_provider.closed is True
    assert source_duplicate_checks == [
        {
            "user_id": 42,
            "source_provider": "mock_literature",
            "reference_id": "mock-ref-55",
            "reference_curie": "MOCK:55",
            "converted_artifact_id": "markdown-55",
            "source_md5": checksum,
        }
    ]
    assert len(created_documents) == 1
    created_document = created_documents[0][1]
    assert created_document.source_provenance["provider"] == "mock_literature"
    assert created_document.source_provenance["external_ids"] == {
        "doi": "10.5555/provider-flow"
    }
    assert "token" not in created_document.source_provenance["external_ids"]

    assert len(session.added) == 1
    record = session.added[0]
    assert record.id.hex == result.document_id.replace("-", "")
    assert record.viewer_mode == "local_pdf"
    assert record.file_path == f"user-provider/{result.document_id}/paper.pdf"
    assert record.file_hash != local_checksum
    assert len(record.file_hash) == 64
    assert record.source_provider == "mock_literature"
    assert record.source_provider_reference_curie == "MOCK:55"
    assert record.source_provider_source_file_id == "source-file-55"
    assert record.source_provider_pdf_artifact_id == "source-pdf-55"
    assert record.source_provider_converted_artifact_id == "markdown-55"
    assert record.source_external_ids == {"doi": "10.5555/provider-flow"}
    assert record.source_file_class == "semantic_text"
    assert record.source_file_extension == "md"
    assert record.source_md5 == checksum
    assert record.source_access_scope == "restricted"
    assert record.source_access_mods == {"mods": ["FAKE"]}
    assert record.source_import_status == "pending"
    assert session.commit_calls == 1
    assert session.closed is True

    user_dir = tmp_path / "user-provider"
    assert user_dir.exists()
    assert (tmp_path / record.file_path).exists()
    assert len(background_tasks.tasks) == 1
    task = background_tasks.tasks[0]
    assert task.func == execution_service.execute_provider_markdown
    queued_request = cast(ProviderMarkdownExecutionRequest, task.args[0])
    assert queued_request.document_id == result.document_id
    assert queued_request.converted_artifact_id == "markdown-55"
    assert queued_request.curator_token == "curator-token"
    assert queued_request.source_provenance["provider"] == "mock_literature"

    await task.func(*task.args, **task.kwargs)

    assert download_provider.downloads == [
        {
            "artifact_id": "markdown-55",
            "request_bearer_token": "curator-token",
        }
    ]
    assert download_provider.closed is True
    assert providers == []
    assert source_markdown_writes == [
        {
            "markdown": markdown,
            "document_id": result.document_id,
            "user_id": "user-provider",
        }
    ]
    assert processed_json_writes[0]["document_id"] == result.document_id
    assert persisted_metadata[0]["document_id"] == result.document_id
    assert persisted_metadata[0]["owner_user_id"] == 42
    assert persisted_metadata[0]["source_provenance"]["provider"] == "mock_literature"
    assert persisted_metadata[0]["source_provenance"]["external_ids"] == {
        "doi": "10.5555/provider-flow"
    }
    assert "token" not in persisted_metadata[0]["source_provenance"]["external_ids"]
    assert persisted_metadata[0]["source_provenance"]["access_mods"] == {
        "mods": ["FAKE"]
    }
    assert persisted_metadata[0]["source_markdown_path"] == (
        f"user-provider/source_markdown/{result.document_id}.md"
    )
    assert stored_chunks == [
        {
                    "chunks": [
                        {
                            "chunk_index": 0,
                            "content": markdown,
                            "metadata": {"document_id": result.document_id},
                        }
                    ],
            "document_id": result.document_id,
            "weaviate_client": "weaviate-client",
            "user_id": "user-provider",
        }
    ]
    assert ingestion_status_updates[-1]["status"] == "completed"
    assert progress_updates[0]["stage"] == ProcessingStage.UPLOAD.value
    assert progress_updates[0]["status"] == PdfJobStatus.RUNNING.value
    assert progress_updates[1]["stage"] == ProcessingStage.PARSING.value
    assert completed_events == [
        {
            "job_id": "job-provider-flow",
            "message": "Processing completed",
        }
    ]
    assert failed_events == []
    assert tracker.calls[0]["stage"] == ProcessingStage.UPLOAD
    assert tracker.calls[-1]["stage"] == ProcessingStage.COMPLETED


def _async_value(value):
    async def _coro(*_args, **_kwargs):
        return value

    return _coro()

"""Unit tests for document-source identifier imports."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks

from src.lib.document_sources.access import DocumentSourceRequestContext
from src.lib.document_sources.identifier_import import (
    IdentifierImportService,
    ReferenceImportDecisionStatus,
    normalize_source_identifier,
    parse_source_identifier_batch,
    select_reference_import_candidate,
)
from src.lib.document_sources.models import (
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
from src.lib.pdf_jobs.upload_execution_service import (
    ProviderConversionExecutionRequest,
    ProviderMarkdownExecutionRequest,
    UploadExecutionRequest,
)


class _ExecuteResult:
    def __init__(self, row=None):
        self._row = row

    def scalars(self):
        return self

    def first(self):
        return self._row


class _FakeSession:
    def __init__(self, sessions):
        self.sessions = sessions
        self.sessions.append(self)
        self.added = []
        self.deleted = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def execute(self, *_args, **_kwargs):
        return _ExecuteResult()

    def add(self, row):
        self.added.append(row)

    def delete(self, row):
        self.deleted.append(row)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


class _DispatchRecorder:
    def __init__(self):
        self.upload_calls = []
        self.provider_markdown_calls = []
        self.provider_conversion_calls = []

    async def dispatch_upload_execution(self, *, background_tasks, request):
        self.upload_calls.append({"background_tasks": background_tasks, "request": request})

    async def dispatch_provider_markdown_execution(self, *, background_tasks, request):
        self.provider_markdown_calls.append(
            {"background_tasks": background_tasks, "request": request}
        )

    async def dispatch_provider_conversion_execution(self, *, background_tasks, request):
        self.provider_conversion_calls.append(
            {"background_tasks": background_tasks, "request": request}
        )


class _FakeProvider:
    provider_id = "fake_provider"

    def __init__(self, *, artifacts=None):
        self.artifacts = artifacts
        self.closed = False
        self.resolve_reference_calls = []
        self.download_calls = []
        self.list_artifact_calls = []

    async def aclose(self):
        self.closed = True

    async def resolve_reference(
        self,
        identifier: str,
        *,
        request_bearer_token: str | None = None,
    ) -> SourceReference:
        self.resolve_reference_calls.append(
            {"identifier": identifier, "request_bearer_token": request_bearer_token}
        )
        return SourceReference(
            provider=self.provider_id,
            reference_id="ref-123",
            reference_curie="AGRKB:123",
            title="Reference title",
            external_ids={"pmid": "123"},
        )

    async def list_artifacts(self, reference, *, request_bearer_token=None):
        self.list_artifact_calls.append(
            {"reference": reference, "request_bearer_token": request_bearer_token}
        )
        return list(self.artifacts or _ready_artifacts())

    async def find_artifacts_by_checksum(self, checksum: str, *, request_bearer_token=None):
        _ = checksum, request_bearer_token
        return []

    async def download_artifact(self, artifact_id: str, *, request_bearer_token=None):
        self.download_calls.append(
            {"artifact_id": artifact_id, "request_bearer_token": request_bearer_token}
        )
        return b"%PDF-1.7 fake provider pdf"

    async def health(self) -> DocumentSourceHealth:
        return DocumentSourceHealth(provider=self.provider_id, ok=True, message="ok")


class _FakeConversionProvider(_FakeProvider):
    def __init__(
        self,
        *,
        artifacts=None,
        conversion_result: SourceConversionResult,
        post_conversion_artifacts=None,
    ):
        super().__init__(artifacts=artifacts)
        self.conversion_result = conversion_result
        self.post_conversion_artifacts = post_conversion_artifacts
        self.request_conversion_calls = []

    async def request_conversion(
        self,
        reference,
        *,
        wait=False,
        request_bearer_token=None,
    ):
        self.request_conversion_calls.append(
            {
                "reference": reference,
                "wait": wait,
                "request_bearer_token": request_bearer_token,
            }
        )
        if self.post_conversion_artifacts is not None:
            self.artifacts = self.post_conversion_artifacts
        return self.conversion_result


def _ready_artifacts():
    source = SourceArtifact(
        provider="fake_provider",
        artifact_id="pdf-1",
        role=SourceArtifactRole.SOURCE_PDF,
        artifact_format=SourceArtifactFormat.PDF,
        status=SourceArtifactStatus.AVAILABLE,
        reference_id="ref-123",
        reference_curie="AGRKB:123",
        display_name="provider-paper.pdf",
        md5sum="source-md5",
        access_policy=SourceAccessPolicy(
            scope=SourceAccessScope.RESTRICTED,
            mods=("FB",),
        ),
    )
    converted = SourceArtifact(
        provider="fake_provider",
        artifact_id="md-1",
        role=SourceArtifactRole.CONVERTED_TEXT,
        artifact_format=SourceArtifactFormat.MARKDOWN,
        status=SourceArtifactStatus.UNKNOWN,
        reference_id="ref-123",
        reference_curie="AGRKB:123",
        display_name="provider-paper.md",
        parent_artifact_id="pdf-1",
        metadata={"file_class": "converted_merged_main", "file_extension": "md"},
    )
    return (source, converted)


def _source_artifact(
    *,
    artifact_id="pdf-1",
    provider="fake_provider",
    access_scope=SourceAccessScope.RESTRICTED,
    mods=("FB",),
):
    return SourceArtifact(
        provider=provider,
        artifact_id=artifact_id,
        role=SourceArtifactRole.SOURCE_PDF,
        artifact_format=SourceArtifactFormat.PDF,
        status=SourceArtifactStatus.AVAILABLE,
        reference_id="ref-123",
        reference_curie="AGRKB:123",
        display_name="provider-paper.pdf",
        md5sum="source-md5",
        access_policy=SourceAccessPolicy(scope=access_scope, mods=mods),
    )


def _converted_artifact(
    *,
    artifact_id,
    provider="fake_provider",
    parent_artifact_id="pdf-1",
    display_name="provider-paper.md",
    file_class="converted_merged_main",
    status=SourceArtifactStatus.AVAILABLE,
):
    return SourceArtifact(
        provider=provider,
        artifact_id=artifact_id,
        role=SourceArtifactRole.CONVERTED_TEXT,
        artifact_format=SourceArtifactFormat.MARKDOWN,
        status=status,
        reference_id="ref-123",
        reference_curie="AGRKB:123",
        display_name=display_name,
        parent_artifact_id=parent_artifact_id,
        metadata={"file_class": file_class, "file_extension": "md"},
    )


def test_normalize_source_identifier_accepts_supported_forms():
    assert normalize_source_identifier("123").normalized == "PMID:123"
    assert normalize_source_identifier("PMID:123").normalized == "PMID:123"
    assert normalize_source_identifier("PubMed ID 123").normalized == "PMID:123"
    assert normalize_source_identifier("agrkb:101").normalized == "AGRKB:101"
    assert normalize_source_identifier("abc:ABC-1").normalized == "ABC:ABC-1"


def test_normalize_source_identifier_rejects_unproven_forms():
    result = normalize_source_identifier("doi:10.123/example")

    assert result.normalized is None
    assert result.error


def test_parse_source_identifier_batch_enforces_limit():
    with pytest.raises(ValueError, match="At most 2 identifiers"):
        parse_source_identifier_batch("1,2,3", batch_limit=2)


@pytest.mark.asyncio
async def test_select_reference_import_candidate_selects_authorized_pdf_and_markdown():
    provider = _FakeProvider()

    decision = await select_reference_import_candidate(
        provider=provider,
        identifier="PMID:123",
        authorized_group_ids=("FB",),
        request_bearer_token="curator-token",
    )

    assert decision.status == ReferenceImportDecisionStatus.READY
    assert decision.selected is not None
    assert decision.selected.source_artifact.artifact_id == "pdf-1"
    assert decision.selected.converted_artifact.artifact_id == "md-1"
    assert provider.resolve_reference_calls == [
        {"identifier": "PMID:123", "request_bearer_token": "curator-token"}
    ]
    assert provider.list_artifact_calls[0]["request_bearer_token"] == "curator-token"


@pytest.mark.asyncio
async def test_select_reference_import_candidate_rejects_inaccessible_pdf():
    decision = await select_reference_import_candidate(
        provider=_FakeProvider(),
        identifier="PMID:123",
        authorized_group_ids=("WB",),
    )

    assert decision.status == ReferenceImportDecisionStatus.ACCESS_DENIED
    assert decision.selected is None


@pytest.mark.asyncio
async def test_select_reference_import_candidate_keeps_pdf_ready_without_matching_markdown():
    provider = _FakeProvider(
        artifacts=(
            _source_artifact(artifact_id="pdf-1"),
            _converted_artifact(artifact_id="md-other", parent_artifact_id="pdf-2"),
        )
    )

    decision = await select_reference_import_candidate(
        provider=provider,
        identifier="PMID:123",
        authorized_group_ids=("FB",),
    )

    assert decision.status == ReferenceImportDecisionStatus.READY
    assert decision.selected is not None
    assert decision.selected.source_artifact.artifact_id == "pdf-1"
    assert decision.selected.converted_artifact is None


@pytest.mark.asyncio
async def test_select_reference_import_candidate_requests_conversion_when_supported():
    provider = _FakeConversionProvider(
        artifacts=(
            _source_artifact(artifact_id="pdf-1"),
            _converted_artifact(artifact_id="md-other", parent_artifact_id="pdf-2"),
        ),
        conversion_result=SourceConversionResult(
            provider="fake_provider",
            status=SourceConversionStatus.RUNNING,
            reference_curie="AGRKB:123",
            job_id="job-abc",
        ),
    )

    decision = await select_reference_import_candidate(
        provider=provider,
        identifier="PMID:123",
        authorized_group_ids=("FB",),
        request_bearer_token="curator-token",
    )

    assert decision.status == ReferenceImportDecisionStatus.CONVERSION_RUNNING
    assert decision.metadata == {
        "conversion_status": "running",
        "conversion_job_id": "job-abc",
    }
    assert provider.request_conversion_calls == [
        {
            "reference": decision.reference,
            "wait": False,
            "request_bearer_token": "curator-token",
        }
    ]


@pytest.mark.asyncio
async def test_select_reference_import_candidate_uses_reference_nxml_after_conversion():
    source = _source_artifact(artifact_id="pdf-1")
    nxml_markdown = _converted_artifact(
        artifact_id="md-nxml",
        parent_artifact_id=None,
        display_name="provider_nxml.md",
        file_class="converted_merged_main",
    )
    provider = _FakeConversionProvider(
        artifacts=(source,),
        post_conversion_artifacts=(source, nxml_markdown),
        conversion_result=SourceConversionResult(
            provider="fake_provider",
            status=SourceConversionStatus.RUNNING,
            reference_curie="AGRKB:123",
            job_id="job-abc",
            converted_classes=("converted_merged_main",),
        ),
    )

    decision = await select_reference_import_candidate(
        provider=provider,
        identifier="PMID:123",
        authorized_group_ids=("FB",),
    )

    assert decision.status == ReferenceImportDecisionStatus.READY
    assert decision.selected is not None
    assert decision.selected.converted_artifact is not None
    assert decision.selected.converted_artifact.artifact_id == "md-nxml"


@pytest.mark.asyncio
async def test_select_reference_import_candidate_prefers_canonical_nxml_markdown():
    provider = _FakeProvider(
        artifacts=(
            _source_artifact(artifact_id="pdf-1"),
            _converted_artifact(
                artifact_id="md-tei",
                display_name="provider_tei.md",
                file_class="converted_merged_main",
            ),
            _converted_artifact(
                artifact_id="md-merged",
                display_name="provider_merged.md",
                file_class="converted_merged_main",
            ),
            _converted_artifact(
                artifact_id="md-nxml",
                display_name="provider_nxml.md",
                file_class="converted_merged_main",
            ),
        )
    )

    decision = await select_reference_import_candidate(
        provider=provider,
        identifier="PMID:123",
        authorized_group_ids=("FB",),
    )

    assert decision.status == ReferenceImportDecisionStatus.READY
    assert decision.selected is not None
    assert decision.selected.converted_artifact.artifact_id == "md-nxml"


@pytest.mark.asyncio
async def test_select_reference_import_candidate_does_not_select_abc_tei_only_markdown():
    source = _source_artifact(artifact_id="pdf-1", provider="abc_literature")
    provider = _FakeConversionProvider(
        artifacts=(
            source,
            _converted_artifact(
                artifact_id="md-tei",
                provider="abc_literature",
                display_name="provider_tei.md",
                file_class="converted_merged_main",
            ),
        ),
        conversion_result=SourceConversionResult(
            provider="abc_literature",
            status=SourceConversionStatus.RUNNING,
            reference_curie="AGRKB:123",
            job_id="job-abc",
        ),
    )
    provider.provider_id = "abc_literature"

    decision = await select_reference_import_candidate(
        provider=provider,
        identifier="PMID:123",
        authorized_group_ids=("FB",),
    )

    assert decision.status == ReferenceImportDecisionStatus.CONVERSION_RUNNING
    assert decision.selected is not None
    assert decision.selected.converted_artifact is None
    assert provider.request_conversion_calls == [
        {
            "reference": decision.reference,
            "wait": False,
            "request_bearer_token": None,
        }
    ]


@pytest.mark.asyncio
async def test_select_reference_import_candidate_blocks_ambiguous_post_conversion_markdown():
    source = _source_artifact(artifact_id="pdf-1", provider="abc_literature")
    provider = _FakeConversionProvider(
        artifacts=(source,),
        conversion_result=SourceConversionResult(
            provider="abc_literature",
            status=SourceConversionStatus.CONVERTED,
            reference_curie="AGRKB:123",
            job_id="job-abc",
            converted_classes=("converted_merged_main",),
        ),
        post_conversion_artifacts=(
            source,
            _converted_artifact(
                artifact_id="md-nxml-a",
                provider="abc_literature",
                display_name="provider_nxml.md",
                file_class="converted_merged_main",
            ),
            _converted_artifact(
                artifact_id="md-nxml-b",
                provider="abc_literature",
                display_name="provider_nxml.md",
                file_class="converted_merged_main",
            ),
        ),
    )
    provider.provider_id = "abc_literature"

    decision = await select_reference_import_candidate(
        provider=provider,
        identifier="PMID:123",
        authorized_group_ids=("FB",),
    )

    assert decision.status == ReferenceImportDecisionStatus.AMBIGUOUS_MATCH
    assert decision.selected is None
    assert decision.metadata["conversion_status"] == "converted"
    assert decision.metadata["match_count"] == 2
    assert provider.request_conversion_calls == [
        {
            "reference": decision.reference,
            "wait": False,
            "request_bearer_token": None,
        }
    ]


@pytest.mark.asyncio
async def test_select_reference_import_candidate_blocks_ambiguous_main_markdown():
    provider = _FakeProvider(
        artifacts=(
            _source_artifact(artifact_id="pdf-1"),
            _converted_artifact(
                artifact_id="md-nxml-a",
                display_name="provider_a_nxml.md",
                file_class="converted_merged_main",
            ),
            _converted_artifact(
                artifact_id="md-nxml-b",
                display_name="provider_b_nxml.md",
                file_class="converted_merged_main",
            ),
        )
    )

    decision = await select_reference_import_candidate(
        provider=provider,
        identifier="PMID:123",
        authorized_group_ids=("FB",),
    )

    assert decision.status == ReferenceImportDecisionStatus.AMBIGUOUS_MATCH
    assert decision.selected is None
    assert decision.metadata == {"match_count": 2}


@pytest.mark.asyncio
async def test_identifier_import_service_returns_partial_success_and_dispatches_pdf_backed_import(tmp_path):
    sessions = []
    created_documents = []
    provider = _FakeProvider()
    dispatch_recorder = _DispatchRecorder()
    jobs = []

    async def _create_document(user_sub, document):
        created_documents.append((user_sub, document))
        return {"success": True, "document_id": document.id}

    def _create_job(**kwargs):
        jobs.append(kwargs)
        return SimpleNamespace(job_id=f"job-{len(jobs)}")

    service = IdentifierImportService(
        upload_execution_service=dispatch_recorder,
        session_factory=lambda: _FakeSession(sessions),
        storage_path_provider=lambda: tmp_path,
        principal_from_claims_fn=lambda claims: SimpleNamespace(subject=claims["sub"]),
        provision_user_fn=lambda _session, _principal: SimpleNamespace(id=42),
        provider_factory=lambda: provider,
        create_document_fn=_create_document,
        create_job_fn=_create_job,
        get_document_fn=lambda *_args, **_kwargs: None,
        delete_document_fn=lambda *_args, **_kwargs: None,
        find_existing_source_document_fn=lambda *_args, **_kwargs: None,
        import_batch_limit_provider=lambda: 10,
    )

    result = await service.import_identifiers(
        background_tasks=BackgroundTasks(),
        identifiers="123, doi:10.123/example",
        user={"sub": "user-1"},
        document_source_context=DocumentSourceRequestContext(
            provider_groups=("FBStaff",),
            authorized_group_ids=("FB",),
            curator_token=" curator-token ",
        ),
    )

    assert result.requested_count == 2
    assert result.imported_count == 1
    assert result.error_count == 1
    assert result.results[0].status == "imported"
    assert result.results[0].normalized_identifier == "PMID:123"
    assert result.results[1].status == "error"
    assert result.results[1].error_code == "invalid_identifier"
    assert created_documents[0][0] == "user-1"
    assert created_documents[0][1].source_provenance["viewer_mode"] == "local_pdf"

    added_records = [row for session in sessions for row in session.added]
    assert added_records
    assert added_records[0].viewer_mode == "local_pdf"
    assert added_records[0].file_path.endswith("/provider-paper.pdf")
    assert added_records[0].source_import_status == "pending"
    assert (tmp_path / added_records[0].file_path).exists()

    assert len(dispatch_recorder.provider_markdown_calls) == 1
    provider_request = dispatch_recorder.provider_markdown_calls[0]["request"]
    assert isinstance(provider_request, ProviderMarkdownExecutionRequest)
    assert provider_request.converted_artifact_id == "md-1"
    assert provider_request.curator_token == "curator-token"
    assert provider_request.source_provenance["viewer_mode"] == "local_pdf"
    assert provider.download_calls == [
        {"artifact_id": "pdf-1", "request_bearer_token": "curator-token"}
    ]
    assert provider.resolve_reference_calls[0]["request_bearer_token"] == "curator-token"
    assert provider.list_artifact_calls[0]["request_bearer_token"] == "curator-token"


@pytest.mark.asyncio
async def test_identifier_import_service_resolve_does_not_download_or_dispatch(tmp_path):
    sessions = []
    provider = _FakeConversionProvider(
        artifacts=(
            _source_artifact(artifact_id="pdf-1"),
            _converted_artifact(artifact_id="md-other", parent_artifact_id="pdf-2"),
        ),
        conversion_result=SourceConversionResult(
            provider="abc_literature",
            status=SourceConversionStatus.RUNNING,
            reference_curie="AGRKB:123",
            job_id="job-abc",
        ),
    )
    provider.provider_id = "abc_literature"
    dispatch_recorder = _DispatchRecorder()

    service = IdentifierImportService(
        upload_execution_service=dispatch_recorder,
        session_factory=lambda: _FakeSession(sessions),
        storage_path_provider=lambda: tmp_path,
        principal_from_claims_fn=lambda claims: SimpleNamespace(subject=claims["sub"]),
        provision_user_fn=lambda _session, _principal: SimpleNamespace(id=42),
        provider_factory=lambda: provider,
        find_existing_source_document_fn=lambda *_args, **_kwargs: None,
        import_batch_limit_provider=lambda: 10,
    )

    result = await service.resolve_identifiers(
        identifiers="123",
        user={"sub": "user-1"},
        document_source_context=DocumentSourceRequestContext(
            provider_groups=("FBStaff",),
            authorized_group_ids=("FB",),
            curator_token="curator-token",
        ),
    )

    assert result.requested_count == 1
    assert result.imported_count == 0
    assert result.error_count == 0
    assert result.results[0].status == "resolved"
    assert result.results[0].filename == "provider-paper.pdf"
    assert result.results[0].source_provenance["viewer_mode"] == "local_pdf"
    assert provider.request_conversion_calls == []
    assert provider.download_calls == []
    assert dispatch_recorder.provider_markdown_calls == []
    assert dispatch_recorder.provider_conversion_calls == []
    assert not any(session.added for session in sessions)


@pytest.mark.asyncio
async def test_identifier_resolve_cleans_phantom_provider_duplicate(tmp_path):
    sessions = []
    cleanup_calls = []
    provider = _FakeProvider()
    existing = SimpleNamespace(id=uuid4(), filename="stale-provider.pdf")

    async def _missing_document(*_args, **_kwargs):
        raise ValueError("missing from weaviate")

    service = IdentifierImportService(
        upload_execution_service=_DispatchRecorder(),
        session_factory=lambda: _FakeSession(sessions),
        storage_path_provider=lambda: tmp_path,
        principal_from_claims_fn=lambda claims: SimpleNamespace(subject=claims["sub"]),
        provision_user_fn=lambda _session, _principal: SimpleNamespace(id=42),
        provider_factory=lambda: provider,
        get_document_fn=_missing_document,
        find_existing_source_document_fn=lambda *_args, **_kwargs: existing,
        cleanup_document_dependencies_fn=lambda session, document_id: cleanup_calls.append(
            (session, document_id)
        ),
        import_batch_limit_provider=lambda: 10,
    )

    result = await service.resolve_identifiers(
        identifiers="123",
        user={"sub": "user-1"},
        document_source_context=DocumentSourceRequestContext(
            provider_groups=("FBStaff",),
            authorized_group_ids=("FB",),
            curator_token="curator-token",
        ),
    )

    assert result.results[0].status == "resolved"
    assert result.duplicate_count == 0
    assert cleanup_calls and cleanup_calls[0][1] == existing.id
    assert any(existing in session.deleted for session in sessions)
    assert provider.download_calls == []


@pytest.mark.asyncio
async def test_identifier_import_cleans_phantom_provider_duplicate_before_importing(tmp_path):
    sessions = []
    cleanup_calls = []
    created_documents = []
    provider = _FakeProvider()
    dispatch_recorder = _DispatchRecorder()
    existing = SimpleNamespace(id=uuid4(), filename="stale-provider.pdf")
    jobs = []

    async def _missing_document(*_args, **_kwargs):
        raise ValueError("missing from weaviate")

    async def _create_document(user_sub, document):
        created_documents.append((user_sub, document))
        return {"success": True, "document_id": document.id}

    def _create_job(**kwargs):
        jobs.append(kwargs)
        return SimpleNamespace(job_id=f"job-{len(jobs)}")

    service = IdentifierImportService(
        upload_execution_service=dispatch_recorder,
        session_factory=lambda: _FakeSession(sessions),
        storage_path_provider=lambda: tmp_path,
        principal_from_claims_fn=lambda claims: SimpleNamespace(subject=claims["sub"]),
        provision_user_fn=lambda _session, _principal: SimpleNamespace(id=42),
        provider_factory=lambda: provider,
        create_document_fn=_create_document,
        create_job_fn=_create_job,
        get_document_fn=_missing_document,
        delete_document_fn=lambda *_args, **_kwargs: None,
        find_existing_source_document_fn=lambda *_args, **_kwargs: existing,
        cleanup_document_dependencies_fn=lambda session, document_id: cleanup_calls.append(
            (session, document_id)
        ),
        import_batch_limit_provider=lambda: 10,
    )

    result = await service.import_identifiers(
        background_tasks=BackgroundTasks(),
        identifiers="123",
        user={"sub": "user-1"},
        document_source_context=DocumentSourceRequestContext(
            provider_groups=("FBStaff",),
            authorized_group_ids=("FB",),
            curator_token="curator-token",
        ),
    )

    assert result.results[0].status == "imported"
    assert result.imported_count == 1
    assert result.duplicate_count == 0
    assert cleanup_calls and cleanup_calls[0][1] == existing.id
    assert any(existing in session.deleted for session in sessions)
    assert created_documents
    assert provider.download_calls == [
        {"artifact_id": "pdf-1", "request_bearer_token": "curator-token"}
    ]


@pytest.mark.asyncio
async def test_identifier_import_service_imports_pdf_when_markdown_is_missing(tmp_path):
    sessions = []
    created_documents = []
    provider = _FakeProvider(
        artifacts=(
            _source_artifact(artifact_id="pdf-1"),
            _converted_artifact(artifact_id="md-other", parent_artifact_id="pdf-other"),
        )
    )
    dispatch_recorder = _DispatchRecorder()
    jobs = []

    async def _create_document(user_sub, document):
        created_documents.append((user_sub, document))
        return {"success": True, "document_id": document.id}

    def _create_job(**kwargs):
        jobs.append(kwargs)
        return SimpleNamespace(job_id=f"job-{len(jobs)}")

    service = IdentifierImportService(
        upload_execution_service=dispatch_recorder,
        session_factory=lambda: _FakeSession(sessions),
        storage_path_provider=lambda: tmp_path,
        principal_from_claims_fn=lambda claims: SimpleNamespace(subject=claims["sub"]),
        provision_user_fn=lambda _session, _principal: SimpleNamespace(id=42),
        provider_factory=lambda: provider,
        create_document_fn=_create_document,
        create_job_fn=_create_job,
        get_document_fn=lambda *_args, **_kwargs: None,
        delete_document_fn=lambda *_args, **_kwargs: None,
        find_existing_source_document_fn=lambda *_args, **_kwargs: None,
        import_batch_limit_provider=lambda: 10,
    )

    result = await service.import_identifiers(
        background_tasks=BackgroundTasks(),
        identifiers="123",
        user={"sub": "user-1"},
        document_source_context=DocumentSourceRequestContext(
            provider_groups=("FBStaff",),
            authorized_group_ids=("FB",),
            curator_token="curator-token",
        ),
    )

    assert result.imported_count == 1
    assert result.results[0].status == "imported"
    assert result.results[0].source_provenance["viewer_mode"] == "local_pdf"
    assert "converted_artifact_id" not in result.results[0].source_provenance
    assert created_documents[0][1].source_provenance["viewer_mode"] == "local_pdf"

    added_records = [row for session in sessions for row in session.added]
    assert added_records[0].viewer_mode == "local_pdf"
    assert added_records[0].source_provider_pdf_artifact_id == "pdf-1"
    assert added_records[0].source_provider_converted_artifact_id is None
    assert added_records[0].source_import_status is None

    assert dispatch_recorder.provider_markdown_calls == []
    assert len(dispatch_recorder.upload_calls) == 1
    upload_request = dispatch_recorder.upload_calls[0]["request"]
    assert isinstance(upload_request, UploadExecutionRequest)
    assert upload_request.file_path.exists()
    assert provider.download_calls == [
        {"artifact_id": "pdf-1", "request_bearer_token": "curator-token"}
    ]


@pytest.mark.asyncio
async def test_identifier_import_service_queues_abc_conversion_when_markdown_is_pending(tmp_path):
    sessions = []
    created_documents = []
    source = _source_artifact(artifact_id="pdf-1")
    provider = _FakeConversionProvider(
        artifacts=(source,),
        conversion_result=SourceConversionResult(
            provider="abc_literature",
            status=SourceConversionStatus.RUNNING,
            reference_curie="AGRKB:123",
            job_id="abc-job-1",
            per_file_progress=(
                {
                    "source": {
                        "display_name": "provider-paper.pdf",
                        "file_class": "main",
                        "referencefile_id": 12,
                    },
                    "converted": {
                        "display_name": "provider-paper_merged",
                        "file_class": "converted_merged_main",
                        "referencefile_id": None,
                    },
                    "status": "pending",
                    "error": None,
                },
            ),
        ),
    )
    provider.provider_id = "abc_literature"
    dispatch_recorder = _DispatchRecorder()
    jobs = []

    async def _create_document(user_sub, document):
        created_documents.append((user_sub, document))
        return {"success": True, "document_id": document.id}

    def _create_job(**kwargs):
        jobs.append(kwargs)
        return SimpleNamespace(job_id=f"job-{len(jobs)}")

    service = IdentifierImportService(
        upload_execution_service=dispatch_recorder,
        session_factory=lambda: _FakeSession(sessions),
        storage_path_provider=lambda: tmp_path,
        principal_from_claims_fn=lambda claims: SimpleNamespace(subject=claims["sub"]),
        provision_user_fn=lambda _session, _principal: SimpleNamespace(id=42),
        provider_factory=lambda: provider,
        create_document_fn=_create_document,
        create_job_fn=_create_job,
        get_document_fn=lambda *_args, **_kwargs: None,
        delete_document_fn=lambda *_args, **_kwargs: None,
        find_existing_source_document_fn=lambda *_args, **_kwargs: None,
        import_batch_limit_provider=lambda: 10,
    )

    result = await service.import_identifiers(
        background_tasks=BackgroundTasks(),
        identifiers="123",
        user={"sub": "user-1"},
        document_source_context=DocumentSourceRequestContext(
            provider_groups=("FBStaff",),
            authorized_group_ids=("FB",),
            curator_token="curator-token",
        ),
    )

    assert result.imported_count == 1
    assert result.results[0].status == "imported"
    assert result.results[0].source_provenance["provider"] == "abc_literature"
    assert "converted_artifact_id" not in result.results[0].source_provenance
    added_records = [row for session in sessions for row in session.added]
    assert added_records[0].source_provider == "abc_literature"
    assert added_records[0].source_provider_converted_artifact_id is None
    assert added_records[0].source_import_status == "pending"
    assert dispatch_recorder.upload_calls == []
    assert dispatch_recorder.provider_markdown_calls == []
    assert len(dispatch_recorder.provider_conversion_calls) == 1
    conversion_request = dispatch_recorder.provider_conversion_calls[0]["request"]
    assert isinstance(conversion_request, ProviderConversionExecutionRequest)
    assert conversion_request.reference == "AGRKB:123"
    assert conversion_request.source_artifact_id == "pdf-1"
    assert conversion_request.curator_token == "curator-token"


@pytest.mark.asyncio
async def test_identifier_import_service_rejects_missing_curator_token_before_provider_lookup():
    sessions = []
    provider_created = False

    def _provider_factory():
        nonlocal provider_created
        provider_created = True
        return _FakeProvider()

    service = IdentifierImportService(
        upload_execution_service=_DispatchRecorder(),
        session_factory=lambda: _FakeSession(sessions),
        principal_from_claims_fn=lambda claims: SimpleNamespace(subject=claims["sub"]),
        provision_user_fn=lambda _session, _principal: SimpleNamespace(id=42),
        provider_factory=_provider_factory,
        import_batch_limit_provider=lambda: 10,
    )

    result = await service.import_identifiers(
        background_tasks=BackgroundTasks(),
        identifiers="123",
        user={"sub": "user-1"},
        document_source_context=DocumentSourceRequestContext(
            provider_groups=("FBStaff",),
            authorized_group_ids=("FB",),
            curator_token="   ",
        ),
    )

    assert result.requested_count == 1
    assert result.error_count == 1
    assert result.results[0].status == "error"
    assert result.results[0].error_code == "document_source_curator_token_unavailable"
    assert provider_created is False


@pytest.mark.asyncio
async def test_identifier_import_service_cleans_phantom_hash_duplicate():
    sessions = []
    cleanup_calls = []

    async def _missing_document(*_args, **_kwargs):
        raise ValueError("missing from weaviate")

    service = IdentifierImportService(
        upload_execution_service=_DispatchRecorder(),
        session_factory=lambda: _FakeSession(sessions),
        get_document_fn=_missing_document,
        cleanup_document_dependencies_fn=lambda session, document_id: cleanup_calls.append(
            (session, document_id)
        ),
    )
    session = _FakeSession(sessions)
    existing = SimpleNamespace(id=uuid4())

    result = await service._resolve_phantom_duplicate(
        session,
        existing,
        user_sub="user-1",
    )

    assert result is None
    assert cleanup_calls == [(session, existing.id)]
    assert session.deleted == [existing]
    assert session.commits == 1

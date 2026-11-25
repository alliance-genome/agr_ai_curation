"""Unit tests for document-source identifier imports."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from unittest.mock import AsyncMock

import pytest
from fastapi import BackgroundTasks
from src.lib.document_sources.access import DocumentSourceRequestContext
from src.lib.document_sources.identifier_import import (
    IdentifierImportService,
    ReferenceImportCandidate,
    ReferenceImportDecision,
    ReferenceImportDecisionStatus,
    normalize_source_identifier,
    parse_source_identifier_batch,
    select_reference_import_candidate,
)
from src.lib.document_sources.models import (
    DocumentSourceAccessDenied,
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
from agr_ai_curation_alliance.document_sources.abc_literature import (
    ABCLiteratureDocumentSourceProvider,
)
from src.lib.pdf_jobs.upload_execution_service import (
    ProviderConversionExecutionRequest,
    ProviderMarkdownExecutionRequest,
    UploadExecutionRequest,
)
from src.lib.document_sources.models import ProviderBearerKind


class _FakePaperLockConnection:
    """In-memory PostgreSQL advisory lock simulator for import compensation tests."""

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
    from src.lib.document_sources import identifier_import as import_module

    _FakePaperLockConnection.held = set()
    monkeypatch.setattr(
        "src.models.sql.database.engine",
        SimpleNamespace(connect=lambda: _FakePaperLockConnection()),
        raising=False,
    )
    monkeypatch.setattr(
        import_module.pdf_job_service,
        "get_active_job_for_document",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        import_module.pdf_job_service,
        "mark_failed",
        lambda **_kwargs: None,
    )

    def _open_lock_session(conn=None):
        if conn is not None:
            assert isinstance(conn, _FakePaperLockConnection)
        return None

    monkeypatch.setattr(import_module, "open_paper_lock_session", _open_lock_session)
    monkeypatch.setattr(
        "src.lib.document_files.paper_lock.open_paper_lock_session",
        _open_lock_session,
    )
    yield _FakePaperLockConnection
    assert _FakePaperLockConnection.held == set(), "compensation left paper lock held"


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
        self.download_error: Exception | None = None
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
        if getattr(self, "download_error", None):
            raise self.download_error
        return b"%PDF-1.7 fake provider pdf"

    async def health(self) -> DocumentSourceHealth:
        return DocumentSourceHealth(provider=self.provider_id, ok=True, message="ok")

    def is_main_text_artifact(self, artifact: SourceArtifact) -> bool:
        return _fake_is_main_text_artifact(artifact, provider_id=self.provider_id)

    def main_text_artifact_sort_key(self, artifact: SourceArtifact) -> tuple[int, ...]:
        return _fake_main_text_artifact_sort_key(artifact, provider_id=self.provider_id)

    def is_reference_source_artifact(self, artifact: SourceArtifact) -> bool:
        return _fake_is_reference_source_artifact(artifact, provider_id=self.provider_id)

    def reference_source_artifact_sort_key(
        self,
        artifact: SourceArtifact,
        authorized_group_ids: tuple[str, ...] | list[str] | set[str],
    ) -> tuple[int, ...]:
        return _fake_reference_source_artifact_sort_key(
            artifact,
            provider_id=self.provider_id,
            authorized_group_ids=authorized_group_ids,
        )

    def conversion_exposes_main_text(self, result: SourceConversionResult) -> bool:
        return _fake_conversion_exposes_main_text(result, provider_id=self.provider_id)

    def provider_metadata_artifacts_for_source(self, source_artifact, artifacts):
        return tuple(
            artifact
            for artifact in artifacts
            if artifact.role is SourceArtifactRole.PROVIDER_METADATA
            and artifact.reference_curie == source_artifact.reference_curie
        )

    def supplement_file_category(self, artifact: SourceArtifact):
        from src.lib.document_sources.models import SupplementFileCategory

        if artifact.role is SourceArtifactRole.SOURCE_SUPPLEMENT:
            return SupplementFileCategory.CONVERTIBLE
        return None

    def is_listed_converted_supplement_text(self, artifact: SourceArtifact) -> bool:
        return artifact.role is SourceArtifactRole.CONVERTED_TEXT and bool(artifact.parent_artifact_id)

    def supplement_text_artifact_sort_key(self, artifact: SourceArtifact) -> tuple[int, ...]:
        del artifact
        return (0,)

    def converted_text_has_single_title(self, original: SourceArtifact) -> bool:
        del original
        return False


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


def _fake_is_main_text_artifact(
    artifact: SourceArtifact,
    *,
    provider_id: str,
) -> bool:
    file_class = str(artifact.metadata.get("file_class") or "").strip().lower()
    display_name = str(artifact.display_name or "").strip().lower()
    combined = f"{file_class} {display_name}"
    if provider_id == "abc_literature":
        return file_class == "converted_merged_main" and "_tei" not in display_name
    return "supplement" not in combined


def _fake_main_text_artifact_sort_key(
    artifact: SourceArtifact,
    *,
    provider_id: str,
) -> tuple[int, ...]:
    file_class = str(artifact.metadata.get("file_class") or "").strip().lower()
    display_name = str(artifact.display_name or "").strip().lower()
    combined = f"{file_class} {display_name}"
    if provider_id == "abc_literature":
        return (0,) if "_nxml" in display_name else (1,)
    if "_nxml" in combined or "nxml" in file_class:
        return (0,)
    if "_merged" in combined or "merged" in file_class:
        return (1,)
    if "tei" in combined:
        return (2,)
    return (10,)


def _fake_is_reference_source_artifact(
    artifact: SourceArtifact,
    *,
    provider_id: str,
) -> bool:
    if provider_id != "abc_literature":
        return True
    return artifact.metadata.get("file_class") == "main"


def _fake_reference_source_artifact_sort_key(
    artifact: SourceArtifact,
    *,
    provider_id: str,
    authorized_group_ids: tuple[str, ...] | list[str] | set[str],
) -> tuple[int, ...]:
    if provider_id != "abc_literature":
        return (0,)
    tier = (
        0
        if artifact.metadata.get("file_publication_status") == "final"
        and artifact.metadata.get("pdf_type") == "pdf"
        else 1
    )
    authorized = {str(group_id).strip().casefold() for group_id in authorized_group_ids}
    artifact_mods = {mod.strip().casefold() for mod in artifact.access_policy.group_ids}
    if (
        artifact.access_policy.scope is SourceAccessScope.RESTRICTED
        and authorized.intersection(artifact_mods)
    ):
        return (tier, 0)
    if artifact.access_policy.scope is SourceAccessScope.GLOBAL:
        return (tier, 1)
    return (tier, 10)


def _fake_conversion_exposes_main_text(
    result: SourceConversionResult,
    *,
    provider_id: str,
) -> bool:
    if provider_id != "abc_literature":
        return result.status in {
            SourceConversionStatus.CONVERTED,
            SourceConversionStatus.RUNNING,
        } and bool(result.converted_classes or result.per_file_progress)
    if "converted_merged_main" in result.converted_classes:
        return True
    for progress in result.per_file_progress:
        converted = progress.get("converted")
        if isinstance(converted, dict) and converted.get("file_class") == "converted_merged_main":
            return True
    return False


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
            group_ids=("FB",),
        ),
    )
    converted = SourceArtifact(
        provider="fake_provider",
        artifact_id="md-1",
        role=SourceArtifactRole.CONVERTED_TEXT,
        artifact_format=SourceArtifactFormat.MARKDOWN,
        status=SourceArtifactStatus.AVAILABLE,
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
    group_ids=("FB",),
    display_name="provider-paper.pdf",
    md5sum="source-md5",
    metadata=None,
):
    return SourceArtifact(
        provider=provider,
        artifact_id=artifact_id,
        role=SourceArtifactRole.SOURCE_PDF,
        artifact_format=SourceArtifactFormat.PDF,
        status=SourceArtifactStatus.AVAILABLE,
        reference_id="ref-123",
        reference_curie="AGRKB:123",
        display_name=display_name,
        md5sum=md5sum,
        access_policy=SourceAccessPolicy(scope=access_scope, group_ids=group_ids),
        # A canonical main PDF unless a test says otherwise.
        metadata=metadata
        or {"file_class": "main", "file_publication_status": "final", "pdf_type": "pdf"},
    )


def _converted_artifact(
    *,
    artifact_id,
    provider="fake_provider",
    parent_artifact_id: str | None = "pdf-1",
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


def _provider_metadata_artifact(*, artifact_id="figure-meta-1"):
    return SourceArtifact(
        provider="fake_provider",
        artifact_id=artifact_id,
        role=SourceArtifactRole.PROVIDER_METADATA,
        artifact_format=SourceArtifactFormat.JSON,
        status=SourceArtifactStatus.AVAILABLE,
        reference_id="ref-123",
        reference_curie="AGRKB:123",
        display_name="provider-paper_image_001",
        metadata={
            "file_class": "converted_main_figure_metadata",
            "file_extension": "json",
        },
    )


def test_normalize_source_identifier_accepts_generic_supported_forms():
    assert normalize_source_identifier("123").normalized == "PMID:123"
    assert normalize_source_identifier("PMID:123").normalized == "PMID:123"
    assert normalize_source_identifier("PubMed ID 123").normalized == "PMID:123"


def test_abc_provider_normalizes_abc_specific_identifier_forms():
    provider = object.__new__(ABCLiteratureDocumentSourceProvider)

    assert normalize_source_identifier("agrkb:101", provider=provider).normalized == "AGRKB:101"
    assert normalize_source_identifier("abc:ABC-1", provider=provider).normalized == "ABC:ABC-1"
    assert normalize_source_identifier("FBrf0223182", provider=provider).normalized == "FBrf0223182"


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
        bearer_kind=ProviderBearerKind.CURATOR,
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
        bearer_kind=ProviderBearerKind.CURATOR,
        provider=_FakeProvider(),
        identifier="PMID:123",
        authorized_group_ids=("WB",),
    )

    assert decision.status == ReferenceImportDecisionStatus.ACCESS_DENIED
    assert decision.selected is None


@pytest.mark.parametrize(
    ("pmid", "mod_pdf_id", "shared_pdf_id", "converted_id", "pmc_stem"),
    (
        ("41902664", "5013742", "5015962", "5020781", "PMC13232752.1"),
        ("41902692", "5013740", "5018839", "5020796", "PMC13232516.1"),
    ),
)
@pytest.mark.asyncio
async def test_select_reference_import_candidate_prefers_abc_mod_pdf_over_shared_pmc(
    pmid: str,
    mod_pdf_id: str,
    shared_pdf_id: str,
    converted_id: str,
    pmc_stem: str,
):
    provider = _FakeProvider(
        artifacts=(
            _source_artifact(
                artifact_id=mod_pdf_id,
                provider="abc_literature",
                group_ids=("FB",),
                display_name=pmid,
                md5sum="fb-pdf-md5",
                metadata={
                    "file_class": "main",
                    "file_publication_status": "final",
                    "pdf_type": "pdf",
                },
            ),
            _source_artifact(
                artifact_id=shared_pdf_id,
                provider="abc_literature",
                access_scope=SourceAccessScope.GLOBAL,
                group_ids=(),
                display_name=pmc_stem,
                md5sum="pmc-pdf-md5",
                metadata={
                    "file_class": "main",
                    "file_publication_status": "final",
                    "pdf_type": "pdf",
                },
            ),
            _converted_artifact(
                artifact_id=converted_id,
                provider="abc_literature",
                parent_artifact_id=None,
                display_name=f"{pmc_stem}_nxml",
                file_class="converted_merged_main",
            ),
        )
    )
    provider.provider_id = "abc_literature"

    decision = await select_reference_import_candidate(
        bearer_kind=ProviderBearerKind.CURATOR,
        provider=provider,
        identifier=f"PMID:{pmid}",
        authorized_group_ids=("FB",),
    )

    assert decision.status == ReferenceImportDecisionStatus.READY
    selected = decision.selected
    assert selected is not None
    assert selected.source_artifact.artifact_id == mod_pdf_id
    converted = selected.converted_artifact
    assert converted is not None
    assert converted.artifact_id == converted_id


@pytest.mark.asyncio
async def test_select_reference_import_candidate_keeps_true_abc_mod_tie_ambiguous():
    provider = _FakeProvider(
        artifacts=(
            _source_artifact(
                artifact_id="fb-pdf",
                provider="abc_literature",
                group_ids=("FB",),
                display_name="flybase-paper",
                md5sum="fb-pdf-md5",
                metadata={
                    "file_class": "main",
                    "file_publication_status": "final",
                    "pdf_type": "pdf",
                },
            ),
            _source_artifact(
                artifact_id="wb-pdf",
                provider="abc_literature",
                group_ids=("WB",),
                display_name="wormbase-paper",
                md5sum="wb-pdf-md5",
                metadata={
                    "file_class": "main",
                    "file_publication_status": "final",
                    "pdf_type": "pdf",
                },
            ),
            _source_artifact(
                artifact_id="pmc-pdf",
                provider="abc_literature",
                access_scope=SourceAccessScope.GLOBAL,
                group_ids=(),
                display_name="PMC-paper",
                md5sum="pmc-pdf-md5",
                metadata={
                    "file_class": "main",
                    "file_publication_status": "final",
                    "pdf_type": "pdf",
                },
            ),
        )
    )
    provider.provider_id = "abc_literature"

    decision = await select_reference_import_candidate(
        bearer_kind=ProviderBearerKind.CURATOR,
        provider=provider,
        identifier="PMID:123",
        authorized_group_ids=("FB", "WB"),
    )

    assert decision.status == ReferenceImportDecisionStatus.AMBIGUOUS_MATCH
    assert decision.selected is None
    assert decision.metadata == {"match_count": 2}


@pytest.mark.asyncio
async def test_select_reference_import_candidate_without_provider_preference_stays_ambiguous():
    class _ProviderWithoutSourcePreference:
        provider_id = "provider_without_preference"

        def is_reference_source_artifact(self, artifact):
            return True

        async def resolve_reference(self, identifier, *, request_bearer_token=None):
            _ = identifier, request_bearer_token
            return SourceReference(
                provider=self.provider_id,
                reference_id="ref-123",
                reference_curie="AGRKB:123",
            )

        async def list_artifacts(self, reference, *, request_bearer_token=None):
            _ = reference, request_bearer_token
            return [
                _source_artifact(artifact_id="pdf-1", provider=self.provider_id),
                _source_artifact(artifact_id="pdf-2", provider=self.provider_id),
            ]

    decision = await select_reference_import_candidate(
        bearer_kind=ProviderBearerKind.CURATOR,
        provider=_ProviderWithoutSourcePreference(),  # type: ignore[arg-type]
        identifier="PMID:123",
        authorized_group_ids=("FB",),
    )

    assert decision.status is ReferenceImportDecisionStatus.AMBIGUOUS_MATCH
    assert decision.selected is None
    assert decision.metadata == {"match_count": 2}


@pytest.mark.asyncio
async def test_select_reference_import_candidate_keeps_pdf_ready_without_matching_markdown():
    provider = _FakeProvider(
        artifacts=(
            _source_artifact(artifact_id="pdf-1"),
            _converted_artifact(artifact_id="md-other", parent_artifact_id="pdf-2"),
        )
    )

    decision = await select_reference_import_candidate(
        bearer_kind=ProviderBearerKind.CURATOR,
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
        bearer_kind=ProviderBearerKind.CURATOR,
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
        bearer_kind=ProviderBearerKind.CURATOR,
        provider=provider,
        identifier="PMID:123",
        authorized_group_ids=("FB",),
    )

    assert decision.status == ReferenceImportDecisionStatus.READY
    assert decision.selected is not None
    assert decision.selected.converted_artifact is not None
    assert decision.selected.converted_artifact.artifact_id == "md-nxml"


@pytest.mark.asyncio
async def test_select_reference_import_candidate_accepts_provider_mapped_readiness():
    source = _source_artifact(artifact_id="pdf-1", provider="abc_literature")
    markdown = _converted_artifact(
        artifact_id="md-1",
        provider="abc_literature",
        parent_artifact_id=None,
    )
    provider = _FakeConversionProvider(
        artifacts=(source,),
        post_conversion_artifacts=(source, markdown),
        conversion_result=SourceConversionResult(
            provider="abc_literature",
            status=SourceConversionStatus.RUNNING,
            converted_classes=("converted_merged_main",),
        ),
    )
    provider.provider_id = "abc_literature"

    decision = await select_reference_import_candidate(
        bearer_kind=ProviderBearerKind.CURATOR,
        provider=provider,
        identifier="PMID:123",
        authorized_group_ids=("FB",),
    )

    assert decision.status is ReferenceImportDecisionStatus.READY
    assert decision.selected is not None
    assert decision.selected.converted_artifact is markdown


@pytest.mark.asyncio
async def test_select_reference_import_candidate_rejects_explicit_unknown_markdown():
    provider = _FakeProvider(
        artifacts=(
            _source_artifact(artifact_id="pdf-1"),
            _converted_artifact(
                artifact_id="md-unknown",
                status=SourceArtifactStatus.UNKNOWN,
            ),
        )
    )

    decision = await select_reference_import_candidate(
        bearer_kind=ProviderBearerKind.CURATOR,
        provider=provider,
        identifier="PMID:123",
        authorized_group_ids=("FB",),
        allow_conversion_request=False,
    )

    assert decision.status is ReferenceImportDecisionStatus.READY
    assert decision.selected is not None
    assert decision.selected.converted_artifact is None
    assert "No converted main Markdown artifact" in decision.message


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
        bearer_kind=ProviderBearerKind.CURATOR,
        provider=provider,
        identifier="PMID:123",
        authorized_group_ids=("FB",),
    )

    assert decision.status == ReferenceImportDecisionStatus.READY
    assert decision.selected is not None
    assert decision.selected.converted_artifact.artifact_id == "md-nxml"


@pytest.mark.asyncio
async def test_select_reference_import_candidate_routes_abc_tei_only_main_to_conversion():
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
        bearer_kind=ProviderBearerKind.CURATOR,
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
        bearer_kind=ProviderBearerKind.CURATOR,
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
        bearer_kind=ProviderBearerKind.CURATOR,
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
    metadata = _provider_metadata_artifact()
    provider = _FakeProvider(artifacts=(*_ready_artifacts(), metadata))
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
        provision_user_fn=AsyncMock(return_value=SimpleNamespace(id=42)),
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
            bearer_kind=ProviderBearerKind.CURATOR,
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
    assert added_records[0].main_file.display_mode == "local_pdf"
    # Writer pin (P9a): a pending local PDF with a placeholder page count. The
    # viewer's per-file routes serve it only once the file is ready.
    assert (added_records[0].main_file.status, added_records[0].main_file.page_count) == ("pending", 1)
    assert added_records[0].document_kind == "library" and added_records[0].next_chunk_index == 0
    assert added_records[0].main_file.file_path.endswith("/provider-paper.pdf")
    assert added_records[0].main_file.source_import_status == "pending"
    assert (tmp_path / added_records[0].main_file.file_path).exists()

    assert len(dispatch_recorder.provider_markdown_calls) == 1
    provider_request = dispatch_recorder.provider_markdown_calls[0]["request"]
    assert isinstance(provider_request, ProviderMarkdownExecutionRequest)
    assert provider_request.converted_artifact_id == "md-1"
    assert provider_request.curator_token == "curator-token"
    assert provider_request.figure_metadata_artifact_ids == (metadata.artifact_id,)
    assert provider_request.source_provenance["viewer_mode"] == "local_pdf"
    assert provider_request.file_path == tmp_path / added_records[0].main_file.file_path
    assert provider.download_calls == [
        {"artifact_id": "pdf-1", "request_bearer_token": "curator-token"}
    ]
    assert provider.resolve_reference_calls[0]["request_bearer_token"] == "curator-token"
    assert provider.list_artifact_calls[0]["request_bearer_token"] == "curator-token"


@pytest.mark.asyncio
@pytest.mark.parametrize("operational", [False, True])
@pytest.mark.parametrize("reporter_broken", [False, True])
async def test_identifier_import_reports_source_pdf_download_access_denied(tmp_path, monkeypatch, caplog, operational, reporter_broken):
    from src.lib.document_sources import identifier_import as module
    from src.lib.document_sources.models import DocumentSourceError
    captures = []
    def capture(exc, **kwargs):
        captures.append((exc, kwargs))
        if reporter_broken:
            raise RuntimeError("reporter unavailable")
        return True
    monkeypatch.setattr(module, "report_runtime_exception", capture)
    sessions = []
    provider = _FakeProvider()
    provider.download_error = (DocumentSourceError if operational else DocumentSourceAccessDenied)(
        "Document-source artifact access was denied"
    )
    dispatch_recorder = _DispatchRecorder()

    service = IdentifierImportService(
        upload_execution_service=dispatch_recorder,
        session_factory=lambda: _FakeSession(sessions),
        storage_path_provider=lambda: tmp_path,
        principal_from_claims_fn=lambda claims: SimpleNamespace(subject=claims["sub"]),
        provision_user_fn=AsyncMock(return_value=SimpleNamespace(id=42)),
        provider_factory=lambda: provider,
        create_document_fn=lambda *_args, **_kwargs: None,
        create_job_fn=lambda **_kwargs: SimpleNamespace(job_id="unused"),
        get_document_fn=lambda *_args, **_kwargs: None,
        delete_document_fn=lambda *_args, **_kwargs: None,
        find_existing_source_document_fn=lambda *_args, **_kwargs: None,
        import_batch_limit_provider=lambda: 10,
    )

    result = await service.import_identifiers(
        background_tasks=BackgroundTasks(),
        identifiers="PMID:123",
        user={"sub": "user-1"},
        document_source_context=DocumentSourceRequestContext(
            bearer_kind=ProviderBearerKind.CURATOR,
            provider_groups=("FBStaff",),
            authorized_group_ids=("FB",),
            curator_token="curator-token",
        ),
    )

    assert result.error_count == 1
    assert result.results[0].status == "error"
    assert result.results[0].error_code == ("document_source_unavailable" if operational else "document_source_access_denied")
    assert len(captures) == int(operational)
    source_warnings = [record for record in caplog.records if record.name == module.logger.name]
    assert source_warnings
    assert all(getattr(record, "sentry_skip_event", False) for record in source_warnings)
    assert dispatch_recorder.provider_markdown_calls == []
    assert dispatch_recorder.provider_conversion_calls == []
    assert dispatch_recorder.upload_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("import_mode", [False, True])
async def test_reference_not_found_is_per_item_without_runtime_error(tmp_path, monkeypatch, import_mode):
    from src.lib.document_sources.models import DocumentSourceReferenceNotFound

    class MissingProvider(_FakeProvider):
        async def list_artifacts(self, reference, **kwargs):
            return []

        async def resolve_reference(self, identifier, **kwargs):
            if identifier == "PMID:123":
                raise DocumentSourceReferenceNotFound("Check the publication identifier or upload the PDF.")
            return await super().resolve_reference(identifier, **kwargs)

    provider = MissingProvider(artifacts=())
    reports = []
    monkeypatch.setattr("src.lib.document_sources.identifier_import._report_source_failure", lambda *args: reports.append(args))
    service = IdentifierImportService(
        upload_execution_service=_DispatchRecorder(),
        provider_factory=lambda: provider,
        find_existing_source_document_fn=lambda *_args, **_kwargs: None,
        session_factory=lambda: _FakeSession([]),
        storage_path_provider=lambda: tmp_path,
        principal_from_claims_fn=lambda claims: SimpleNamespace(subject=claims["sub"]),
        provision_user_fn=AsyncMock(return_value=SimpleNamespace(id=42)),
        import_batch_limit_provider=lambda: 10,
    )
    kwargs = dict(
        identifiers="123,456", user={"sub": "user-1"},
        document_source_context=DocumentSourceRequestContext(
            bearer_kind=ProviderBearerKind.CURATOR,
            provider_groups=("FBStaff",), authorized_group_ids=("FB",), curator_token="curator-token",
        ),
    )
    if import_mode:
        result = await service.import_identifiers(background_tasks=BackgroundTasks(), **kwargs)
    else:
        result = await service.resolve_identifiers(**kwargs)
    assert len(result.results) == 2
    assert result.results[0].error_code == "document_source_reference_not_found"
    assert "Check the publication" in result.results[0].message
    assert result.results[1].error_code == "document_source_no_source_artifact"
    assert reports == []


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
        provision_user_fn=AsyncMock(return_value=SimpleNamespace(id=42)),
        provider_factory=lambda: provider,
        find_existing_source_document_fn=lambda *_args, **_kwargs: None,
        import_batch_limit_provider=lambda: 10,
    )

    result = await service.resolve_identifiers(
        identifiers="123",
        user={"sub": "user-1"},
        document_source_context=DocumentSourceRequestContext(
            bearer_kind=ProviderBearerKind.CURATOR,
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
        provision_user_fn=AsyncMock(return_value=SimpleNamespace(id=42)),
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
            bearer_kind=ProviderBearerKind.CURATOR,
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
        provision_user_fn=AsyncMock(return_value=SimpleNamespace(id=42)),
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
            bearer_kind=ProviderBearerKind.CURATOR,
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
        provision_user_fn=AsyncMock(return_value=SimpleNamespace(id=42)),
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
            bearer_kind=ProviderBearerKind.CURATOR,
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
    assert added_records[0].main_file.display_mode == "local_pdf"
    assert added_records[0].main_file.source_provider_pdf_artifact_id == "pdf-1"
    assert added_records[0].main_file.source_provider_converted_artifact_id is None
    assert added_records[0].main_file.source_import_status is None

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
    metadata = _provider_metadata_artifact()
    provider = _FakeConversionProvider(
        artifacts=(source, metadata),
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
                    "figures": [
                        {
                            "display_name": "provider-paper_image_001",
                            "metadata_referencefile_id": "raw-bogus-meta",
                        }
                    ],
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
        provision_user_fn=AsyncMock(return_value=SimpleNamespace(id=42)),
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
            bearer_kind=ProviderBearerKind.CURATOR,
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
    assert added_records[0].main_file.source_provider_converted_artifact_id is None
    assert added_records[0].main_file.source_import_status == "pending"
    assert dispatch_recorder.upload_calls == []
    assert dispatch_recorder.provider_markdown_calls == []
    assert len(dispatch_recorder.provider_conversion_calls) == 1
    conversion_request = dispatch_recorder.provider_conversion_calls[0]["request"]
    assert isinstance(conversion_request, ProviderConversionExecutionRequest)
    assert conversion_request.reference == "AGRKB:123"
    assert conversion_request.source_artifact_id == "pdf-1"
    assert conversion_request.curator_token == "curator-token"
    assert conversion_request.figure_metadata_artifact_ids == (metadata.artifact_id,)


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
        provision_user_fn=AsyncMock(return_value=SimpleNamespace(id=42)),
        provider_factory=_provider_factory,
        import_batch_limit_provider=lambda: 10,
    )

    result = await service.import_identifiers(
        background_tasks=BackgroundTasks(),
        identifiers="123",
        user={"sub": "user-1"},
        document_source_context=DocumentSourceRequestContext(
            bearer_kind=ProviderBearerKind.CURATOR,
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


# The import, its resolve step and the file listing share one rule for "this paper can be imported".


def _gate_decision(status):
    reference = SourceReference(provider="fake_provider", reference_id="ref-123", reference_curie="AGRKB:123")
    candidate = ReferenceImportCandidate(reference=reference, source_artifact=_source_artifact(artifact_id="pdf-1"))
    return ReferenceImportDecision(
        status=status, provider="fake_provider", identifier="AGRKB:123", reference=reference, selected=candidate,
    )


class _ReachedDuplicateCheck(Exception):
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize("can_import", [True, False])
@pytest.mark.parametrize("step", ["import", "resolve"])
async def test_the_import_and_the_listing_read_one_can_import_rule(monkeypatch, tmp_path, can_import, step):
    """Flip ``ReferenceImportDecision.can_import`` alone: the import, its resolve step and the
    listing's whole-paper check all follow it, so they cannot disagree."""

    from fastapi import BackgroundTasks

    from src.lib.document_sources.file_listing import ListingRoute, _paper_refusal
    from src.lib.document_sources.file_reasons import FileReason

    monkeypatch.setattr(ReferenceImportDecision, "can_import", property(lambda self: can_import))
    # A status the rule would refuse on its own: only the shared predicate decides here.
    decision = _gate_decision(ReferenceImportDecisionStatus.NO_SOURCE_ARTIFACT)

    def duplicate_check(*_args, **_kwargs):
        raise _ReachedDuplicateCheck()

    sessions = []
    service = IdentifierImportService(
        upload_execution_service=_DispatchRecorder(),
        session_factory=lambda: _FakeSession(sessions),
        storage_path_provider=lambda: tmp_path,
        principal_from_claims_fn=lambda claims: SimpleNamespace(subject=claims["sub"]),
        provision_user_fn=AsyncMock(return_value=SimpleNamespace(id=42)),
        provider_factory=_FakeProvider,
        reference_import_selector=AsyncMock(return_value=decision),
        find_existing_source_document_fn=duplicate_check,
        import_batch_limit_provider=lambda: 10,
    )
    context = DocumentSourceRequestContext(
        bearer_kind=ProviderBearerKind.CURATOR, provider_groups=("FBStaff",), authorized_group_ids=("FB",),
        curator_token="curator-token",
    )
    call = (
        service.import_identifiers(
            background_tasks=BackgroundTasks(), identifiers="123", user={"sub": "user-1"},
            document_source_context=context,
        )
        if step == "import"
        else service.resolve_identifiers(identifiers="123", user={"sub": "user-1"}, document_source_context=context)
    )
    try:
        result = await call
        reached_duplicate_check = False
        assert result.results[0].status == "error"
    except _ReachedDuplicateCheck:
        reached_duplicate_check = True

    async def not_in_library(_decision):
        return None

    reason, _ = await _paper_refusal(decision, ListingRoute.IDENTIFIER, not_in_library)
    assert reached_duplicate_check is can_import
    assert (reason is None) is can_import
    if not can_import:
        assert reason is FileReason.PAPER_NOT_IMPORTABLE


@pytest.mark.parametrize(
    "status",
    [value for name, value in vars(ReferenceImportDecisionStatus).items() if name.isupper()],
)
@pytest.mark.parametrize("selected", [True, False])
def test_can_import_is_ready_or_still_converting_with_a_selected_main(status, selected):
    decision = _gate_decision(status)
    if not selected:
        decision = ReferenceImportDecision(status=status, provider="fake_provider", identifier="AGRKB:123")
    expected = selected and status in (
        ReferenceImportDecisionStatus.READY, ReferenceImportDecisionStatus.CONVERSION_RUNNING,
    )
    assert decision.can_import is expected


@pytest.mark.asyncio
@pytest.mark.parametrize("step", ["resolve", "import"])
@pytest.mark.parametrize("bearer_kind", [ProviderBearerKind.CURATOR, ProviderBearerKind.SERVICE])
async def test_resolve_reference_401_sentry_reporting_depends_on_bearer_kind(
    tmp_path, monkeypatch, step, bearer_kind,
):
    """ALL-1751 review: DocumentSourceLoginExpired stays quiet for curator bearers (user's own
    expired login) and reports one sanitized Sentry event for service bearers (broken machine
    reader token) across both dry-run resolve and import callers, keeping HTTP responses intact."""
    from src.lib.document_sources import identifier_import as module
    from src.lib.document_sources.models import DocumentSourceLoginExpired

    captures = []
    monkeypatch.setattr(
        module,
        "report_runtime_exception",
        lambda exc, **kwargs: captures.append((exc, kwargs)),
    )

    secret = "secret-service-token-abc999"

    class _UnauthorizedProvider(_FakeProvider):
        async def resolve_reference(self, identifier, *, request_bearer_token=None):
            raise DocumentSourceLoginExpired(f"ABC Literature reference lookup failed ({secret})")

    sessions = []
    service = IdentifierImportService(
        upload_execution_service=_DispatchRecorder(),
        session_factory=lambda: _FakeSession(sessions),
        storage_path_provider=lambda: tmp_path,
        principal_from_claims_fn=lambda claims: SimpleNamespace(subject=claims["sub"]),
        provision_user_fn=AsyncMock(return_value=SimpleNamespace(id=42)),
        provider_factory=_UnauthorizedProvider,
        create_document_fn=lambda *_args, **_kwargs: None,
        create_job_fn=lambda **_kwargs: SimpleNamespace(job_id="unused"),
        get_document_fn=lambda *_args, **_kwargs: None,
        delete_document_fn=lambda *_args, **_kwargs: None,
        find_existing_source_document_fn=lambda *_args, **_kwargs: None,
        import_batch_limit_provider=lambda: 10,
    )
    context = DocumentSourceRequestContext(
        bearer_kind=bearer_kind,
        provider_groups=("FBStaff",),
        authorized_group_ids=("FB",),
        curator_token=secret,
    )

    if step == "resolve":
        result = await service.resolve_identifiers(
            identifiers="PMID:123", user={"sub": "user-1"}, document_source_context=context,
        )
        expected_op = "source_resolve_failed"
    else:
        result = await service.import_identifiers(
            background_tasks=BackgroundTasks(),
            identifiers="PMID:123",
            user={"sub": "user-1"},
            document_source_context=context,
        )
        expected_op = "source_import_failed"

    assert result.error_count == 1
    assert result.results[0].status == "error"
    assert result.results[0].error_code == "document_source_unavailable"

    if bearer_kind is ProviderBearerKind.CURATOR:
        assert captures == []
    else:
        assert len(captures) == 1
        exc, kwargs = captures[0]
        assert kwargs["component"] == "identifier_import"
        assert kwargs["operation"] == expected_op
        assert kwargs["include_request"] is False
        assert secret not in repr((str(exc), kwargs))
        assert "PMID:123" not in repr((str(exc), kwargs))


@pytest.mark.asyncio
async def test_post_commit_import_compensation_cancelled_during_chunk_wipe_cleans_saved_pdf(
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
    def _session():
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

    chunks.data.delete_many.side_effect = _delete_many
    papers.data.delete_by_id.side_effect = _delete_by_id
    monkeypatch.setattr(weaviate_docs, "get_connection", lambda: connection)
    monkeypatch.setattr(weaviate_docs, "get_user_collections", lambda *_: (chunks, papers))

    sql_owner_deleted = False

    def _delete_sql_row(_user_id, _document_id, **_kwargs):
        nonlocal sql_owner_deleted
        sql_owner_deleted = True
        events.append("sql")
        return True

    monkeypatch.setattr(weaviate_docs, "_delete_sql_row", _delete_sql_row)

    sessions: list[_FakeSession] = []
    persisted_row_holder: list[object] = []

    class _TrackingSession(_FakeSession):
        def add(self, row):
            super().add(row)
            persisted_row_holder.append(row)

        def execute(self, *_args, **_kwargs):
            if sql_owner_deleted or not persisted_row_holder:
                return _ExecuteResult(None)
            return _ExecuteResult(persisted_row_holder[0])

    class _FailingDispatch(_DispatchRecorder):
        async def dispatch_provider_markdown_execution(self, *, background_tasks, request):
            raise RuntimeError("dispatch boom after commit")

        async def dispatch_upload_execution(self, *, background_tasks, request):
            raise RuntimeError("dispatch boom after commit")

    provider = _FakeProvider()
    service = IdentifierImportService(
        upload_execution_service=_FailingDispatch(),
        session_factory=lambda: _TrackingSession(sessions),
        storage_path_provider=lambda: tmp_path,
        principal_from_claims_fn=lambda claims: SimpleNamespace(subject=claims["sub"]),
        provision_user_fn=AsyncMock(return_value=SimpleNamespace(id=42)),
        provider_factory=lambda: provider,
        create_document_fn=AsyncMock(return_value={"success": True}),
        create_job_fn=lambda **_kwargs: SimpleNamespace(job_id="job-1"),
        get_document_fn=AsyncMock(return_value=None),
        delete_document_fn=weaviate_docs.delete_document,
        find_existing_source_document_fn=lambda *_args, **_kwargs: None,
        import_batch_limit_provider=lambda: 10,
    )

    task = asyncio.create_task(
        service.import_identifiers(
            background_tasks=BackgroundTasks(),
            identifiers="PMID:123",
            user={"sub": "user-1"},
            document_source_context=DocumentSourceRequestContext(
                bearer_kind=ProviderBearerKind.CURATOR,
                provider_groups=("FBStaff",),
                authorized_group_ids=("FB",),
                curator_token="curator-token",
            ),
        )
    )
    try:
        assert await asyncio.to_thread(wipe_entered.wait, 5)
        # Saved PDF is already cleaned up before Weaviate/SQL ownership is removed.
        assert not list((tmp_path / "user-1").rglob("*.pdf"))
        task.cancel("cancelled during import compensation")
        await asyncio.sleep(0.02)
        assert not task.done()
    finally:
        resume_wipe.set()

    with pytest.raises(asyncio.CancelledError, match="cancelled during import compensation"):
        await asyncio.wait_for(task, timeout=5)

    assert events == ["chunks", "parent", "sql"]
    assert sql_owner_deleted is True
    assert not list(tmp_path.rglob("*.pdf"))


@pytest.mark.asyncio
async def test_post_commit_import_compensation_retains_owner_on_persistent_unlink_failure(
    monkeypatch, tmp_path,
):
    import shutil

    sessions: list[_FakeSession] = []
    persisted_row_holder: list[object] = []

    class _TrackingSession(_FakeSession):
        def add(self, row):
            super().add(row)
            persisted_row_holder.append(row)

        def execute(self, *_args, **_kwargs):
            return _ExecuteResult(persisted_row_holder[0] if persisted_row_holder else None)

    class _FailingDispatch(_DispatchRecorder):
        async def dispatch_provider_markdown_execution(self, *, background_tasks, request):
            raise RuntimeError("dispatch boom after commit")

        async def dispatch_upload_execution(self, *, background_tasks, request):
            raise RuntimeError("dispatch boom after commit")

    delete_document_mock = AsyncMock()
    monkeypatch.setattr(shutil, "rmtree", lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError("locked")))

    provider = _FakeProvider()
    service = IdentifierImportService(
        upload_execution_service=_FailingDispatch(),
        session_factory=lambda: _TrackingSession(sessions),
        storage_path_provider=lambda: tmp_path,
        principal_from_claims_fn=lambda claims: SimpleNamespace(subject=claims["sub"]),
        provision_user_fn=AsyncMock(return_value=SimpleNamespace(id=42)),
        provider_factory=lambda: provider,
        create_document_fn=AsyncMock(return_value={"success": True}),
        create_job_fn=lambda **_kwargs: SimpleNamespace(job_id="job-1"),
        get_document_fn=AsyncMock(return_value=None),
        delete_document_fn=delete_document_mock,
        find_existing_source_document_fn=lambda *_args, **_kwargs: None,
        import_batch_limit_provider=lambda: 10,
    )

    with pytest.raises(RuntimeError, match="dispatch boom after commit"):
        await service.import_identifiers(
            background_tasks=BackgroundTasks(),
            identifiers="PMID:123",
            user={"sub": "user-1"},
            document_source_context=DocumentSourceRequestContext(
                bearer_kind=ProviderBearerKind.CURATOR,
                provider_groups=("FBStaff",),
                authorized_group_ids=("FB",),
                curator_token="curator-token",
            ),
        )

    # Persistent unlink failure leaves the PDF on disk and retains BOTH Weaviate and SQL ownership.
    assert list(tmp_path.rglob("*.pdf"))
    delete_document_mock.assert_not_awaited()
    assert not any(session.deleted for session in sessions)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled", [False, True])
async def test_post_commit_import_compensation_retains_owner_on_incomplete_wipe(
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
    def _session():
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
    monkeypatch.setattr(weaviate_docs, "get_connection", lambda: connection)
    monkeypatch.setattr(weaviate_docs, "get_user_collections", lambda *_: (chunks, papers))
    delete_sql_row = MagicMock()
    monkeypatch.setattr(weaviate_docs, "_delete_sql_row", delete_sql_row)
    monkeypatch.setattr(weaviate_docs.runtime, "report_runtime_exception", MagicMock())

    sessions: list[_FakeSession] = []
    persisted_row_holder: list[object] = []

    class _TrackingSession(_FakeSession):
        def add(self, row):
            super().add(row)
            persisted_row_holder.append(row)

        def execute(self, *_args, **_kwargs):
            return _ExecuteResult(persisted_row_holder[0] if persisted_row_holder else None)

    class _FailingDispatch(_DispatchRecorder):
        async def dispatch_provider_markdown_execution(self, *, background_tasks, request):
            raise RuntimeError("dispatch boom after commit")

        async def dispatch_upload_execution(self, *, background_tasks, request):
            raise RuntimeError("dispatch boom after commit")

    service = IdentifierImportService(
        upload_execution_service=_FailingDispatch(),
        session_factory=lambda: _TrackingSession(sessions),
        storage_path_provider=lambda: tmp_path,
        principal_from_claims_fn=lambda claims: SimpleNamespace(subject=claims["sub"]),
        provision_user_fn=AsyncMock(return_value=SimpleNamespace(id=42)),
        provider_factory=_FakeProvider,
        create_document_fn=AsyncMock(return_value={"success": True}),
        create_job_fn=lambda **_kwargs: SimpleNamespace(job_id="job-1"),
        get_document_fn=AsyncMock(return_value=None),
        delete_document_fn=weaviate_docs.delete_document,
        find_existing_source_document_fn=lambda *_args, **_kwargs: None,
        import_batch_limit_provider=lambda: 10,
    )

    coro = service.import_identifiers(
        background_tasks=BackgroundTasks(),
        identifiers="PMID:123",
        user={"sub": "user-1"},
        document_source_context=DocumentSourceRequestContext(
            bearer_kind=ProviderBearerKind.CURATOR,
            provider_groups=("FBStaff",),
            authorized_group_ids=("FB",),
            curator_token="curator-token",
        ),
    )

    if cancelled:
        task = asyncio.create_task(coro)
        try:
            assert await asyncio.to_thread(wipe_entered.wait, 5)
            task.cancel("cancelled during incomplete compensation wipe")
            await asyncio.sleep(0.02)
            assert not task.done()
        finally:
            resume_wipe.set()
        with pytest.raises(asyncio.CancelledError, match="cancelled during incomplete compensation wipe"):
            await asyncio.wait_for(task, timeout=5)
    else:
        with pytest.raises(RuntimeError, match="dispatch boom after commit"):
            await coro

    # Incomplete wipe retains BOTH the Weaviate parent and the SQL owner for retry.
    papers.data.delete_by_id.assert_not_called()
    delete_sql_row.assert_not_called()
    assert not any(session.deleted for session in sessions)


@pytest.mark.asyncio
async def test_pre_commit_import_compensation_retains_weaviate_and_reports_on_persistent_unlink_failure(
    monkeypatch, tmp_path,
):
    import shutil
    from unittest.mock import Mock
    from src.lib.document_cleanup import runtime as cleanup_runtime
    from src.lib.observability.sentry import hash_sentry_identifier

    sessions: list[_FakeSession] = []

    class _CommitFailingSession(_FakeSession):
        def commit(self):
            raise RuntimeError("sql commit failed")

    delete_document_mock = AsyncMock()
    report_mock = Mock()
    created_doc_ids: list[str] = []

    async def _create_document(_user_sub, doc):
        created_doc_ids.append(str(doc.id))
        return {"success": True}

    monkeypatch.setattr(cleanup_runtime, "report_runtime_exception", report_mock)
    monkeypatch.setattr(
        shutil,
        "rmtree",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError("locked")),
    )

    service = IdentifierImportService(
        upload_execution_service=_DispatchRecorder(),
        session_factory=lambda: _CommitFailingSession(sessions),
        storage_path_provider=lambda: tmp_path,
        principal_from_claims_fn=lambda claims: SimpleNamespace(subject=claims["sub"]),
        provision_user_fn=AsyncMock(return_value=SimpleNamespace(id=42)),
        provider_factory=_FakeProvider,
        create_document_fn=_create_document,
        create_job_fn=lambda **_kwargs: SimpleNamespace(job_id="job-1"),
        get_document_fn=AsyncMock(return_value=None),
        delete_document_fn=delete_document_mock,
        find_existing_source_document_fn=lambda *_args, **_kwargs: None,
        import_batch_limit_provider=lambda: 10,
    )

    with pytest.raises(RuntimeError, match="sql commit failed"):
        await service.import_identifiers(
            background_tasks=BackgroundTasks(),
            identifiers="PMID:123",
            user={"sub": "user-1"},
            document_source_context=DocumentSourceRequestContext(
                bearer_kind=ProviderBearerKind.CURATOR,
                provider_groups=("FBStaff",),
                authorized_group_ids=("FB",),
                curator_token="curator-token",
            ),
        )

    assert len(created_doc_ids) == 1
    doc_id = created_doc_ids[0]
    assert list(tmp_path.rglob("*.pdf"))
    delete_document_mock.assert_not_awaited()
    report_mock.assert_called_once()
    (err,) = report_mock.call_args.args
    assert err.__cause__ is None and err.__context__ is None
    assert report_mock.call_args.kwargs == {
        "component": "document_sources",
        "operation": "intake_compensation_file_cleanup_failed",
        "context": {
            "document_id_hash": hash_sentry_identifier(doc_id),
            "sql_committed": False,
            "weaviate_retained": True,
        },
        "fingerprint": ["intake_compensation_file_cleanup_failed", "document_sources", "pre_commit"],
        "include_request": False,
    }
    assert doc_id not in repr(report_mock.call_args)
    assert str(tmp_path) not in repr(report_mock.call_args)


@pytest.mark.asyncio
async def test_post_commit_import_compensation_fails_undispatched_job_and_holds_lock_during_wipe(
    monkeypatch, tmp_path,
):
    from src.lib.document_files.paper_lock import paper_lock_key

    sessions: list[_FakeSession] = []
    persisted_row_holder: list[object] = []

    class _TrackingSession(_FakeSession):
        def add(self, row):
            super().add(row)
            persisted_row_holder.append(row)

        def execute(self, *_args, **_kwargs):
            return _ExecuteResult(persisted_row_holder[0] if persisted_row_holder else None)

    class _FailingDispatch(_DispatchRecorder):
        async def dispatch_provider_markdown_execution(self, *, background_tasks, request):
            raise RuntimeError("dispatch boom after commit")

        async def dispatch_upload_execution(self, *, background_tasks, request):
            raise RuntimeError("dispatch boom after commit")

    order: list[str] = []
    failed_jobs: list[dict] = []

    def _mark_failed(**kwargs):
        assert not _FakePaperLockConnection.held, (
            "undispatched job must be failed BEFORE acquiring paper_operation_lock"
        )
        failed_jobs.append(kwargs)
        order.append("mark_failed")

    def _get_active_job(**kwargs):
        assert _FakePaperLockConnection.held, "active-job check must run under paper lock"
        order.append("get_active_job")
        return None

    async def _delete_document(_user_sub, doc_id, *, connection=None):
        assert isinstance(connection, _FakePaperLockConnection)
        assert paper_lock_key(doc_id) in _FakePaperLockConnection.held
        assert not list(tmp_path.rglob("*.pdf"))
        order.append("delete_document")
        return {"success": True, "postgres_deleted": True}

    service = IdentifierImportService(
        upload_execution_service=_FailingDispatch(),
        session_factory=lambda: _TrackingSession(sessions),
        storage_path_provider=lambda: tmp_path,
        principal_from_claims_fn=lambda claims: SimpleNamespace(subject=claims["sub"]),
        provision_user_fn=AsyncMock(return_value=SimpleNamespace(id=42)),
        provider_factory=_FakeProvider,
        create_document_fn=AsyncMock(return_value={"success": True}),
        create_job_fn=lambda **_kwargs: SimpleNamespace(job_id="job-undispatched-1"),
        get_document_fn=AsyncMock(return_value=None),
        delete_document_fn=_delete_document,
        find_existing_source_document_fn=lambda *_args, **_kwargs: None,
        import_batch_limit_provider=lambda: 10,
        get_active_job_fn=_get_active_job,
        mark_job_failed_fn=_mark_failed,
    )

    with pytest.raises(RuntimeError, match="dispatch boom after commit"):
        await service.import_identifiers(
            background_tasks=BackgroundTasks(),
            identifiers="PMID:123",
            user={"sub": "user-1"},
            document_source_context=DocumentSourceRequestContext(
                bearer_kind=ProviderBearerKind.CURATOR,
                provider_groups=("FBStaff",),
                authorized_group_ids=("FB",),
                curator_token="curator-token",
            ),
        )

    assert order == ["mark_failed", "get_active_job", "delete_document"]
    assert len(failed_jobs) == 1
    assert failed_jobs[0]["job_id"] == "job-undispatched-1"
    assert _FakePaperLockConnection.held == set()


@pytest.mark.asyncio
@pytest.mark.parametrize("competing_mode", ["lock_held", "active_job"])
async def test_post_commit_import_compensation_skips_when_competing_supplement_add_owns_paper(
    monkeypatch, tmp_path, competing_mode,
):
    from src.lib.document_files.paper_lock import paper_lock_key

    sessions: list[_FakeSession] = []
    persisted_row_holder: list[object] = []

    class _TrackingSession(_FakeSession):
        def add(self, row):
            super().add(row)
            persisted_row_holder.append(row)

        def execute(self, *_args, **_kwargs):
            return _ExecuteResult(persisted_row_holder[0] if persisted_row_holder else None)

    created_doc_ids: list[str] = []

    async def _create_document(_user_sub, doc):
        created_doc_ids.append(str(doc.id))
        return {"success": True}

    def _fail_create_job(**kwargs):
        doc_id = str(kwargs["document_id"])
        if competing_mode == "lock_held":
            _FakePaperLockConnection.held.add(paper_lock_key(doc_id))
        raise RuntimeError("job row failed after commit")

    delete_document_mock = AsyncMock()

    service = IdentifierImportService(
        upload_execution_service=_DispatchRecorder(),
        session_factory=lambda: _TrackingSession(sessions),
        storage_path_provider=lambda: tmp_path,
        principal_from_claims_fn=lambda claims: SimpleNamespace(subject=claims["sub"]),
        provision_user_fn=AsyncMock(return_value=SimpleNamespace(id=42)),
        provider_factory=_FakeProvider,
        create_document_fn=_create_document,
        create_job_fn=_fail_create_job,
        get_document_fn=AsyncMock(return_value=None),
        delete_document_fn=delete_document_mock,
        find_existing_source_document_fn=lambda *_args, **_kwargs: None,
        import_batch_limit_provider=lambda: 10,
        get_active_job_fn=(
            (lambda **_kwargs: SimpleNamespace(status="pending", metadata={"job_kind": "supplement_add"}))
            if competing_mode == "active_job"
            else (lambda **_kwargs: None)
        ),
    )

    try:
        with pytest.raises(RuntimeError, match="job row failed after commit"):
            await service.import_identifiers(
                background_tasks=BackgroundTasks(),
                identifiers="PMID:123",
                user={"sub": "user-1"},
                document_source_context=DocumentSourceRequestContext(
                    bearer_kind=ProviderBearerKind.CURATOR,
                    provider_groups=("FBStaff",),
                    authorized_group_ids=("FB",),
                    curator_token="curator-token",
                ),
            )
    finally:
        if competing_mode == "lock_held" and created_doc_ids:
            _FakePaperLockConnection.held.discard(paper_lock_key(created_doc_ids[0]))

    assert list(tmp_path.rglob("*.pdf"))
    delete_document_mock.assert_not_awaited()
    assert not any(session.deleted for session in sessions)


@pytest.mark.asyncio
async def test_post_commit_import_compensation_drains_undispatched_job_failure_on_cancel_before_lock(
    monkeypatch, tmp_path,
):
    import asyncio
    import threading

    sessions: list[_FakeSession] = []
    persisted_row_holder: list[object] = []

    class _TrackingSession(_FakeSession):
        def add(self, row):
            super().add(row)
            persisted_row_holder.append(row)

        def execute(self, *_args, **_kwargs):
            return _ExecuteResult(persisted_row_holder[0] if persisted_row_holder else None)

    class _FailingDispatch(_DispatchRecorder):
        async def dispatch_provider_markdown_execution(self, *, background_tasks, request):
            raise RuntimeError("dispatch boom after commit")

        async def dispatch_upload_execution(self, *, background_tasks, request):
            raise RuntimeError("dispatch boom after commit")

    mark_entered = threading.Event()
    resume_mark = threading.Event()
    marked_jobs: list[str] = []
    lock_acquired = False

    def _blocking_mark_failed(**kwargs):
        mark_entered.set()
        assert resume_mark.wait(timeout=5)
        marked_jobs.append(kwargs["job_id"])

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _recording_lock(_doc_id):
        nonlocal lock_acquired
        lock_acquired = True
        yield None

    service = IdentifierImportService(
        upload_execution_service=_FailingDispatch(),
        session_factory=lambda: _TrackingSession(sessions),
        storage_path_provider=lambda: tmp_path,
        principal_from_claims_fn=lambda claims: SimpleNamespace(subject=claims["sub"]),
        provision_user_fn=AsyncMock(return_value=SimpleNamespace(id=42)),
        provider_factory=_FakeProvider,
        create_document_fn=AsyncMock(return_value={"success": True}),
        create_job_fn=lambda **_kwargs: SimpleNamespace(job_id="job-cancel-before-lock"),
        get_document_fn=AsyncMock(return_value=None),
        delete_document_fn=AsyncMock(),
        find_existing_source_document_fn=lambda *_args, **_kwargs: None,
        import_batch_limit_provider=lambda: 10,
        mark_job_failed_fn=_blocking_mark_failed,
        paper_operation_lock_fn=_recording_lock,
    )

    task = asyncio.create_task(
        service.import_identifiers(
            background_tasks=BackgroundTasks(),
            identifiers="PMID:123",
            user={"sub": "user-1"},
            document_source_context=DocumentSourceRequestContext(
                bearer_kind=ProviderBearerKind.CURATOR,
                provider_groups=("FBStaff",),
                authorized_group_ids=("FB",),
                curator_token="curator-token",
            ),
        )
    )
    try:
        assert await asyncio.to_thread(mark_entered.wait, 5)
        task.cancel("cancelled while failing undispatched job")
        await asyncio.sleep(0.02)
        assert not task.done()
    finally:
        resume_mark.set()

    with pytest.raises(asyncio.CancelledError, match="cancelled while failing undispatched job"):
        await asyncio.wait_for(task, timeout=5)

    assert marked_jobs == ["job-cancel-before-lock"]
    assert lock_acquired is False


@pytest.mark.asyncio
async def test_pre_commit_import_compensation_handles_stat_permission_error(monkeypatch, tmp_path):
    from pathlib import Path as RealPath
    from unittest.mock import Mock
    from src.lib.document_cleanup import runtime as cleanup_runtime

    sessions: list[_FakeSession] = []

    class _CommitFailingSession(_FakeSession):
        def commit(self):
            raise RuntimeError("sql commit failed")

    delete_document_mock = AsyncMock()
    report_mock = Mock()
    monkeypatch.setattr(cleanup_runtime, "report_runtime_exception", report_mock)

    orig_exists = RealPath.exists

    def _deny_pdf_exists(self):
        if self.suffix == ".pdf":
            raise PermissionError("cannot stat pdf parent")
        return orig_exists(self)

    monkeypatch.setattr(RealPath, "exists", _deny_pdf_exists)

    service = IdentifierImportService(
        upload_execution_service=_DispatchRecorder(),
        session_factory=lambda: _CommitFailingSession(sessions),
        storage_path_provider=lambda: tmp_path,
        principal_from_claims_fn=lambda claims: SimpleNamespace(subject=claims["sub"]),
        provision_user_fn=AsyncMock(return_value=SimpleNamespace(id=42)),
        provider_factory=_FakeProvider,
        create_document_fn=AsyncMock(return_value={"success": True}),
        create_job_fn=lambda **_kwargs: SimpleNamespace(job_id="job-1"),
        get_document_fn=AsyncMock(return_value=None),
        delete_document_fn=delete_document_mock,
        find_existing_source_document_fn=lambda *_args, **_kwargs: None,
        import_batch_limit_provider=lambda: 10,
    )

    with pytest.raises(RuntimeError, match="sql commit failed"):
        await service.import_identifiers(
            background_tasks=BackgroundTasks(),
            identifiers="PMID:123",
            user={"sub": "user-1"},
            document_source_context=DocumentSourceRequestContext(
                bearer_kind=ProviderBearerKind.CURATOR,
                provider_groups=("FBStaff",),
                authorized_group_ids=("FB",),
                curator_token="curator-token",
            ),
        )

    delete_document_mock.assert_not_awaited()
    report_mock.assert_called_once()
    assert report_mock.call_args.kwargs["operation"] == "intake_compensation_file_cleanup_failed"
    assert report_mock.call_args.kwargs["include_request"] is False


@pytest.mark.asyncio
async def test_post_commit_import_compensation_removes_live_renamed_sql_file_snapshot(tmp_path):
    sessions: list[_FakeSession] = []
    persisted_row_holder: list[object] = []

    class _TrackingSession(_FakeSession):
        def add(self, row):
            super().add(row)
            persisted_row_holder.append(row)

        def execute(self, *_args, **_kwargs):
            return _ExecuteResult(persisted_row_holder[0] if persisted_row_holder else None)

    renamed_rel = "user-1/renamed_dir/renamed_paper.pdf"

    class _RenamingFailingDispatch(_DispatchRecorder):
        async def _rename_and_fail(self, request):
            # Simulate a concurrent rename updating the live PDFDocumentFile row & path on disk.
            target = tmp_path / renamed_rel
            target.parent.mkdir(parents=True, exist_ok=True)
            request.file_path.replace(target)
            persisted_row_holder[0].files[0].file_path = renamed_rel
            raise RuntimeError("dispatch boom after rename")

        async def dispatch_provider_markdown_execution(self, *, background_tasks, request):
            await self._rename_and_fail(request)

        async def dispatch_upload_execution(self, *, background_tasks, request):
            await self._rename_and_fail(request)

    delete_document_mock = AsyncMock(return_value={"success": True, "postgres_deleted": True})
    service = IdentifierImportService(
        upload_execution_service=_RenamingFailingDispatch(),
        session_factory=lambda: _TrackingSession(sessions),
        storage_path_provider=lambda: tmp_path,
        principal_from_claims_fn=lambda claims: SimpleNamespace(subject=claims["sub"]),
        provision_user_fn=AsyncMock(return_value=SimpleNamespace(id=42)),
        provider_factory=_FakeProvider,
        create_document_fn=AsyncMock(return_value={"success": True}),
        create_job_fn=lambda **_kwargs: SimpleNamespace(job_id="job-1"),
        get_document_fn=AsyncMock(return_value=None),
        delete_document_fn=delete_document_mock,
        find_existing_source_document_fn=lambda *_args, **_kwargs: None,
        import_batch_limit_provider=lambda: 10,
    )

    with pytest.raises(RuntimeError, match="dispatch boom after rename"):
        await service.import_identifiers(
            background_tasks=BackgroundTasks(),
            identifiers="PMID:123",
            user={"sub": "user-1"},
            document_source_context=DocumentSourceRequestContext(
                bearer_kind=ProviderBearerKind.CURATOR,
                provider_groups=("FBStaff",),
                authorized_group_ids=("FB",),
                curator_token="curator-token",
            ),
        )

    assert not (tmp_path / renamed_rel).exists()
    assert not list(tmp_path.rglob("*.pdf"))
    delete_document_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_post_commit_import_compensation_reports_one_sanitized_sentry_event_when_delete_document_sql_fails(
    monkeypatch, tmp_path,
):
    from contextlib import contextmanager
    from unittest.mock import MagicMock, Mock
    from src.lib.document_cleanup import runtime as cleanup_runtime
    from src.lib.observability.sentry import hash_sentry_identifier
    from src.lib.weaviate_client import documents as weaviate_docs

    @contextmanager
    def _session():
        yield MagicMock()

    w_conn = MagicMock()
    w_conn.session.side_effect = _session
    chunks, papers = MagicMock(), MagicMock()
    chunks.data.delete_many.return_value = SimpleNamespace(successful=0, matches=0, failed=0)
    papers.data.delete_by_id.return_value = True
    monkeypatch.setattr(weaviate_docs, "get_connection", lambda: w_conn)
    monkeypatch.setattr(weaviate_docs, "get_user_collections", lambda *_: (chunks, papers))

    secret_detail = "private-import-sql-failure-detail"
    sql_delete_calls = 0

    def _failing_sql_row(*_args, **_kwargs):
        nonlocal sql_delete_calls
        assert isinstance(_kwargs.get("connection"), _FakePaperLockConnection)
        sql_delete_calls += 1
        raise RuntimeError(secret_detail)

    monkeypatch.setattr(weaviate_docs, "_delete_sql_row", _failing_sql_row)
    report_mock = Mock()
    monkeypatch.setattr(cleanup_runtime, "report_runtime_exception", report_mock)

    sessions: list[_FakeSession] = []
    persisted_row_holder: list[object] = []

    class _TrackingSession(_FakeSession):
        def add(self, row):
            super().add(row)
            persisted_row_holder.append(row)

        def execute(self, *_args, **_kwargs):
            return _ExecuteResult(persisted_row_holder[0] if persisted_row_holder else None)

    class _FailingDispatch(_DispatchRecorder):
        async def dispatch_provider_markdown_execution(self, *, background_tasks, request):
            raise RuntimeError("dispatch boom after commit")

        async def dispatch_upload_execution(self, *, background_tasks, request):
            raise RuntimeError("dispatch boom after commit")

    created_doc_ids: list[str] = []

    async def _create_document(_user_sub, doc):
        created_doc_ids.append(str(doc.id))
        return {"success": True}

    service = IdentifierImportService(
        upload_execution_service=_FailingDispatch(),
        session_factory=lambda: _TrackingSession(sessions),
        storage_path_provider=lambda: tmp_path,
        principal_from_claims_fn=lambda claims: SimpleNamespace(subject=claims["sub"]),
        provision_user_fn=AsyncMock(return_value=SimpleNamespace(id=42)),
        provider_factory=_FakeProvider,
        create_document_fn=_create_document,
        create_job_fn=lambda **_kwargs: SimpleNamespace(job_id="job-1"),
        get_document_fn=AsyncMock(return_value=None),
        delete_document_fn=weaviate_docs.delete_document,
        find_existing_source_document_fn=lambda *_args, **_kwargs: None,
        import_batch_limit_provider=lambda: 10,
    )

    with pytest.raises(RuntimeError, match="dispatch boom after commit"):
        await service.import_identifiers(
            background_tasks=BackgroundTasks(),
            identifiers="PMID:123",
            user={"sub": "user-1"},
            document_source_context=DocumentSourceRequestContext(
                bearer_kind=ProviderBearerKind.CURATOR,
                provider_groups=("FBStaff",),
                authorized_group_ids=("FB",),
                curator_token="curator-token",
            ),
        )

    assert len(created_doc_ids) == 1
    doc_id = created_doc_ids[0]
    assert sql_delete_calls == 1
    assert not any(session.deleted for session in sessions)
    report_mock.assert_called_once()
    assert report_mock.call_args.kwargs == {
        "component": "weaviate_client",
        "operation": "paper_delete_sql_cleanup_failed",
        "context": {
            "document_id_hash": hash_sentry_identifier(doc_id),
            "exception_type": "RuntimeError",
        },
        "fingerprint": ["paper_delete_sql_cleanup_failed", "weaviate_client", "RuntimeError"],
        "include_request": False,
    }
    assert doc_id not in repr(report_mock.call_args)
    assert secret_detail not in repr(report_mock.call_args)


@pytest.mark.asyncio
async def test_committed_import_compensation_cancel_before_file_removal_submission_aborts_cleanly(
    tmp_path, monkeypatch,
):
    """Cancellation arriving after create_task schedules compensation but before file-worker submission deletes nothing."""
    import asyncio
    from contextlib import asynccontextmanager
    from unittest.mock import MagicMock
    from uuid import uuid4

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

    service = IdentifierImportService(
        upload_execution_service=MagicMock(),
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
        service._compensate_partial_import(
            user_sub="user-1",
            document_id=doc_id,
            saved_path=staged_pdf,
            weaviate_document_created=True,
            sql_record_committed=True,
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


@pytest.mark.asyncio
async def test_duplicate_identifier_import_staged_cleanup_failure_reports_sanitized_sentry_event(
    tmp_path, monkeypatch,
):
    """Duplicate-paper staged cleanup failure during identifier import reports once via sanitized Sentry reporter."""
    from unittest.mock import MagicMock, Mock
    from uuid import uuid4
    from src.lib.document_sources import identifier_import as module

    existing_doc = SimpleNamespace(id=uuid4(), filename="existing.pdf")
    report_mock = Mock(return_value=True)
    monkeypatch.setattr("src.lib.document_cleanup.runtime.report_runtime_exception", report_mock)
    monkeypatch.setattr(module, "_cleanup_saved_pdf", lambda *_a, **_kw: False)

    class _DupSession:
        def execute(self, *_a, **_kw):
            return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: existing_doc))

        def rollback(self):
            pass

        def close(self):
            pass

    service = IdentifierImportService(
        upload_execution_service=MagicMock(),
        session_factory=lambda: _DupSession(),
        storage_path_provider=lambda: tmp_path,
        principal_from_claims_fn=lambda claims: SimpleNamespace(subject=claims["sub"]),
        provision_user_fn=AsyncMock(return_value=SimpleNamespace(id=42)),
        provider_factory=_FakeProvider,
        create_document_fn=AsyncMock(return_value={"success": True}),
        get_document_fn=AsyncMock(return_value={"document": {"id": str(existing_doc.id)}}),
        find_existing_source_document_fn=lambda *_a, **_kw: None,
        import_batch_limit_provider=lambda: 10,
    )

    response = await service.import_identifiers(
        background_tasks=BackgroundTasks(),
        identifiers="PMID:123",
        user={"sub": "user-1"},
        document_source_context=DocumentSourceRequestContext(
            bearer_kind=ProviderBearerKind.CURATOR,
            provider_groups=("FBStaff",),
            authorized_group_ids=("FB",),
            curator_token="curator-token",
        ),
    )

    assert response.results[0].status == "duplicate"
    report_mock.assert_called_once()
    kwargs = report_mock.call_args.kwargs
    assert kwargs["component"] == "document_sources"
    assert kwargs["operation"] == "intake_compensation_file_cleanup_failed"
    assert kwargs["context"]["sql_committed"] is False
    assert kwargs["context"]["weaviate_retained"] is False
    assert kwargs["context"]["document_id_hash"].startswith("sha256:")
    assert kwargs["fingerprint"] == [
        "intake_compensation_file_cleanup_failed",
        "document_sources",
        "pre_commit",
    ]
    assert kwargs["include_request"] is False
    assert str(existing_doc.id) not in repr(report_mock.call_args)

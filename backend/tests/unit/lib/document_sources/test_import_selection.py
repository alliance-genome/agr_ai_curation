"""Tests for checksum-backed provider import selection."""

from __future__ import annotations

import pytest

from src.lib.document_sources.import_selection import (
    ChecksumImportDecisionStatus,
    provider_metadata_artifacts_for_source,
    select_checksum_import_candidate,
    source_artifact_is_authorized,
)
from src.lib.document_sources.models import (
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
from src.lib.document_sources.providers.abc_literature import (
    ABCLiteratureDocumentSourceProvider,
)


class FakeChecksumProvider:
    provider_id = "fake_provider"

    def __init__(self, artifacts: list[SourceArtifact]):
        self.artifacts = artifacts
        self.calls: list[dict[str, str | None]] = []

    async def find_artifacts_by_checksum(
        self,
        checksum: str,
        *,
        request_bearer_token: str | None = None,
    ) -> list[SourceArtifact]:
        self.calls.append(
            {"checksum": checksum, "request_bearer_token": request_bearer_token}
        )
        return self.artifacts

    async def __aenter__(self) -> "FakeChecksumProvider":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def aclose(self) -> None:
        return None

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

    def is_main_text_artifact(self, artifact: SourceArtifact) -> bool:
        return _fake_is_main_text_artifact(artifact, provider_id=self.provider_id)

    def main_text_artifact_sort_key(self, artifact: SourceArtifact) -> tuple[int, ...]:
        return _fake_main_text_artifact_sort_key(artifact, provider_id=self.provider_id)

    def conversion_exposes_main_text(self, result: SourceConversionResult) -> bool:
        return _fake_conversion_exposes_main_text(result, provider_id=self.provider_id)

    def provider_metadata_artifacts_for_source(
        self,
        source_artifact: SourceArtifact,
        artifacts: list[SourceArtifact] | tuple[SourceArtifact, ...],
    ) -> tuple[SourceArtifact, ...]:
        return tuple(
            artifact
            for artifact in artifacts
            if artifact.role is SourceArtifactRole.PROVIDER_METADATA
            and artifact.reference_curie == source_artifact.reference_curie
        )


class FakeConversionProvider(FakeChecksumProvider):
    def __init__(
        self,
        artifacts: list[SourceArtifact],
        *,
        conversion_result: SourceConversionResult,
        listed_artifacts: list[SourceArtifact] | None = None,
    ):
        super().__init__(artifacts)
        self.conversion_result = conversion_result
        self.listed_artifacts = listed_artifacts or []

    async def request_conversion(
        self,
        reference: SourceReference | SourceArtifact | str,
        *,
        wait: bool = False,
        request_bearer_token: str | None = None,
    ) -> SourceConversionResult:
        self.calls.append(
            {
                "request_conversion": str(
                    reference.reference_curie
                    if isinstance(reference, SourceArtifact)
                    else reference
                ),
                "wait": str(wait),
                "request_bearer_token": request_bearer_token,
            }
        )
        return self.conversion_result

    async def list_artifacts(
        self,
        reference: SourceReference | str,
        *,
        request_bearer_token: str | None = None,
    ) -> list[SourceArtifact]:
        self.calls.append(
            {
                "list_artifacts": str(reference),
                "request_bearer_token": request_bearer_token,
            }
        )
        return self.listed_artifacts


def _fake_is_main_text_artifact(
    artifact: SourceArtifact,
    *,
    provider_id: str,
) -> bool:
    file_class = str(artifact.metadata.get("file_class") or "").strip().lower()
    display_name = str(artifact.display_name or "").strip().lower()
    combined = f"{file_class} {display_name}"
    if provider_id == "abc_literature":
        return file_class == "converted_merged_main"
    return "supplement" not in combined


def _fake_main_text_artifact_sort_key(
    artifact: SourceArtifact,
    *,
    provider_id: str,
) -> tuple[int, ...]:
    file_class = str(artifact.metadata.get("file_class") or "").strip().lower()
    display_name = str(artifact.display_name or "").strip().lower()
    combined = f"{file_class} {display_name}"
    if provider_id == "abc_literature" and "tei" in combined:
        return (100,)
    if "_nxml" in combined or "nxml" in file_class:
        return (0,)
    if "_merged" in combined or "merged" in file_class:
        return (1,)
    if "tei" in combined:
        return (2,)
    return (10,)


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
    return any(status.get("main_converted") is True for status in result.per_mod_status)


def _source(
    artifact_id: str,
    *,
    provider: str = "fake_provider",
    scope: SourceAccessScope = SourceAccessScope.GLOBAL,
    mods: tuple[str, ...] = (),
    reference_curie: str = "AGRKB:101",
) -> SourceArtifact:
    return SourceArtifact(
        provider=provider,
        artifact_id=artifact_id,
        role=SourceArtifactRole.SOURCE_PDF,
        artifact_format=SourceArtifactFormat.PDF,
        status=SourceArtifactStatus.AVAILABLE,
        reference_id="101",
        reference_curie=reference_curie,
        display_name=f"{artifact_id}.pdf",
        md5sum="abc123",
        access_policy=SourceAccessPolicy(scope=scope, mods=mods),
    )


def _converted(
    artifact_id: str,
    parent_artifact_id: str,
    *,
    provider: str = "fake_provider",
    artifact_format: SourceArtifactFormat = SourceArtifactFormat.MARKDOWN,
    status: SourceArtifactStatus = SourceArtifactStatus.AVAILABLE,
    display_name: str | None = None,
    file_class: str = "converted_merged_main",
) -> SourceArtifact:
    return SourceArtifact(
        provider=provider,
        artifact_id=artifact_id,
        role=SourceArtifactRole.CONVERTED_TEXT,
        artifact_format=artifact_format,
        status=status,
        reference_id="101",
        reference_curie="AGRKB:101",
        display_name=display_name or f"{artifact_id}.md",
        parent_artifact_id=parent_artifact_id,
        metadata={"file_class": file_class},
    )


def _provider_metadata(
    artifact_id: str,
    *,
    provider: str = "abc_literature",
    display_name: str = "source-1_image_001",
    file_class: str = "converted_main_figure_metadata",
    status: SourceArtifactStatus = SourceArtifactStatus.AVAILABLE,
) -> SourceArtifact:
    return SourceArtifact(
        provider=provider,
        artifact_id=artifact_id,
        role=SourceArtifactRole.PROVIDER_METADATA,
        artifact_format=SourceArtifactFormat.JSON,
        status=status,
        reference_id="101",
        reference_curie="AGRKB:101",
        display_name=display_name,
        metadata={"file_class": file_class, "file_extension": "json"},
    )


def test_provider_metadata_artifacts_for_source_filters_by_class_and_display_prefix():
    provider = ABCLiteratureDocumentSourceProvider(client=None)  # type: ignore[arg-type]
    source = _source("paper", provider="abc_literature")
    main_metadata = _provider_metadata(
        "main-meta",
        display_name="paper_image_001",
        file_class="converted_main_figure_metadata",
    )
    supplement_metadata = _provider_metadata(
        "supp-meta",
        display_name="supplement_image_001",
        file_class="converted_supplement_figure_metadata",
    )

    assert provider_metadata_artifacts_for_source(
        provider=provider,
        source_artifact=source,
        artifacts=[source, supplement_metadata, main_metadata],
    ) == (main_metadata,)


def test_provider_metadata_artifacts_for_source_prefers_exact_png_sidecar_match():
    provider = ABCLiteratureDocumentSourceProvider(client=None)  # type: ignore[arg-type]
    source = _source("paper", provider="abc_literature")
    figure_png = SourceArtifact(
        provider="abc_literature",
        artifact_id="fig-png-1",
        role=SourceArtifactRole.CONVERTED_TEXT,
        artifact_format=SourceArtifactFormat.UNKNOWN,
        status=SourceArtifactStatus.AVAILABLE,
        reference_id="101",
        reference_curie="AGRKB:101",
        display_name="paper_image_001",
        metadata={"file_class": "converted_main_figure", "file_extension": "png"},
    )
    matching_metadata = _provider_metadata(
        "matching-meta",
        display_name="paper_image_001",
    )
    prefix_only_metadata = _provider_metadata(
        "prefix-only-meta",
        display_name="paper_image_002",
    )

    assert provider_metadata_artifacts_for_source(
        provider=provider,
        source_artifact=source,
        artifacts=[source, figure_png, prefix_only_metadata, matching_metadata],
    ) == (matching_metadata,)


@pytest.mark.asyncio
async def test_select_checksum_import_candidate_returns_ready_for_single_authorized_match():
    provider = FakeChecksumProvider([_source("source-1"), _converted("md-1", "source-1")])

    decision = await select_checksum_import_candidate(
        provider=provider,
        checksum=" abc123 ",
        authorized_group_ids=(),
    )

    assert provider.calls == [{"checksum": "abc123", "request_bearer_token": None}]
    assert decision.status is ChecksumImportDecisionStatus.READY
    assert decision.is_ready is True
    assert decision.selected is not None
    assert decision.selected.source_artifact.artifact_id == "source-1"
    assert decision.selected.converted_artifact.artifact_id == "md-1"


@pytest.mark.asyncio
async def test_select_checksum_import_candidate_carries_provider_metadata_sidecars():
    source = _source("source-1", provider="abc_literature")
    markdown = _converted("md-1", "source-1", provider="abc_literature")
    metadata = _provider_metadata("fig-meta-1")
    provider = FakeChecksumProvider([source, markdown, metadata])
    provider.provider_id = "abc_literature"

    decision = await select_checksum_import_candidate(
        provider=provider,
        checksum="abc123",
        authorized_group_ids=(),
    )

    assert decision.status is ChecksumImportDecisionStatus.READY
    assert decision.selected is not None
    assert decision.selected.converted_artifact is markdown
    assert decision.selected.provider_metadata_artifacts == (metadata,)


@pytest.mark.asyncio
async def test_select_checksum_import_candidate_does_not_select_metadata_json_as_markdown():
    source = _source("source-1", provider="abc_literature")
    metadata = _provider_metadata("fig-meta-1")
    provider = FakeChecksumProvider([source, metadata])
    provider.provider_id = "abc_literature"

    decision = await select_checksum_import_candidate(
        provider=provider,
        checksum="abc123",
        authorized_group_ids=(),
        allow_conversion_request=False,
    )

    assert decision.status is ChecksumImportDecisionStatus.READY
    assert decision.selected is not None
    assert decision.selected.converted_artifact is None
    assert decision.selected.provider_metadata_artifacts == (metadata,)


@pytest.mark.asyncio
async def test_select_checksum_import_candidate_accepts_non_abc_markdown_classes():
    provider = FakeChecksumProvider(
        [
            _source("source-1"),
            _converted("md-1", "source-1", file_class="semantic_text"),
        ]
    )

    decision = await select_checksum_import_candidate(
        provider=provider,
        checksum="abc123",
        authorized_group_ids=(),
    )

    assert decision.status is ChecksumImportDecisionStatus.READY
    assert decision.selected is not None
    assert decision.selected.converted_artifact is not None
    assert decision.selected.converted_artifact.artifact_id == "md-1"


@pytest.mark.asyncio
async def test_select_checksum_import_candidate_accepts_same_reference_unparented_markdown():
    provider = FakeChecksumProvider(
        [
            _source("source-1"),
            SourceArtifact(
                provider="fake_provider",
                artifact_id="md-reference-1",
                role=SourceArtifactRole.CONVERTED_TEXT,
                artifact_format=SourceArtifactFormat.MARKDOWN,
                status=SourceArtifactStatus.AVAILABLE,
                reference_id="101",
                reference_curie="AGRKB:101",
                display_name="provider.md",
                metadata={"file_class": "semantic_text"},
            ),
        ]
    )

    decision = await select_checksum_import_candidate(
        provider=provider,
        checksum="abc123",
        authorized_group_ids=(),
    )

    assert decision.status is ChecksumImportDecisionStatus.READY
    assert decision.selected is not None
    assert decision.selected.converted_artifact is not None
    assert decision.selected.converted_artifact.artifact_id == "md-reference-1"


@pytest.mark.asyncio
async def test_select_checksum_import_candidate_returns_no_match():
    provider = FakeChecksumProvider([])

    decision = await select_checksum_import_candidate(
        provider=provider,
        checksum="abc123",
        authorized_group_ids=("FB",),
    )

    assert decision.status is ChecksumImportDecisionStatus.NO_MATCH
    assert decision.selected is None


@pytest.mark.asyncio
async def test_select_checksum_import_candidate_forwards_request_bearer_token():
    provider = FakeChecksumProvider([])

    await select_checksum_import_candidate(
        provider=provider,
        checksum="abc123",
        authorized_group_ids=("FB",),
        request_bearer_token="curator-token",
    )

    assert provider.calls == [
        {"checksum": "abc123", "request_bearer_token": "curator-token"}
    ]


@pytest.mark.asyncio
async def test_select_checksum_import_candidate_requires_source_artifact():
    provider = FakeChecksumProvider([_converted("md-1", "source-1")])

    decision = await select_checksum_import_candidate(
        provider=provider,
        checksum="abc123",
        authorized_group_ids=("FB",),
    )

    assert decision.status is ChecksumImportDecisionStatus.NO_SOURCE_ARTIFACT


@pytest.mark.asyncio
async def test_select_checksum_import_candidate_blocks_inaccessible_restricted_match():
    provider = FakeChecksumProvider(
        [
            _source("source-1", scope=SourceAccessScope.RESTRICTED, mods=("FB",)),
            _converted("md-1", "source-1"),
        ]
    )

    decision = await select_checksum_import_candidate(
        provider=provider,
        checksum="abc123",
        authorized_group_ids=("WB",),
    )

    assert decision.status is ChecksumImportDecisionStatus.ACCESS_DENIED
    assert decision.selected is None


@pytest.mark.asyncio
async def test_select_checksum_import_candidate_allows_restricted_group_case_insensitive():
    provider = FakeChecksumProvider(
        [
            _source("source-1", scope=SourceAccessScope.RESTRICTED, mods=("fb",)),
            _converted("md-1", "source-1"),
        ]
    )

    decision = await select_checksum_import_candidate(
        provider=provider,
        checksum="abc123",
        authorized_group_ids=("FB",),
    )

    assert decision.status is ChecksumImportDecisionStatus.READY


@pytest.mark.asyncio
async def test_select_checksum_import_candidate_ambiguous_when_multiple_sources_accessible():
    provider = FakeChecksumProvider(
        [
            _source("source-1", reference_curie="AGRKB:101"),
            _converted("md-1", "source-1"),
            _source("source-2", reference_curie="AGRKB:202"),
            _converted("md-2", "source-2"),
        ]
    )

    decision = await select_checksum_import_candidate(
        provider=provider,
        checksum="abc123",
        authorized_group_ids=(),
    )

    assert decision.status is ChecksumImportDecisionStatus.AMBIGUOUS_MATCH
    assert decision.metadata == {"match_count": 2}


@pytest.mark.asyncio
async def test_select_checksum_import_candidate_prefers_nxml_when_multiple_markdown_children():
    provider = FakeChecksumProvider(
        [
            _source("source-1"),
            _converted("md-tei", "source-1", display_name="paper_tei"),
            _converted("md-merged", "source-1", display_name="paper_merged"),
            _converted("md-nxml", "source-1", display_name="paper_nxml"),
        ]
    )

    decision = await select_checksum_import_candidate(
        provider=provider,
        checksum="abc123",
        authorized_group_ids=(),
    )

    assert decision.status is ChecksumImportDecisionStatus.READY
    assert decision.selected is not None
    assert decision.selected.source_artifact.artifact_id == "source-1"
    assert decision.selected.converted_artifact is not None
    assert decision.selected.converted_artifact.artifact_id == "md-nxml"


@pytest.mark.asyncio
async def test_select_checksum_import_candidate_blocks_ambiguous_equal_nxml_markdown():
    provider = FakeChecksumProvider(
        [
            _source("source-1", provider="abc_literature"),
            _converted(
                "md-nxml-a",
                "source-1",
                provider="abc_literature",
                display_name="paper_a_nxml.md",
            ),
            _converted(
                "md-nxml-b",
                "source-1",
                provider="abc_literature",
                display_name="paper_b_nxml.md",
            ),
        ]
    )
    provider.provider_id = "abc_literature"

    decision = await select_checksum_import_candidate(
        provider=provider,
        checksum="abc123",
        authorized_group_ids=(),
    )

    assert decision.status is ChecksumImportDecisionStatus.AMBIGUOUS_MATCH
    assert decision.selected is None
    assert decision.metadata == {"match_count": 2}


@pytest.mark.asyncio
async def test_select_checksum_import_candidate_accepts_abc_tei_only_main_markdown():
    source = _source("source-1", provider="abc_literature")
    provider = FakeConversionProvider(
        [
            source,
            _converted(
                "md-tei",
                "source-1",
                provider="abc_literature",
                display_name="paper_tei.md",
                file_class="converted_merged_main",
            ),
        ],
        conversion_result=SourceConversionResult(
            provider="abc_literature",
            status=SourceConversionStatus.RUNNING,
            reference_curie="AGRKB:101",
            job_id="job-abc",
        ),
    )
    provider.provider_id = "abc_literature"

    decision = await select_checksum_import_candidate(
        provider=provider,
        checksum="abc123",
        authorized_group_ids=(),
    )

    assert decision.status is ChecksumImportDecisionStatus.READY
    assert decision.selected is not None
    assert decision.selected.converted_artifact is not None
    assert decision.selected.converted_artifact.artifact_id == "md-tei"
    assert provider.calls == [{"checksum": "abc123", "request_bearer_token": None}]


@pytest.mark.asyncio
async def test_select_checksum_import_candidate_blocks_ambiguous_post_conversion_markdown():
    source = _source("source-1", provider="abc_literature")
    provider = FakeConversionProvider(
        [source],
        conversion_result=SourceConversionResult(
            provider="abc_literature",
            status=SourceConversionStatus.CONVERTED,
            reference_curie="AGRKB:101",
            job_id="job-abc",
            converted_classes=("converted_merged_main",),
        ),
        listed_artifacts=[
            _converted(
                "md-nxml-a",
                "source-1",
                provider="abc_literature",
                display_name="paper_a_nxml.md",
            ),
            _converted(
                "md-nxml-b",
                "source-1",
                provider="abc_literature",
                display_name="paper_b_nxml.md",
            ),
        ],
    )
    provider.provider_id = "abc_literature"

    decision = await select_checksum_import_candidate(
        provider=provider,
        checksum="abc123",
        authorized_group_ids=(),
    )

    assert decision.status is ChecksumImportDecisionStatus.AMBIGUOUS_MATCH
    assert decision.selected is None
    assert decision.metadata["conversion_status"] == "converted"
    assert decision.metadata["match_count"] == 2


@pytest.mark.asyncio
async def test_select_checksum_import_candidate_accepts_per_mod_only_readiness():
    source = _source("source-1", provider="abc_literature")
    markdown = _converted("md-1", "source-1", provider="abc_literature")
    provider = FakeConversionProvider(
        [source],
        conversion_result=SourceConversionResult(
            provider="abc_literature",
            status=SourceConversionStatus.RUNNING,
            reference_curie="AGRKB:101",
            per_mod_status=({"mod": "FB", "main_converted": True},),
        ),
        listed_artifacts=[source, markdown],
    )
    provider.provider_id = "abc_literature"

    decision = await select_checksum_import_candidate(
        provider=provider,
        checksum="abc123",
        authorized_group_ids=(),
    )

    assert decision.status is ChecksumImportDecisionStatus.READY
    assert decision.selected is not None
    assert decision.selected.converted_artifact is markdown


@pytest.mark.asyncio
async def test_select_checksum_import_candidate_rejects_explicit_unknown_markdown():
    provider = FakeChecksumProvider(
        [
            _source("source-1"),
            _converted(
                "md-unknown",
                "source-1",
                status=SourceArtifactStatus.UNKNOWN,
            ),
        ]
    )

    decision = await select_checksum_import_candidate(
        provider=provider,
        checksum="abc123",
        authorized_group_ids=(),
        allow_conversion_request=False,
    )

    assert decision.status is ChecksumImportDecisionStatus.READY
    assert decision.selected is not None
    assert decision.selected.converted_artifact is None
    assert "No converted Markdown artifact" in decision.message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sibling_status",
    [SourceArtifactStatus.RUNNING, SourceArtifactStatus.FAILED],
)
async def test_select_checksum_import_candidate_prefers_ready_markdown_with_mixed_statuses(
    sibling_status,
):
    provider = FakeChecksumProvider(
        [
            _source("source-1"),
            _converted("md-1", "source-1"),
            _converted("md-2", "source-1", status=sibling_status),
        ]
    )

    decision = await select_checksum_import_candidate(
        provider=provider,
        checksum="abc123",
        authorized_group_ids=(),
    )

    assert decision.status is ChecksumImportDecisionStatus.READY
    assert decision.selected is not None
    assert decision.selected.converted_artifact is not None
    assert decision.selected.converted_artifact.artifact_id == "md-1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "supplement_status",
    [SourceArtifactStatus.RUNNING, SourceArtifactStatus.FAILED],
)
async def test_select_checksum_import_candidate_ignores_non_main_markdown_status_for_conversion(
    supplement_status,
):
    source = _source("source-1", provider="abc_literature")
    provider = FakeConversionProvider(
        [
            source,
            _converted(
                "md-supplement",
                "source-1",
                provider="abc_literature",
                status=supplement_status,
                file_class="converted_supplement",
            ),
        ],
        conversion_result=SourceConversionResult(
            provider="fake_provider",
            status=SourceConversionStatus.RUNNING,
            reference_curie="AGRKB:101",
            job_id="job-1",
        ),
    )

    decision = await select_checksum_import_candidate(
        provider=provider,
        checksum="abc123",
        authorized_group_ids=(),
    )

    assert decision.status is ChecksumImportDecisionStatus.CONVERSION_RUNNING
    assert provider.calls == [
        {"checksum": "abc123", "request_bearer_token": None},
        {
            "request_conversion": "AGRKB:101",
            "wait": "False",
            "request_bearer_token": None,
        },
    ]


@pytest.mark.asyncio
async def test_select_checksum_import_candidate_keeps_pdf_ready_without_converted_markdown():
    provider = FakeChecksumProvider(
        [
            _source("source-1"),
            _converted(
                "xml-1",
                "source-1",
                artifact_format=SourceArtifactFormat.XML,
            ),
        ]
    )

    decision = await select_checksum_import_candidate(
        provider=provider,
        checksum="abc123",
        authorized_group_ids=(),
    )

    assert decision.status is ChecksumImportDecisionStatus.READY
    assert decision.selected is not None
    assert decision.selected.source_artifact.artifact_id == "source-1"
    assert decision.selected.converted_artifact is None


@pytest.mark.asyncio
@pytest.mark.parametrize("xml_status", [SourceArtifactStatus.RUNNING, SourceArtifactStatus.FAILED])
async def test_select_checksum_import_candidate_ignores_non_markdown_statuses(xml_status):
    provider = FakeChecksumProvider(
        [
            _source("source-1"),
            _converted(
                "xml-1",
                "source-1",
                artifact_format=SourceArtifactFormat.XML,
                status=xml_status,
            ),
        ]
    )

    decision = await select_checksum_import_candidate(
        provider=provider,
        checksum="abc123",
        authorized_group_ids=(),
    )

    assert decision.status is ChecksumImportDecisionStatus.READY
    assert decision.selected is not None
    assert decision.selected.converted_artifact is None


@pytest.mark.asyncio
async def test_select_checksum_import_candidate_reports_running_conversion():
    metadata = _provider_metadata("fig-meta-1", provider="fake_provider")
    provider = FakeChecksumProvider(
        [
            _source("source-1"),
            _converted("md-1", "source-1", status=SourceArtifactStatus.RUNNING),
            metadata,
        ]
    )

    decision = await select_checksum_import_candidate(
        provider=provider,
        checksum="abc123",
        authorized_group_ids=(),
    )

    assert decision.status is ChecksumImportDecisionStatus.CONVERSION_RUNNING
    assert decision.selected is not None
    assert decision.selected.converted_artifact is None
    assert decision.selected.provider_metadata_artifacts == (metadata,)


@pytest.mark.asyncio
async def test_select_checksum_import_candidate_reports_failed_conversion():
    provider = FakeChecksumProvider(
        [
            _source("source-1"),
            _converted("md-1", "source-1", status=SourceArtifactStatus.FAILED),
        ]
    )

    decision = await select_checksum_import_candidate(
        provider=provider,
        checksum="abc123",
        authorized_group_ids=(),
    )

    assert decision.status is ChecksumImportDecisionStatus.CONVERSION_FAILED
    assert decision.selected is not None
    assert decision.selected.converted_artifact is None


@pytest.mark.asyncio
async def test_select_checksum_import_candidate_requests_conversion_when_supported():
    source = _source("source-1")
    metadata = _provider_metadata("fig-meta-1", provider="fake_provider")
    provider = FakeConversionProvider(
        [source, metadata],
        conversion_result=SourceConversionResult(
            provider="fake_provider",
            status=SourceConversionStatus.RUNNING,
            reference_curie="AGRKB:101",
            job_id="job-1",
        ),
    )

    decision = await select_checksum_import_candidate(
        provider=provider,
        checksum="abc123",
        authorized_group_ids=(),
        request_bearer_token="curator-token",
    )

    assert decision.status is ChecksumImportDecisionStatus.CONVERSION_RUNNING
    assert decision.selected is not None
    assert decision.selected.provider_metadata_artifacts == (metadata,)
    assert decision.metadata == {
        "conversion_status": "running",
        "conversion_job_id": "job-1",
    }
    assert provider.calls == [
        {"checksum": "abc123", "request_bearer_token": "curator-token"},
        {
            "request_conversion": "AGRKB:101",
            "wait": "False",
            "request_bearer_token": "curator-token",
        },
    ]


@pytest.mark.asyncio
async def test_select_checksum_import_candidate_reports_no_sources_conversion():
    source = _source("source-1")
    provider = FakeConversionProvider(
        [source],
        conversion_result=SourceConversionResult(
            provider="fake_provider",
            status=SourceConversionStatus.NO_SOURCES,
            reference_curie="AGRKB:101",
            job_id="job-1",
        ),
    )

    decision = await select_checksum_import_candidate(
        provider=provider,
        checksum="abc123",
        authorized_group_ids=(),
        request_bearer_token="curator-token",
    )

    assert decision.status is ChecksumImportDecisionStatus.NO_CONVERTED_TEXT
    assert decision.message == "Provider has no convertible source for this reference"
    assert decision.metadata == {
        "conversion_status": "no_sources",
        "conversion_job_id": "job-1",
    }


@pytest.mark.asyncio
async def test_select_checksum_import_candidate_can_skip_conversion_request():
    source = _source("source-1")
    provider = FakeConversionProvider(
        [source],
        conversion_result=SourceConversionResult(
            provider="fake_provider",
            status=SourceConversionStatus.RUNNING,
            reference_curie="AGRKB:101",
            job_id="job-1",
        ),
    )

    decision = await select_checksum_import_candidate(
        provider=provider,
        checksum="abc123",
        authorized_group_ids=(),
        request_bearer_token=None,
        allow_conversion_request=False,
    )

    assert decision.status is ChecksumImportDecisionStatus.READY
    assert decision.selected is not None
    assert decision.selected.source_artifact is source
    assert decision.selected.converted_artifact is None
    assert provider.calls == [
        {"checksum": "abc123", "request_bearer_token": None},
    ]


@pytest.mark.asyncio
async def test_select_checksum_import_candidate_uses_reference_level_non_abc_markdown_after_conversion():
    source = _source("source-1")
    nxml_markdown = SourceArtifact(
        provider="fake_provider",
        artifact_id="semantic-md-1",
        role=SourceArtifactRole.CONVERTED_TEXT,
        artifact_format=SourceArtifactFormat.MARKDOWN,
        status=SourceArtifactStatus.AVAILABLE,
        reference_id="101",
        reference_curie="AGRKB:101",
        display_name="provider_semantic.md",
        metadata={"file_class": "semantic_text"},
    )
    provider = FakeConversionProvider(
        [source],
        conversion_result=SourceConversionResult(
            provider="fake_provider",
            status=SourceConversionStatus.RUNNING,
            reference_curie="AGRKB:101",
            job_id="job-1",
            converted_classes=("semantic_text",),
        ),
        listed_artifacts=[source, nxml_markdown],
    )

    decision = await select_checksum_import_candidate(
        provider=provider,
        checksum="abc123",
        authorized_group_ids=(),
    )

    assert decision.status is ChecksumImportDecisionStatus.READY
    assert decision.selected is not None
    assert decision.selected.source_artifact.artifact_id == "source-1"
    assert decision.selected.converted_artifact is nxml_markdown


def test_source_artifact_is_authorized_rejects_unknown_access() -> None:
    assert (
        source_artifact_is_authorized(
            _source("source-1", scope=SourceAccessScope.UNKNOWN),
            authorized_group_ids=("FB",),
        )
        is False
    )


@pytest.mark.asyncio
async def test_select_checksum_import_candidate_requires_checksum():
    provider = FakeChecksumProvider([])

    with pytest.raises(DocumentSourceError, match="checksum is required"):
        await select_checksum_import_candidate(
            provider=provider,
            checksum=" ",
            authorized_group_ids=(),
        )

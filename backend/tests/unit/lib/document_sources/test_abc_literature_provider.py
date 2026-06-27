"""Tests for the ABC Literature document-source adapter."""

from __future__ import annotations

from typing import Any

import pytest

from src.lib.document_sources.models import (
    DocumentSourceConfigError,
    DocumentSourceError,
    SourceAccessScope,
    SourceArtifactFormat,
    SourceArtifactRole,
    SourceArtifactStatus,
    SourceConversionStatus,
    SourceReference,
)
from src.lib.document_sources.providers.abc_literature import (
    ABCLiteratureDocumentSourceProvider,
)
from src.lib.document_sources.registry import get_configured_document_source_provider
from src.lib.literature.client import ABCLiteratureHTTPError


class FakeABCLiteratureClient:
    def __init__(self):
        self.calls: list[tuple[str, Any]] = []
        self.external_lookup_payload: dict[str, Any] = {}
        self.cross_reference_payload: dict[str, Any] = {}
        self.show_reference_payload: dict[str, Any] = {}
        self.show_referencefiles_payload: dict[str, Any] = {}
        self.by_md5_payload: list[dict[str, Any]] = []
        self.by_md5_error: Exception | None = None
        self.conversion_payload: dict[str, Any] = {}
        self.conversion_error: Exception | None = None
        self.download_payload = b"artifact-bytes"
        self.closed = False

    async def lookup_external_curie(
        self,
        external_curie: str,
        *,
        request_bearer_token: str | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            (
                "lookup_external_curie",
                {
                    "external_curie": external_curie,
                    "request_bearer_token": request_bearer_token,
                },
            )
        )
        return self.external_lookup_payload

    async def lookup_cross_reference(
        self,
        cross_reference: str,
        *,
        request_bearer_token: str | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            (
                "lookup_cross_reference",
                {
                    "cross_reference": cross_reference,
                    "request_bearer_token": request_bearer_token,
                },
            )
        )
        return self.cross_reference_payload

    async def show_reference(
        self,
        reference: str,
        *,
        request_bearer_token: str | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            (
                "show_reference",
                {
                    "reference": reference,
                    "request_bearer_token": request_bearer_token,
                },
            )
        )
        return self.show_reference_payload

    async def show_referencefiles(
        self,
        reference: str,
        *,
        request_bearer_token: str | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            (
                "show_referencefiles",
                {
                    "reference": reference,
                    "request_bearer_token": request_bearer_token,
                },
            )
        )
        return self.show_referencefiles_payload

    async def lookup_referencefile_by_md5(
        self,
        md5sum: str,
        *,
        request_bearer_token: str | None = None,
    ) -> list[dict[str, Any]]:
        self.calls.append(
            (
                "lookup_referencefile_by_md5",
                {
                    "md5sum": md5sum,
                    "request_bearer_token": request_bearer_token,
                },
            )
        )
        if self.by_md5_error:
            raise self.by_md5_error
        return self.by_md5_payload

    async def download_referencefile(
        self,
        artifact_id: str,
        *,
        request_bearer_token: str | None = None,
    ) -> bytes:
        self.calls.append(
            (
                "download_referencefile",
                {
                    "artifact_id": artifact_id,
                    "request_bearer_token": request_bearer_token,
                },
            )
        )
        return self.download_payload

    async def request_referencefile_conversion(
        self,
        reference: str,
        *,
        wait: bool = False,
        request_bearer_token: str | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            (
                "request_referencefile_conversion",
                {
                    "reference": reference,
                    "wait": wait,
                    "request_bearer_token": request_bearer_token,
                },
            )
        )
        if self.conversion_error:
            raise self.conversion_error
        return self.conversion_payload

    async def search_references(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("search_references", payload))
        return {"results": []}

    async def aclose(self) -> None:
        self.closed = True


def provider_from_fake(
    fake_client: FakeABCLiteratureClient,
) -> ABCLiteratureDocumentSourceProvider:
    return ABCLiteratureDocumentSourceProvider(fake_client)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_resolve_numeric_identifier_as_pmid() -> None:
    fake_client = FakeABCLiteratureClient()
    fake_client.external_lookup_payload = {
        "reference_id": 101,
        "reference_curie": "AGRKB:101",
        "title": "Example paper",
        "pmid": "12345",
    }
    provider = provider_from_fake(fake_client)

    reference = await provider.resolve_reference("12345")

    assert fake_client.calls == [
        (
            "lookup_external_curie",
            {"external_curie": "PMID:12345", "request_bearer_token": None},
        )
    ]
    assert reference.provider == "abc_literature"
    assert reference.reference_id == "101"
    assert reference.reference_curie == "AGRKB:101"
    assert reference.external_ids == {"pmid": "12345"}


@pytest.mark.asyncio
async def test_resolve_doi_as_cross_reference() -> None:
    fake_client = FakeABCLiteratureClient()
    fake_client.cross_reference_payload = {
        "id": 202,
        "curie": "AGRKB:202",
        "doi": "10.1234/example",
    }
    provider = provider_from_fake(fake_client)

    reference = await provider.resolve_reference("10.1234/example")

    assert fake_client.calls == [
        (
            "lookup_cross_reference",
            {
                "cross_reference": "10.1234/example",
                "request_bearer_token": None,
            },
        )
    ]
    assert reference.reference_id == "202"
    assert reference.reference_curie == "AGRKB:202"
    assert reference.external_ids == {"doi": "10.1234/example"}


@pytest.mark.asyncio
async def test_resolve_reference_forwards_request_bearer_token() -> None:
    fake_client = FakeABCLiteratureClient()
    fake_client.external_lookup_payload = {
        "reference_id": 101,
        "reference_curie": "AGRKB:101",
        "title": "Example paper",
        "pmid": "12345",
    }
    provider = provider_from_fake(fake_client)

    await provider.resolve_reference(
        "PMID:12345",
        request_bearer_token="curator-token",
    )

    assert fake_client.calls == [
        (
            "lookup_external_curie",
            {
                "external_curie": "PMID:12345",
                "request_bearer_token": "curator-token",
            },
        )
    ]


@pytest.mark.asyncio
async def test_list_artifacts_maps_converted_markdown_access() -> None:
    fake_client = FakeABCLiteratureClient()
    fake_client.show_referencefiles_payload = {
        "referencefiles": [
            {
                "referencefile_id": 55,
                "reference_curie": "AGRKB:101",
                "display_name": "converted.md",
                "file_class": "converted_merged_main",
                "file_extension": "md",
                "referencefile_mods": [{"mod_abbreviation": None}],
            }
        ]
    }
    provider = provider_from_fake(fake_client)

    artifacts = await provider.list_artifacts("AGRKB:101")

    assert fake_client.calls == [
        (
            "show_referencefiles",
            {"reference": "AGRKB:101", "request_bearer_token": None},
        )
    ]
    assert len(artifacts) == 1
    artifact = artifacts[0]
    assert artifact.artifact_id == "55"
    assert artifact.role is SourceArtifactRole.CONVERTED_TEXT
    assert artifact.artifact_format is SourceArtifactFormat.MARKDOWN
    assert artifact.status is SourceArtifactStatus.AVAILABLE
    assert artifact.access_policy.scope is SourceAccessScope.UNKNOWN


@pytest.mark.asyncio
async def test_list_artifacts_expands_converted_referencefile_children() -> None:
    fake_client = FakeABCLiteratureClient()
    fake_client.show_referencefiles_payload = {
        "referencefiles": [
            {
                "referencefile_id": 55,
                "reference_id": 101,
                "reference_curie": "AGRKB:101",
                "display_name": "source.pdf",
                "file_class": "main",
                "file_extension": "pdf",
                "referencefile_mods": [{"mod_abbreviation": "FB"}],
                "converted_referencefiles": [
                    {
                        "referencefile_id": 56,
                        "display_name": "source_nxml.md",
                        "file_class": "converted_merged_main",
                        "file_extension": "md",
                    }
                ],
            }
        ]
    }
    provider = provider_from_fake(fake_client)

    artifacts = await provider.list_artifacts("AGRKB:101")

    assert [artifact.artifact_id for artifact in artifacts] == ["55", "56"]
    source_artifact, converted_artifact = artifacts
    assert source_artifact.role is SourceArtifactRole.SOURCE_PDF
    assert converted_artifact.role is SourceArtifactRole.CONVERTED_TEXT
    assert converted_artifact.parent_artifact_id == "55"
    assert converted_artifact.reference_id == "101"
    assert converted_artifact.reference_curie == "AGRKB:101"
    assert converted_artifact.access_policy.scope is SourceAccessScope.RESTRICTED
    assert converted_artifact.access_policy.mods == ("FB",)
    assert converted_artifact.status is SourceArtifactStatus.AVAILABLE


@pytest.mark.asyncio
async def test_list_artifacts_forwards_request_bearer_token() -> None:
    fake_client = FakeABCLiteratureClient()
    fake_client.show_referencefiles_payload = {"referencefiles": []}
    provider = provider_from_fake(fake_client)

    await provider.list_artifacts("AGRKB:101", request_bearer_token="curator-token")

    assert fake_client.calls == [
        (
            "show_referencefiles",
            {
                "reference": "AGRKB:101",
                "request_bearer_token": "curator-token",
            },
        )
    ]


@pytest.mark.asyncio
async def test_checksum_lookup_maps_source_and_inherited_converted_access() -> None:
    fake_client = FakeABCLiteratureClient()
    fake_client.by_md5_payload = [
        {
            "referencefile_id": 10,
            "reference_id": 101,
            "reference_curie": "AGRKB:101",
            "display_name": "source.pdf",
            "file_class": "main",
            "file_extension": "pdf",
            "md5sum": "abc123",
            "referencefile_mods": [{"mod_abbreviation": "FB"}],
            "converted_referencefiles": [
                {
                    "referencefile_id": 11,
                    "display_name": "converted.md",
                    "file_class": "converted_merged_main",
                    "file_extension": "md",
                    "referencefile_mods": [{"mod_abbreviation": None}],
                }
            ],
        }
    ]
    provider = provider_from_fake(fake_client)

    artifacts = await provider.find_artifacts_by_checksum("abc123")

    assert fake_client.calls == [
        (
            "lookup_referencefile_by_md5",
            {"md5sum": "abc123", "request_bearer_token": None},
        )
    ]
    assert [artifact.artifact_id for artifact in artifacts] == ["10", "11"]
    source_artifact, converted_artifact = artifacts
    assert source_artifact.role is SourceArtifactRole.SOURCE_PDF
    assert source_artifact.artifact_format is SourceArtifactFormat.PDF
    assert source_artifact.access_policy.scope is SourceAccessScope.RESTRICTED
    assert source_artifact.access_policy.mods == ("FB",)
    assert converted_artifact.parent_artifact_id == "10"
    assert converted_artifact.role is SourceArtifactRole.CONVERTED_TEXT
    assert converted_artifact.artifact_format is SourceArtifactFormat.MARKDOWN
    assert converted_artifact.status is SourceArtifactStatus.AVAILABLE
    assert converted_artifact.access_policy.scope is SourceAccessScope.RESTRICTED
    assert converted_artifact.access_policy.mods == ("FB",)


@pytest.mark.asyncio
async def test_checksum_lookup_preserves_explicit_unknown_converted_status() -> None:
    fake_client = FakeABCLiteratureClient()
    fake_client.by_md5_payload = [
        {
            "referencefile_id": 10,
            "reference_id": 101,
            "reference_curie": "AGRKB:101",
            "display_name": "source.pdf",
            "file_class": "main",
            "file_extension": "pdf",
            "md5sum": "abc123",
            "referencefile_mods": [{"mod_abbreviation": "FB"}],
            "converted_referencefiles": [
                {
                    "referencefile_id": 11,
                    "display_name": "converted.md",
                    "file_class": "converted_merged_main",
                    "file_extension": "md",
                    "status": "queued",
                }
            ],
        }
    ]
    provider = provider_from_fake(fake_client)

    artifacts = await provider.find_artifacts_by_checksum("abc123")

    assert artifacts[1].role is SourceArtifactRole.CONVERTED_TEXT
    assert artifacts[1].artifact_format is SourceArtifactFormat.MARKDOWN
    assert artifacts[1].status is SourceArtifactStatus.UNKNOWN


@pytest.mark.asyncio
async def test_checksum_lookup_wraps_abc_client_errors() -> None:
    fake_client = FakeABCLiteratureClient()
    fake_client.by_md5_error = ABCLiteratureHTTPError(
        "ABC Literature returned HTTP 503",
        status_code=503,
        endpoint="/reference/referencefile/by_md5/abc123",
    )
    provider = provider_from_fake(fake_client)

    with pytest.raises(DocumentSourceError, match="checksum lookup failed"):
        await provider.find_artifacts_by_checksum("abc123")


@pytest.mark.asyncio
async def test_checksum_lookup_passes_request_bearer_token() -> None:
    fake_client = FakeABCLiteratureClient()
    provider = provider_from_fake(fake_client)

    await provider.find_artifacts_by_checksum(
        "abc123",
        request_bearer_token="curator-token",
    )

    assert fake_client.calls == [
        (
            "lookup_referencefile_by_md5",
            {"md5sum": "abc123", "request_bearer_token": "curator-token"},
        )
    ]


@pytest.mark.asyncio
async def test_download_artifact_delegates_to_abc_client() -> None:
    fake_client = FakeABCLiteratureClient()
    provider = provider_from_fake(fake_client)

    payload = await provider.download_artifact("55")

    assert payload == b"artifact-bytes"
    assert fake_client.calls == [
        (
            "download_referencefile",
            {"artifact_id": "55", "request_bearer_token": None},
        )
    ]


@pytest.mark.asyncio
async def test_download_artifact_passes_request_bearer_token() -> None:
    fake_client = FakeABCLiteratureClient()
    provider = provider_from_fake(fake_client)

    payload = await provider.download_artifact(
        "55",
        request_bearer_token="curator-token",
    )

    assert payload == b"artifact-bytes"
    assert fake_client.calls == [
        (
            "download_referencefile",
            {"artifact_id": "55", "request_bearer_token": "curator-token"},
        )
    ]


@pytest.mark.asyncio
async def test_request_conversion_delegates_with_safe_defaults() -> None:
    fake_client = FakeABCLiteratureClient()
    fake_client.conversion_payload = {
        "reference_curie": "AGRKB:101",
        "status": "running",
        "job_id": "job-1",
        "converted_classes": ["converted_merged_main"],
        "per_file_progress": [
            {
                "converted": {
                    "file_class": "converted_merged_main",
                    "referencefile_id": 88,
                }
            }
        ],
    }
    provider = provider_from_fake(fake_client)

    result = await provider.request_conversion(
        "AGRKB:101",
        request_bearer_token="curator-token",
    )

    assert fake_client.calls == [
        (
            "request_referencefile_conversion",
            {
                "reference": "AGRKB:101",
                "wait": False,
                "request_bearer_token": "curator-token",
            },
        )
    ]
    assert result.provider == "abc_literature"
    assert result.reference_curie == "AGRKB:101"
    assert result.status is SourceConversionStatus.RUNNING
    assert result.job_id == "job-1"
    assert result.converted_classes == ("converted_merged_main",)
    assert result.per_file_progress[0]["converted"]["referencefile_id"] == 88


@pytest.mark.asyncio
async def test_request_conversion_can_wait_without_tei_overwrite() -> None:
    fake_client = FakeABCLiteratureClient()
    fake_client.conversion_payload = {"status": "converted"}
    provider = provider_from_fake(fake_client)

    await provider.request_conversion(
        SourceReference(provider="abc_literature", reference_curie="AGRKB:101"),
        wait=True,
    )

    assert fake_client.calls == [
        (
            "request_referencefile_conversion",
            {
                "reference": "AGRKB:101",
                "wait": True,
                "request_bearer_token": None,
            },
        )
    ]


@pytest.mark.asyncio
async def test_request_conversion_wraps_abc_client_errors() -> None:
    fake_client = FakeABCLiteratureClient()
    fake_client.conversion_error = ABCLiteratureHTTPError(
        "ABC Literature returned HTTP 503",
        status_code=503,
        endpoint="/reference/referencefile/conversion_request/AGRKB%3A101",
    )
    provider = provider_from_fake(fake_client)

    with pytest.raises(DocumentSourceError, match="conversion request failed"):
        await provider.request_conversion("AGRKB:101")


def test_registry_rejects_local_pdf_as_external_provider() -> None:
    with pytest.raises(DocumentSourceConfigError, match="local_pdf is handled"):
        get_configured_document_source_provider("local_pdf")


def test_registry_normalizes_abc_config_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ABC_LITERATURE_API_BASE_URL", raising=False)

    with pytest.raises(DocumentSourceConfigError, match="ABC_LITERATURE_API_BASE_URL"):
        get_configured_document_source_provider("abc_literature")


@pytest.mark.asyncio
async def test_provider_context_closes_underlying_client() -> None:
    fake_client = FakeABCLiteratureClient()

    async with provider_from_fake(fake_client):
        assert fake_client.closed is False

    assert fake_client.closed is True


@pytest.mark.asyncio
async def test_malformed_referencefile_payload_raises_provider_error() -> None:
    fake_client = FakeABCLiteratureClient()
    fake_client.show_referencefiles_payload = {
        "referencefiles": [{"file_extension": "md"}]
    }
    provider = provider_from_fake(fake_client)

    with pytest.raises(DocumentSourceError, match="referencefile payload missing id"):
        await provider.list_artifacts("AGRKB:101")


@pytest.mark.asyncio
async def test_source_pdf_null_mods_map_to_global_for_inheritance() -> None:
    fake_client = FakeABCLiteratureClient()
    fake_client.by_md5_payload = [
        {
            "referencefile_id": 10,
            "reference_curie": "AGRKB:101",
            "display_name": "source.pdf",
            "file_class": "main",
            "file_extension": "pdf",
            "referencefile_mods": [{"mod_abbreviation": None}],
            "converted_referencefiles": [
                {
                    "referencefile_id": 11,
                    "display_name": "converted.md",
                    "file_class": "converted_merged_main",
                    "file_extension": "md",
                    "referencefile_mods": [{"mod_abbreviation": None}],
                }
            ],
        }
    ]
    provider = provider_from_fake(fake_client)

    source_artifact, converted_artifact = await provider.find_artifacts_by_checksum(
        "abc123"
    )

    assert source_artifact.access_policy.scope is SourceAccessScope.GLOBAL
    assert converted_artifact.access_policy.scope is SourceAccessScope.GLOBAL


@pytest.mark.asyncio
async def test_empty_mod_payload_does_not_infer_global_access() -> None:
    fake_client = FakeABCLiteratureClient()
    fake_client.by_md5_payload = [
        {
            "referencefile_id": 10,
            "display_name": "source.pdf",
            "file_class": "main",
            "file_extension": "pdf",
            "referencefile_mods": [{}],
        }
    ]
    provider = provider_from_fake(fake_client)

    artifacts = await provider.find_artifacts_by_checksum("abc123")

    assert artifacts[0].access_policy.scope is SourceAccessScope.UNKNOWN

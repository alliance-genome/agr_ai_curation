"""Tests for the ABC Literature document-source adapter."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, call

import pytest
from src.lib.document_sources.identifier_import import (
    ReferenceImportDecisionStatus,
    select_reference_import_candidate,
)
from src.lib.document_sources.import_selection import source_artifact_is_authorized
from src.lib.document_sources.main_text import select_preferred_main_text_artifact
from src.lib.document_sources.models import (
    DocumentSourceAccessDenied,
    DocumentSourceConfigError,
    DocumentSourceError,
    DocumentSourceReferenceNotFound,
    SourceAccessPolicy,
    SourceAccessScope,
    SourceArtifact,
    SourceArtifactFormat,
    SourceArtifactRole,
    SourceArtifactStatus,
    SourceConversionStatus,
    SourceReference,
)
from agr_ai_curation_alliance.document_sources.abc_literature import (
    ABCLiteratureDocumentSourceProvider,
)
from agr_ai_curation_alliance.document_sources.registration import (
    _build_abc_literature_client_config,
    get_document_source_provider_registrations,
)
from src.lib.document_sources.registry import (
    get_configured_document_source_provider,
)
from agr_ai_curation_alliance.literature.client import (
    ABCLiteratureAuthMode,
    ABCLiteratureConfigError,
    ABCLiteratureHTTPError,
)
from src.lib.document_sources.models import ProviderBearerKind


def test_abc_registration_owns_public_identifier_guidance() -> None:
    registration = get_document_source_provider_registrations()[0]

    assert registration.presentation.identifier_help_label == (
        "PMID, PubMed ID, AGRKB, or ABC identifiers; comma or newline separated."
    )
    assert registration.presentation.identifier_examples == (
        "PMID:23970418",
        "PubMed ID 23970418",
        "AGRKB:101000000055784",
    )


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
        self.download_error: Exception | None = None
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
        max_bytes: int | None = None,
    ) -> bytes:
        call_kwargs: dict[str, Any] = {
            "artifact_id": artifact_id,
            "request_bearer_token": request_bearer_token,
        }
        if max_bytes is not None:
            call_kwargs["max_bytes"] = max_bytes
        self.calls.append(("download_referencefile", call_kwargs))
        if self.download_error:
            raise self.download_error
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


def _main_markdown(*, artifact_id: str, display_name: str) -> SourceArtifact:
    return SourceArtifact(
        provider="abc_literature",
        artifact_id=artifact_id,
        role=SourceArtifactRole.CONVERTED_TEXT,
        artifact_format=SourceArtifactFormat.MARKDOWN,
        status=SourceArtifactStatus.AVAILABLE,
        display_name=display_name,
        metadata={"file_class": "converted_merged_main", "file_extension": "md"},
    )


def test_main_text_contract_excludes_tei_before_ranking() -> None:
    provider = provider_from_fake(FakeABCLiteratureClient())
    tei = _main_markdown(artifact_id="tei-1", display_name="paper_tei.md")
    nxml = _main_markdown(artifact_id="nxml-1", display_name="paper_nxml.md")
    merged = _main_markdown(artifact_id="merged-1", display_name="paper_merged.md")

    assert select_preferred_main_text_artifact(provider, [tei]) == (None, 0)
    assert select_preferred_main_text_artifact(provider, [tei, merged, nxml]) == (
        nxml,
        1,
    )
    assert provider.main_text_artifact_sort_key(nxml) == (0,)
    assert provider.main_text_artifact_sort_key(merged) == (1,)


def test_checksum_match_uses_local_pdf_only_for_supplements() -> None:
    provider = ABCLiteratureDocumentSourceProvider(client=None)  # type: ignore[arg-type]

    def _source(file_class: str) -> SourceArtifact:
        return SourceArtifact(
            provider="abc_literature",
            artifact_id=f"{file_class}-1",
            role=SourceArtifactRole.SOURCE_PDF,
            artifact_format=SourceArtifactFormat.PDF,
            status=SourceArtifactStatus.AVAILABLE,
            access_policy=SourceAccessPolicy(scope=SourceAccessScope.GLOBAL),
            metadata={"file_class": file_class},
        )

    assert provider.checksum_match_uses_local_pdf(_source("supplement")) is True
    assert provider.checksum_match_uses_local_pdf(_source("main")) is False


def test_reference_source_artifact_sort_key_prefers_curator_mod_over_global() -> None:
    provider = provider_from_fake(FakeABCLiteratureClient())
    mod_pdf = SourceArtifact(
        provider="abc_literature",
        artifact_id="mod-pdf",
        role=SourceArtifactRole.SOURCE_PDF,
        artifact_format=SourceArtifactFormat.PDF,
        access_policy=SourceAccessPolicy(
            scope=SourceAccessScope.RESTRICTED,
            group_ids=("FB",),
        ),
        metadata={
            "file_class": "main",
            "file_publication_status": "final",
            "pdf_type": "pdf",
            "mod_abbreviations": ("FB",),
        },
    )
    shared_pdf = SourceArtifact(
        provider="abc_literature",
        artifact_id="shared-pdf",
        role=SourceArtifactRole.SOURCE_PDF,
        artifact_format=SourceArtifactFormat.PDF,
        access_policy=SourceAccessPolicy(scope=SourceAccessScope.GLOBAL),
        metadata={
            "file_class": "main",
            "file_publication_status": "final",
            "pdf_type": "pdf",
        },
    )

    assert provider.reference_source_artifact_sort_key(
        mod_pdf,
        ("fb",),
    ) < provider.reference_source_artifact_sort_key(
        shared_pdf,
        ("fb",),
    )


@pytest.mark.asyncio
async def test_reference_import_uses_actual_abc_main_pdf_precedence() -> None:
    fake_client = FakeABCLiteratureClient()
    fake_client.external_lookup_payload = _abc_external_lookup(
        "PMID:41902664",
        reference_curie="AGRKB:101",
        title="Production-shaped ABC paper",
    )
    fake_client.show_reference_payload = {
        "reference_id": 101,
        "curie": "AGRKB:101",
        "title": "Production-shaped ABC paper",
    }
    fake_client.show_referencefiles_payload = {
        "referencefiles": [
            {
                "referencefile_id": 9001,
                "reference_id": 101,
                "file_class": "supplement",
                "file_extension": "pdf",
                "file_publication_status": "final",
                "pdf_type": "pdf",
                "referencefile_mods": [{"mod_abbreviation": "FB"}],
            },
            {
                "referencefile_id": 9002,
                "reference_id": 101,
                "file_class": "main",
                "file_extension": "pdf",
                "file_publication_status": "obsolete",
                "pdf_type": "pdf",
                "referencefile_mods": [{"mod_abbreviation": "FB"}],
            },
            {
                "referencefile_id": 5015962,
                "reference_id": 101,
                "display_name": "PMC13232752.1",
                "file_class": "main",
                "file_extension": "pdf",
                "file_publication_status": "final",
                "pdf_type": "pdf",
                "referencefile_mods": [{"mod_abbreviation": None}],
            },
            {
                "referencefile_id": 5013742,
                "reference_id": 101,
                "display_name": "41902664",
                "file_class": "main",
                "file_extension": "pdf",
                "file_publication_status": "final",
                "pdf_type": "pdf",
                "referencefile_mods": [{"mod_abbreviation": "FB"}],
            },
            {
                "referencefile_id": 5020781,
                "reference_id": 101,
                "display_name": "PMC13232752.1_nxml",
                "file_class": "converted_merged_main",
                "file_extension": "md",
                "file_publication_status": "final",
                "referencefile_mods": [{"mod_abbreviation": None}],
            },
        ]
    }
    provider = provider_from_fake(fake_client)

    decision = await select_reference_import_candidate(
        bearer_kind=ProviderBearerKind.CURATOR,
        provider=provider,
        identifier="PMID:41902664",
        authorized_group_ids=("FB",),
        allow_conversion_request=False,
    )

    assert decision.status is ReferenceImportDecisionStatus.READY
    assert decision.selected is not None
    assert decision.selected.source_artifact.artifact_id == "5013742"
    assert decision.selected.converted_artifact is not None
    assert decision.selected.converted_artifact.artifact_id == "5020781"

def test_package_builds_complete_abc_client_config_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("ABC_LITERATURE_API_BASE_URL", " https://literature.example/api ")
    monkeypatch.setenv("ABC_LITERATURE_AUTH_MODE", "cognito_client_credentials")
    monkeypatch.setenv("ABC_LITERATURE_BEARER_TOKEN", "static-token")
    monkeypatch.setenv("ABC_LITERATURE_COGNITO_TOKEN_URL", "https://auth.example/token")
    monkeypatch.setenv("ABC_LITERATURE_COGNITO_CLIENT_ID", "client-id")
    monkeypatch.setenv("ABC_LITERATURE_COGNITO_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("ABC_LITERATURE_COGNITO_SCOPE", "scope/read")
    monkeypatch.setenv("DOCUMENT_SOURCE_REQUEST_TIMEOUT_SECONDS", "12.5")

    config = _build_abc_literature_client_config()

    assert config.base_url == "https://literature.example/api"
    assert config.auth_mode is ABCLiteratureAuthMode.COGNITO_CLIENT_CREDENTIALS
    assert config.bearer_token == "static-token"
    assert config.cognito_token_url == "https://auth.example/token"
    assert config.cognito_client_id == "client-id"
    assert config.cognito_client_secret == "client-secret"
    assert config.cognito_scope == "scope/read"
    assert config.timeout_seconds == 12.5


def test_package_rejects_unknown_abc_auth_mode(monkeypatch) -> None:
    monkeypatch.setenv("ABC_LITERATURE_API_BASE_URL", "https://literature.example/api")
    monkeypatch.setenv("ABC_LITERATURE_AUTH_MODE", "unknown")

    with pytest.raises(ABCLiteratureConfigError, match="ABC_LITERATURE_AUTH_MODE"):
        _build_abc_literature_client_config()


@pytest.mark.parametrize(
    ("raw_timeout", "expected_timeout"),
    [
        (None, 10.0),
        ("not-a-number", 10.0),
        ("0", 0.1),
    ],
)
def test_package_preserves_document_source_timeout_defaults_and_minimum(
    monkeypatch,
    raw_timeout: str | None,
    expected_timeout: float,
) -> None:
    monkeypatch.setenv("ABC_LITERATURE_API_BASE_URL", "https://literature.example/api")
    if raw_timeout is None:
        monkeypatch.delenv("DOCUMENT_SOURCE_REQUEST_TIMEOUT_SECONDS", raising=False)
    else:
        monkeypatch.setenv("DOCUMENT_SOURCE_REQUEST_TIMEOUT_SECONDS", raw_timeout)

    assert _build_abc_literature_client_config().timeout_seconds == expected_timeout


@pytest.mark.asyncio
@pytest.mark.parametrize("identifier", ["ZDB-PUB-200229-13", "ZFIN:ZDB-PUB-200229-13"])
async def test_zfin_publication_identifier_uses_canonical_cross_reference(identifier) -> None:
    client = FakeABCLiteratureClient()
    client.cross_reference_payload = {"id": 202, "curie": "AGRKB:202"}
    provider = provider_from_fake(client)
    normalized = provider.normalize_identifier(identifier)
    assert normalized.normalized == "ZFIN:ZDB-PUB-200229-13"
    assert provider.normalize_identifier(normalized.normalized).normalized == normalized.normalized
    await provider.resolve_reference(identifier, request_bearer_token="curator-token")
    assert client.calls == [("lookup_cross_reference", {
        "cross_reference": "ZFIN:ZDB-PUB-200229-13",
        "request_bearer_token": "curator-token",
    })]


@pytest.mark.asyncio
@pytest.mark.parametrize("status,endpoint", [(404, "/reference/by_cross_reference/test"), (404, "token"), (401, "/reference/test"), (403, "/reference/test"), (429, "/reference/test"), (500, "/reference/test")])
async def test_reference_lookup_classifies_only_not_found(status, endpoint) -> None:
    class FailingClient(FakeABCLiteratureClient):
        async def lookup_cross_reference(self, *args, **kwargs):
            raise ABCLiteratureHTTPError("lookup failed", status_code=status, endpoint=endpoint)

    provider = provider_from_fake(FailingClient())
    with pytest.raises(DocumentSourceError) as error:
        await provider.resolve_reference("ZDB-PUB-200229-13")
    assert isinstance(error.value, DocumentSourceReferenceNotFound) is (status == 404 and endpoint != "token")


@pytest.mark.parametrize("identifier", ["FBrf0123456", "FB:FBrf0123456", "ZDB-GENE-200229-13", "ZDB-PUB-20-13", "ZFIN:ZDB-PUB-200229-13"])
def test_zfin_normalization_does_not_rewrite_other_cross_references(identifier) -> None:
    provider = provider_from_fake(FakeABCLiteratureClient())
    assert provider.normalize_identifier(identifier).normalized == identifier


def _abc_external_lookup(
    external_curie: str,
    *,
    reference_curie: str | None,
    title: str = "",
    external_curie_found: bool = True,
) -> dict[str, Any]:
    """ABC ``ReferenceExternalLookupResponse`` for ``/reference/external_lookup``."""

    return {
        "exists_in_db": reference_curie is not None,
        "reference_curie": reference_curie,
        "external_curie": external_curie,
        "external_curie_found": external_curie_found,
        "title": title,
    }


@pytest.mark.asyncio
async def test_resolve_numeric_identifier_as_pmid() -> None:
    fake_client = FakeABCLiteratureClient()
    fake_client.external_lookup_payload = _abc_external_lookup(
        "PMID:12345",
        reference_curie="AGRKB:101",
        title="Example paper",
    )
    fake_client.show_reference_payload = {
        "reference_id": 101,
        "curie": "AGRKB:101",
        "title": "Example paper",
        "cross_references": [
            _abc_cross_reference(1, "PMID:12345", "PMID"),
            _abc_cross_reference(2, "DOI:10.1234/example", "DOI"),
        ],
    }
    provider = provider_from_fake(fake_client)

    reference = await provider.resolve_reference("12345")

    assert fake_client.calls == [
        (
            "lookup_external_curie",
            {"external_curie": "PMID:12345", "request_bearer_token": None},
        ),
        (
            "show_reference",
            {"reference": "AGRKB:101", "request_bearer_token": None},
        ),
    ]
    assert reference.provider == "abc_literature"
    assert reference.reference_id == "101"
    assert reference.reference_curie == "AGRKB:101"
    assert reference.title == "Example paper"
    assert reference.external_ids == {
        "pmid": "12345",
        "doi": "10.1234/example",
        "cross_references": ["PMID:12345", "DOI:10.1234/example"],
    }


@pytest.mark.asyncio
async def test_pmid_not_in_abc_does_not_fetch_a_reference() -> None:
    fake_client = FakeABCLiteratureClient()
    fake_client.external_lookup_payload = _abc_external_lookup(
        "PMID:99999",
        reference_curie=None,
        title="Known to PubMed only",
    )
    provider = provider_from_fake(fake_client)

    reference = await provider.resolve_reference("PMID:99999")

    assert fake_client.calls == [
        (
            "lookup_external_curie",
            {"external_curie": "PMID:99999", "request_bearer_token": None},
        )
    ]
    assert reference.reference_curie is None
    assert reference.reference_id is None
    assert reference.title == "Known to PubMed only"
    assert reference.external_ids == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [404, 403, 500])
async def test_pmid_reference_fetch_failure_uses_lookup_error_mapping(status) -> None:
    class FailingShowClient(FakeABCLiteratureClient):
        async def show_reference(self, *args, **kwargs):
            raise ABCLiteratureHTTPError(
                "show failed",
                status_code=status,
                endpoint="/reference/AGRKB:101",
            )

    fake_client = FailingShowClient()
    fake_client.external_lookup_payload = _abc_external_lookup(
        "PMID:12345",
        reference_curie="AGRKB:101",
    )
    provider = provider_from_fake(fake_client)

    with pytest.raises(DocumentSourceError) as error:
        await provider.resolve_reference("PMID:12345")

    assert isinstance(error.value, DocumentSourceReferenceNotFound) is (status == 404)


@pytest.mark.asyncio
async def test_resolve_doi_as_cross_reference() -> None:
    fake_client = FakeABCLiteratureClient()
    fake_client.cross_reference_payload = {
        "reference_id": 202,
        "curie": "AGRKB:202",
        "cross_references": [
            {
                "cross_reference_id": 7,
                "curie": "DOI:10.1234/example",
                "curie_prefix": "DOI",
                "is_obsolete": False,
            }
        ],
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
    assert reference.external_ids == {
        "doi": "10.1234/example",
        "cross_references": ["DOI:10.1234/example"],
    }


def _abc_cross_reference(
    cross_reference_id: int,
    curie: str,
    curie_prefix: str,
    *,
    is_obsolete: bool | None = False,
) -> dict[str, Any]:
    """One entry of ABC's ``ReferenceSchemaShow.cross_references``."""

    return {
        "cross_reference_id": cross_reference_id,
        "curie": curie,
        "curie_prefix": curie_prefix,
        "url": f"https://example.org/{cross_reference_id}",
        "pages": [{"name": "reference", "url": None}],
        "is_obsolete": is_obsolete,
        "issn_type": None,
    }


@pytest.mark.asyncio
async def test_show_reference_reads_identifiers_from_abc_cross_references() -> None:
    fake_client = FakeABCLiteratureClient()
    fake_client.show_reference_payload = {
        "reference_id": 303,
        "curie": "AGRKB:303",
        "title": "ABC-shaped paper",
        "cross_references": [
            _abc_cross_reference(1, "PMID:41902664", "PMID"),
            _abc_cross_reference(2, "DOI:10.1016/j.cell.2020.01.001", "DOI"),
            _abc_cross_reference(3, "PMCID:PMC7000001", "PMCID"),
            _abc_cross_reference(4, "FB:FBrf0250001", "FB", is_obsolete=None),
            _abc_cross_reference(5, "PMID:11111111", "PMID", is_obsolete=True),
        ],
    }
    provider = provider_from_fake(fake_client)

    reference = await provider.resolve_reference("AGRKB:303")

    assert reference.external_ids == {
        "pmid": "41902664",
        "doi": "10.1016/j.cell.2020.01.001",
        "pmcid": "PMC7000001",
        "cross_references": [
            "PMID:41902664",
            "DOI:10.1016/j.cell.2020.01.001",
            "PMCID:PMC7000001",
            "FB:FBrf0250001",
        ],
    }


@pytest.mark.asyncio
async def test_cross_reference_without_curie_prefix_lists_curie_only() -> None:
    fake_client = FakeABCLiteratureClient()
    entry = _abc_cross_reference(1, "PMID:12345", "PMID")
    del entry["curie_prefix"]
    fake_client.show_reference_payload = {
        "reference_id": 404,
        "curie": "AGRKB:404",
        "cross_references": [entry],
    }
    provider = provider_from_fake(fake_client)

    reference = await provider.resolve_reference("AGRKB:404")

    assert reference.external_ids == {"cross_references": ["PMID:12345"]}


@pytest.mark.asyncio
async def test_duplicate_current_prefix_keeps_curies_but_derives_no_identifier() -> None:
    fake_client = FakeABCLiteratureClient()
    fake_client.show_reference_payload = {
        "reference_id": 505,
        "curie": "AGRKB:505",
        "cross_references": [
            _abc_cross_reference(1, "DOI:10.1/first", "DOI"),
            _abc_cross_reference(2, "DOI:10.1/second", "DOI"),
            _abc_cross_reference(3, "PMID:555", "PMID"),
        ],
    }
    provider = provider_from_fake(fake_client)

    reference = await provider.resolve_reference("AGRKB:505")

    assert reference.external_ids == {
        "pmid": "555",
        "cross_references": ["DOI:10.1/first", "DOI:10.1/second", "PMID:555"],
    }


@pytest.mark.asyncio
async def test_reference_top_level_identifier_fields_are_not_read() -> None:
    fake_client = FakeABCLiteratureClient()
    fake_client.show_reference_payload = {
        "reference_id": 606,
        "curie": "AGRKB:606",
        "pmid": "999",
        "doi": "10.9/top-level",
    }
    provider = provider_from_fake(fake_client)

    reference = await provider.resolve_reference("AGRKB:606")

    assert reference.external_ids == {}


@pytest.mark.asyncio
async def test_resolve_reference_forwards_request_bearer_token() -> None:
    fake_client = FakeABCLiteratureClient()
    fake_client.external_lookup_payload = _abc_external_lookup(
        "PMID:12345",
        reference_curie="AGRKB:101",
        title="Example paper",
    )
    fake_client.show_reference_payload = {"reference_id": 101, "curie": "AGRKB:101"}
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
        ),
        (
            "show_reference",
            {
                "reference": "AGRKB:101",
                "request_bearer_token": "curator-token",
            },
        ),
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
async def test_list_artifacts_maps_figure_metadata_sidecars_as_provider_metadata() -> None:
    fake_client = FakeABCLiteratureClient()
    fake_client.show_referencefiles_payload = {
        "referencefiles": [
            {
                "referencefile_id": 110,
                "reference_id": 101,
                "reference_curie": "AGRKB:101",
                "display_name": "paper_image_001",
                "file_class": "converted_main_figure_metadata",
                "file_extension": "json",
                "file_publication_status": "final",
            }
        ]
    }
    provider = provider_from_fake(fake_client)

    artifacts = await provider.list_artifacts("AGRKB:101")

    assert len(artifacts) == 1
    artifact = artifacts[0]
    assert artifact.artifact_id == "110"
    assert artifact.role is SourceArtifactRole.PROVIDER_METADATA
    assert artifact.artifact_format is SourceArtifactFormat.JSON
    assert artifact.status is SourceArtifactStatus.AVAILABLE
    assert artifact.metadata["file_class"] == "converted_main_figure_metadata"


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
@pytest.mark.parametrize("license_name, open_access", [(None, None), ("Closed license", False)])
async def test_checksum_lookup_maps_source_and_inherited_converted_access(
    license_name: str | None, open_access: bool | None
) -> None:
    fake_client = FakeABCLiteratureClient()
    fake_client.show_reference_payload = _abc_reference_record(
        copyright_license_name=license_name, copyright_license_open_access=open_access
    )
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
        ),
        ("show_reference", {"reference": "AGRKB:101", "request_bearer_token": None}),
    ]
    assert [artifact.artifact_id for artifact in artifacts] == ["10", "11"]
    source_artifact, converted_artifact = artifacts
    assert source_artifact.role is SourceArtifactRole.SOURCE_PDF
    assert source_artifact.artifact_format is SourceArtifactFormat.PDF
    assert source_artifact.access_policy.scope is SourceAccessScope.RESTRICTED
    assert source_artifact.access_policy.group_ids == ("FB",)
    assert converted_artifact.parent_artifact_id == "10"
    assert converted_artifact.role is SourceArtifactRole.CONVERTED_TEXT
    assert converted_artifact.artifact_format is SourceArtifactFormat.MARKDOWN
    assert converted_artifact.status is SourceArtifactStatus.AVAILABLE
    assert converted_artifact.access_policy.scope is SourceAccessScope.RESTRICTED
    assert converted_artifact.access_policy.group_ids == ("FB",)


@pytest.mark.asyncio
async def test_checksum_without_reference_curie_cannot_grant_mod_access() -> None:
    fake_client = FakeABCLiteratureClient()
    row = _abc_by_md5_main_pdf("WB")
    del row["reference_curie"]
    fake_client.by_md5_payload = [row]

    source, child = await provider_from_fake(fake_client).find_artifacts_by_checksum("abc123")

    assert source.access_policy.scope is SourceAccessScope.UNKNOWN
    assert child.access_policy == source.access_policy


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reference_fields", "message"),
    [
        ({"reference_id": 101, "curie": "AGRKB:101"}, "lacks license metadata"),
        ({"reference_id": 102, "curie": "AGRKB:101"}, "reference mismatch"),
    ],
)
async def test_incomplete_or_mismatched_checksum_reference_fails_closed(reference_fields, message) -> None:
    fake_client = FakeABCLiteratureClient()
    fake_client.by_md5_payload = [_abc_by_md5_main_pdf("WB")]
    fake_client.show_reference_payload = reference_fields

    with pytest.raises(DocumentSourceError, match=message):
        await provider_from_fake(fake_client).find_artifacts_by_checksum("abc123")


@pytest.mark.asyncio
async def test_checksum_matches_use_each_references_own_license(monkeypatch) -> None:
    fake_client = FakeABCLiteratureClient()
    first = _abc_by_md5_main_pdf("WB") | {"converted_referencefiles": []}
    second = first | {"referencefile_id": 20, "reference_id": 102, "reference_curie": "AGRKB:102"}
    fake_client.by_md5_payload = [first, second, first | {"referencefile_id": 30}]
    lookup = AsyncMock(side_effect=[
        _abc_reference_record(copyright_license_name="Named license"),
        _abc_reference_record() | {"reference_id": 102, "curie": "AGRKB:102"},
    ])
    monkeypatch.setattr(fake_client, "show_reference", lookup)

    artifacts = await provider_from_fake(fake_client).find_artifacts_by_checksum(
        "abc123", request_bearer_token="curator-token"
    )

    assert [artifact.access_policy.scope for artifact in artifacts] == [
        SourceAccessScope.UNKNOWN, SourceAccessScope.RESTRICTED, SourceAccessScope.UNKNOWN
    ]
    assert lookup.await_args_list == [
        call("AGRKB:101", request_bearer_token="curator-token"),
        call("AGRKB:102", request_bearer_token="curator-token"),
    ]


@pytest.mark.asyncio
async def test_checksum_lookup_preserves_explicit_unknown_converted_status() -> None:
    fake_client = FakeABCLiteratureClient()
    fake_client.show_reference_payload = _abc_reference_record()
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
async def test_download_artifact_maps_forbidden_response_to_access_denied() -> None:
    fake_client = FakeABCLiteratureClient()
    fake_client.download_error = ABCLiteratureHTTPError(
        "ABC Literature request failed",
        status_code=403,
        endpoint="/reference/referencefile/download/55",
    )
    provider = provider_from_fake(fake_client)

    with pytest.raises(DocumentSourceAccessDenied):
        await provider.download_artifact(
            "55",
            request_bearer_token="curator-token",
        )


@pytest.mark.asyncio
async def test_download_artifact_keeps_non_forbidden_http_error_generic() -> None:
    fake_client = FakeABCLiteratureClient()
    fake_client.download_error = ABCLiteratureHTTPError(
        "ABC Literature request failed",
        status_code=500,
        endpoint="/reference/referencefile/download/55",
    )
    provider = provider_from_fake(fake_client)

    with pytest.raises(DocumentSourceError) as exc_info:
        await provider.download_artifact(
            "55",
            request_bearer_token="curator-token",
        )

    assert not isinstance(exc_info.value, DocumentSourceAccessDenied)


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
async def test_conversion_maps_private_per_mod_status_to_generic_readiness() -> None:
    fake_client = FakeABCLiteratureClient()
    fake_client.conversion_payload = {
        "status": "running",
        "per_mod_status": [{"mod": "FB", "main_converted": True}],
    }
    provider = provider_from_fake(fake_client)
    result = await provider.request_conversion("AGRKB:101")

    assert result.converted_classes == ("converted_merged_main",)
    assert not hasattr(result, "per_mod_status")
    assert provider.conversion_exposes_main_text(result) is True
    assert provider.conversion_progress_percentage(result) == 35
    assert provider.conversion_progress_message(result) == (
        "ABC Literature main text is ready; importing converted Markdown"
    )


@pytest.mark.asyncio
async def test_tei_derived_progress_row_is_not_reported_as_main_text() -> None:
    """ABC keeps listing the legacy TEI row while overwrite_tei_md re-converts the PDF."""
    fake_client = FakeABCLiteratureClient()
    fake_client.conversion_payload = {
        "status": "running",
        "converted_classes": [],
        "per_file_progress": [
            {
                "source": {"file_class": "tei", "display_name": "00064359_Miwa22"},
                "converted": {
                    "display_name": "00064359_Miwa22_tei",
                    "file_class": "converted_merged_main",
                    "referencefile_id": 4880764,
                },
                "status": "success",
            },
            {
                "source": {"file_class": "main", "display_name": "00064359_Miwa22"},
                "status": "pending",
            },
        ],
    }
    provider = provider_from_fake(fake_client)
    result = await provider.request_conversion("AGRKB:101000000904359")

    assert provider.conversion_exposes_main_text(result) is False
    assert provider.conversion_progress_percentage(result) == 20
    assert provider.conversion_progress_message(result) == (
        "ABC Literature conversion running (1 file pending)"
    )


@pytest.mark.asyncio
async def test_request_conversion_can_wait() -> None:
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


@pytest.mark.parametrize(
    "lookup",
    [
        get_configured_document_source_provider,
    ],
)
def test_registry_rejects_unknown_provider(lookup) -> None:
    with pytest.raises(DocumentSourceConfigError, match="unsupported_provider"):
        lookup("unsupported_provider")


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
    fake_client.show_reference_payload = _abc_reference_record()
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

    assert source_artifact.access_policy.scope is SourceAccessScope.ANY_GROUP
    assert converted_artifact.access_policy.scope is SourceAccessScope.ANY_GROUP


@pytest.mark.asyncio
async def test_empty_mod_payload_does_not_infer_global_access() -> None:
    fake_client = FakeABCLiteratureClient()
    fake_client.show_reference_payload = _abc_reference_record()
    fake_client.by_md5_payload = [
        {
            "referencefile_id": 10,
            "reference_curie": "AGRKB:101",
            "display_name": "source.pdf",
            "file_class": "main",
            "file_extension": "pdf",
            "referencefile_mods": [{}],
        }
    ]
    provider = provider_from_fake(fake_client)

    artifacts = await provider.find_artifacts_by_checksum("abc123")

    assert artifacts[0].access_policy.scope is SourceAccessScope.UNKNOWN


@pytest.mark.asyncio
async def test_bare_mod_string_does_not_create_access_entitlement() -> None:
    fake_client = FakeABCLiteratureClient()
    fake_client.show_reference_payload = _abc_reference_record()
    fake_client.by_md5_payload = [
        {
            "referencefile_id": 10,
            "reference_curie": "AGRKB:101",
            "display_name": "source.pdf",
            "file_class": "main",
            "file_extension": "pdf",
            "referencefile_mods": ["FB"],
        }
    ]
    provider = provider_from_fake(fake_client)

    artifacts = await provider.find_artifacts_by_checksum("abc123")

    assert artifacts[0].access_policy.scope is SourceAccessScope.UNKNOWN
    assert artifacts[0].access_policy.group_ids == ()
    assert not source_artifact_is_authorized(
        artifacts[0], authorized_group_ids=("FB",)
    )


# ALL-1543: AI Curation's file-access rule matches ABC's ``download_file``:
# open-access reference, OR a no-MOD (null) entry on the source PDF, OR the
# curator's MOD among the file's entries. Payloads below follow ABC's
# ``ReferencefileSchemaRelated`` (show_all), ``ReferencefileByMd5MatchSchema``
# (by_md5) and ``ReferenceSchemaShow`` (reference record) shapes.


def _abc_mod_rows(*mod_abbreviations: str | None) -> list[dict[str, Any]]:
    return [
        {"referencefile_mod_id": 7000 + index, "mod_abbreviation": mod}
        for index, mod in enumerate(mod_abbreviations)
    ]


def _abc_show_all_main_pdf(
    referencefile_id: int,
    *mods: str | None,
    display_name: str = "WBPaper00067001",
) -> dict[str, Any]:
    return {
        "referencefile_id": referencefile_id,
        "display_name": display_name,
        "file_class": "main",
        "file_publication_status": "final",
        "file_extension": "pdf",
        "pdf_type": "pdf",
        "md5sum": f"md5-{referencefile_id}",
        "is_annotation": False,
        "referencefile_mods": _abc_mod_rows(*mods),
    }


def _abc_by_md5_main_pdf(
    *mods: str | None,
    open_access: bool = False,
) -> dict[str, Any]:
    return {
        "referencefile_id": 10,
        "reference_id": 101,
        "reference_curie": "AGRKB:101",
        "display_name": "WBPaper00067001",
        "file_class": "main",
        "file_publication_status": "final",
        "file_extension": "pdf",
        "pdf_type": "pdf",
        "md5sum": "abc123",
        "is_annotation": False,
        "open_access": open_access,
        "copyright_license_name": None,
        "referencefile_mods": _abc_mod_rows(*mods),
        "converted_referencefiles": [
            {
                "referencefile_id": 11,
                "display_name": "WBPaper00067001_merged",
                "file_class": "converted_merged_main",
                "file_extension": "md",
            }
        ],
    }


def _abc_reference_record(
    *,
    copyright_license_open_access: bool | None = None,
    copyright_license_name: str | None = None,
) -> dict[str, Any]:
    return {
        "reference_id": 101,
        "curie": "AGRKB:101",
        "title": "A WormBase paper",
        "cross_references": [
            {"curie": "PMID:41000001", "curie_prefix": "PMID", "is_obsolete": False},
            {
                "curie": "WB:WBPaper00067001",
                "curie_prefix": "WB",
                "is_obsolete": False,
            },
        ],
        "copyright_license_name": copyright_license_name,
        "copyright_license_open_access": copyright_license_open_access,
    }


@pytest.mark.asyncio
async def test_source_pdf_null_and_named_mod_maps_to_any_group() -> None:
    fake_client = FakeABCLiteratureClient()
    fake_client.show_reference_payload = _abc_reference_record()
    fake_client.by_md5_payload = [_abc_by_md5_main_pdf(None, "WB")]
    provider = provider_from_fake(fake_client)

    source_artifact, converted_artifact = await provider.find_artifacts_by_checksum(
        "abc123"
    )

    assert source_artifact.access_policy == SourceAccessPolicy(
        scope=SourceAccessScope.ANY_GROUP
    )
    assert converted_artifact.access_policy == source_artifact.access_policy
    assert source_artifact_is_authorized(source_artifact, authorized_group_ids=("FB",))
    assert source_artifact_is_authorized(source_artifact, authorized_group_ids=("WB",))
    # ABC refuses a caller with no MOD access the no-MOD grant.
    assert not source_artifact_is_authorized(source_artifact, authorized_group_ids=())


@pytest.mark.asyncio
async def test_listed_source_pdf_null_and_named_mod_maps_to_any_group() -> None:
    fake_client = FakeABCLiteratureClient()
    fake_client.show_referencefiles_payload = [  # type: ignore[assignment]
        _abc_show_all_main_pdf(5001, None, "WB"),
    ]
    provider = provider_from_fake(fake_client)

    (artifact,) = await provider.list_artifacts("AGRKB:101")

    assert artifact.access_policy == SourceAccessPolicy(
        scope=SourceAccessScope.ANY_GROUP
    )


@pytest.mark.asyncio
async def test_source_pdf_with_only_named_mods_stays_restricted_to_them() -> None:
    fake_client = FakeABCLiteratureClient()
    fake_client.show_referencefiles_payload = [  # type: ignore[assignment]
        _abc_show_all_main_pdf(5001, "WB", "ZFIN"),
    ]
    provider = provider_from_fake(fake_client)

    (artifact,) = await provider.list_artifacts("AGRKB:101")

    assert artifact.access_policy == SourceAccessPolicy(
        scope=SourceAccessScope.RESTRICTED,
        group_ids=("WB", "ZFIN"),
    )
    assert not source_artifact_is_authorized(artifact, authorized_group_ids=("FB",))
    assert source_artifact_is_authorized(artifact, authorized_group_ids=("ZFIN",))


@pytest.mark.asyncio
async def test_source_pdf_without_mod_entries_stays_unknown_and_denied() -> None:
    fake_client = FakeABCLiteratureClient()
    fake_client.show_reference_payload = _abc_reference_record()
    fake_client.by_md5_payload = [_abc_by_md5_main_pdf()]
    provider = provider_from_fake(fake_client)

    source_artifact, _converted = await provider.find_artifacts_by_checksum("abc123")

    assert source_artifact.access_policy.scope is SourceAccessScope.UNKNOWN
    assert not source_artifact_is_authorized(
        source_artifact, authorized_group_ids=("WB",)
    )


@pytest.mark.asyncio
async def test_null_mod_on_converted_row_is_still_not_an_access_grant() -> None:
    fake_client = FakeABCLiteratureClient()
    fake_client.show_referencefiles_payload = [  # type: ignore[assignment]
        {
            "referencefile_id": 6001,
            "display_name": "WBPaper00067001_merged",
            "file_class": "converted_merged_main",
            "file_publication_status": "final",
            "file_extension": "md",
            "md5sum": "md5-6001",
            "is_annotation": False,
            "referencefile_mods": _abc_mod_rows(None, "WB"),
        }
    ]
    provider = provider_from_fake(fake_client)

    (artifact,) = await provider.list_artifacts("AGRKB:101")

    assert artifact.role is SourceArtifactRole.CONVERTED_TEXT
    assert artifact.access_policy == SourceAccessPolicy(
        scope=SourceAccessScope.RESTRICTED,
        group_ids=("WB",),
    )


@pytest.mark.asyncio
async def test_checksum_open_access_reference_maps_named_mod_pdf_to_global() -> None:
    fake_client = FakeABCLiteratureClient()
    fake_client.by_md5_payload = [_abc_by_md5_main_pdf("WB", open_access=True)]
    provider = provider_from_fake(fake_client)

    source_artifact, converted_artifact = await provider.find_artifacts_by_checksum(
        "abc123"
    )

    assert source_artifact.access_policy == SourceAccessPolicy(
        scope=SourceAccessScope.GLOBAL
    )
    assert converted_artifact.access_policy == source_artifact.access_policy
    # ABC serves open-access files even to a caller with no MOD access.
    assert source_artifact_is_authorized(source_artifact, authorized_group_ids=())


@pytest.mark.asyncio
async def test_checksum_open_access_reference_maps_entryless_pdf_to_global() -> None:
    fake_client = FakeABCLiteratureClient()
    fake_client.by_md5_payload = [_abc_by_md5_main_pdf(open_access=True)]
    provider = provider_from_fake(fake_client)

    source_artifact, _converted = await provider.find_artifacts_by_checksum("abc123")

    assert source_artifact.access_policy.scope is SourceAccessScope.GLOBAL


@pytest.mark.asyncio
async def test_listing_uses_open_access_from_the_resolved_reference_record() -> None:
    fake_client = FakeABCLiteratureClient()
    fake_client.show_reference_payload = _abc_reference_record(
        copyright_license_open_access=True
    )
    fake_client.show_referencefiles_payload = [  # type: ignore[assignment]
        _abc_show_all_main_pdf(5001, "WB"),
    ]
    provider = provider_from_fake(fake_client)

    reference = await provider.resolve_reference("AGRKB:101")
    (artifact,) = await provider.list_artifacts(reference)

    assert artifact.access_policy == SourceAccessPolicy(scope=SourceAccessScope.GLOBAL)


@pytest.mark.asyncio
@pytest.mark.parametrize("open_access", [False, None])
async def test_listing_without_open_access_reference_keeps_mod_rule(
    open_access: bool | None,
) -> None:
    fake_client = FakeABCLiteratureClient()
    fake_client.show_reference_payload = _abc_reference_record(
        copyright_license_open_access=open_access
    )
    fake_client.show_referencefiles_payload = [  # type: ignore[assignment]
        _abc_show_all_main_pdf(5001, "WB"),
    ]
    provider = provider_from_fake(fake_client)

    reference = await provider.resolve_reference("AGRKB:101")
    (artifact,) = await provider.list_artifacts(reference)

    assert artifact.access_policy == SourceAccessPolicy(
        scope=SourceAccessScope.RESTRICTED,
        group_ids=("WB",),
    )


@pytest.mark.asyncio
async def test_identifier_import_of_null_and_wb_pdf_is_ready_for_non_wb_curator() -> None:
    fake_client = FakeABCLiteratureClient()
    fake_client.external_lookup_payload = _abc_external_lookup(
        "PMID:41000001",
        reference_curie="AGRKB:101",
        title="A WormBase paper",
    )
    fake_client.show_reference_payload = _abc_reference_record()
    fake_client.show_referencefiles_payload = [  # type: ignore[assignment]
        _abc_show_all_main_pdf(5001, None, "WB"),
    ]
    provider = provider_from_fake(fake_client)

    decision = await select_reference_import_candidate(
        bearer_kind=ProviderBearerKind.CURATOR,
        provider=provider,
        identifier="PMID:41000001",
        authorized_group_ids=("FB",),
        allow_conversion_request=False,
    )

    assert decision.status is not ReferenceImportDecisionStatus.ACCESS_DENIED
    assert decision.selected is not None
    assert decision.selected.source_artifact.artifact_id == "5001"


@pytest.mark.asyncio
async def test_identifier_import_of_open_access_wb_pdf_is_ready_for_non_wb_curator() -> None:
    fake_client = FakeABCLiteratureClient()
    fake_client.external_lookup_payload = _abc_external_lookup(
        "PMID:41000001",
        reference_curie="AGRKB:101",
        title="A WormBase paper",
    )
    fake_client.show_reference_payload = _abc_reference_record(
        copyright_license_open_access=True
    )
    fake_client.show_referencefiles_payload = [  # type: ignore[assignment]
        _abc_show_all_main_pdf(5001, "WB"),
    ]
    provider = provider_from_fake(fake_client)

    decision = await select_reference_import_candidate(
        bearer_kind=ProviderBearerKind.CURATOR,
        provider=provider,
        identifier="PMID:41000001",
        authorized_group_ids=("FB",),
        allow_conversion_request=False,
    )

    assert decision.selected is not None
    assert decision.selected.source_artifact.artifact_id == "5001"


@pytest.mark.asyncio
async def test_identifier_import_of_wb_only_pdf_stays_denied_for_non_wb_curator() -> None:
    fake_client = FakeABCLiteratureClient()
    fake_client.external_lookup_payload = _abc_external_lookup(
        "PMID:41000001",
        reference_curie="AGRKB:101",
        title="A WormBase paper",
    )
    fake_client.show_reference_payload = _abc_reference_record()
    fake_client.show_referencefiles_payload = [  # type: ignore[assignment]
        _abc_show_all_main_pdf(5001, "WB"),
    ]
    provider = provider_from_fake(fake_client)

    decision = await select_reference_import_candidate(
        bearer_kind=ProviderBearerKind.CURATOR,
        provider=provider,
        identifier="PMID:41000001",
        authorized_group_ids=("FB",),
        allow_conversion_request=False,
    )

    assert decision.status is ReferenceImportDecisionStatus.ACCESS_DENIED
    assert decision.selected is None


async def _two_shared_pdf_decision(
    authorized_group_ids: tuple[str, ...],
    *,
    first_pdf_mods: tuple[str | None, ...] = (None, "WB"),
):
    fake_client = FakeABCLiteratureClient()
    fake_client.external_lookup_payload = _abc_external_lookup(
        "PMID:41000001",
        reference_curie="AGRKB:101",
        title="A WormBase paper",
    )
    fake_client.show_reference_payload = _abc_reference_record()
    fake_client.show_referencefiles_payload = [  # type: ignore[assignment]
        _abc_show_all_main_pdf(5001, *first_pdf_mods, display_name="WBPaper00067001"),
        _abc_show_all_main_pdf(5002, None, display_name="PMC12000001"),
    ]
    provider = provider_from_fake(fake_client)
    return await select_reference_import_candidate(
        bearer_kind=ProviderBearerKind.CURATOR,
        provider=provider,
        identifier="PMID:41000001",
        authorized_group_ids=authorized_group_ids,
        allow_conversion_request=False,
    )


@pytest.mark.asyncio
async def test_two_shared_pdfs_still_prefer_the_curators_mod_pdf() -> None:
    decision = await _two_shared_pdf_decision(("WB",))

    assert decision.status is ReferenceImportDecisionStatus.READY
    assert decision.selected is not None
    assert decision.selected.source_artifact.artifact_id == "5001"


@pytest.mark.asyncio
async def test_two_shared_pdfs_prefer_the_no_mod_only_pdf_for_other_curators() -> None:
    # AI Curation's own tie-break below ABC's precedence: the general no-MOD
    # copy beats a no-MOD PDF that also names another MOD.
    decision = await _two_shared_pdf_decision(("FB",))

    assert decision.status is ReferenceImportDecisionStatus.READY
    assert decision.selected is not None
    assert decision.selected.source_artifact.artifact_id == "5002"


@pytest.mark.asyncio
async def test_two_no_mod_only_pdfs_remain_ambiguous() -> None:
    decision = await _two_shared_pdf_decision(("FB",), first_pdf_mods=(None,))

    assert decision.status is ReferenceImportDecisionStatus.AMBIGUOUS_MATCH
    assert decision.metadata == {"match_count": 2}


async def _identifier_decision(
    referencefiles: list[dict[str, Any]],
    authorized_group_ids: tuple[str, ...],
    **reference_fields: Any,
):
    fake_client = FakeABCLiteratureClient()
    fake_client.external_lookup_payload = _abc_external_lookup(
        "PMID:41000001",
        reference_curie="AGRKB:101",
        title="A WormBase paper",
    )
    fake_client.show_reference_payload = _abc_reference_record(**reference_fields)
    fake_client.show_referencefiles_payload = referencefiles  # type: ignore[assignment]
    provider = provider_from_fake(fake_client)
    return await select_reference_import_candidate(
        bearer_kind=ProviderBearerKind.SERVICE,
        provider=provider,
        identifier="PMID:41000001",
        authorized_group_ids=authorized_group_ids,
        allow_conversion_request=False,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mods", [(None,), (None, "WB")])
async def test_caller_without_groups_is_denied_no_mod_pdfs(
    mods: tuple[str | None, ...],
) -> None:
    decision = await _identifier_decision([_abc_show_all_main_pdf(5001, *mods)], ())

    assert decision.status is ReferenceImportDecisionStatus.ACCESS_DENIED
    assert decision.selected is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mods", [(None,), (None, "WB")])
async def test_caller_with_any_group_is_allowed_no_mod_pdfs(
    mods: tuple[str | None, ...],
) -> None:
    decision = await _identifier_decision(
        [_abc_show_all_main_pdf(5001, *mods)], ("SGD",)
    )

    assert decision.status is ReferenceImportDecisionStatus.READY
    assert decision.selected is not None
    assert decision.selected.source_artifact.artifact_id == "5001"


@pytest.mark.asyncio
async def test_caller_without_groups_is_allowed_open_access_pdfs() -> None:
    decision = await _identifier_decision(
        [_abc_show_all_main_pdf(5001, "WB")],
        (),
        copyright_license_name="CC BY",
        copyright_license_open_access=True,
    )

    assert decision.status is ReferenceImportDecisionStatus.READY


@pytest.mark.asyncio
@pytest.mark.parametrize("mods", [("WB",), (None,), (None, "WB")])
async def test_license_with_unset_open_access_refuses_every_caller(
    mods: tuple[str | None, ...],
) -> None:
    # ABC's download_file: a license whose open_access is NULL leaves the
    # permission unset, skips the MOD checks and refuses the download.
    decision = await _identifier_decision(
        [_abc_show_all_main_pdf(5001, *mods)],
        ("WB",),
        copyright_license_name="Some license",
        copyright_license_open_access=None,
    )

    assert decision.status is ReferenceImportDecisionStatus.ACCESS_DENIED


@pytest.mark.asyncio
async def test_closed_license_keeps_the_mod_rule() -> None:
    decision = await _identifier_decision(
        [_abc_show_all_main_pdf(5001, "WB")],
        ("WB",),
        copyright_license_name="Closed license",
        copyright_license_open_access=False,
    )

    assert decision.status is ReferenceImportDecisionStatus.READY


def _abc_show_all_supplement_pdf(referencefile_id: int, *mods: str | None) -> dict[str, Any]:
    return {
        **_abc_show_all_main_pdf(referencefile_id, *mods, display_name="supp1"),
        "file_class": "supplement",
    }


@pytest.mark.asyncio
async def test_lone_accessible_supplement_is_never_imported_as_the_paper() -> None:
    decision = await _identifier_decision(
        [
            _abc_show_all_main_pdf(5001, "WB"),
            _abc_show_all_supplement_pdf(5002, None, "WB"),
        ],
        ("FB",),
    )

    assert decision.status is ReferenceImportDecisionStatus.ACCESS_DENIED
    assert decision.selected is None


@pytest.mark.asyncio
async def test_lone_accessible_supplement_alone_is_not_a_candidate() -> None:
    decision = await _identifier_decision(
        [_abc_show_all_supplement_pdf(5002, None)],
        ("FB",),
    )

    assert decision.status is ReferenceImportDecisionStatus.NO_SOURCE_ARTIFACT
    assert decision.selected is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [{"pdf_type": "ocr"}, {"pdf_type": "tif"}, {"file_publication_status": "temp"}],
)
async def test_lone_non_standard_main_pdf_still_imports(overrides: dict[str, str]) -> None:
    decision = await _identifier_decision(
        [
            {**_abc_show_all_main_pdf(5001, None), **overrides},
            _abc_show_all_supplement_pdf(5002, None),
        ],
        ("FB",),
    )

    assert decision.status is ReferenceImportDecisionStatus.READY
    assert decision.selected is not None
    assert decision.selected.source_artifact.artifact_id == "5001"


@pytest.mark.asyncio
async def test_standard_final_pdf_main_wins_over_an_ocr_main() -> None:
    decision = await _identifier_decision(
        [
            {**_abc_show_all_main_pdf(5001, None), "pdf_type": "ocr"},
            _abc_show_all_main_pdf(5003, None, display_name="PMC12000001"),
        ],
        ("FB",),
    )

    assert decision.status is ReferenceImportDecisionStatus.READY
    assert decision.selected is not None
    assert decision.selected.source_artifact.artifact_id == "5003"


def test_reference_source_artifact_contract_accepts_any_main_class_pdf() -> None:
    provider = provider_from_fake(FakeABCLiteratureClient())

    def _pdf(
        artifact_format: SourceArtifactFormat = SourceArtifactFormat.PDF,
        **metadata: str,
    ) -> SourceArtifact:
        return SourceArtifact(
            provider="abc_literature",
            artifact_id="pdf",
            role=SourceArtifactRole.SOURCE_PDF,
            artifact_format=artifact_format,
            metadata={
                "file_class": "main",
                "file_publication_status": "final",
                "pdf_type": "pdf",
                **metadata,
            },
        )

    assert provider.is_reference_source_artifact(_pdf()) is True
    assert provider.is_reference_source_artifact(_pdf(pdf_type="ocr")) is True
    assert provider.is_reference_source_artifact(_pdf(file_publication_status="temp")) is True
    assert provider.is_reference_source_artifact(_pdf(file_class="supplement")) is False
    assert (
        provider.is_reference_source_artifact(_pdf(SourceArtifactFormat.UNKNOWN)) is False
    )


@pytest.mark.asyncio
async def test_listed_main_html_row_is_not_a_candidate() -> None:
    decision = await _identifier_decision(
        [{**_abc_show_all_main_pdf(5001, None), "file_extension": "html"}],
        ("FB",),
    )

    assert decision.status is ReferenceImportDecisionStatus.NO_SOURCE_ARTIFACT


def _ocr_main(referencefile_id: int, *mods: str | None) -> dict[str, Any]:
    return {
        **_abc_show_all_main_pdf(referencefile_id, *mods, display_name=f"scan{referencefile_id}"),
        "pdf_type": "ocr",
    }


@pytest.mark.asyncio
async def test_non_standard_mains_prefer_the_curators_mod() -> None:
    decision = await _identifier_decision(
        [_ocr_main(5001, "WB"), _ocr_main(5002, None)],
        ("WB",),
    )

    assert decision.status is ReferenceImportDecisionStatus.READY
    assert decision.selected is not None
    assert decision.selected.source_artifact.artifact_id == "5001"


@pytest.mark.asyncio
async def test_non_standard_mains_prefer_the_no_mod_only_copy() -> None:
    decision = await _identifier_decision(
        [_ocr_main(5001, None, "WB"), _ocr_main(5002, None)],
        ("FB",),
    )

    assert decision.status is ReferenceImportDecisionStatus.READY
    assert decision.selected is not None
    assert decision.selected.source_artifact.artifact_id == "5002"


@pytest.mark.asyncio
async def test_two_no_mod_only_non_standard_mains_remain_ambiguous() -> None:
    decision = await _identifier_decision(
        [_ocr_main(5001, None), _ocr_main(5002, None)],
        ("FB",),
    )

    assert decision.status is ReferenceImportDecisionStatus.AMBIGUOUS_MATCH
    assert decision.metadata == {"match_count": 2}


@pytest.mark.asyncio
async def test_standard_no_mod_pdf_beats_the_curators_mod_ocr_main() -> None:
    decision = await _identifier_decision(
        [_ocr_main(5001, "WB"), _abc_show_all_main_pdf(5002, None)],
        ("WB",),
    )

    assert decision.status is ReferenceImportDecisionStatus.READY
    assert decision.selected is not None
    assert decision.selected.source_artifact.artifact_id == "5002"


# KANBAN-1976 / ALL-1460 P2: rows below follow ABC's flat ``show_all`` shape
# (``ReferencefileSchemaRelated``): one row per file, and a converted text row
# names its original in ``source`` (null when ABC cannot resolve it).


def _abc_show_all_row(
    referencefile_id: int,
    *mods: str | None,
    file_class: str,
    file_extension: str,
    display_name: str,
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "referencefile_id": referencefile_id,
        "reference_id": 101,
        "display_name": display_name,
        "file_class": file_class,
        "file_publication_status": "final",
        "file_extension": file_extension,
        "pdf_type": None,
        "md5sum": f"md5-{referencefile_id}",
        "is_annotation": False,
        "referencefile_mods": _abc_mod_rows(*mods),
        "source": source,
    }


def _abc_source(row: dict[str, Any]) -> dict[str, Any]:
    """ABC ``_source_dict`` of an original row."""

    return {
        key: row[key]
        for key in ("referencefile_id", "display_name", "file_class", "file_extension", "md5sum")
    }


def _office_supplement_pair(
    original_id: int,
    *mods: str | None,
    extension: str = "xlsx",
    converted_mods: tuple[str | None, ...] | None = None,
) -> list[dict[str, Any]]:
    original = _abc_show_all_row(
        original_id,
        *mods,
        file_class="supplement",
        file_extension=extension,
        display_name="Table_S1",
    )
    converted = _abc_show_all_row(
        original_id + 1,
        *(mods if converted_mods is None else converted_mods),
        file_class="converted_merged_supplement",
        file_extension="md",
        display_name=f"Table_S1_{extension}",
        source=_abc_source(original),
    )
    return [original, converted]


async def _list_with_reference(
    rows: list[dict[str, Any]],
    *,
    copyright_license_open_access: bool | None = None,
    copyright_license_name: str | None = None,
) -> list[SourceArtifact]:
    fake_client = FakeABCLiteratureClient()
    fake_client.show_reference_payload = _abc_reference_record(
        copyright_license_open_access=copyright_license_open_access,
        copyright_license_name=copyright_license_name,
    )
    fake_client.show_referencefiles_payload = rows  # type: ignore[assignment]
    provider = provider_from_fake(fake_client)
    reference = await provider.resolve_reference("AGRKB:101")
    return await provider.list_artifacts(reference)


@pytest.mark.asyncio
@pytest.mark.parametrize("extension", ["xlsx", "xlsm", "docx", "docm"])
async def test_office_supplement_maps_to_supplement_role_and_links_its_converted_text(
    extension: str,
) -> None:
    original, converted = await _list_with_reference(
        _office_supplement_pair(8001, None, extension=extension)
    )

    assert original.role is SourceArtifactRole.SOURCE_SUPPLEMENT
    assert original.parent_artifact_id is None
    assert converted.role is SourceArtifactRole.CONVERTED_TEXT
    assert converted.artifact_format is SourceArtifactFormat.MARKDOWN
    assert converted.parent_artifact_id == "8001"
    assert converted.reference_id == "101"


@pytest.mark.asyncio
async def test_no_mod_xlsx_and_its_converted_row_are_readable_by_any_group() -> None:
    original, converted = await _list_with_reference(_office_supplement_pair(8001, None))

    assert original.access_policy == SourceAccessPolicy(scope=SourceAccessScope.ANY_GROUP)
    assert converted.access_policy == original.access_policy
    for group in ("FB", "MGI", "WB"):
        assert source_artifact_is_authorized(converted, authorized_group_ids=(group,))
    # ABC's no-MOD grant needs some MOD access; a caller with none is refused.
    assert not source_artifact_is_authorized(converted, authorized_group_ids=())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("license_name", "open_access"), [(None, None), ("Closed license", False)]
)
async def test_mod_restricted_docx_on_a_not_open_access_reference_is_denied_to_another_mod(
    license_name: str | None, open_access: bool | None
) -> None:
    main_pdf = _abc_show_all_main_pdf(5001, None, display_name="paper_main")
    artifacts = await _list_with_reference(
        [main_pdf, *_office_supplement_pair(8001, "WB", extension="docx")],
        copyright_license_name=license_name,
        copyright_license_open_access=open_access,
    )
    main, docx, converted = artifacts

    assert source_artifact_is_authorized(main, authorized_group_ids=("FB",))
    assert docx.access_policy == SourceAccessPolicy(
        scope=SourceAccessScope.RESTRICTED, group_ids=("WB",)
    )
    assert not source_artifact_is_authorized(docx, authorized_group_ids=("FB",))
    assert not source_artifact_is_authorized(converted, authorized_group_ids=("FB",))
    assert source_artifact_is_authorized(converted, authorized_group_ids=("WB",))


@pytest.mark.asyncio
async def test_mod_restricted_docx_on_an_open_access_reference_is_readable_by_any_caller() -> None:
    _docx, converted = await _list_with_reference(
        _office_supplement_pair(8001, "WB", extension="docx"),
        copyright_license_name="Open license",
        copyright_license_open_access=True,
    )

    assert converted.access_policy == SourceAccessPolicy(scope=SourceAccessScope.GLOBAL)
    assert source_artifact_is_authorized(converted, authorized_group_ids=("FB",))
    assert source_artifact_is_authorized(converted, authorized_group_ids=())


@pytest.mark.asyncio
async def test_license_with_unset_open_access_refuses_a_no_mod_supplement() -> None:
    original, converted = await _list_with_reference(
        _office_supplement_pair(8001, None),
        copyright_license_name="Named license",
        copyright_license_open_access=None,
    )

    assert original.access_policy.scope is SourceAccessScope.UNKNOWN
    assert not source_artifact_is_authorized(converted, authorized_group_ids=("WB",))


@pytest.mark.asyncio
async def test_converted_supplement_takes_its_originals_policy_not_its_own_mod_rows() -> None:
    _original, converted = await _list_with_reference(
        _office_supplement_pair(8001, "WB", converted_mods=(None,))
    )

    assert converted.access_policy == SourceAccessPolicy(
        scope=SourceAccessScope.RESTRICTED, group_ids=("WB",)
    )
    assert not source_artifact_is_authorized(converted, authorized_group_ids=("FB",))


@pytest.mark.asyncio
async def test_converted_supplement_with_null_source_is_never_offered() -> None:
    original, converted = _office_supplement_pair(8001, None)
    converted["source"] = None

    artifacts = await _list_with_reference([original, converted])

    assert [artifact.artifact_id for artifact in artifacts] == ["8001"]


@pytest.mark.asyncio
async def test_converted_supplement_naming_an_unlisted_source_is_left_out_and_reported(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from agr_ai_curation_alliance.document_sources import abc_literature

    reports: list[dict[str, Any]] = []

    def _report(exc: BaseException, **kwargs: Any) -> bool:
        reports.append({"message": str(exc), **kwargs})
        return True

    monkeypatch.setattr(abc_literature, "report_runtime_exception", _report)
    main_pdf = _abc_show_all_main_pdf(5001, None, display_name="paper_main")
    _original, orphan = _office_supplement_pair(8001, None)
    orphan["display_name"] = "Secret_Title_S1_xlsx"
    kept_original, kept_converted = _office_supplement_pair(8101, None)

    with caplog.at_level("WARNING", logger=abc_literature.__name__):
        artifacts = await _list_with_reference(
            [main_pdf, orphan, kept_original, kept_converted]
        )

    assert [artifact.artifact_id for artifact in artifacts] == ["5001", "8101", "8102"]
    warning = next(record for record in caplog.records if "left out" in record.getMessage())
    assert "8002" in warning.getMessage() and "8001" in warning.getMessage()
    assert getattr(warning, "sentry_skip_event", False) is True
    (report,) = reports
    assert report["component"] == "document_source"
    assert report["operation"] == "converted_source_not_listed"
    assert report["level"] == "warning"
    assert report["fingerprint"] == ("document_source", "converted_source_not_listed")
    # Only hashed ids reach Sentry: no raw ids, file names or reference.
    flattened = repr(report)
    for raw in ("8002", "8001", "Secret_Title", "AGRKB:101"):
        assert raw not in flattened
    assert set(report["context"]) == {"artifact_id_hash", "source_artifact_id_hash"}
    assert all(report["context"].values())


@pytest.mark.asyncio
async def test_supplement_pdf_converted_text_is_linked_to_the_pdf_and_carries_its_policy() -> None:
    # The converted row here names only WB while its PDF has a no-MOD row: the
    # state before ABC syncs a converted row's MOD links from its source. ABC's
    # own download check would use the converted row's WB row, so the inherited
    # ANY_GROUP is wider than ABC for this row. Nothing reads converted
    # supplement policy in P2; P6 authorizes with the intersection of the
    # original's policy and the converted row's own rule.
    pdf = _abc_show_all_row(
        8101, None, file_class="supplement", file_extension="pdf", display_name="Data_S2"
    )
    converted = _abc_show_all_row(
        8102,
        "WB",
        file_class="converted_merged_supplement",
        file_extension="md",
        display_name="Data_S2_merged",
        source=_abc_source(pdf),
    )

    source, text = await _list_with_reference([pdf, converted])

    assert source.role is SourceArtifactRole.SOURCE_PDF
    assert text.parent_artifact_id == "8101"
    assert text.access_policy == SourceAccessPolicy(scope=SourceAccessScope.ANY_GROUP)


@pytest.mark.asyncio
async def test_large_data_file_and_unconverted_supplements_keep_their_roles() -> None:
    htp = _abc_show_all_row(
        8201, None, file_class="htp_supplement", file_extension="xlsx", display_name="Data_S3"
    )
    csv = _abc_show_all_row(
        8202, None, file_class="supplement", file_extension="csv", display_name="Data_S4"
    )

    htp_artifact, csv_artifact = await _list_with_reference([htp, csv])

    assert htp_artifact.role is SourceArtifactRole.UNKNOWN
    assert csv_artifact.role is SourceArtifactRole.UNKNOWN
    # Originals carry ABC's no-MOD grant whatever their class.
    assert htp_artifact.access_policy.scope is SourceAccessScope.ANY_GROUP


@pytest.mark.asyncio
async def test_main_paper_import_is_unchanged_for_a_one_pdf_paper_with_supplements() -> None:
    main_pdf = {**_abc_show_all_main_pdf(5001, None, display_name="paper_main"), "source": None}
    main_text = _abc_show_all_row(
        5002,
        None,
        file_class="converted_merged_main",
        file_extension="md",
        display_name="paper_main_merged",
        source=_abc_source(main_pdf),
    )
    fake_client = FakeABCLiteratureClient()
    fake_client.external_lookup_payload = _abc_external_lookup(
        "PMID:41000001", reference_curie="AGRKB:101", title="A paper"
    )
    fake_client.show_reference_payload = _abc_reference_record()
    fake_client.show_referencefiles_payload = [  # type: ignore[assignment]
        main_pdf, main_text, *_office_supplement_pair(8001, None),
    ]

    decision = await select_reference_import_candidate(
        bearer_kind=ProviderBearerKind.CURATOR,
        provider=provider_from_fake(fake_client),
        identifier="PMID:41000001",
        authorized_group_ids=("FB",),
        allow_conversion_request=False,
    )

    assert decision.status is ReferenceImportDecisionStatus.READY
    assert decision.selected is not None
    assert decision.selected.source_artifact.artifact_id == "5001"
    assert decision.selected.converted_artifact is not None
    assert decision.selected.converted_artifact.artifact_id == "5002"
    assert decision.selected.provider_metadata_artifacts == ()


def _figure_metadata_row(referencefile_id: int, file_class: str, display_name: str) -> dict[str, Any]:
    return _abc_show_all_row(
        referencefile_id,
        None,
        file_class=file_class,
        file_extension="json",
        display_name=display_name,
    )


@pytest.mark.asyncio
async def test_main_paper_without_its_own_figure_metadata_gets_no_supplement_sidecars() -> None:
    main_pdf = _abc_show_all_main_pdf(5001, None, display_name="paper_main")
    artifacts = await _list_with_reference(
        [
            main_pdf,
            _figure_metadata_row(9001, "converted_supplement_figure_metadata", "Data_S2_image_1"),
        ]
    )
    provider = provider_from_fake(FakeABCLiteratureClient())

    assert provider.provider_metadata_artifacts_for_source(artifacts[0], artifacts) == ()


@pytest.mark.asyncio
async def test_main_paper_gets_no_sidecars_named_for_another_file() -> None:
    main_pdf = _abc_show_all_main_pdf(5001, None, display_name="paper_main")
    artifacts = await _list_with_reference(
        [
            main_pdf,
            _figure_metadata_row(9001, "converted_main_figure_metadata", "other_main_image_1"),
            _figure_metadata_row(9002, "converted_main_figure_metadata", "paper_main_image_1"),
        ]
    )
    provider = provider_from_fake(FakeABCLiteratureClient())

    sidecars = provider.provider_metadata_artifacts_for_source(artifacts[0], artifacts)

    assert [artifact.artifact_id for artifact in sidecars] == ["9002"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (404, "DocumentSourceArtifactNotFound"),
        (502, "DocumentSourceUnavailable"),
        (503, "DocumentSourceUnavailable"),
        (504, "DocumentSourceUnavailable"),
    ],
)
async def test_download_maps_missing_file_and_unreachable_abc_to_their_own_errors(
    status: int, error_type: str
) -> None:
    from src.lib.document_sources import models

    fake_client = FakeABCLiteratureClient()
    fake_client.download_error = ABCLiteratureHTTPError(
        f"ABC Literature returned HTTP {status}",
        status_code=status,
        endpoint="/reference/referencefile/download_file/55",
    )

    with pytest.raises(getattr(models, error_type)) as caught:
        await provider_from_fake(fake_client).download_artifact("55")

    assert not isinstance(caught.value, DocumentSourceAccessDenied)


@pytest.mark.asyncio
async def test_download_before_abc_reports_missing_files_stays_a_generic_failure() -> None:
    from src.lib.document_sources.models import (
        DocumentSourceArtifactNotFound,
        DocumentSourceUnavailable,
    )

    fake_client = FakeABCLiteratureClient()
    fake_client.download_error = ABCLiteratureHTTPError(
        "ABC Literature returned HTTP 500",
        status_code=500,
        endpoint="/reference/referencefile/download_file/55",
    )

    with pytest.raises(DocumentSourceError) as caught:
        await provider_from_fake(fake_client).download_artifact("55")

    assert type(caught.value) is DocumentSourceError
    assert not isinstance(caught.value, (DocumentSourceArtifactNotFound, DocumentSourceUnavailable))


@pytest.mark.asyncio
async def test_download_passes_the_cap_to_the_client() -> None:
    fake_client = FakeABCLiteratureClient()
    fake_client.download_referencefile = AsyncMock(return_value=b"text")  # type: ignore[method-assign]

    await provider_from_fake(fake_client).download_artifact(
        "55", request_bearer_token="curator-token", max_bytes=1000
    )

    fake_client.download_referencefile.assert_awaited_once_with(
        "55", request_bearer_token="curator-token", max_bytes=1000
    )


@pytest.mark.asyncio
async def test_oversized_download_stops_mid_stream_with_its_own_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    from src.lib.openai_agents.config import get_document_file_max_converted_text_bytes

    monkeypatch.setenv("DOCUMENT_FILE_MAX_CONVERTED_TEXT_BYTES", "15")

    from agr_ai_curation_alliance.literature.client import (
        ABCLiteratureClient,
        ABCLiteratureClientConfig,
    )
    from src.lib.document_sources.models import DocumentSourceArtifactTooLarge

    class _Stream(httpx.AsyncByteStream):
        def __init__(self) -> None:
            self.reads = 0
            self.closed = False

        async def __aiter__(self):
            for chunk in (b"| a | b |\n", b"| 1 | 2 |\n", b"never read"):
                self.reads += 1
                yield chunk

        async def aclose(self) -> None:
            self.closed = True

    stream = _Stream()
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, stream=stream))
    async with httpx.AsyncClient(transport=transport) as http:
        client = ABCLiteratureClient(
            ABCLiteratureClientConfig(base_url="https://literature.test"), http_client=http
        )
        provider = ABCLiteratureDocumentSourceProvider(client)

        with pytest.raises(DocumentSourceArtifactTooLarge) as caught:
            await provider.download_artifact(
                "55", max_bytes=get_document_file_max_converted_text_bytes()
            )

    assert caught.value.max_bytes == 15
    # The second chunk crosses the limit; the stream is closed before the third.
    assert stream.reads == 2
    assert stream.closed


@pytest.mark.asyncio
async def test_curator_import_keeps_todays_main_text_choice_with_nxml_and_merged_main_text() -> None:
    # P2 links only supplement text. Main-text rows stay unlinked, so a curator
    # import still takes the nXML-derived main text even though ABC names the
    # nXML row, not the PDF, as its ``source``.
    main_pdf = {**_abc_show_all_main_pdf(5001, None, display_name="paper_main"), "source": None}
    nxml = _abc_show_all_row(
        5002, None, file_class="nXML", file_extension="xml", display_name="paper_main"
    )
    nxml_text = _abc_show_all_row(
        5003,
        None,
        file_class="converted_merged_main",
        file_extension="md",
        display_name="paper_main_nxml",
        source=_abc_source(nxml),
    )
    merged_text = _abc_show_all_row(
        5004,
        None,
        file_class="converted_merged_main",
        file_extension="md",
        display_name="paper_main_merged",
        source=_abc_source(main_pdf),
    )
    fake_client = FakeABCLiteratureClient()
    fake_client.external_lookup_payload = _abc_external_lookup(
        "PMID:41000001", reference_curie="AGRKB:101", title="A paper"
    )
    fake_client.show_reference_payload = _abc_reference_record()
    fake_client.show_referencefiles_payload = [  # type: ignore[assignment]
        main_pdf, nxml, nxml_text, merged_text,
    ]

    decision = await select_reference_import_candidate(
        bearer_kind=ProviderBearerKind.CURATOR,
        provider=provider_from_fake(fake_client),
        identifier="PMID:41000001",
        authorized_group_ids=("FB",),
        allow_conversion_request=False,
    )

    assert decision.status is ReferenceImportDecisionStatus.READY
    assert decision.selected is not None
    assert decision.selected.source_artifact.artifact_id == "5001"
    assert decision.selected.converted_artifact is not None
    assert decision.selected.converted_artifact.artifact_id == "5003"
    assert decision.selected.converted_artifact.parent_artifact_id is None


@pytest.mark.asyncio
async def test_two_main_pdf_paper_keeps_the_main_sidecars_of_the_text_it_imports() -> None:
    # The curator's chosen PDF was never converted; the reference-level main
    # text (and its figure metadata) comes from the other main PDF. Today's
    # behaviour keeps those main sidecars; supplement sidecars stay out.
    chosen = _abc_show_all_main_pdf(5001, None, display_name="shared_copy")
    other = _abc_show_all_main_pdf(5002, "FB", display_name="mod_copy")
    artifacts = await _list_with_reference(
        [
            chosen,
            other,
            _figure_metadata_row(9001, "converted_main_figure_metadata", "mod_copy_image_1"),
            _figure_metadata_row(9002, "converted_supplement_figure_metadata", "Data_S2_image_1"),
        ]
    )
    provider = provider_from_fake(FakeABCLiteratureClient())

    sidecars = provider.provider_metadata_artifacts_for_source(artifacts[0], artifacts)

    assert [artifact.artifact_id for artifact in sidecars] == ["9001"]

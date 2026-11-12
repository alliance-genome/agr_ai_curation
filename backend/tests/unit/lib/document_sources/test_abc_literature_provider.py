"""Tests for the ABC Literature document-source adapter."""

from __future__ import annotations

from typing import Any

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
    assert converted_artifact.access_policy.group_ids == ("FB",)
    assert converted_artifact.status is SourceArtifactStatus.AVAILABLE


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
    assert source_artifact.access_policy.group_ids == ("FB",)
    assert converted_artifact.parent_artifact_id == "10"
    assert converted_artifact.role is SourceArtifactRole.CONVERTED_TEXT
    assert converted_artifact.artifact_format is SourceArtifactFormat.MARKDOWN
    assert converted_artifact.status is SourceArtifactStatus.AVAILABLE
    assert converted_artifact.access_policy.scope is SourceAccessScope.RESTRICTED
    assert converted_artifact.access_policy.group_ids == ("FB",)


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


@pytest.mark.asyncio
async def test_bare_mod_string_does_not_create_access_entitlement() -> None:
    fake_client = FakeABCLiteratureClient()
    fake_client.by_md5_payload = [
        {
            "referencefile_id": 10,
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

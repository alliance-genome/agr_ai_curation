"""AI Curation's own group gate under the all-access development machine reader.

ABC returns every file to a client-credentials reader, so a login-free
development user's DEV_USER_GROUPS are the only thing standing between that user
and restricted papers. These tests drive the real dev-mode identity, request
context, ABC client and provider against a fake ABC that hides nothing.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from starlette.requests import Request

from agr_ai_curation_alliance.document_sources.abc_literature import (
    ABCLiteratureDocumentSourceProvider,
)
from agr_ai_curation_alliance.literature.client import (
    ABCLiteratureAuthMode,
    ABCLiteratureClient,
    ABCLiteratureClientConfig,
)
from src.api import auth as api_auth
from src.lib.document_sources import access
from src.lib.document_sources.identifier_import import (
    ReferenceImportDecisionStatus,
    select_reference_import_candidate,
)
from src.lib.document_sources.import_selection import (
    ChecksumImportDecisionStatus,
    select_checksum_import_candidate,
)
from src.lib.packages.document_source_provider_models import (
    DevelopmentReaderCredentials,
)

READER_BEARER = "synthetic-reader-bearer"
CHECKSUM = "0123456789abcdef0123456789abcdef"


def _source_pdf(referencefile_id: int, mod: str) -> dict[str, Any]:
    return {
        "referencefile_id": referencefile_id,
        "reference_id": 101,
        "reference_curie": "AGRKB:101",
        "display_name": f"{mod.lower()}-main",
        "file_class": "main",
        "file_extension": "pdf",
        "file_publication_status": "final",
        "pdf_type": "pdf",
        "md5sum": CHECKSUM,
        "referencefile_mods": [{"mod_abbreviation": mod}],
        "converted_referencefiles": [
            {
                "referencefile_id": referencefile_id + 1,
                "reference_id": 101,
                "reference_curie": "AGRKB:101",
                "display_name": f"{mod.lower()}-main_nxml",
                "file_class": "converted_merged_main",
                "file_extension": "md",
                "file_publication_status": "final",
                "referencefile_mods": [{"mod_abbreviation": None}],
            }
        ],
    }


class _AllAccessABC:
    """Fake ABC that, like a client-credentials reader, returns every file."""

    def __init__(self, mod: str):
        self.files = [_source_pdf(10, mod)]
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path.removeprefix("/api")
        if path == "/reference/AGRKB:101":
            return httpx.Response(200, json={"reference_id": 101, "reference_curie": "AGRKB:101"})
        if path == "/reference/referencefile/show_all/AGRKB:101":
            return httpx.Response(200, json=self.files)
        if path == f"/reference/referencefile/by_md5/{CHECKSUM}":
            return httpx.Response(200, json=self.files)
        if path.startswith("/reference/referencefile/download_file/"):
            return httpx.Response(200, content=b"%PDF-1.7 restricted bytes")
        return httpx.Response(404, json={"detail": "not found"})

    def provider(self) -> ABCLiteratureDocumentSourceProvider:
        transport = httpx.MockTransport(self.handle)
        return ABCLiteratureDocumentSourceProvider(
            ABCLiteratureClient(
                ABCLiteratureClientConfig(
                    base_url="https://literature.example/api",
                    auth_mode=ABCLiteratureAuthMode.NONE,
                ),
                http_client=httpx.AsyncClient(transport=transport),
            )
        )

    def downloads(self) -> list[str]:
        return [
            request.url.path
            for request in self.requests
            if "/download_file/" in request.url.path
        ]


async def _dev_context(monkeypatch, dev_user_groups: str):
    """Build the real login-free development identity and request context."""

    monkeypatch.setenv("DEV_MODE", "true")
    monkeypatch.setenv("DOCUMENT_SOURCE_IMPORT_ENABLED", "true")
    monkeypatch.setenv("DOCUMENT_SOURCE_PROVIDER", "abc_literature")
    monkeypatch.setenv("DEV_USER_GROUPS", dev_user_groups)
    monkeypatch.delenv("TESTING_API_KEY", raising=False)
    monkeypatch.setattr(api_auth, "is_dev_mode", lambda: True)

    async def reader():
        return DevelopmentReaderCredentials(token=READER_BEARER, expires_at=9999999999)

    monkeypatch.setattr(access, "get_development_reader_credentials", reader)
    request = Request({"type": "http", "headers": [], "query_string": b""})
    user_claims = await api_auth._get_user_from_cookie_impl(request)
    assert access.development_reader_required() is True
    return await access.build_document_source_request_context(
        request=request, user_claims=user_claims
    )


@pytest.mark.asyncio
async def test_fb_only_dev_user_cannot_import_a_wb_only_paper_by_identifier(monkeypatch):
    context = await _dev_context(monkeypatch, "FB")
    assert context.authorized_group_ids == ("FB",)
    abc = _AllAccessABC("WB")

    decision = await select_reference_import_candidate(
        provider=abc.provider(),
        identifier="AGRKB:101",
        authorized_group_ids=context.authorized_group_ids,
        request_bearer_token=context.curator_token,
        allow_conversion_request=False,
    )

    assert decision.status is ReferenceImportDecisionStatus.ACCESS_DENIED
    assert decision.selected is None
    assert abc.downloads() == []
    assert {request.headers["Authorization"] for request in abc.requests} == {
        f"Bearer {READER_BEARER}"
    }


@pytest.mark.asyncio
async def test_fb_only_dev_user_cannot_import_a_wb_only_paper_by_checksum(monkeypatch):
    context = await _dev_context(monkeypatch, "FB")
    abc = _AllAccessABC("WB")

    decision = await select_checksum_import_candidate(
        provider=abc.provider(),
        checksum=CHECKSUM,
        authorized_group_ids=context.authorized_group_ids,
        request_bearer_token=context.curator_token,
        allow_conversion_request=False,
    )

    assert decision.status is ChecksumImportDecisionStatus.ACCESS_DENIED
    assert decision.selected is None
    assert abc.downloads() == []


@pytest.mark.asyncio
async def test_dev_user_without_groups_cannot_import_restricted_papers(monkeypatch):
    context = await _dev_context(monkeypatch, "")
    assert context.authorized_group_ids == ()
    abc = _AllAccessABC("FB")

    decision = await select_reference_import_candidate(
        provider=abc.provider(),
        identifier="AGRKB:101",
        authorized_group_ids=context.authorized_group_ids,
        request_bearer_token=context.curator_token,
        allow_conversion_request=False,
    )

    assert decision.status is ReferenceImportDecisionStatus.ACCESS_DENIED
    assert abc.downloads() == []


@pytest.mark.asyncio
async def test_fb_dev_user_can_import_an_fb_paper(monkeypatch):
    context = await _dev_context(monkeypatch, "FB")
    abc = _AllAccessABC("FB")

    decision = await select_reference_import_candidate(
        provider=abc.provider(),
        identifier="AGRKB:101",
        authorized_group_ids=context.authorized_group_ids,
        request_bearer_token=context.curator_token,
        allow_conversion_request=False,
    )

    assert decision.status is ReferenceImportDecisionStatus.READY
    assert decision.selected is not None
    assert decision.selected.source_artifact.artifact_id == "10"

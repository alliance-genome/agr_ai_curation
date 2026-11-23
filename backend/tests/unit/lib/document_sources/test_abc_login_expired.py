"""ABC refusing the curator's login is a named failure (KANBAN-1976 / ALL-1460, P7b).

Papers late in a batch, and supplement waits, can outlive a curator's login.
ABC then answers 401. The failure is now named (``DocumentSourceLoginExpired``)
but keeps the text the call failed with before, so an ordinary import shows
exactly what it showed before. All values are made up.
"""

from __future__ import annotations

import pytest

from agr_ai_curation_alliance.literature.client import ABCLiteratureHTTPError
from src.lib.document_sources.models import (
    DocumentSourceAccessDenied,
    DocumentSourceLoginExpired,
)
from tests.unit.lib.document_sources.test_abc_literature_provider import (
    FakeABCLiteratureClient,
    provider_from_fake,
)


def _http(status: int, endpoint: str) -> ABCLiteratureHTTPError:
    return ABCLiteratureHTTPError(
        f"ABC Literature returned HTTP {status}", status_code=status, endpoint=endpoint
    )


class _RefusingClient(FakeABCLiteratureClient):
    def __init__(self, status: int):
        super().__init__()
        self.status = status

    async def show_reference(self, reference, *, request_bearer_token=None):
        raise _http(self.status, "/reference/AGRKB:101")

    async def show_referencefiles(self, reference, *, request_bearer_token=None):
        raise _http(self.status, "/reference/referencefile/show_all/AGRKB:101")

    async def lookup_referencefile_by_md5(self, md5sum, *, request_bearer_token=None):
        raise _http(self.status, "/reference/referencefile/by_md5sum")

    async def download_referencefile(self, artifact_id, *, request_bearer_token=None, max_bytes=None):
        raise _http(self.status, "/reference/referencefile/download_file/1")

    async def request_referencefile_conversion(self, reference, *, wait=False, request_bearer_token=None):
        raise _http(self.status, "/reference/referencefile/conversion_request/AGRKB:101")


CALLS = {
    "conversion request": lambda provider: provider.request_conversion(
        "AGRKB:101", request_bearer_token="expired-token"
    ),
    "listing": lambda provider: provider.list_artifacts(
        "AGRKB:101", request_bearer_token="expired-token"
    ),
    "download": lambda provider: provider.download_artifact(
        "1", request_bearer_token="expired-token"
    ),
    "checksum lookup": lambda provider: provider.find_artifacts_by_checksum(
        "made-up-md5", request_bearer_token="expired-token"
    ),
}
# What each call failed with before P7b, for any ABC error, a 401 included.
TEXT_BEFORE = {
    "conversion request": "ABC Literature conversion request failed",
    "listing": "ABC Literature artifact listing failed",
    "download": "ABC Literature artifact download failed",
    "checksum lookup": "ABC Literature checksum lookup failed",
}


@pytest.mark.asyncio
@pytest.mark.parametrize("call", sorted(CALLS))
async def test_a_401_from_abc_is_a_named_login_failure(call):
    provider = provider_from_fake(_RefusingClient(401))

    with pytest.raises(DocumentSourceLoginExpired) as raised:
        await CALLS[call](provider)

    assert str(raised.value) == TEXT_BEFORE[call]


@pytest.mark.asyncio
async def test_a_403_is_still_access_denied_not_an_expired_login():
    provider = provider_from_fake(_RefusingClient(403))

    with pytest.raises(DocumentSourceAccessDenied):
        await CALLS["download"](provider)


def test_the_curator_wording_is_kept_for_imports_with_supplements():
    from src.lib.document_sources.models import LOGIN_EXPIRED_MESSAGE

    assert LOGIN_EXPIRED_MESSAGE == (
        "Your sign-in expired before this import finished. "
        "Sign in again, then import the paper again."
    )

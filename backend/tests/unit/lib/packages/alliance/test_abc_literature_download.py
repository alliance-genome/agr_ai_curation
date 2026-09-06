"""Bounded downloads enforce size before retention and always close the stream."""

import asyncio

import httpx
import pytest

from agr_ai_curation_alliance.literature.client import (
    ABCLiteratureAuthMode,
    ABCLiteratureClient,
    ABCLiteratureClientConfig,
    ABCLiteratureConfigError,
    ABCLiteratureHTTPError,
    ABCLiteratureResponseError,
)


class Stream(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.reads = 0
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            self.reads += 1
            if isinstance(chunk, BaseException):
                raise chunk
            yield chunk

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [
    "exact", "overflow", "declared", "lying", "encoded", "denied", "redirect",
    "timeout", "transport", "cancelled", "invalid_length",
])
async def test_bounded_download_closes_without_retry_and_preserves_auth(mode):
    content = "Synthetic α\r\npaper".encode()
    chunks = [content[:5], content[5:]]
    headers = {}
    status = 200
    limit = len(content)
    errors = {"timeout": httpx.ReadTimeout("private transport details"),
              "transport": httpx.ReadError("private transport details"),
              "cancelled": asyncio.CancelledError()}
    if mode in {"overflow", "lying"}:
        limit = 5
        chunks.append(b"must-not-be-read")
    if mode == "declared":
        headers["Content-Length"] = str(limit + 1)
    elif mode == "lying":
        headers["Content-Length"] = "1"
    elif mode == "encoded":
        headers["Content-Encoding"] = "gzip"
    elif mode == "invalid_length":
        headers["Content-Length"] = "invalid"
    elif mode in {"denied", "redirect"}:
        status = 403 if mode == "denied" else 302
        headers["Location"] = "https://other.test/private"
    elif mode in errors:
        chunks = [content[:5], errors[mode]]
    stream = Stream(chunks)
    requests = []

    def handle(request):
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer delegated-human"
        assert request.headers["Accept-Encoding"] == "identity"
        return httpx.Response(status, headers=headers, stream=stream)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle), follow_redirects=True) as http:
        client = ABCLiteratureClient(ABCLiteratureClientConfig(
            base_url="https://literature.test", auth_mode=ABCLiteratureAuthMode.STATIC_BEARER,
            bearer_token="unused-service-token",
        ), http_client=http)
        if mode == "exact":
            assert await client.download_referencefile(
                7, request_bearer_token="delegated-human", max_bytes=limit,
            ) == content
        else:
            expected = asyncio.CancelledError if mode == "cancelled" else (
                ABCLiteratureResponseError if mode in {"encoded", "invalid_length"}
                else ABCLiteratureHTTPError
            )
            with pytest.raises(expected) as caught:
                await client.download_referencefile(
                    7, request_bearer_token="delegated-human", max_bytes=limit,
                )
            assert "private transport details" not in str(caught.value)
            assert caught.value.__cause__ is None and caught.value.__context__ is None
            if isinstance(caught.value, ABCLiteratureHTTPError):
                assert caught.value.status_code == {
                    "overflow": 413, "declared": 413, "lying": 413,
                    "denied": 403, "redirect": 302, "timeout": 504, "transport": 502,
                }[mode]
    assert stream.closed
    assert len(requests) == 1
    assert stream.reads == (0 if mode in {
        "declared", "encoded", "denied", "redirect", "invalid_length",
    } else 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("bound", [0, -1, True, 1.5, "5"])
async def test_invalid_download_bound_precedes_network_and_auth(bound):
    def unexpected(request):
        pytest.fail("Invalid bound must not reach the network")

    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as http:
        client = ABCLiteratureClient(ABCLiteratureClientConfig(
            base_url="https://literature.test", auth_mode=ABCLiteratureAuthMode.STATIC_BEARER,
        ), http_client=http)
        with pytest.raises(ABCLiteratureConfigError, match="positive integer"):
            await client.download_referencefile(7, max_bytes=bound)

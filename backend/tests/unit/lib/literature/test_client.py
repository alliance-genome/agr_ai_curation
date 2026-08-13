"""Tests for the allowed ABC Literature REST client operations."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from src.lib.literature.client import (
    ABCLiteratureAuthMode,
    ABCLiteratureClient,
    ABCLiteratureClientConfig,
    ABCLiteratureConfigError,
    ABCLiteratureHTTPError,
    ABCLiteratureResponseError,
)


class FakeAsyncClient:
    """Minimal async httpx-compatible test double."""

    def __init__(self, responses: list[httpx.Response] | None = None):
        self.responses = list(responses or [])
        self.requests: list[dict[str, Any]] = []
        self.closed = False

    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        self.requests.append({"method": method, "url": url, **kwargs})
        if not self.responses:
            raise AssertionError("No fake response queued")
        return self.responses.pop(0)

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        self.requests.append({"method": "POST", "url": url, **kwargs})
        if not self.responses:
            raise AssertionError("No fake response queued")
        return self.responses.pop(0)

    async def aclose(self) -> None:
        self.closed = True


class FakeABCLiteratureHTTPService:
    """Endpoint-aware fake for ABC Literature access-contract tests."""

    def __init__(self):
        self.requests: list[dict[str, Any]] = []
        self.closed = False
        self.source_file = {
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
                    "reference_id": 101,
                    "reference_curie": "AGRKB:101",
                    "display_name": "source_nxml.md",
                    "file_class": "converted_merged_main",
                    "file_extension": "md",
                    "referencefile_mods": [{"mod_abbreviation": None}],
                }
            ],
        }

    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        headers = kwargs.get("headers") or {}
        self.requests.append({"method": method, "url": url, **kwargs})

        if method == "GET" and url.endswith("/reference/referencefile/by_md5/abc123"):
            return json_response(200, [self.source_file])

        if method == "GET" and url.endswith("/reference/referencefile/show_all/AGRKB%3A101"):
            return json_response(200, [self.source_file])

        if method == "GET" and url.endswith("/reference/referencefile/download_file/10"):
            if headers.get("Authorization") == "Bearer authorized-curator":
                return httpx.Response(200, content=b"%PDF-1.7 fake source")
            return json_response(403, {"detail": "forbidden"})

        if (
            "/reference/add" in url
            or "/reference/referencefile/file_upload" in url
            or "/reference/referencefile/conversion_request/" in url
        ):
            raise AssertionError(f"Forbidden endpoint called during read-only fake test: {url}")

        raise AssertionError(f"Unexpected fake Literature request: {method} {url}")

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        self.requests.append({"method": "POST", "url": url, **kwargs})
        if "/reference/add" in url or "/reference/referencefile/file_upload" in url:
            raise AssertionError(f"Forbidden endpoint called during read-only fake test: {url}")
        raise AssertionError(f"Unexpected fake Literature POST request: {url}")

    async def aclose(self) -> None:
        self.closed = True


def json_response(status_code: int, payload: Any) -> httpx.Response:
    return httpx.Response(status_code=status_code, json=payload)


@pytest.mark.asyncio
async def test_static_bearer_auth_and_endpoint_path() -> None:
    fake_http = FakeAsyncClient([json_response(200, {"curie": "AGRKB:101"})])
    client = ABCLiteratureClient(
        ABCLiteratureClientConfig(
            base_url="https://literature.example/api/",
            auth_mode=ABCLiteratureAuthMode.STATIC_BEARER,
            bearer_token="secret-token",
        ),
        http_client=fake_http,  # type: ignore[arg-type]
    )

    payload = await client.lookup_external_curie("PMID:1234")

    assert payload == {"curie": "AGRKB:101"}
    assert fake_http.requests == [
        {
            "method": "GET",
            "url": (
                "https://literature.example/api/reference/external_lookup/"
                "PMID%3A1234"
            ),
            "headers": {"Authorization": "Bearer secret-token"},
        }
    ]


@pytest.mark.asyncio
async def test_request_bearer_token_overrides_configured_auth_for_download() -> None:
    fake_http = FakeAsyncClient([httpx.Response(200, content=b"markdown")])
    client = ABCLiteratureClient(
        ABCLiteratureClientConfig(
            base_url="https://literature.example/api/",
            auth_mode=ABCLiteratureAuthMode.STATIC_BEARER,
            bearer_token="service-token",
        ),
        http_client=fake_http,  # type: ignore[arg-type]
    )

    payload = await client.download_referencefile(
        "file-55",
        request_bearer_token=" curator-token ",
    )

    assert payload == b"markdown"
    assert fake_http.requests == [
        {
            "method": "GET",
            "url": (
                "https://literature.example/api/reference/referencefile/"
                "download_file/file-55"
            ),
            "headers": {"Authorization": "Bearer curator-token"},
        }
    ]


@pytest.mark.asyncio
async def test_blank_request_bearer_token_keeps_configured_auth_for_download() -> None:
    fake_http = FakeAsyncClient([httpx.Response(200, content=b"markdown")])
    client = ABCLiteratureClient(
        ABCLiteratureClientConfig(
            base_url="https://literature.example/api/",
            auth_mode=ABCLiteratureAuthMode.STATIC_BEARER,
            bearer_token="service-token",
        ),
        http_client=fake_http,  # type: ignore[arg-type]
    )

    payload = await client.download_referencefile(
        "file-55",
        request_bearer_token=" ",
    )

    assert payload == b"markdown"
    assert fake_http.requests[0]["headers"] == {
        "Authorization": "Bearer service-token"
    }


@pytest.mark.asyncio
async def test_request_bearer_token_skips_missing_static_bearer_config() -> None:
    fake_http = FakeAsyncClient([httpx.Response(200, content=b"markdown")])
    client = ABCLiteratureClient(
        ABCLiteratureClientConfig(
            base_url="https://literature.example/api/",
            auth_mode=ABCLiteratureAuthMode.STATIC_BEARER,
        ),
        http_client=fake_http,  # type: ignore[arg-type]
    )

    payload = await client.download_referencefile(
        "file-55",
        request_bearer_token="curator-token",
    )

    assert payload == b"markdown"
    assert fake_http.requests == [
        {
            "method": "GET",
            "url": (
                "https://literature.example/api/reference/referencefile/"
                "download_file/file-55"
            ),
            "headers": {"Authorization": "Bearer curator-token"},
        }
    ]


@pytest.mark.asyncio
async def test_request_bearer_token_skips_cognito_client_credentials() -> None:
    fake_http = FakeAsyncClient([httpx.Response(200, content=b"markdown")])
    client = ABCLiteratureClient(
        ABCLiteratureClientConfig(
            base_url="https://literature.example/api/",
            auth_mode=ABCLiteratureAuthMode.COGNITO_CLIENT_CREDENTIALS,
        ),
        http_client=fake_http,  # type: ignore[arg-type]
    )

    payload = await client.download_referencefile(
        "file-55",
        request_bearer_token="curator-token",
    )

    assert payload == b"markdown"
    assert fake_http.requests == [
        {
            "method": "GET",
            "url": (
                "https://literature.example/api/reference/referencefile/"
                "download_file/file-55"
            ),
            "headers": {"Authorization": "Bearer curator-token"},
        }
    ]


@pytest.mark.asyncio
async def test_lookup_external_curie_uses_request_bearer_token() -> None:
    fake_http = FakeAsyncClient([json_response(200, {"curie": "AGRKB:101"})])
    client = ABCLiteratureClient(
        ABCLiteratureClientConfig(
            base_url="https://literature.example/api/",
            auth_mode=ABCLiteratureAuthMode.STATIC_BEARER,
            bearer_token="service-token",
        ),
        http_client=fake_http,  # type: ignore[arg-type]
    )

    await client.lookup_external_curie(
        "PMID:1234",
        request_bearer_token="curator-token",
    )

    assert fake_http.requests == [
        {
            "method": "GET",
            "url": (
                "https://literature.example/api/reference/external_lookup/"
                "PMID%3A1234"
            ),
            "headers": {"Authorization": "Bearer curator-token"},
        }
    ]


@pytest.mark.asyncio
async def test_identifier_path_segments_are_url_encoded() -> None:
    fake_http = FakeAsyncClient([json_response(200, {"curie": "AGRKB:101"})])
    client = ABCLiteratureClient(
        ABCLiteratureClientConfig(base_url="https://literature.example/api"),
        http_client=fake_http,  # type: ignore[arg-type]
    )

    await client.lookup_cross_reference("DOI:10.1234/a/b?x#frag")

    assert fake_http.requests[0]["url"] == (
        "https://literature.example/api/reference/by_cross_reference/"
        "DOI%3A10.1234%2Fa%2Fb%3Fx%23frag"
    )


@pytest.mark.asyncio
async def test_lookup_referencefile_by_md5_accepts_json_array() -> None:
    fake_http = FakeAsyncClient(
        [json_response(200, [{"referencefile_id": 10}, {"referencefile_id": 11}])]
    )
    client = ABCLiteratureClient(
        ABCLiteratureClientConfig(base_url="https://literature.example/api"),
        http_client=fake_http,  # type: ignore[arg-type]
    )

    payload = await client.lookup_referencefile_by_md5("abc123")

    assert payload == [{"referencefile_id": 10}, {"referencefile_id": 11}]
    assert fake_http.requests[0]["url"].endswith(
        "/reference/referencefile/by_md5/abc123"
    )


@pytest.mark.asyncio
async def test_lookup_referencefile_by_md5_uses_request_bearer_token() -> None:
    fake_http = FakeAsyncClient([json_response(200, [])])
    client = ABCLiteratureClient(
        ABCLiteratureClientConfig(base_url="https://literature.example/api"),
        http_client=fake_http,  # type: ignore[arg-type]
    )

    await client.lookup_referencefile_by_md5(
        "abc123",
        request_bearer_token="curator-token",
    )

    assert fake_http.requests[0]["headers"] == {
        "Authorization": "Bearer curator-token"
    }


@pytest.mark.asyncio
async def test_show_referencefiles_accepts_json_array() -> None:
    fake_http = FakeAsyncClient([json_response(200, [{"referencefile_id": 10}])])
    client = ABCLiteratureClient(
        ABCLiteratureClientConfig(base_url="https://literature.example/api"),
        http_client=fake_http,  # type: ignore[arg-type]
    )

    payload = await client.show_referencefiles("AGRKB:101")

    assert payload == [{"referencefile_id": 10}]
    assert fake_http.requests[0]["url"].endswith(
        "/reference/referencefile/show_all/AGRKB%3A101"
    )


@pytest.mark.asyncio
async def test_show_referencefiles_uses_request_bearer_token() -> None:
    fake_http = FakeAsyncClient([json_response(200, {"referencefiles": []})])
    client = ABCLiteratureClient(
        ABCLiteratureClientConfig(
            base_url="https://literature.example/api",
            auth_mode=ABCLiteratureAuthMode.STATIC_BEARER,
            bearer_token="service-token",
        ),
        http_client=fake_http,  # type: ignore[arg-type]
    )

    await client.show_referencefiles(
        "AGRKB:101",
        request_bearer_token="curator-token",
    )

    assert fake_http.requests[0]["headers"] == {
        "Authorization": "Bearer curator-token"
    }


@pytest.mark.asyncio
async def test_request_referencefile_conversion_uses_safe_defaults() -> None:
    fake_http = FakeAsyncClient(
        [json_response(202, {"status": "running", "job_id": "job-1"})]
    )
    client = ABCLiteratureClient(
        ABCLiteratureClientConfig(base_url="https://literature.example/api"),
        http_client=fake_http,  # type: ignore[arg-type]
    )

    payload = await client.request_referencefile_conversion(
        "AGRKB:101",
        request_bearer_token="curator-token",
    )

    assert payload == {"status": "running", "job_id": "job-1"}
    assert fake_http.requests == [
        {
            "method": "GET",
            "url": (
                "https://literature.example/api/reference/referencefile/"
                "conversion_request/AGRKB%3A101"
            ),
            "headers": {"Authorization": "Bearer curator-token"},
            "params": {"wait": "false", "overwrite_tei_md": "false"},
        }
    ]


@pytest.mark.asyncio
async def test_request_referencefile_conversion_can_wait_but_not_overwrite_by_default() -> None:
    fake_http = FakeAsyncClient([json_response(200, {"status": "converted"})])
    client = ABCLiteratureClient(
        ABCLiteratureClientConfig(base_url="https://literature.example/api"),
        http_client=fake_http,  # type: ignore[arg-type]
    )

    await client.request_referencefile_conversion("AGRKB:101", wait=True)

    assert fake_http.requests[0]["params"] == {
        "wait": "true",
        "overwrite_tei_md": "false",
    }


@pytest.mark.asyncio
async def test_fake_service_models_unfiltered_metadata_and_download_access_gate() -> None:
    fake_service = FakeABCLiteratureHTTPService()
    client = ABCLiteratureClient(
        ABCLiteratureClientConfig(
            base_url="https://literature.example/api",
            auth_mode=ABCLiteratureAuthMode.STATIC_BEARER,
            bearer_token="service-token",
        ),
        http_client=fake_service,  # type: ignore[arg-type]
    )

    by_md5_payload = await client.lookup_referencefile_by_md5(
        "abc123",
        request_bearer_token="unauthorized-curator",
    )
    show_all_payload = await client.show_referencefiles(
        "AGRKB:101",
        request_bearer_token="unauthorized-curator",
    )

    assert by_md5_payload == [fake_service.source_file]
    assert show_all_payload == [fake_service.source_file]
    assert by_md5_payload[0]["referencefile_mods"] == [{"mod_abbreviation": "FB"}]
    assert by_md5_payload[0]["converted_referencefiles"][0]["referencefile_mods"] == [
        {"mod_abbreviation": None}
    ]

    with pytest.raises(ABCLiteratureHTTPError) as unauthorized_exc:
        await client.download_referencefile(
            "10",
            request_bearer_token="unauthorized-curator",
        )

    authorized_payload = await client.download_referencefile(
        "10",
        request_bearer_token="authorized-curator",
    )

    assert unauthorized_exc.value.status_code == 403
    assert authorized_payload == b"%PDF-1.7 fake source"
    assert [request["url"] for request in fake_service.requests] == [
        "https://literature.example/api/reference/referencefile/by_md5/abc123",
        "https://literature.example/api/reference/referencefile/show_all/AGRKB%3A101",
        "https://literature.example/api/reference/referencefile/download_file/10",
        "https://literature.example/api/reference/referencefile/download_file/10",
    ]
    assert [request["headers"] for request in fake_service.requests] == [
        {"Authorization": "Bearer unauthorized-curator"},
        {"Authorization": "Bearer unauthorized-curator"},
        {"Authorization": "Bearer unauthorized-curator"},
        {"Authorization": "Bearer authorized-curator"},
    ]


def test_client_does_not_expose_reference_create_or_file_upload_operations() -> None:
    client = ABCLiteratureClient(
        ABCLiteratureClientConfig(base_url="https://literature.example/api"),
        http_client=FakeAsyncClient(),  # type: ignore[arg-type]
    )

    assert not hasattr(client, "create_reference")
    assert not hasattr(client, "add_reference")
    assert not hasattr(client, "upload_file")
    assert not hasattr(client, "file_upload")


@pytest.mark.asyncio
async def test_lookup_referencefile_by_md5_rejects_non_object_entries() -> None:
    fake_http = FakeAsyncClient([json_response(200, [{"referencefile_id": 10}, "bad"])])
    client = ABCLiteratureClient(
        ABCLiteratureClientConfig(base_url="https://literature.example/api"),
        http_client=fake_http,  # type: ignore[arg-type]
    )

    with pytest.raises(
        ABCLiteratureResponseError,
        match="entries must be JSON objects",
    ):
        await client.lookup_referencefile_by_md5("abc123")


@pytest.mark.asyncio
async def test_http_status_error_is_sanitized() -> None:
    fake_http = FakeAsyncClient([json_response(503, {"secret": "do not leak"})])
    client = ABCLiteratureClient(
        ABCLiteratureClientConfig(base_url="https://literature.example/api"),
        http_client=fake_http,  # type: ignore[arg-type]
    )

    with pytest.raises(ABCLiteratureHTTPError) as exc_info:
        await client.show_reference("AGRKB:101")

    assert exc_info.value.status_code == 503
    assert exc_info.value.endpoint == "/reference/AGRKB%3A101"
    assert "do not leak" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_redirect_response_is_rejected_for_downloads() -> None:
    fake_http = FakeAsyncClient(
        [
            httpx.Response(
                302,
                headers={"location": "https://literature.example/login"},
                content=b"login redirect",
            )
        ]
    )
    client = ABCLiteratureClient(
        ABCLiteratureClientConfig(base_url="https://literature.example/api"),
        http_client=fake_http,  # type: ignore[arg-type]
    )

    with pytest.raises(ABCLiteratureHTTPError) as exc_info:
        await client.download_referencefile("file/123")

    assert exc_info.value.status_code == 302
    assert exc_info.value.endpoint == (
        "/reference/referencefile/download_file/file%2F123"
    )


@pytest.mark.asyncio
async def test_missing_static_bearer_token_raises_config_error() -> None:
    client = ABCLiteratureClient(
        ABCLiteratureClientConfig(
            base_url="https://literature.example/api",
            auth_mode=ABCLiteratureAuthMode.STATIC_BEARER,
        ),
        http_client=FakeAsyncClient(),  # type: ignore[arg-type]
    )

    with pytest.raises(ABCLiteratureConfigError, match="ABC_LITERATURE_BEARER_TOKEN"):
        await client.show_reference("AGRKB:101")


@pytest.mark.asyncio
async def test_cognito_client_credentials_token_is_cached() -> None:
    fake_http = FakeAsyncClient(
        [
            json_response(200, {"access_token": "token-1", "expires_in": 3600}),
            json_response(200, {"curie": "AGRKB:101"}),
            json_response(200, {"curie": "AGRKB:102"}),
        ]
    )
    client = ABCLiteratureClient(
        ABCLiteratureClientConfig(
            base_url="https://literature.example/api",
            auth_mode=ABCLiteratureAuthMode.COGNITO_CLIENT_CREDENTIALS,
            cognito_token_url="https://auth.example/oauth2/token",
            cognito_client_id="client-id",
            cognito_client_secret="client-secret",
            cognito_scope="literature/read",
        ),
        http_client=fake_http,  # type: ignore[arg-type]
    )

    await client.show_reference("AGRKB:101")
    await client.show_reference("AGRKB:102")

    assert [request["method"] for request in fake_http.requests] == [
        "POST",
        "GET",
        "GET",
    ]
    assert fake_http.requests[0]["url"] == "https://auth.example/oauth2/token"
    assert fake_http.requests[1]["headers"] == {"Authorization": "Bearer token-1"}
    assert fake_http.requests[2]["headers"] == {"Authorization": "Bearer token-1"}

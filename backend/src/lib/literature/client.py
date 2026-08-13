"""ABC Literature REST client for allowed AI Curation import operations.

The client intentionally exposes only lookup/list/download/search operations
plus the constrained existing-reference conversion request used by ABC-backed
imports. It never exposes Literature upload/create or TEI-overwrite behavior.
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping
from urllib.parse import quote

import httpx

from src.lib.openai_agents.config import (
    get_abc_literature_api_base_url,
    get_abc_literature_auth_mode,
    get_abc_literature_bearer_token,
    get_abc_literature_cognito_client_id,
    get_abc_literature_cognito_client_secret,
    get_abc_literature_cognito_scope,
    get_abc_literature_cognito_token_url,
    get_document_source_request_timeout_seconds,
)


class ABCLiteratureAuthMode(str, Enum):
    NONE = "none"
    STATIC_BEARER = "static_bearer"
    COGNITO_CLIENT_CREDENTIALS = "cognito_client_credentials"


class ABCLiteratureClientError(RuntimeError):
    """Base error for sanitized Literature client failures."""


class ABCLiteratureConfigError(ABCLiteratureClientError):
    """Raised when Literature client configuration is missing or invalid."""


class ABCLiteratureHTTPError(ABCLiteratureClientError):
    """Raised for non-2xx Literature HTTP responses."""

    def __init__(self, message: str, *, status_code: int, endpoint: str):
        super().__init__(message)
        self.status_code = status_code
        self.endpoint = endpoint


class ABCLiteratureResponseError(ABCLiteratureClientError):
    """Raised when Literature returns an unexpected response shape."""


@dataclass(slots=True)
class ABCLiteratureClientConfig:
    base_url: str
    auth_mode: ABCLiteratureAuthMode = ABCLiteratureAuthMode.NONE
    timeout_seconds: float = 10.0
    bearer_token: str | None = None
    cognito_token_url: str | None = None
    cognito_client_id: str | None = None
    cognito_client_secret: str | None = None
    cognito_scope: str | None = None

    @classmethod
    def from_env(cls) -> "ABCLiteratureClientConfig":
        """Build configuration from documented environment variables."""
        raw_base_url = get_abc_literature_api_base_url().strip()
        if not raw_base_url:
            raise ABCLiteratureConfigError("ABC_LITERATURE_API_BASE_URL is required")

        raw_auth_mode = get_abc_literature_auth_mode().strip().lower()
        try:
            auth_mode = ABCLiteratureAuthMode(raw_auth_mode)
        except ValueError as exc:
            allowed = ", ".join(mode.value for mode in ABCLiteratureAuthMode)
            raise ABCLiteratureConfigError(
                f"Unsupported ABC_LITERATURE_AUTH_MODE {raw_auth_mode!r}; "
                f"expected one of {allowed}"
            ) from exc

        return cls(
            base_url=raw_base_url,
            auth_mode=auth_mode,
            timeout_seconds=get_document_source_request_timeout_seconds(),
            bearer_token=get_abc_literature_bearer_token(),
            cognito_token_url=get_abc_literature_cognito_token_url(),
            cognito_client_id=get_abc_literature_cognito_client_id(),
            cognito_client_secret=get_abc_literature_cognito_client_secret(),
            cognito_scope=get_abc_literature_cognito_scope(),
        )


class ABCLiteratureClient:
    """Small async client for allowed ABC Literature import operations."""

    def __init__(
        self,
        config: ABCLiteratureClientConfig,
        *,
        http_client: httpx.AsyncClient | None = None,
    ):
        self.config = config
        self._base_url = config.base_url.rstrip("/")
        self._owned_http_client = http_client is None
        self._http_client = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(config.timeout_seconds)
        )
        self._cached_token: str | None = None
        self._cached_token_expires_at: float = 0.0

    async def __aenter__(self) -> "ABCLiteratureClient":
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owned_http_client:
            await self._http_client.aclose()

    async def lookup_external_curie(
        self,
        external_curie: str,
        *,
        request_bearer_token: str | None = None,
    ) -> Mapping[str, Any]:
        external_curie_path = self._path_segment(external_curie)
        return await self._get_json(
            f"/reference/external_lookup/{external_curie_path}",
            request_bearer_token=request_bearer_token,
        )

    async def lookup_cross_reference(
        self,
        curie_or_cross_reference_id: str,
        *,
        request_bearer_token: str | None = None,
    ) -> Mapping[str, Any]:
        cross_reference_path = self._path_segment(curie_or_cross_reference_id)
        return await self._get_json(
            f"/reference/by_cross_reference/{cross_reference_path}",
            request_bearer_token=request_bearer_token,
        )

    async def show_reference(
        self,
        curie_or_reference_id: str,
        *,
        request_bearer_token: str | None = None,
    ) -> Mapping[str, Any]:
        reference_path = self._path_segment(curie_or_reference_id)
        return await self._get_json(
            f"/reference/{reference_path}",
            request_bearer_token=request_bearer_token,
        )

    async def search_references(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return await self._post_json("/search/references/", json_payload=payload)

    async def lookup_referencefile_by_md5(
        self,
        md5sum: str,
        *,
        request_bearer_token: str | None = None,
    ) -> list[Mapping[str, Any]]:
        md5sum_path = self._path_segment(md5sum)
        payload = await self._get_json_value(
            f"/reference/referencefile/by_md5/{md5sum_path}",
            request_bearer_token=request_bearer_token,
        )
        if not isinstance(payload, list):
            raise ABCLiteratureResponseError("by_md5 response must be a JSON array")
        if not all(isinstance(item, Mapping) for item in payload):
            raise ABCLiteratureResponseError(
                "by_md5 response entries must be JSON objects"
            )
        return payload

    async def show_referencefiles(
        self,
        curie_or_reference_id: str,
        *,
        request_bearer_token: str | None = None,
    ) -> Mapping[str, Any] | list[Mapping[str, Any]]:
        reference_path = self._path_segment(curie_or_reference_id)
        payload = await self._get_json_value(
            f"/reference/referencefile/show_all/{reference_path}",
            request_bearer_token=request_bearer_token,
        )
        if isinstance(payload, Mapping):
            return payload
        if isinstance(payload, list) and all(
            isinstance(item, Mapping) for item in payload
        ):
            return payload
        raise ABCLiteratureResponseError(
            "show_all response must be a JSON object or array"
        )

    async def download_referencefile(
        self,
        referencefile_id: int | str,
        *,
        request_bearer_token: str | None = None,
    ) -> bytes:
        referencefile_id_path = self._path_segment(referencefile_id)
        response = await self._request(
            "GET",
            f"/reference/referencefile/download_file/{referencefile_id_path}",
            request_bearer_token=request_bearer_token,
        )
        return response.content

    async def request_referencefile_conversion(
        self,
        curie_or_reference_id: str,
        *,
        wait: bool = False,
        request_bearer_token: str | None = None,
    ) -> Mapping[str, Any]:
        reference_path = self._path_segment(curie_or_reference_id)
        return await self._get_json(
            f"/reference/referencefile/conversion_request/{reference_path}",
            params={
                "wait": _bool_param(wait),
                "overwrite_tei_md": "false",
            },
            request_bearer_token=request_bearer_token,
        )

    async def _get_json(self, path: str, **kwargs: Any) -> Mapping[str, Any]:
        response = await self._request("GET", path, **kwargs)
        return self._parse_json_object(response, path)

    async def _get_json_value(self, path: str, **kwargs: Any) -> Any:
        response = await self._request("GET", path, **kwargs)
        return self._parse_json(response, path)

    async def _post_json(
        self,
        path: str,
        *,
        json_payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        response = await self._request("POST", path, json=json_payload)
        return self._parse_json_object(response, path)

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        endpoint = f"{self._base_url}{path}"
        headers = dict(kwargs.pop("headers", {}) or {})
        request_bearer_token = str(
            kwargs.pop("request_bearer_token", "") or ""
        ).strip()
        if request_bearer_token:
            headers["Authorization"] = f"Bearer {request_bearer_token}"
        else:
            headers.update(await self._auth_headers())

        try:
            response = await self._http_client.request(
                method,
                endpoint,
                headers=headers,
                **kwargs,
            )
        except httpx.TimeoutException as exc:
            raise ABCLiteratureHTTPError(
                "ABC Literature request timed out",
                status_code=504,
                endpoint=path,
            ) from exc
        except httpx.RequestError as exc:
            raise ABCLiteratureHTTPError(
                "ABC Literature request failed",
                status_code=502,
                endpoint=path,
            ) from exc

        if not response.is_success:
            raise ABCLiteratureHTTPError(
                f"ABC Literature returned HTTP {response.status_code}",
                status_code=response.status_code,
                endpoint=path,
            )

        return response

    async def _auth_headers(self) -> dict[str, str]:
        auth_mode = self.config.auth_mode
        if auth_mode is ABCLiteratureAuthMode.NONE:
            return {}

        if auth_mode is ABCLiteratureAuthMode.STATIC_BEARER:
            token = (self.config.bearer_token or "").strip()
            if not token:
                raise ABCLiteratureConfigError(
                    "ABC_LITERATURE_BEARER_TOKEN is required when "
                    "ABC_LITERATURE_AUTH_MODE=static_bearer"
                )
            return {"Authorization": f"Bearer {token}"}

        if auth_mode is not ABCLiteratureAuthMode.COGNITO_CLIENT_CREDENTIALS:
            raise ABCLiteratureConfigError(f"Unsupported auth mode: {auth_mode}")

        token = await self._get_cognito_token()
        return {"Authorization": f"Bearer {token}"}

    async def _get_cognito_token(self) -> str:
        now = time.monotonic()
        if self._cached_token and now < (self._cached_token_expires_at - 30):
            return self._cached_token

        token_url = (self.config.cognito_token_url or "").strip()
        client_id = (self.config.cognito_client_id or "").strip()
        client_secret = (self.config.cognito_client_secret or "").strip()
        scope = (self.config.cognito_scope or "").strip()
        if not token_url or not client_id or not client_secret or not scope:
            raise ABCLiteratureConfigError(
                "ABC_LITERATURE_COGNITO_TOKEN_URL, ABC_LITERATURE_COGNITO_CLIENT_ID, "
                "ABC_LITERATURE_COGNITO_CLIENT_SECRET, and "
                "ABC_LITERATURE_COGNITO_SCOPE are required for "
                "cognito_client_credentials auth mode"
            )

        auth_basic = base64.b64encode(
            f"{client_id}:{client_secret}".encode("utf-8")
        ).decode("ascii")
        try:
            response = await self._http_client.post(
                token_url,
                data={"grant_type": "client_credentials", "scope": scope},
                headers={
                    "Authorization": f"Basic {auth_basic}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
        except httpx.TimeoutException as exc:
            raise ABCLiteratureHTTPError(
                "ABC Literature token request timed out",
                status_code=504,
                endpoint="token",
            ) from exc
        except httpx.RequestError as exc:
            raise ABCLiteratureHTTPError(
                "ABC Literature token request failed",
                status_code=502,
                endpoint="token",
            ) from exc

        if response.status_code != 200:
            raise ABCLiteratureHTTPError(
                f"ABC Literature token endpoint returned HTTP {response.status_code}",
                status_code=response.status_code,
                endpoint="token",
            )

        payload = self._parse_json_object(response, "token")
        token = str(payload.get("access_token") or "").strip()
        if not token:
            raise ABCLiteratureResponseError(
                "ABC Literature token response missing access_token"
            )

        try:
            expires_in = int(payload.get("expires_in", 3600))
        except (TypeError, ValueError):
            expires_in = 3600

        self._cached_token = token
        self._cached_token_expires_at = time.monotonic() + max(60, expires_in)
        return token

    @staticmethod
    def _path_segment(value: int | str) -> str:
        return quote(str(value), safe="")

    @staticmethod
    def _parse_json(response: httpx.Response, endpoint: str) -> Any:
        try:
            return response.json()
        except ValueError as exc:
            raise ABCLiteratureResponseError(
                f"ABC Literature response from {endpoint} was not valid JSON"
            ) from exc

    @classmethod
    def _parse_json_object(
        cls,
        response: httpx.Response,
        endpoint: str,
    ) -> Mapping[str, Any]:
        payload = cls._parse_json(response, endpoint)
        if not isinstance(payload, Mapping):
            raise ABCLiteratureResponseError(
                f"ABC Literature response from {endpoint} must be a JSON object"
            )
        return payload


def _bool_param(value: bool) -> str:
    return "true" if value else "false"

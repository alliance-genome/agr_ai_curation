"""Renewable provider credentials for login-free development imports.

The application user remains the ordinary dev-mode principal.  This module only
supplies a short-lived, server-side bearer identity to the configured external
document source and never exposes that identity to the browser.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import math
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import boto3
import jwt
from botocore.config import Config
from jwt import PyJWKClient

from src.config import is_dev_mode
from src.lib.logging_config import suppress_sensitive_aws_sdk_debug_logging
from src.lib.openai_agents.config import (
    get_document_source_dev_curator_auth_mode,
    get_document_source_dev_curator_refresh_skew_seconds,
    get_document_source_import_enabled,
    get_document_source_import_timeout_seconds,
    get_document_source_provider,
    get_document_source_request_timeout_seconds,
)

logger = logging.getLogger(__name__)

COGNITO_USER_PASSWORD_MODE = "cognito_user_password"
_SUPPORTED_AUTH_MODES = {"none", COGNITO_USER_PASSWORD_MODE}


class DevCuratorCredentialUnavailable(RuntimeError):
    """Raised with a sanitized message when the dev curator token is unavailable."""


@dataclass(frozen=True, slots=True)
class DevCuratorCredentials:
    """Validated ABC bearer and non-secret claims from the paired ID token."""

    token: str = field(repr=False)
    claims: Mapping[str, Any] = field(repr=False)
    expires_at: float


@dataclass(frozen=True, slots=True)
class _CognitoSettings:
    region: str
    user_pool_id: str = field(repr=False)
    client_id: str = field(repr=False)
    client_secret: str = field(repr=False)
    username: str = field(repr=False)
    password: str = field(repr=False)
    request_timeout_seconds: float


def renewable_dev_curator_auth_required() -> bool:
    """Return whether this process/request path needs the renewable dev identity."""

    return (
        is_dev_mode()
        and get_document_source_import_enabled()
        and get_document_source_provider().strip().lower() != "local_pdf"
    )


def _secret_hash(*, username: str, client_id: str, client_secret: str) -> str:
    """Build Cognito's SECRET_HASH without retaining intermediate plaintext."""

    digest = hmac.new(
        client_secret.encode("utf-8"),
        f"{username}{client_id}".encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.b64encode(digest).decode("ascii")


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise DevCuratorCredentialUnavailable(
            "Development document-source curator credentials are unavailable."
        )
    return value


def _load_settings() -> _CognitoSettings:
    mode = get_document_source_dev_curator_auth_mode()
    if mode not in _SUPPORTED_AUTH_MODES:
        raise DevCuratorCredentialUnavailable(
            "Development document-source curator authentication is misconfigured."
        )
    if mode != COGNITO_USER_PASSWORD_MODE:
        raise DevCuratorCredentialUnavailable(
            "Development document-source curator credentials are unavailable."
        )
    return _CognitoSettings(
        region=(
            os.getenv("DOCUMENT_SOURCE_DEV_CURATOR_COGNITO_REGION", "us-east-1").strip()
            or "us-east-1"
        ),
        user_pool_id=_required_env(
            "DOCUMENT_SOURCE_DEV_CURATOR_COGNITO_USER_POOL_ID"
        ),
        client_id=_required_env("DOCUMENT_SOURCE_DEV_CURATOR_COGNITO_CLIENT_ID"),
        client_secret=_required_env(
            "DOCUMENT_SOURCE_DEV_CURATOR_COGNITO_CLIENT_SECRET"
        ),
        username=_required_env("DOCUMENT_SOURCE_DEV_CURATOR_USERNAME"),
        password=_required_env("DOCUMENT_SOURCE_DEV_CURATOR_PASSWORD"),
        request_timeout_seconds=get_document_source_request_timeout_seconds(),
    )


def _authenticate_sync(settings: _CognitoSettings) -> DevCuratorCredentials:
    """Perform bounded Cognito and JWKS calls in the worker thread."""

    # Botocore's DEBUG request/response logging includes passwords, secret hashes,
    # and returned tokens. Enforce the suppression here as well as at app startup
    # so this security boundary also holds in scripts and isolated workers.
    suppress_sensitive_aws_sdk_debug_logging()
    client = boto3.client(
        "cognito-idp",
        region_name=settings.region,
        config=Config(
            connect_timeout=settings.request_timeout_seconds,
            read_timeout=settings.request_timeout_seconds,
        ),
    )
    response = client.initiate_auth(
        AuthFlow="USER_PASSWORD_AUTH",
        ClientId=settings.client_id,
        AuthParameters={
            "USERNAME": settings.username,
            "PASSWORD": settings.password,
            "SECRET_HASH": _secret_hash(
                username=settings.username,
                client_id=settings.client_id,
                client_secret=settings.client_secret,
            ),
        },
    )
    if response.get("ChallengeName"):
        raise DevCuratorCredentialUnavailable(
            "Development document-source curator authentication requires interaction."
        )
    authentication_result = response.get("AuthenticationResult")
    if not isinstance(authentication_result, Mapping):
        raise DevCuratorCredentialUnavailable(
            "Development document-source curator authentication returned no token."
        )
    id_token = str(authentication_result.get("IdToken") or "").strip()
    access_token = str(authentication_result.get("AccessToken") or "").strip()
    if not id_token or not access_token:
        raise DevCuratorCredentialUnavailable(
            "Development document-source curator authentication returned incomplete tokens."
        )

    issuer = (
        f"https://cognito-idp.{settings.region}.amazonaws.com/{settings.user_pool_id}"
    )
    jwks_client = PyJWKClient(
        f"{issuer}/.well-known/jwks.json",
        timeout=max(1, math.ceil(settings.request_timeout_seconds)),
    )
    id_signing_key = jwks_client.get_signing_key_from_jwt(id_token)
    id_claims = jwt.decode(
        id_token,
        id_signing_key.key,
        algorithms=["RS256"],
        audience=settings.client_id,
        issuer=issuer,
        options={"require": ["exp", "sub", "token_use"]},
    )
    if id_claims.get("token_use") != "id":
        raise DevCuratorCredentialUnavailable(
            "Development document-source curator authentication returned the wrong token type."
        )

    access_signing_key = jwks_client.get_signing_key_from_jwt(access_token)
    access_claims = jwt.decode(
        access_token,
        access_signing_key.key,
        algorithms=["RS256"],
        issuer=issuer,
        options={
            "require": ["exp", "sub", "token_use", "client_id"],
            "verify_aud": False,
        },
    )
    if access_claims.get("token_use") != "access":
        raise DevCuratorCredentialUnavailable(
            "Development document-source curator authentication returned the wrong bearer token type."
        )
    if access_claims.get("client_id") != settings.client_id:
        raise DevCuratorCredentialUnavailable(
            "Development document-source curator authentication returned the wrong bearer client."
        )
    if access_claims.get("sub") != id_claims.get("sub"):
        raise DevCuratorCredentialUnavailable(
            "Development document-source curator authentication returned mismatched token identities."
        )

    expires_at = min(float(id_claims["exp"]), float(access_claims["exp"]))
    return DevCuratorCredentials(
        token=access_token,
        claims=dict(id_claims),
        expires_at=expires_at,
    )


class DevCuratorCredentialService:
    """Per-worker, lock-protected cache for validated dev curator credentials."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._cached: DevCuratorCredentials | None = None

    def _cache_is_usable(self, credentials: DevCuratorCredentials) -> bool:
        required_lifetime = max(
            float(get_document_source_dev_curator_refresh_skew_seconds()),
            get_document_source_import_timeout_seconds(),
        )
        return credentials.expires_at > time.time() + required_lifetime

    async def get_credentials(self) -> DevCuratorCredentials:
        """Return cached credentials or renew them once for concurrent callers."""

        if not renewable_dev_curator_auth_required():
            raise DevCuratorCredentialUnavailable(
                "Development document-source curator authentication is not active."
            )
        cached = self._cached
        if cached is not None and self._cache_is_usable(cached):
            return cached

        async with self._lock:
            cached = self._cached
            if cached is not None and self._cache_is_usable(cached):
                return cached
            try:
                settings = _load_settings()
                credentials = await asyncio.wait_for(
                    asyncio.to_thread(_authenticate_sync, settings),
                    timeout=settings.request_timeout_seconds,
                )
            except DevCuratorCredentialUnavailable:
                raise
            except Exception as exc:
                logger.warning(
                    "Development document-source curator authentication failed",
                    extra={"failure_type": type(exc).__name__},
                )
                raise DevCuratorCredentialUnavailable(
                    "Development document-source curator credentials are unavailable."
                ) from None
            self._cached = credentials
            return credentials


_credential_service = DevCuratorCredentialService()


async def get_dev_curator_credentials() -> DevCuratorCredentials:
    """Return the current worker's renewable dev curator credentials."""

    return await _credential_service.get_credentials()

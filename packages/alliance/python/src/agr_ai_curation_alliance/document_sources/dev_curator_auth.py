"""Alliance-owned Cognito credentials for login-free development imports."""

from __future__ import annotations

import asyncio
import os

from src.lib.packages.document_source_provider_models import (
    DevCuratorCredentials,
    DevCuratorCredentialUnavailable,
)
from agr_ai_curation_alliance.cognito_user_password import (
    CognitoUserPasswordAuthError,
    CognitoUserPasswordSettings,
    authenticate_user_password,
)
from agr_ai_curation_alliance.document_sources.registration import (
    _document_source_request_timeout_seconds,
)

COGNITO_USER_PASSWORD_MODE = "cognito_user_password"
_SUPPORTED_AUTH_MODES = {"none", COGNITO_USER_PASSWORD_MODE}


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise DevCuratorCredentialUnavailable(
            "Development document-source curator credentials are unavailable."
        )
    return value


def _load_settings() -> CognitoUserPasswordSettings:
    mode = (
        os.getenv("DOCUMENT_SOURCE_DEV_CURATOR_AUTH_MODE", "none").strip().lower()
        or "none"
    )
    if mode not in _SUPPORTED_AUTH_MODES:
        raise DevCuratorCredentialUnavailable(
            "Development document-source curator authentication is misconfigured."
        )
    if mode != COGNITO_USER_PASSWORD_MODE:
        raise DevCuratorCredentialUnavailable(
            "Development document-source curator credentials are unavailable."
        )
    return CognitoUserPasswordSettings(
        region=(
            os.getenv("DOCUMENT_SOURCE_DEV_CURATOR_COGNITO_REGION", "us-east-1").strip()
            or "us-east-1"
        ),
        user_pool_id=_required_env("DOCUMENT_SOURCE_DEV_CURATOR_COGNITO_USER_POOL_ID"),
        client_id=_required_env("DOCUMENT_SOURCE_DEV_CURATOR_COGNITO_CLIENT_ID"),
        client_secret=_required_env(
            "DOCUMENT_SOURCE_DEV_CURATOR_COGNITO_CLIENT_SECRET"
        ),
        username=_required_env("DOCUMENT_SOURCE_DEV_CURATOR_USERNAME"),
        password=_required_env("DOCUMENT_SOURCE_DEV_CURATOR_PASSWORD"),
        request_timeout_seconds=_document_source_request_timeout_seconds(),
    )


def _authenticate_sync(settings: CognitoUserPasswordSettings) -> DevCuratorCredentials:
    """Sign in through the shared Cognito helper in the worker thread."""

    try:
        tokens = authenticate_user_password(settings)
    except CognitoUserPasswordAuthError as exc:
        raise DevCuratorCredentialUnavailable(
            f"Development document-source curator credentials are unusable: {exc}"
        ) from None
    return DevCuratorCredentials(
        token=tokens.access_token,
        claims=dict(tokens.id_claims),
        expires_at=tokens.expires_at,
    )


async def resolve_development_credentials() -> DevCuratorCredentials:
    """Authenticate and validate a fresh credential pair off the event loop.

    The neutral runtime bounds this resolver and caches its validated result.
    """

    return await asyncio.to_thread(_authenticate_sync, _load_settings())

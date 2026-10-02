"""Cognito USER_PASSWORD_AUTH sign-in shared by Alliance development identities.

Both the login-free development curator and the ABC Literature client's
``cognito_user_password`` service mode sign in with a dedicated development
Cognito user. This module owns that one implementation: it signs in, validates
the returned ID and access tokens against the pool's JWKS, and hands back only
the access token, its expiry, and the ID-token claims.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import boto3
import jwt
from botocore.config import Config
from jwt import PyJWKClient

from src.lib.logging_config import suppress_sensitive_aws_sdk_debug_logging


class CognitoUserPasswordAuthError(RuntimeError):
    """Raised when Cognito sign-in returns an unusable result."""


@dataclass(frozen=True, slots=True)
class CognitoUserPasswordSettings:
    region: str
    user_pool_id: str = field(repr=False)
    client_id: str = field(repr=False)
    client_secret: str | None = field(repr=False)
    username: str = field(repr=False)
    password: str = field(repr=False)
    request_timeout_seconds: float


@dataclass(frozen=True, slots=True)
class CognitoUserPasswordTokens:
    access_token: str = field(repr=False)
    id_claims: Mapping[str, Any] = field(repr=False)
    expires_at: float


def secret_hash(*, username: str, client_id: str, client_secret: str) -> str:
    """Build Cognito's SECRET_HASH without retaining intermediate plaintext."""

    digest = hmac.new(
        client_secret.encode("utf-8"),
        f"{username}{client_id}".encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.b64encode(digest).decode("ascii")


def authenticate_user_password(
    settings: CognitoUserPasswordSettings,
) -> CognitoUserPasswordTokens:
    """Sign in and validate the paired tokens with bounded Cognito and JWKS calls.

    This blocks; async callers run it in a worker thread.
    """

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
    auth_parameters = {
        "USERNAME": settings.username,
        "PASSWORD": settings.password,
    }
    if settings.client_secret:
        auth_parameters["SECRET_HASH"] = secret_hash(
            username=settings.username,
            client_id=settings.client_id,
            client_secret=settings.client_secret,
        )
    response = client.initiate_auth(
        AuthFlow="USER_PASSWORD_AUTH",
        ClientId=settings.client_id,
        AuthParameters=auth_parameters,
    )
    if response.get("ChallengeName"):
        raise CognitoUserPasswordAuthError(
            "Cognito user-password authentication requires interaction."
        )
    authentication_result = response.get("AuthenticationResult")
    if not isinstance(authentication_result, Mapping):
        raise CognitoUserPasswordAuthError(
            "Cognito user-password authentication returned no token."
        )
    id_token = str(authentication_result.get("IdToken") or "").strip()
    access_token = str(authentication_result.get("AccessToken") or "").strip()
    if not id_token or not access_token:
        raise CognitoUserPasswordAuthError(
            "Cognito user-password authentication returned incomplete tokens."
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
        raise CognitoUserPasswordAuthError(
            "Cognito user-password authentication returned the wrong token type."
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
        raise CognitoUserPasswordAuthError(
            "Cognito user-password authentication returned the wrong bearer token type."
        )
    if access_claims.get("client_id") != settings.client_id:
        raise CognitoUserPasswordAuthError(
            "Cognito user-password authentication returned the wrong bearer client."
        )
    if access_claims.get("sub") != id_claims.get("sub"):
        raise CognitoUserPasswordAuthError(
            "Cognito user-password authentication returned mismatched token identities."
        )

    return CognitoUserPasswordTokens(
        access_token=access_token,
        id_claims=dict(id_claims),
        expires_at=min(float(id_claims["exp"]), float(access_claims["exp"])),
    )

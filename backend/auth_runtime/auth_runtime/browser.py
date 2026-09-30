"""Shared PKCE, callback validation, and browser session cookie handling."""

import base64
import hashlib
import os
import secrets
from typing import Any

from .base import AuthPrincipal, AuthProvider, TokenSet


def create_pkce() -> tuple[str, str, str]:
    verifier = secrets.token_urlsafe(32)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    return secrets.token_urlsafe(32), verifier, challenge


def set_oauth_cookies(
    response: Any, state: str, verifier: str, *, secure: bool, max_age: int | None = None
) -> None:
    if max_age is None:
        max_age = max(1, int(os.getenv("AUTH_OAUTH_COOKIE_MAX_AGE_SECONDS", "600")))
    for key, value in (("oauth_state", state), ("oauth_code_verifier", verifier)):
        response.set_cookie(
            key=key,
            value=value,
            httponly=True,
            secure=secure,
            samesite="lax",
            max_age=max_age,
        )


async def authenticate_callback(
    provider: AuthProvider, code: str, verifier: str
) -> tuple[TokenSet, AuthPrincipal]:
    tokens = await provider.handle_callback(code, verifier)
    claims = await provider.validate_token(tokens.id_token)
    principal = provider.extract_principal(claims)
    return tokens, principal


def set_session_cookie(
    response: Any, token: str, *, secure: bool, max_age: int | None = None
) -> None:
    if max_age is None:
        max_age = max(1, int(os.getenv("AUTH_SESSION_COOKIE_MAX_AGE_SECONDS", "86400")))
    response.set_cookie(
        key="auth_token",
        value=token,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=max_age,
    )
    for key in ("oauth_state", "oauth_code_verifier"):
        response.delete_cookie(key=key, secure=secure, samesite="lax")

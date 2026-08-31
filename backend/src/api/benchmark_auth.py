"""Scoped authorization policy for benchmark API callers."""

from __future__ import annotations

import logging
import threading
from typing import Any, Final

from fastapi import HTTPException, Request
from fastapi.security import SecurityScopes
from jwt.exceptions import (
    InvalidTokenError,
    PyJWKClientConnectionError,
    PyJWKClientError,
)

from src.api import auth as browser_auth
from src.auth.providers.oidc import OIDCAuthProvider
from src.lib.http_errors import raise_sanitized_http_exception
from src.lib.openai_agents.config import (
    get_benchmark_oidc_allowed_client_ids,
    get_benchmark_oidc_audience,
    get_benchmark_oidc_capability_scopes,
    get_benchmark_oidc_clock_skew_seconds,
    get_benchmark_oidc_issuer_url,
    get_benchmark_oidc_jwks_cache_ttl_seconds,
    get_benchmark_oidc_jwks_timeout_seconds,
    get_benchmark_operator_capability_groups,
)

BENCHMARK_READ: Final = "benchmark:read"
BENCHMARK_RUN: Final = "benchmark:run"
BENCHMARK_CANCEL: Final = "benchmark:cancel"
BENCHMARK_DELETE: Final = "benchmark:delete"
BENCHMARK_SOURCE_READ: Final = "benchmark:source:read"

BENCHMARK_CAPABILITIES: Final = frozenset(
    {
        BENCHMARK_READ,
        BENCHMARK_RUN,
        BENCHMARK_CANCEL,
        BENCHMARK_DELETE,
        BENCHMARK_SOURCE_READ,
    }
)

logger = logging.getLogger(__name__)
_provider: OIDCAuthProvider | None = None
_provider_lock = threading.Lock()


class _InvalidBenchmarkTokenError(InvalidTokenError):
    """Raised when a signed token violates the benchmark principal contract."""


def _get_benchmark_provider() -> OIDCAuthProvider:
    global _provider

    if _provider is not None:
        return _provider

    with _provider_lock:
        if _provider is not None:
            return _provider

        issuer = get_benchmark_oidc_issuer_url()
        audience = get_benchmark_oidc_audience()
        allowed_clients = get_benchmark_oidc_allowed_client_ids()
        if not issuer or not audience or not allowed_clients:
            raise HTTPException(
                status_code=503,
                detail="Benchmark bearer authentication is not configured",
            )

        timeout = get_benchmark_oidc_jwks_timeout_seconds()
        _provider = OIDCAuthProvider(
            {
                "issuer_url": issuer,
                "validation_issuer": issuer,
                "client_id": audience,
                "audience": audience,
                "timeout_seconds": timeout,
                "jwks_timeout_seconds": timeout,
                "jwks_cache_ttl_seconds": get_benchmark_oidc_jwks_cache_ttl_seconds(),
                "clock_skew_seconds": get_benchmark_oidc_clock_skew_seconds(),
                "required_claims": ("exp", "iat", "sub"),
            }
        )
        return _provider


def _token_scopes(claims: dict[str, Any]) -> set[str]:
    value = claims.get("scope")
    if isinstance(value, str):
        return {scope for scope in value.split() if scope}
    if isinstance(value, list):
        return {str(scope) for scope in value if str(scope)}
    return set()


def _authorized_client_id(claims: dict[str, Any]) -> str:
    asserted = {
        value
        for claim in ("client_id", "azp")
        if isinstance((value := claims.get(claim)), str) and value
    }
    allowed = set(get_benchmark_oidc_allowed_client_ids())
    if not asserted or not asserted.issubset(allowed):
        raise _InvalidBenchmarkTokenError("Unapproved benchmark token client")
    return sorted(asserted)[0]


async def _authenticate_bearer(token: str, capability: str) -> dict[str, Any]:
    provider = _get_benchmark_provider()
    try:
        claims = await provider.validate_token(token)
        client_id = _authorized_client_id(claims)
        required_scopes = set(get_benchmark_oidc_capability_scopes(capability))
        if not required_scopes or _token_scopes(claims).isdisjoint(required_scopes):
            raise HTTPException(status_code=403, detail="Benchmark capability required")
        return {
            "sub": claims["sub"],
            "client_id": client_id,
            "token_use": "bearer",
            "benchmark_capabilities": [capability],
        }
    except HTTPException:
        raise
    except PyJWKClientConnectionError as exc:
        raise_sanitized_http_exception(
            logger,
            status_code=503,
            detail="Benchmark authentication provider unavailable",
            log_message="Benchmark authentication provider unavailable during token validation",
            exc=exc,
        )
    except PyJWKClientError as exc:
        if not browser_auth.is_unknown_signing_key_error(exc):
            raise_sanitized_http_exception(
                logger,
                status_code=503,
                detail="Benchmark authentication provider unavailable",
                log_message="Benchmark authentication provider returned unusable signing keys",
                exc=exc,
            )
        logger.info("Benchmark token rejected", extra={"reason": "unknown_signing_key"})
        raise HTTPException(status_code=401, detail="Invalid benchmark access token")
    except InvalidTokenError as exc:
        logger.info(
            "Benchmark token rejected",
            extra={"reason": browser_auth.expected_token_failure_reason(exc)},
        )
        raise HTTPException(status_code=401, detail="Invalid benchmark access token")
    except Exception as exc:
        raise_sanitized_http_exception(
            logger,
            status_code=503,
            detail="Benchmark authentication provider unavailable",
            log_message="Unexpected benchmark authentication provider failure",
            exc=exc,
        )


async def _authorize(capability: str, request: Request) -> dict[str, Any]:
    authorization = request.headers.get("authorization", "")
    if authorization:
        scheme, separator, token = authorization.partition(" ")
        if not separator or scheme.lower() != "bearer" or not token.strip():
            raise HTTPException(status_code=401, detail="Invalid benchmark authorization header")
        return await _authenticate_bearer(token.strip(), capability)

    user = await browser_auth._get_user_from_cookie_impl(request, SecurityScopes())
    configured_groups = set(get_benchmark_operator_capability_groups(capability))
    user_groups = {
        str(group)
        for group in user.get("groups", [])
        if isinstance(group, str) and group
    }
    if not configured_groups or user_groups.isdisjoint(configured_groups):
        raise HTTPException(status_code=403, detail="Benchmark capability required")
    principal = dict(user)
    principal["benchmark_capabilities"] = [capability]
    return principal


async def require_benchmark_read(request: Request) -> dict[str, Any]:
    return await _authorize(BENCHMARK_READ, request)


async def require_benchmark_run(request: Request) -> dict[str, Any]:
    return await _authorize(BENCHMARK_RUN, request)


async def require_benchmark_cancel(request: Request) -> dict[str, Any]:
    return await _authorize(BENCHMARK_CANCEL, request)


async def require_benchmark_delete(request: Request) -> dict[str, Any]:
    return await _authorize(BENCHMARK_DELETE, request)


async def require_benchmark_source_read(request: Request) -> dict[str, Any]:
    return await _authorize(BENCHMARK_SOURCE_READ, request)


def reset_benchmark_auth_cache() -> None:
    """Clear the process-local verifier for tests and process reconfiguration."""
    global _provider
    with _provider_lock:
        _provider = None


__all__ = [
    "BENCHMARK_CAPABILITIES",
    "BENCHMARK_CANCEL",
    "BENCHMARK_DELETE",
    "BENCHMARK_READ",
    "BENCHMARK_RUN",
    "BENCHMARK_SOURCE_READ",
    "require_benchmark_cancel",
    "require_benchmark_delete",
    "require_benchmark_read",
    "require_benchmark_run",
    "require_benchmark_source_read",
]

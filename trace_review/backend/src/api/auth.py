"""TraceReview authentication endpoints backed by the shared auth runtime."""

import os
import logging
import secrets
from functools import lru_cache
from typing import Any, Dict, Optional

from auth_runtime.base import AuthProvider

from auth_runtime.service import TRUSTED_CALLER_EMAIL_HEADER, TRUSTED_CALLER_SUB_HEADER
from auth_runtime.browser import (
    authenticate_callback,
    create_pkce,
    set_oauth_cookies,
    set_session_cookie,
)
from auth_runtime.factory import create_auth_provider
from jwt.exceptions import (
    InvalidTokenError,
    PyJWKClientConnectionError,
    PyJWKClientError,
)
from fastapi import APIRouter, HTTPException, Request, Response, Depends
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse
from fastapi.security import SecurityScopes

from ..config import is_dev_mode, get_secure_cookies, get_frontend_url
from ..models.requests import DevBypassRequest
from ..observability import report_failure

logger = logging.getLogger(__name__)
router = APIRouter()
TRACE_REVIEW_INTERNAL_API_TOKEN_ENV = "TRACE_REVIEW_INTERNAL_API_TOKEN"
# AGR-branded trusted-caller headers were dropped after ALL-908 because core
# service authentication is project-neutral. Only this canonical protocol is read.


@lru_cache(maxsize=1)
def _configured_provider() -> AuthProvider:
    return create_auth_provider(dev_mode=is_dev_mode())


def _get_provider_or_503() -> AuthProvider:
    try:
        return _configured_provider()
    except ValueError:
        report_failure("auth_configuration")
        raise HTTPException(
            status_code=503, detail="Authentication not configured"
        ) from None


async def _get_user_from_cookie_impl(
    request: Request,
    security_scopes: SecurityScopes = SecurityScopes(),
) -> Dict[str, Any]:
    internal_user = _get_internal_service_user(request)
    if internal_user is not None:
        return internal_user
    provider = _get_provider_or_503()
    token = request.cookies.get("auth_token")
    if not token and not is_dev_mode():
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        claims = await provider.validate_token(token or "dev-token")
        principal = provider.extract_principal(claims)
        if not principal.subject:
            raise InvalidTokenError("Authenticated principal missing subject")
    except PyJWKClientConnectionError:
        report_failure("auth_validation")
        raise HTTPException(
            status_code=503, detail="Authentication provider unavailable"
        ) from None
    except PyJWKClientError as exc:
        if str(exc).startswith("Unable to find a signing key that matches:"):
            raise HTTPException(
                status_code=401, detail="Invalid authentication token"
            ) from None
        report_failure("auth_validation")
        raise HTTPException(
            status_code=503, detail="Authentication provider unavailable"
        ) from None
    except InvalidTokenError:
        raise HTTPException(
            status_code=401, detail="Invalid authentication token"
        ) from None
    except Exception:
        report_failure("auth_validation")
        raise HTTPException(
            status_code=503, detail="Authentication provider unavailable"
        ) from None
    return {
        "sub": principal.subject,
        "uid": principal.subject,
        "email": principal.email,
        "name": principal.display_name,
        "groups": principal.groups,
        "provider": principal.provider,
    }


def _get_internal_service_user(request: Request) -> Optional[Dict[str, Any]]:
    expected_token = os.getenv(TRACE_REVIEW_INTERNAL_API_TOKEN_ENV, "").strip()
    if not expected_token:
        return None

    authorization = request.headers.get("authorization", "")
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer":
        return None

    if not secrets.compare_digest(token.strip(), expected_token):
        raise HTTPException(
            status_code=401,
            detail="Invalid TraceReview service token.",
        )

    user = {
        "sub": "trace-review-internal-service",
        "uid": "trace-review-internal-service",
        "email": "trace-review-internal-service@internal",
        "name": "TraceReview internal service",
        "token_use": "internal_service",
    }
    caller_sub = request.headers.get(TRUSTED_CALLER_SUB_HEADER.lower(), "").strip()
    caller_email = request.headers.get(TRUSTED_CALLER_EMAIL_HEADER.lower(), "").strip()
    if caller_sub:
        user["trusted_caller_sub"] = caller_sub
    if caller_email:
        user["trusted_caller_email"] = caller_email
    return user


def get_auth_dependency():
    return Depends(_get_user_from_cookie_impl)


@router.get("/login")
async def login(request: Request) -> RedirectResponse:
    provider = _get_provider_or_503()
    if is_dev_mode():
        return RedirectResponse(
            url=f"{get_frontend_url()}?dev_mode=true", status_code=302
        )
    state, verifier, challenge = create_pkce()
    try:
        url = await run_in_threadpool(provider.get_login_url, state, challenge, "S256")
    except Exception:
        report_failure("auth_login")
        raise HTTPException(
            status_code=503, detail="Authentication provider unavailable"
        ) from None
    redirect = RedirectResponse(url=url, status_code=302)
    set_oauth_cookies(redirect, state, verifier, secure=get_secure_cookies())
    return redirect


@router.get("/callback")
async def callback(
    request: Request, response: Response, code: str, state: str
) -> RedirectResponse:
    stored_state = request.cookies.get("oauth_state")
    if not stored_state or not secrets.compare_digest(stored_state, state):
        raise HTTPException(status_code=403, detail="Invalid state parameter")
    verifier = request.cookies.get("oauth_code_verifier")
    if not verifier:
        raise HTTPException(status_code=400, detail="Missing code_verifier")
    provider = _get_provider_or_503()
    try:
        tokens, principal = await authenticate_callback(provider, code, verifier)
    except InvalidTokenError:
        raise HTTPException(
            status_code=401, detail="Invalid authentication token"
        ) from None
    except Exception:
        report_failure("auth_callback")
        raise HTTPException(
            status_code=503, detail="Authentication callback failed"
        ) from None
    if not principal.subject:
        raise HTTPException(
            status_code=401, detail="Authenticated principal missing subject"
        )
    redirect = RedirectResponse(url=get_frontend_url(), status_code=302)
    set_session_cookie(redirect, tokens.id_token, secure=get_secure_cookies())
    return redirect


@router.get("/logout")
async def logout(request: Request) -> RedirectResponse:
    provider = _get_provider_or_503()
    try:
        url = await run_in_threadpool(provider.get_logout_url, get_frontend_url())
    except Exception:
        report_failure("auth_logout")
        raise HTTPException(
            status_code=503, detail="Authentication provider unavailable"
        ) from None
    redirect = RedirectResponse(url=url or get_frontend_url(), status_code=302)
    redirect.delete_cookie(
        key="auth_token", secure=get_secure_cookies(), samesite="lax"
    )
    return redirect


@router.get("/me")
async def get_current_user(
    user: Dict[str, Any] = Depends(_get_user_from_cookie_impl),
) -> Dict[str, Any]:
    return {
        "authenticated": True,
        "user": {
            "sub": user.get("sub"),
            "email": user.get("email"),
            "name": user.get("name"),
            "groups": user.get("groups", []),
        },
        "dev_mode": is_dev_mode(),
    }


@router.post("/dev-bypass")
async def dev_bypass(request: DevBypassRequest) -> Dict[str, Any]:
    """
    Development mode authentication bypass.

    Only works when DEV_MODE=true environment variable is set.

    Args:
        request: Dev bypass request with dev_key

    Returns:
        Mock authentication response
    """
    if not is_dev_mode():
        raise HTTPException(
            status_code=403,
            detail="Dev mode is disabled. Set DEV_MODE=true to enable bypass authentication.",
        )

    # Simple dev key validation
    if request.dev_key != "dev":
        raise HTTPException(status_code=401, detail="Invalid dev key")

    # Return mock authentication
    return {
        "status": "authenticated",
        "user": {"email": "dev@localhost", "name": "Dev User"},
        "dev_mode": True,
    }


@router.get("/health")
async def health() -> Dict[str, Any]:
    try:
        provider = _configured_provider()
    except ValueError:
        return {
            "status": "unavailable",
            "auth_configured": False,
            "dev_mode": is_dev_mode(),
        }
    return {
        "status": "healthy",
        "auth_configured": True,
        "provider": provider.provider_name,
        "dev_mode": is_dev_mode(),
    }

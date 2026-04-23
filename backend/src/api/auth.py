"""Authentication API router with provider abstraction."""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
import threading
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.security import SecurityScopes
from jwt.exceptions import PyJWTError
from sqlalchemy.orm import Session

from src.auth.base import AuthProvider
from src.auth.factory import create_auth_provider
from src.config import get_secure_cookies, is_auth_configured, is_dev_mode
from src.lib.config import get_group
from src.lib.config.groups_loader import get_group_claim_key
from src.models.sql.database import get_db
from src.services.user_service import provision_user


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["authentication"])

_provider: Optional[AuthProvider] = None
_provider_error: Optional[str] = None
_provider_failed: bool = False
_provider_lock = threading.Lock()


class _InvalidAuthTokenError(PyJWTError):
    """Raised when authenticated claims are structurally invalid."""


def _get_provider_or_503() -> AuthProvider:
    """Get initialized auth provider or raise 503.

    Initialization failures are cached for the current process lifetime.
    After configuration changes, restart the process to retry initialization.
    """
    global _provider, _provider_error, _provider_failed

    if _provider is None and not _provider_failed:
        with _provider_lock:
            if _provider is None and not _provider_failed:
                try:
                    _provider = create_auth_provider()
                    _provider_error = None
                    _provider_failed = False
                    logger.info("Auth provider initialized: %s", _provider.provider_name)
                except Exception as exc:
                    _provider = None
                    _provider_error = str(exc)
                    _provider_failed = True
                    logger.error("Failed to initialize auth provider: %s", exc)

    if _provider is None:
        detail = "Authentication not configured"
        if _provider_error:
            detail = f"{detail}: {_provider_error}"
        raise HTTPException(status_code=503, detail=detail)
    return _provider


@router.get("/login")
async def login(request: Request) -> RedirectResponse:
    """Initiate OAuth2 authorization code flow with PKCE."""
    if not is_auth_configured():
        raise HTTPException(status_code=503, detail="Authentication not configured")

    provider = _get_provider_or_503()

    code_verifier = secrets.token_urlsafe(32)
    code_challenge_bytes = hashlib.sha256(code_verifier.encode("utf-8")).digest()
    code_challenge = base64.urlsafe_b64encode(code_challenge_bytes).decode("utf-8").rstrip("=")
    state = secrets.token_urlsafe(32)

    try:
        authorize_url = await run_in_threadpool(
            provider.get_login_url, state, code_challenge, "S256"
        )
    except Exception as exc:
        logger.error("Failed to build login URL: %s", exc)
        raise HTTPException(status_code=503, detail="Authentication provider unavailable")

    redirect_response = RedirectResponse(url=authorize_url, status_code=302)
    secure_cookies = get_secure_cookies()
    redirect_response.set_cookie(
        key="oauth_state",
        value=state,
        httponly=True,
        secure=secure_cookies,
        samesite="lax",
        max_age=600,
    )
    redirect_response.set_cookie(
        key="oauth_code_verifier",
        value=code_verifier,
        httponly=True,
        secure=secure_cookies,
        samesite="lax",
        max_age=600,
    )
    return redirect_response


@router.get("/callback")
async def callback(
    request: Request,
    response: Response,
    code: str,
    state: str,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Handle OAuth callback, provision user, and set auth cookie."""
    _ = response  # FastAPI injects this, kept for backward-compatible signature.

    stored_state = request.cookies.get("oauth_state")
    if not stored_state:
        logger.info("OAuth state cookie missing - redirecting to fresh login")
        return RedirectResponse(url="/api/auth/login", status_code=302)
    if stored_state != state:
        logger.warning("OAuth state mismatch - redirecting to fresh login")
        return RedirectResponse(url="/api/auth/login", status_code=302)

    code_verifier = request.cookies.get("oauth_code_verifier")
    if not code_verifier:
        logger.info("OAuth code_verifier cookie missing - redirecting to fresh login")
        return RedirectResponse(url="/api/auth/login", status_code=302)

    provider = _get_provider_or_503()
    try:
        tokens = await provider.handle_callback(code, code_verifier)
        claims = await provider.validate_token(tokens.id_token)
        principal = provider.extract_principal(claims)
    except Exception as exc:
        logger.error("Authentication callback failed: %s", exc)
        raise HTTPException(status_code=400, detail=f"Authentication callback failed: {exc}")

    if not principal.subject:
        raise HTTPException(status_code=400, detail="Authenticated principal missing subject")

    db_user = provision_user(db, principal)
    logger.info("User authenticated and provisioned: %s", db_user.auth_sub)

    redirect_response = RedirectResponse(url="/", status_code=302)
    secure_cookies = get_secure_cookies()
    redirect_response.set_cookie(
        key="auth_token",
        value=tokens.id_token,
        httponly=True,
        secure=secure_cookies,
        samesite="lax",
        max_age=86400,
    )
    redirect_response.delete_cookie(key="oauth_state")
    redirect_response.delete_cookie(key="oauth_code_verifier")
    return redirect_response


def _build_mock_user(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Return dict supporting both key and attribute access."""

    class MockUser(dict):
        def __getattr__(self, item: str) -> Any:
            try:
                return self[item]
            except KeyError as exc:
                raise AttributeError(item) from exc

    return MockUser(payload)


def _with_group_claim_aliases(payload: Dict[str, Any], groups: List[str]) -> Dict[str, Any]:
    """Populate configured group claim plus compatibility aliases."""
    group_claim_key = get_group_claim_key()
    payload[group_claim_key] = groups
    payload["groups"] = groups
    if group_claim_key != "cognito:groups":
        payload["cognito:groups"] = groups
    return payload


async def _get_user_from_cookie_impl(
    request: Request,
    security_scopes: SecurityScopes = SecurityScopes(),
) -> Dict[str, Any]:
    """Read auth token cookie and return validated user claims."""
    _ = security_scopes

    # API key bypass (used by tests/ops workflows)
    api_key_header = request.headers.get("X-API-Key")
    testing_api_key = os.getenv("TESTING_API_KEY")
    if api_key_header and testing_api_key and api_key_header == testing_api_key:
        api_user_id = os.getenv("TESTING_API_KEY_USER", "test-user")
        api_user_email = os.getenv("TESTING_API_KEY_EMAIL", "test@localhost")
        api_user_groups_str = os.getenv("TESTING_API_KEY_GROUPS", "developers")
        claim_groups = [g.strip() for g in api_user_groups_str.split(",") if g.strip()]
        payload = {
            "sub": f"api-key-{api_user_id}",
            "uid": f"api-key-{api_user_id}",
            "email": api_user_email,
            "name": f"API Key User ({api_user_id})",
        }
        return _build_mock_user(_with_group_claim_aliases(payload, claim_groups))

    if is_dev_mode():
        dev_user_groups = os.getenv("DEV_USER_GROUPS") or os.getenv("DEV_USER_MODS", "")
        claim_groups: List[str] = ["developers"]
        if dev_user_groups:
            parsed_groups = [g.strip().upper() for g in dev_user_groups.split(",") if g.strip()]
            for group_id in parsed_groups:
                group_def = get_group(group_id)
                if group_def and group_def.provider_groups:
                    claim_groups.append(group_def.provider_groups[0])
                else:
                    claim_groups.append(f"{group_id.lower()}-curators")

        payload = {
            "sub": "dev-user-123",
            "uid": "dev-user-123",
            "email": "dev@localhost",
            "name": "Dev User",
        }
        return _build_mock_user(_with_group_claim_aliases(payload, claim_groups))

    if not is_auth_configured():
        raise HTTPException(status_code=401, detail="Not authenticated")

    token = request.cookies.get("auth_token") or request.cookies.get("cognito_token")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    provider = _get_provider_or_503()
    try:
        claims = await provider.validate_token(token)
        principal = provider.extract_principal(claims)
        if not principal.subject:
            raise _InvalidAuthTokenError("Authenticated principal missing subject")

        payload = {
            "sub": principal.subject,
            "uid": principal.subject,
            "email": principal.email,
            "name": principal.display_name or principal.email,
            "provider": principal.provider,
        }
        payload = _with_group_claim_aliases(payload, principal.groups)
        return _build_mock_user(payload)
    except PyJWTError as exc:
        logger.error("Token validation failed: %s", exc)
        raise HTTPException(status_code=401, detail="Invalid authentication token")
    except Exception as exc:
        logger.error("Authentication provider error: %s", exc)
        raise HTTPException(status_code=401, detail="Invalid authentication token")


def get_auth_dependency():
    """Get auth dependency for protected endpoints."""
    return Depends(_get_user_from_cookie_impl)


def _build_logout_redirect_uri(request: Request, provider: AuthProvider) -> str:
    """Build post-logout redirect URI using provider config when available."""
    provider_redirect_uri = getattr(provider, "redirect_uri", None)
    if isinstance(provider_redirect_uri, str) and provider_redirect_uri:
        parsed = urlparse(provider_redirect_uri)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}/"

    return str(request.base_url).rstrip("/") + "/"


def _build_app_root_url(request: Request) -> str:
    """Build the browser destination when the provider has no logout page."""
    return str(request.base_url).rstrip("/") + "/"


def _clear_logout_cookies(response: Response) -> None:
    """Delete current and legacy auth cookies on the outgoing response."""
    secure_cookies = get_secure_cookies()
    response.delete_cookie(key="auth_token", secure=secure_cookies, samesite="lax")
    # Transitional cleanup for old cookie name
    response.delete_cookie(key="cognito_token", secure=secure_cookies, samesite="lax")


@router.post("/logout")
async def logout(
    request: Request,
) -> JSONResponse:
    """Logout endpoint - clears auth cookie and returns provider logout URL."""
    provider = _get_provider_or_503()
    redirect_uri = _build_logout_redirect_uri(request, provider)
    logout_url = await run_in_threadpool(provider.get_logout_url, redirect_uri)
    logout_response = JSONResponse(
        content={
            "status": "logged_out",
            "message": "User session terminated successfully",
            "logout_url": logout_url,
        }
    )
    _clear_logout_cookies(logout_response)
    return logout_response


@router.get("/logout/redirect")
async def logout_redirect(request: Request) -> RedirectResponse:
    """Logout endpoint for browser navigation flows that must process Set-Cookie."""
    provider = _get_provider_or_503()
    redirect_uri = _build_logout_redirect_uri(request, provider)
    logout_url = await run_in_threadpool(provider.get_logout_url, redirect_uri)
    app_root_url = _build_app_root_url(request)
    redirect_target = logout_url
    if not redirect_target:
        # If the provider has no logout page, finish on the app origin so the
        # browser still handles cookie deletion on a first-party navigation.
        redirect_target = app_root_url

    redirect_response = RedirectResponse(url=redirect_target, status_code=302)
    _clear_logout_cookies(redirect_response)
    return redirect_response


class _AuthCompat:
    """Compatibility wrapper for test dependency overrides."""

    @property
    def get_user(self):
        return _get_user_from_cookie_impl


auth = _AuthCompat()

__all__ = ["router", "get_auth_dependency", "get_db", "auth"]

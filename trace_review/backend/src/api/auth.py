"""
Authentication API endpoints with AWS Cognito OAuth2 support

Implements OAuth2 Authorization Code flow with PKCE for secure authentication.
Falls back to dev mode bypass when COGNITO is not configured or DEV_MODE=true.
"""
import os
import logging
import secrets
import hashlib
import base64
from typing import Dict, Any, Optional
from urllib.parse import urlencode

import requests
import jwt
from jwt import PyJWKClient
from fastapi import APIRouter, HTTPException, Request, Response, Depends, Security
from fastapi.responses import RedirectResponse
from fastapi.security import SecurityScopes

from ..config import (
    get_cognito_region,
    get_cognito_user_pool_id,
    get_cognito_client_id,
    get_cognito_client_secret,
    get_cognito_domain,
    get_cognito_redirect_uri,
    is_cognito_configured,
    is_dev_mode,
    get_secure_cookies,
    get_frontend_url,
)
from ..models.requests import DevBypassRequest

logger = logging.getLogger(__name__)
router = APIRouter()


# ===========================
# Cognito Configuration
# ===========================

cognito_region: Optional[str] = None
cognito_user_pool_id: Optional[str] = None
cognito_client_id: Optional[str] = None
cognito_client_secret: Optional[str] = None
cognito_domain: Optional[str] = None
cognito_redirect_uri: Optional[str] = None
jwks_client: Optional[PyJWKClient] = None
secure_cookies: bool = False

# Initialize Cognito configuration
try:
    cognito_region = get_cognito_region()
    cognito_user_pool_id = get_cognito_user_pool_id()
    cognito_client_id = get_cognito_client_id()
    cognito_client_secret = get_cognito_client_secret()
    cognito_domain = get_cognito_domain()
    cognito_redirect_uri = get_cognito_redirect_uri()
    secure_cookies = get_secure_cookies()

    if cognito_user_pool_id and cognito_client_id:
        # Initialize JWKS client for token validation
        jwks_url = f"https://cognito-idp.{cognito_region}.amazonaws.com/{cognito_user_pool_id}/.well-known/jwks.json"
        jwks_client = PyJWKClient(jwks_url)
        logger.info(f"✅ Cognito authentication initialized with pool: {cognito_user_pool_id}")
        logger.info(f"   Redirect URI: {cognito_redirect_uri}")
        logger.info(f"   Secure cookies: {secure_cookies}")
    elif not is_dev_mode():
        # Only warn if not in dev mode (expected in dev mode)
        logger.warning("⚠️  Cognito not fully configured - falling back to dev mode")
        logger.warning("   Set COGNITO_USER_POOL_ID, COGNITO_CLIENT_ID, COGNITO_CLIENT_SECRET, COGNITO_DOMAIN")

except Exception as e:
    logger.error(f"❌ Failed to initialize Cognito configuration: {e}")
    cognito_user_pool_id = None
    cognito_client_id = None


# ===========================
# Authentication Dependency
# ===========================

async def _get_user_from_cookie_impl(
    request: Request,
    security_scopes: SecurityScopes = SecurityScopes()
) -> Dict[str, Any]:
    """
    Internal implementation to extract and validate user from cookie.

    Falls back to dev mode if Cognito is not configured or DEV_MODE=true.

    Args:
        request: FastAPI request object
        security_scopes: Optional security scopes (not used currently)

    Returns:
        Decoded token claims as dictionary

    Raises:
        HTTPException: If token is missing or invalid
    """
    # Dev mode bypass
    if is_dev_mode():
        logger.debug("🔧 DEV_MODE enabled - returning mock user")
        mock_user_dict = {
            "sub": "dev-user-123",
            "uid": "dev-user-123",
            "email": "dev@localhost",
            "name": "Dev User",
            "cognito:groups": ["developers"]
        }

        # Return dict that supports attribute access
        class MockUser(dict):
            def __getattr__(self, item):
                try:
                    return self[item]
                except KeyError:
                    raise AttributeError(f"'{type(self).__name__}' object has no attribute '{item}'")

        return MockUser(mock_user_dict)

    # Cognito authentication
    if not is_cognito_configured():
        raise HTTPException(
            status_code=500,
            detail="Cognito authentication not configured. Set COGNITO_* environment variables or enable DEV_MODE."
        )

    # Get token from cookie
    token = request.cookies.get("cognito_token")
    if not token:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated. No token found in cookie."
        )

    # Validate token with JWKS
    issuer = f"https://cognito-idp.{cognito_region}.amazonaws.com/{cognito_user_pool_id}"

    try:
        signing_key = jwks_client.get_signing_key_from_jwt(token)
        decoded_token = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=cognito_client_id,
            issuer=issuer
        )

        return decoded_token

    except jwt.ExpiredSignatureError:
        logger.warning("⚠️  Token expired")
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError as e:
        logger.error(f"❌ Invalid token: {e}")
        raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")


def get_auth_dependency():
    """
    Get authentication dependency for route protection.

    Usage:
        @router.get("/protected")
        async def protected_route(user: Dict = Depends(get_auth_dependency())):
            return {"user": user.get("email")}

    Returns:
        FastAPI dependency that validates authentication
    """
    return Depends(_get_user_from_cookie_impl)


# ===========================
# OAuth2 Endpoints
# ===========================

@router.get("/login")
async def login(request: Request) -> RedirectResponse:
    """
    Initiate Cognito OAuth2 authorization code flow with PKCE.

    Steps:
    1. Generate PKCE code_verifier and code_challenge
    2. Generate state for CSRF protection
    3. Redirect to Cognito Hosted UI
    4. Store state and code_verifier in httpOnly cookies

    Returns:
        RedirectResponse to Cognito Hosted UI
    """
    # Check if Cognito is configured
    if not is_cognito_configured():
        if is_dev_mode():
            logger.info("🔧 DEV_MODE: Redirecting to dev bypass")
            return RedirectResponse(url=f"{get_frontend_url()}?dev_mode=true", status_code=302)
        else:
            raise HTTPException(
                status_code=500,
                detail="Cognito authentication not configured. Set COGNITO_* environment variables or enable DEV_MODE."
            )

    try:
        # Generate PKCE code_verifier (random 32-byte string, URL-safe base64)
        code_verifier = secrets.token_urlsafe(32)

        # Generate code_challenge from code_verifier using SHA256
        code_challenge_bytes = hashlib.sha256(code_verifier.encode('utf-8')).digest()
        code_challenge = base64.urlsafe_b64encode(code_challenge_bytes).decode('utf-8').rstrip('=')

        # Generate state for CSRF protection
        state = secrets.token_urlsafe(32)

        # Build authorization URL
        authorize_url = f"{cognito_domain}/oauth2/authorize"
        authorize_params = {
            "client_id": cognito_client_id,
            "response_type": "code",
            "scope": "openid profile email",
            "redirect_uri": cognito_redirect_uri,
            "state": state,
            "code_challenge_method": "S256",
            "code_challenge": code_challenge,
        }

        full_authorize_url = f"{authorize_url}?{urlencode(authorize_params)}"

        logger.info(f"🔐 Initiating OAuth2 login for redirect_uri: {cognito_redirect_uri}")

        # Create redirect response
        redirect_response = RedirectResponse(url=full_authorize_url, status_code=302)

        # Store state and code_verifier in httpOnly cookies (expires in 10 minutes)
        redirect_response.set_cookie(
            key="oauth_state",
            value=state,
            httponly=True,
            secure=secure_cookies,
            samesite="lax",
            max_age=600  # 10 minutes
        )
        redirect_response.set_cookie(
            key="oauth_code_verifier",
            value=code_verifier,
            httponly=True,
            secure=secure_cookies,
            samesite="lax",
            max_age=600  # 10 minutes
        )

        return redirect_response

    except Exception as e:
        logger.error(f"❌ Login failed: {e}")
        raise HTTPException(status_code=500, detail=f"Login failed: {str(e)}")


@router.get("/callback")
async def callback(
    request: Request,
    response: Response,
    code: str,
    state: str
) -> RedirectResponse:
    """
    Handle Cognito OAuth2 callback.

    Steps:
    1. Verify state parameter (CSRF protection)
    2. Exchange authorization code for tokens using Basic Auth + PKCE
    3. Validate ID token with JWKS
    4. Store ID token in httpOnly cookie
    5. Redirect to frontend

    Args:
        code: Authorization code from Cognito
        state: State parameter for CSRF protection

    Returns:
        RedirectResponse to frontend with authentication cookie
    """
    try:
        # Verify state (CSRF protection)
        stored_state = request.cookies.get("oauth_state")
        if not stored_state or stored_state != state:
            logger.error("❌ Invalid state parameter (CSRF protection)")
            raise HTTPException(status_code=403, detail="Invalid state parameter")

        # Get code_verifier from cookie
        code_verifier = request.cookies.get("oauth_code_verifier")
        if not code_verifier:
            logger.error("❌ Missing code_verifier cookie")
            raise HTTPException(status_code=400, detail="Missing code_verifier")

        # Exchange authorization code for tokens
        token_endpoint = f"{cognito_domain}/oauth2/token"

        # Build Basic Auth header (client_id:client_secret)
        credentials = f"{cognito_client_id}:{cognito_client_secret}"
        credentials_b64 = base64.b64encode(credentials.encode('utf-8')).decode('utf-8')

        token_data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": cognito_redirect_uri,
            "code_verifier": code_verifier,
        }

        headers = {
            "Authorization": f"Basic {credentials_b64}",
            "Content-Type": "application/x-www-form-urlencoded",
        }

        logger.info(f"🔄 Exchanging authorization code for tokens")

        token_response = requests.post(token_endpoint, data=token_data, headers=headers)

        if token_response.status_code != 200:
            logger.error(f"❌ Token exchange failed: {token_response.status_code} - {token_response.text}")
            raise HTTPException(
                status_code=500,
                detail=f"Token exchange failed: {token_response.text}"
            )

        tokens = token_response.json()
        id_token = tokens.get("id_token")
        access_token = tokens.get("access_token")

        if not id_token or not access_token:
            logger.error("❌ Missing tokens in response")
            raise HTTPException(status_code=500, detail="Missing tokens in response")

        # Validate ID token with JWKS
        issuer = f"https://cognito-idp.{cognito_region}.amazonaws.com/{cognito_user_pool_id}"

        try:
            signing_key = jwks_client.get_signing_key_from_jwt(id_token)
            decoded_token = jwt.decode(
                id_token,
                signing_key.key,
                algorithms=["RS256"],
                audience=cognito_client_id,
                issuer=issuer,
                access_token=access_token,
                options={"verify_at_hash": True}
            )

            logger.info(f"✅ Authentication successful for user: {decoded_token.get('email', 'unknown')}")

        except jwt.InvalidTokenError as e:
            logger.error(f"❌ Token validation failed: {e}")
            raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")

        # Create redirect response to frontend
        frontend_url = get_frontend_url()
        redirect_response = RedirectResponse(url=frontend_url, status_code=302)

        # Set httpOnly cookie with ID token (expires in 24 hours)
        redirect_response.set_cookie(
            key="cognito_token",
            value=id_token,
            httponly=True,
            secure=secure_cookies,
            samesite="lax",
            max_age=86400  # 24 hours
        )

        # Clear OAuth state cookies
        redirect_response.delete_cookie(key="oauth_state")
        redirect_response.delete_cookie(key="oauth_code_verifier")

        return redirect_response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Callback failed: {e}")
        raise HTTPException(status_code=500, detail=f"Callback failed: {str(e)}")


@router.get("/logout")
async def logout(request: Request) -> RedirectResponse:
    """
    Logout user and clear authentication cookie.

    Returns:
        RedirectResponse to Cognito logout endpoint
    """
    frontend_url = get_frontend_url()

    # Create redirect response
    if is_cognito_configured():
        # Redirect to Cognito logout endpoint
        logout_url = f"{cognito_domain}/logout"
        logout_params = {
            "client_id": cognito_client_id,
            "logout_uri": frontend_url,
        }
        full_logout_url = f"{logout_url}?{urlencode(logout_params)}"
        redirect_response = RedirectResponse(url=full_logout_url, status_code=302)
    else:
        # Dev mode - just redirect to frontend
        redirect_response = RedirectResponse(url=frontend_url, status_code=302)

    # Clear authentication cookie
    redirect_response.delete_cookie(key="cognito_token")

    logger.info("👋 User logged out")

    return redirect_response


@router.get("/me")
async def get_current_user(
    user: Dict[str, Any] = Depends(_get_user_from_cookie_impl)
) -> Dict[str, Any]:
    """
    Get current authenticated user information.

    Returns:
        User information from ID token
    """
    return {
        "authenticated": True,
        "user": {
            "sub": user.get("sub"),
            "email": user.get("email"),
            "name": user.get("name"),
            "groups": user.get("cognito:groups", [])
        },
        "dev_mode": is_dev_mode()
    }


# ===========================
# Dev Mode Endpoints
# ===========================

@router.post("/dev-bypass")
async def dev_bypass(request: DevBypassRequest) -> Dict[str, Any]:
    """
    Development mode authentication bypass (legacy endpoint).

    Only works when DEV_MODE=true environment variable is set.

    Args:
        request: Dev bypass request with dev_key

    Returns:
        Mock authentication response
    """
    if not is_dev_mode():
        raise HTTPException(
            status_code=403,
            detail="Dev mode is disabled. Set DEV_MODE=true to enable bypass authentication."
        )

    # Simple dev key validation
    if request.dev_key != "dev":
        raise HTTPException(
            status_code=401,
            detail="Invalid dev key"
        )

    # Return mock authentication
    return {
        "status": "authenticated",
        "user": {
            "email": "dev@localhost",
            "name": "Dev User"
        },
        "dev_mode": True
    }


# ===========================
# Health Check
# ===========================

@router.get("/health")
async def health() -> Dict[str, Any]:
    """
    Authentication service health check.

    Returns:
        Health status and configuration info
    """
    return {
        "status": "healthy",
        "cognito_configured": is_cognito_configured(),
        "dev_mode": is_dev_mode(),
        "jwks_initialized": jwks_client is not None
    }

"""Authentication API router with AWS Cognito integration.

Implements:
- AWS Cognito authentication
- GET /auth/login endpoint (redirects to Cognito Hosted UI)
- GET /auth/callback endpoint (handles Cognito redirect, sets httpOnly cookie)
- POST /auth/logout endpoint
- GET /users/me endpoint (in users.py)

Pattern follows OAuth2 Authorization Code flow with PKCE.
"""

import base64
import hashlib
import logging
import os
import secrets
from types import SimpleNamespace
from typing import Optional, Dict, Any, List
from urllib.parse import urlencode

import requests
from fastapi import APIRouter, Depends, HTTPException, Response, Request
from fastapi.security import SecurityScopes
from fastapi.responses import RedirectResponse
from jose import jwt, JWTError
from jwt import PyJWKClient
from sqlalchemy.orm import Session

from src.config import (
    get_cognito_redirect_uri,
    get_cognito_region,
    get_cognito_user_pool_id,
    get_cognito_client_id,
    get_cognito_client_secret,
    get_cognito_domain,
    is_cognito_configured,
    is_dev_mode,
    get_secure_cookies
)
from src.lib.config import get_group
from src.models.sql.database import get_db
from src.models.sql.user import User
from src.services.user_service import set_global_user_from_cognito


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["authentication"])


# ============================================================================
# Cognito Initialization
# ============================================================================

# Cognito configuration (validated at startup)
cognito_region: Optional[str] = None
cognito_user_pool_id: Optional[str] = None
cognito_client_id: Optional[str] = None
cognito_client_secret: Optional[str] = None
cognito_domain: Optional[str] = None  # Custom domain (loaded from config)
jwks_client: Optional[PyJWKClient] = None

try:
    cognito_region = get_cognito_region()
    cognito_user_pool_id = get_cognito_user_pool_id()
    cognito_client_id = get_cognito_client_id()
    cognito_client_secret = get_cognito_client_secret()
    cognito_domain = get_cognito_domain()

    if cognito_user_pool_id and cognito_client_id:
        # Initialize JWKS client for token validation
        jwks_url = f"https://cognito-idp.{cognito_region}.amazonaws.com/{cognito_user_pool_id}/.well-known/jwks.json"
        jwks_client = PyJWKClient(jwks_url)
        logger.info(f"Cognito authentication initialized with pool: {cognito_user_pool_id}")
    else:
        logger.warning(
            "Cognito authentication not configured - COGNITO_USER_POOL_ID and/or COGNITO_CLIENT_ID not set. "
            "Authentication endpoints will not work."
        )
except Exception as e:
    logger.error(f"Failed to initialize Cognito authentication: {e}")
    logger.warning("Authentication endpoints will not work until Cognito is configured.")


# ============================================================================
# OAuth2 Authorization Code Flow Endpoints
# ============================================================================

@router.get("/login")
async def login(request: Request) -> RedirectResponse:
    """Initiate Cognito OAuth2 authorization code flow.

    Redirects user to Cognito Hosted UI login page. After successful authentication,
    Cognito redirects back to /auth/callback with authorization code.

    Requirements: FR-001, FR-002

    Flow:
    1. User visits /auth/login
    2. Backend generates PKCE code_verifier and state (CSRF protection)
    3. Backend redirects to Cognito authorize endpoint
    4. User authenticates with Cognito
    5. Cognito redirects to /auth/callback?code=...&state=...

    Returns:
        RedirectResponse: Redirects to Cognito authorization URL

    Raises:
        503: If Cognito is not configured
    """
    if not is_cognito_configured():
        raise HTTPException(
            status_code=503,
            detail="Authentication not configured"
        )

    if not cognito_client_id:
        raise HTTPException(
            status_code=503,
            detail="Cognito configuration incomplete - missing client_id"
        )

    # Generate PKCE code_verifier (for Authorization Code flow with PKCE)
    # This prevents authorization code interception attacks
    code_verifier = secrets.token_urlsafe(32)

    # Generate code_challenge from code_verifier using SHA256
    # PKCE requires: code_challenge = BASE64URL(SHA256(code_verifier))
    code_challenge_bytes = hashlib.sha256(code_verifier.encode('utf-8')).digest()
    code_challenge = base64.urlsafe_b64encode(code_challenge_bytes).decode('utf-8').rstrip('=')

    # Generate state parameter for CSRF protection
    state = secrets.token_urlsafe(32)

    # Build Cognito authorization URL
    # Using Authorization Code flow (not Implicit flow for better security)
    redirect_uri = get_cognito_redirect_uri() or str(request.url_for("callback"))

    authorize_params = {
        "client_id": cognito_client_id,
        "response_type": "code",
        "scope": "openid profile email",
        "redirect_uri": str(redirect_uri),
        "state": state,
        # PKCE parameters
        "code_challenge_method": "S256",
        "code_challenge": code_challenge,
    }

    authorize_url = f"{cognito_domain}/oauth2/authorize?{urlencode(authorize_params)}"

    logger.info(f"Redirecting to Cognito login: {authorize_url}")

    # Create redirect response with cookies for PKCE/CSRF state
    # Store state and code_verifier in session for callback verification
    redirect_response = RedirectResponse(url=authorize_url, status_code=302)
    secure_cookies = get_secure_cookies()
    redirect_response.set_cookie(
        key="oauth_state",
        value=state,
        httponly=True,
        secure=secure_cookies,  # True in production (HTTPS), False in dev (HTTP)
        samesite="lax",
        max_age=600,  # 10 minutes - enough time to complete login
    )
    redirect_response.set_cookie(
        key="oauth_code_verifier",
        value=code_verifier,
        httponly=True,
        secure=secure_cookies,  # True in production (HTTPS), False in dev (HTTP)
        samesite="lax",
        max_age=600,  # 10 minutes
    )

    return redirect_response


@router.get("/callback")
async def callback(
    request: Request,
    response: Response,
    code: str,
    state: str,
    db: Session = Depends(get_db)
) -> RedirectResponse:
    """Handle Cognito OAuth2 callback and set authentication cookie.

    Called by Cognito after successful user authentication. Exchanges authorization
    code for access token, validates token, creates/updates user, and sets httpOnly cookie.

    Requirements: FR-002, FR-003, FR-004, FR-005

    Args:
        request: FastAPI request object
        response: FastAPI response object
        code: Authorization code from Cognito
        state: CSRF protection state parameter
        db: Database session

    Returns:
        RedirectResponse: Redirects to frontend homepage with auth cookie set

    Raises:
        400: If authorization code exchange fails
        403: If state parameter doesn't match (CSRF attack)
        503: If Cognito is not configured
    """
    if not is_cognito_configured():
        raise HTTPException(
            status_code=503,
            detail="Authentication not configured"
        )

    # Verify state parameter (CSRF protection)
    stored_state = request.cookies.get("oauth_state")
    if not stored_state:
        # Cookie expired or missing - redirect to start fresh login flow
        # This commonly happens when:
        # 1. User's session timed out and they have a stale redirect URL
        # 2. User took longer than 10 minutes to complete login
        # 3. Browser cleared cookies during the login flow
        logger.info(f"OAuth state cookie missing - redirecting to fresh login. Incoming state: {state}")
        return RedirectResponse(url="/api/auth/login", status_code=302)

    if stored_state != state:
        # State exists but doesn't match - this is a real CSRF concern
        # Still redirect to login for better UX, but log as warning
        logger.warning(f"State mismatch - possible CSRF or stale session. Expected: {stored_state}, Got: {state}")
        return RedirectResponse(url="/api/auth/login", status_code=302)

    # Get configuration
    code_verifier = request.cookies.get("oauth_code_verifier")

    if not cognito_client_id or not cognito_client_secret:
        raise HTTPException(
            status_code=503,
            detail="Cognito configuration incomplete"
        )

    # Exchange authorization code for access token
    # IMPORTANT: Cognito requires Basic Authentication (client_id:client_secret)
    token_endpoint = f"{cognito_domain}/oauth2/token"
    redirect_uri = get_cognito_redirect_uri() or str(request.url_for("callback"))

    # Prepare Basic Auth header: base64(client_id:client_secret)
    credentials = f"{cognito_client_id}:{cognito_client_secret}"
    credentials_b64 = base64.b64encode(credentials.encode('utf-8')).decode('utf-8')

    token_data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,  # PKCE verification
    }

    headers = {
        "Authorization": f"Basic {credentials_b64}",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    try:
        token_response = requests.post(token_endpoint, data=token_data, headers=headers)
        token_response.raise_for_status()
        tokens = token_response.json()

        access_token = tokens.get("access_token")
        id_token = tokens.get("id_token")

        if not access_token or not id_token:
            raise HTTPException(
                status_code=400,
                detail="Failed to obtain tokens from Cognito"
            )

        logger.info("Successfully exchanged authorization code for access token")

    except requests.RequestException as e:
        logger.error(f"Failed to exchange authorization code: {e}")
        raise HTTPException(
            status_code=400,
            detail=f"Failed to exchange authorization code: {str(e)}"
        )

    # Validate ID token and extract user info
    try:
        # Get signing key from JWKS
        signing_key = jwks_client.get_signing_key_from_jwt(id_token)

        # Decode and validate ID token
        issuer = f"https://cognito-idp.{cognito_region}.amazonaws.com/{cognito_user_pool_id}"
        decoded_token = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=cognito_client_id,
            issuer=issuer,
            access_token=access_token,  # Required for at_hash validation
            options={"verify_at_hash": True}
        )

        # Extract user info from Cognito token claims
        cognito_user = {
            "sub": decoded_token.get("sub"),  # Unique user ID (UUIDv4)
            "email": decoded_token.get("email"),
            "name": decoded_token.get("name") or decoded_token.get("email"),
            "groups": decoded_token.get("cognito:groups", [])  # List of group names
        }

        logger.info(f"User authenticated: {cognito_user['sub']} ({cognito_user['email']})")

    except JWTError as e:
        logger.error(f"Failed to validate ID token: {e}")
        raise HTTPException(
            status_code=400,
            detail=f"Failed to validate ID token: {str(e)}"
        )

    # Create or update user in database (FR-005 - automatic provisioning)
    set_global_user_from_cognito(db, cognito_user)

    # Set httpOnly cookie with ID token (contains user profile info)
    # ID tokens are meant for user identity/profile, access tokens for API authorization
    # Since we're not calling external APIs, we only need the ID token
    redirect_response = RedirectResponse(url="/", status_code=302)
    secure_cookies = get_secure_cookies()
    redirect_response.set_cookie(
        key="cognito_token",
        value=id_token,  # Use ID token instead of access token for user profile info
        httponly=True,  # Prevents JavaScript access (XSS protection)
        secure=secure_cookies,  # True in production (HTTPS), False in dev (HTTP)
        samesite="lax",  # CSRF protection
        max_age=86400,  # 24 hours (matches FR-018 session timeout)
    )

    # Clear temporary OAuth state cookies
    redirect_response.delete_cookie(key="oauth_state")
    redirect_response.delete_cookie(key="oauth_code_verifier")

    logger.info(f"Set authentication cookie for user: {cognito_user['sub']}")

    return redirect_response


# ============================================================================
# Authentication Dependency
# ============================================================================


# Inner function that will be used for dependency injection
async def _get_user_from_cookie_impl(
    request: Request,
    security_scopes: SecurityScopes = SecurityScopes()
) -> Dict[str, Any]:
    """Read access token from cookie and validate with Cognito.

    In DEV_MODE, returns a mock user for local development without Cognito.
    API Key authentication: Returns configured user when X-API-Key header matches.
    """
    # API KEY BYPASS: Check for X-API-Key header first (for testing/monitoring)
    api_key_header = request.headers.get("X-API-Key")
    testing_api_key = os.getenv("TESTING_API_KEY")

    if api_key_header and testing_api_key and api_key_header == testing_api_key:
        logger.info("API Key authentication successful - using configured test user")

        # Load user configuration from environment variables
        api_user_id = os.getenv("TESTING_API_KEY_USER", "test-user")
        api_user_email = os.getenv("TESTING_API_KEY_EMAIL", "test@localhost")
        api_user_groups_str = os.getenv("TESTING_API_KEY_GROUPS", "developers")
        api_user_mods_str = os.getenv("TESTING_API_KEY_MODS", "")

        # Parse groups (comma-separated)
        cognito_groups = [g.strip() for g in api_user_groups_str.split(",") if g.strip()]

        # Log the API key user details
        logger.info(
            f"API Key user: {api_user_id}, email: {api_user_email}, "
            f"groups: {cognito_groups}, mods: {api_user_mods_str or 'none'}"
        )

        # Create user dict matching Cognito structure
        api_user_dict = {
            "sub": f"api-key-{api_user_id}",
            "uid": f"api-key-{api_user_id}",
            "email": api_user_email,
            "name": f"API Key User ({api_user_id})",
            "cognito:groups": cognito_groups
        }

        # Use MockUser class (same as DEV_MODE) for dict + attribute access
        class MockUser(dict):
            """Mock user that supports both dict and attribute access."""
            def __getattr__(self, item):
                try:
                    return self[item]
                except KeyError:
                    raise AttributeError(f"'{type(self).__name__}' object has no attribute '{item}'")

        api_user = MockUser(api_user_dict)
        return api_user

    # DEV MODE BYPASS: Return mock user when DEV_MODE=true
    if is_dev_mode():
        logger.debug("DEV_MODE enabled - returning mock user (bypassing Cognito authentication)")

        # Support DEV_USER_MODS env var for testing MOD-specific behavior
        # Format: comma-separated MOD IDs (e.g., "MGI,FB" or "MGI")
        dev_user_mods = os.getenv("DEV_USER_MODS", "")
        cognito_groups: List[str] = ["developers"]  # Always include developers group

        if dev_user_mods:
            # Map MOD IDs to Cognito group names using groups_loader
            parsed_mods = [m.strip().upper() for m in dev_user_mods.split(",") if m.strip()]
            for mod in parsed_mods:
                group_def = get_group(mod)
                if group_def and group_def.cognito_groups:
                    # Use the first cognito group for this MOD
                    cognito_groups.append(group_def.cognito_groups[0])
                else:
                    # Unknown MOD, create a generic group name
                    cognito_groups.append(f"{mod.lower()}-curators")
            logger.info(f"DEV_USER_MODS={dev_user_mods} -> cognito:groups={cognito_groups}")

        # Use SimpleNamespace to support both dict-like .get() and attribute access .uid
        mock_user_dict = {
            "sub": "dev-user-123",
            "uid": "dev-user-123",  # Add uid for compatibility with user.uid access pattern
            "email": "dev@localhost",
            "name": "Dev User",
            "cognito:groups": cognito_groups
        }
        # Create object that supports both user.uid and user.get('sub')
        class MockUser(dict):
            """Mock user that supports both dict and attribute access."""
            def __getattr__(self, item):
                try:
                    return self[item]
                except KeyError:
                    raise AttributeError(f"'{type(self).__name__}' object has no attribute '{item}'")

        mock_user = MockUser(mock_user_dict)
        return mock_user

    if not is_cognito_configured():
        raise HTTPException(
            status_code=401,
            detail="Not authenticated"
        )

    # Read ID token from cookie (changed from access token)
    id_token = request.cookies.get("cognito_token")
    if not id_token:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated"
        )

    # Validate ID token using JWKS
    try:
        signing_key = jwks_client.get_signing_key_from_jwt(id_token)
        issuer = f"https://cognito-idp.{cognito_region}.amazonaws.com/{cognito_user_pool_id}"

        # ID tokens have audience claim (must match client_id)
        # Don't verify at_hash since we're not providing access_token
        decoded_token = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=cognito_client_id,  # ID tokens have aud claim
            issuer=issuer,
            options={"verify_at_hash": False}  # Skip at_hash verification (no access_token stored)
        )

        # Validate token_use claim (must be "id")
        if decoded_token.get("token_use") != "id":
            raise JWTError("Invalid token_use claim - expected 'id'")

        # Extract user info
        user = {
            "sub": decoded_token.get("sub"),
            "email": decoded_token.get("email"),
            "name": decoded_token.get("name") or decoded_token.get("email"),
            "cognito:groups": decoded_token.get("cognito:groups", [])
        }

        return user

    except JWTError as e:
        logger.error(f"Token validation failed: {e}")
        raise HTTPException(
            status_code=401,
            detail="Invalid authentication token"
        )


def get_auth_dependency():
    """Get the auth dependency for route protection.

    Returns a dependency that reads the access token from the cognito_token cookie
    and validates it using JWKS verification.

    Returns:
        Dict[str, Any] containing user info if authenticated, raises 401 if not

    Usage in other routers:
        from src.api.auth import get_auth_dependency

        @router.get("/endpoint")
        async def protected_endpoint(
            user: dict = get_auth_dependency()
        ):
            # user is guaranteed to be authenticated if request succeeds
            # user contains: sub, email, name, cognito:groups
            # ... rest of endpoint logic
    """
    return Depends(_get_user_from_cookie_impl)


@router.post("/logout")
async def logout(
    response: Response,
    user: dict = get_auth_dependency()
):
    """Logout endpoint - terminates user session and clears auth cookie.

    Contract: POST /auth/logout
    Requirements: FR-009, FR-010

    Per contract spec, this endpoint returns 200 with JSON response, NOT a redirect.

    Args:
        response: FastAPI response object for setting cookies
        user: Authenticated Cognito user from dependency

    Returns:
        JSON response with status and message

    Raises:
        401: If authentication token is missing or invalid
    """
    if not user:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated"
        )

    # Log logout event
    logger.info(f"User {user['sub']} ({user['email']}) logged out")

    # Clear the authentication cookie
    # Contract requires JSON response, so we clear cookie via Response object
    secure_cookies = get_secure_cookies()
    response.delete_cookie(
        key="cognito_token",
        secure=secure_cookies,
        samesite="lax"
    )

    # Return JSON response per contract specification
    # Client is responsible for handling Cognito logout redirect if needed
    
    # Construct Cognito logout URL for client redirection
    logout_url = None
    if cognito_domain and cognito_client_id:
        # Use the configured redirect URI as a base to determine the frontend URL
        # If get_cognito_redirect_uri() is set (e.g. http://localhost:3002/auth/callback),
        # we strip the path to get the base URL (http://localhost:3002)
        # Fallback to the request base URL if configuration is missing
        base_redirect_uri = get_cognito_redirect_uri()
        if base_redirect_uri:
            # Simple way to get origin: split by / and take first 3 parts (scheme://host:port)
            # or just use it directly if it's registered as a valid logout URL
            # For safety, we'll assume the root of the app is a valid logout target
            from urllib.parse import urlparse
            parsed = urlparse(base_redirect_uri)
            logout_uri = f"{parsed.scheme}://{parsed.netloc}/"
        else:
            # Fallback to request base URL (usually backend URL, might not be what we want for frontend)
            # But in many setups backend and frontend share origin or are proxied
            logout_uri = str(request.base_url).rstrip('/')

        logout_params = {
            "client_id": cognito_client_id,
            "logout_uri": logout_uri,
        }
        logout_url = f"{cognito_domain}/logout?{urlencode(logout_params)}"

    return {
        "status": "logged_out",
        "message": "User session terminated successfully",
        "logout_url": logout_url
    }


# Note: GET /users/me moved to users.py router to satisfy contract requirement
# that endpoint be at /users/me (not /auth/me). See backend/src/api/users.py


# ============================================================================
# Exports for other modules
# ============================================================================

# Compatibility shim for tests that use dependency override pattern
# Tests use: app.dependency_overrides[auth.get_user] = lambda: mock_user
class _AuthCompat:
    """Compatibility wrapper for test dependency overrides.

    Provides a `get_user` attribute that can be used as a dependency override key.
    This allows tests to use the pattern:
        app.dependency_overrides[auth.get_user] = lambda: mock_user

    The actual dependency function is _get_user_from_cookie_impl.
    """

    @property
    def get_user(self):
        """Return the dependency function that can be overridden in tests."""
        return _get_user_from_cookie_impl


# Create singleton instance for backward compatibility
auth = _AuthCompat()

# Export helper for use in other routers
__all__ = ["router", "get_auth_dependency", "get_db", "auth"]

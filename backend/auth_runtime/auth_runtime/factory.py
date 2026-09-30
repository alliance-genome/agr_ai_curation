"""One environment-driven provider factory for all services."""

import os

from .base import AuthProvider
from .dev import DevAuthProvider
from .oidc import OIDCAuthProvider


def get_auth_provider() -> str:
    provider = os.getenv("AUTH_PROVIDER", "").strip().lower()
    if not provider:
        raise ValueError(
            "AUTH_PROVIDER must be set explicitly (one of: cognito, oidc, dev)"
        )
    if provider not in {"cognito", "oidc", "dev"}:
        raise ValueError(
            f"Invalid AUTH_PROVIDER '{provider}'. Expected one of: cognito, oidc, dev"
        )
    return provider


def create_cognito_provider() -> OIDCAuthProvider:
    """Cognito uses the same OIDC implementation; deployment owns its defaults."""
    required = (
        "COGNITO_REGION",
        "COGNITO_USER_POOL_ID",
        "COGNITO_CLIENT_ID",
        "COGNITO_DOMAIN",
        "COGNITO_REDIRECT_URI",
    )
    if not all(os.getenv(key) for key in required):
        raise ValueError("AUTH_PROVIDER=cognito but Cognito is not fully configured")
    return OIDCAuthProvider(
        {
            "issuer_url": f"https://cognito-idp.{os.environ['COGNITO_REGION']}.amazonaws.com/{os.environ['COGNITO_USER_POOL_ID']}",
            "client_id": os.environ["COGNITO_CLIENT_ID"],
            "client_secret": os.getenv("COGNITO_CLIENT_SECRET"),
            "redirect_uri": os.environ["COGNITO_REDIRECT_URI"],
            "group_claim": "cognito:groups",
            "logout_url": f"{os.environ['COGNITO_DOMAIN'].rstrip('/')}/logout",
            "logout_redirect_param": "logout_uri",
            "scopes": "openid profile email",
        }
    )


def create_auth_provider(
    *, dev_mode: bool, group_claim: str = "groups"
) -> AuthProvider:
    """Use the caller's authorized dev-mode decision, never infer a bypass."""
    if dev_mode:
        return DevAuthProvider()
    provider_type = get_auth_provider()
    if provider_type == "cognito":
        return create_cognito_provider()
    if provider_type == "dev":
        raise ValueError("AUTH_PROVIDER=dev requires DEV_MODE=true")
    required = ("OIDC_ISSUER_URL", "OIDC_CLIENT_ID", "OIDC_REDIRECT_URI")
    if not all(os.getenv(key) for key in required):
        raise ValueError(
            "AUTH_PROVIDER=oidc requires OIDC_ISSUER_URL, OIDC_CLIENT_ID, and OIDC_REDIRECT_URI"
        )
    return OIDCAuthProvider(
        {
            "issuer_url": os.environ["OIDC_ISSUER_URL"],
            "client_id": os.environ["OIDC_CLIENT_ID"],
            "client_secret": os.getenv("OIDC_CLIENT_SECRET"),
            "redirect_uri": os.environ["OIDC_REDIRECT_URI"],
            "group_claim": os.getenv("OIDC_GROUP_CLAIM") or group_claim,
            "scopes": os.getenv("OIDC_SCOPES", "openid profile email"),
            "logout_url": os.getenv("OIDC_LOGOUT_URL"),
            "logout_redirect_param": os.getenv(
                "OIDC_LOGOUT_REDIRECT_PARAM", "post_logout_redirect_uri"
            ),
        }
    )

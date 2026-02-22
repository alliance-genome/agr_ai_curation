"""Cognito provider factory (configured as generic OIDC)."""

from src.auth.providers.oidc import OIDCAuthProvider
from src.config import (
    get_cognito_client_id,
    get_cognito_client_secret,
    get_cognito_domain,
    get_cognito_redirect_uri,
    get_cognito_region,
    get_cognito_user_pool_id,
)


def create_cognito_provider() -> OIDCAuthProvider:
    """Create OIDC provider configured for AWS Cognito."""
    region = get_cognito_region()
    pool_id = get_cognito_user_pool_id()
    issuer_url = f"https://cognito-idp.{region}.amazonaws.com/{pool_id}"

    return OIDCAuthProvider(
        {
            "issuer_url": issuer_url,
            "client_id": get_cognito_client_id(),
            "client_secret": get_cognito_client_secret(),
            "redirect_uri": get_cognito_redirect_uri(),
            "group_claim": "cognito:groups",
            "logout_url": f"{get_cognito_domain()}/logout",
            "logout_redirect_param": "logout_uri",
            "scopes": "openid profile email",
        }
    )

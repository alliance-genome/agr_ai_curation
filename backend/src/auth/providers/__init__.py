"""Authentication provider implementations (lazy exports)."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .cognito_config import create_cognito_provider
    from .dev import DevAuthProvider
    from .oidc import OIDCAuthProvider

__all__ = ["OIDCAuthProvider", "DevAuthProvider", "create_cognito_provider"]


def __getattr__(name: str) -> Any:
    if name == "OIDCAuthProvider":
        from .oidc import OIDCAuthProvider

        return OIDCAuthProvider
    if name == "DevAuthProvider":
        from .dev import DevAuthProvider

        return DevAuthProvider
    if name == "create_cognito_provider":
        from .cognito_config import create_cognito_provider

        return create_cognito_provider
    raise AttributeError(name)

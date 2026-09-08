"""Select authoritative current-principal adapters by configured auth provider."""

from importlib.metadata import entry_points
from typing import cast

from src.auth.base import CurrentPrincipalResolver
from src.config import get_auth_provider, is_dev_mode


def get_current_principal_resolver() -> CurrentPrincipalResolver:
    """Load exactly one trusted adapter, never a token or dev-mode substitute.

    Installed packages may expose a callable under the entry-point group
    ``agr_ai_curation.current_principal_resolvers``, named for AUTH_PROVIDER.
    Cognito is built in; overriding it or registering duplicates is an error.
    """
    provider = get_auth_provider()
    if is_dev_mode() or provider == "dev":
        raise ValueError("Current principal lookup is unavailable in development mode")
    registered = tuple(entry_points(
        group="agr_ai_curation.current_principal_resolvers", name=provider,
    ))
    if len(registered) + (provider == "cognito") != 1:
        raise ValueError("Exactly one current principal resolver must be configured")
    if provider == "cognito":
        from src.auth.providers.cognito_current_principal import resolve_current_principal

        return resolve_current_principal
    resolver = registered[0].load()
    if not callable(resolver):
        raise TypeError("Current principal resolver entry point must be callable")
    return cast(CurrentPrincipalResolver, resolver)

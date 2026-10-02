"""Alliance-owned machine reader for login-free development imports.

The development reader is the environment's own ABC Literature machine client:
the same ``ABC_LITERATURE_AUTH_MODE=cognito_client_credentials`` settings the
ABC client already uses for service calls. One dedicated read-only client per
environment holds ``abc-literature/read``; it is never a user account. ABC gives
that client every file, so the neutral runtime authorizes each import from the
requesting development user's own groups before anything is downloaded.
"""

from __future__ import annotations

from src.lib.packages.document_source_provider_models import (
    DevelopmentReaderCredentials,
    DevelopmentReaderUnavailable,
)
from agr_ai_curation_alliance.document_sources.registration import (
    _build_abc_literature_client_config,
)
from agr_ai_curation_alliance.literature.client import (
    ABCLiteratureAuthMode,
    ABCLiteratureClient,
    ABCLiteratureClientError,
    ABCLiteratureConfigError,
)


async def resolve_development_reader() -> DevelopmentReaderCredentials:
    """Issue a fresh machine reader bearer from the ABC client-credentials settings.

    The neutral runtime bounds this resolver and caches its validated result.
    """

    try:
        config = _build_abc_literature_client_config()
    except ABCLiteratureClientError:
        raise DevelopmentReaderUnavailable(
            "Development document-source reader is misconfigured."
        ) from None
    if config.auth_mode is not ABCLiteratureAuthMode.COGNITO_CLIENT_CREDENTIALS:
        raise DevelopmentReaderUnavailable(
            "Development document-source reader requires "
            "ABC_LITERATURE_AUTH_MODE=cognito_client_credentials."
        )
    try:
        async with ABCLiteratureClient(config) as client:
            token, expires_at = await client.client_credentials_token()
    except ABCLiteratureConfigError:
        raise DevelopmentReaderUnavailable(
            "Development document-source reader is misconfigured."
        ) from None
    except ABCLiteratureClientError:
        raise DevelopmentReaderUnavailable(
            "Development document-source reader credentials are unavailable."
        ) from None
    return DevelopmentReaderCredentials(token=token, expires_at=expires_at)

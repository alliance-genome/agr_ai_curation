"""Provider-neutral renewal and caching for development document imports."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Mapping

from src.config import is_dev_mode
from src.lib.packages.document_source_provider_models import (
    DevCuratorCredentials,
    DevCuratorCredentialUnavailable,
    DevelopmentCredentialResolver,
)
from src.lib.document_sources.registry import (
    get_document_source_development_credential_resolver,
)
from src.lib.openai_agents.config import (
    get_document_source_dev_curator_refresh_skew_seconds,
    get_document_source_import_enabled,
    get_document_source_import_timeout_seconds,
    get_document_source_provider,
    get_document_source_request_timeout_seconds,
)

logger = logging.getLogger(__name__)


def renewable_dev_curator_auth_required() -> bool:
    """Return whether this process/request path needs the renewable dev identity."""

    return (
        is_dev_mode()
        and get_document_source_import_enabled()
        and get_document_source_provider().strip().lower() != "local_pdf"
    )


class DevCuratorCredentialService:
    """Per-worker, lock-protected cache for validated dev curator credentials."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._cached: dict[
            str, tuple[DevelopmentCredentialResolver, DevCuratorCredentials]
        ] = {}

    def _cache_is_usable(self, credentials: DevCuratorCredentials) -> bool:
        required_lifetime = max(
            float(get_document_source_dev_curator_refresh_skew_seconds()),
            get_document_source_import_timeout_seconds(),
        )
        return credentials.expires_at > time.time() + required_lifetime

    async def get_credentials(self) -> DevCuratorCredentials:
        """Return cached credentials or renew them once for concurrent callers."""

        if not renewable_dev_curator_auth_required():
            raise DevCuratorCredentialUnavailable(
                "Development document-source curator authentication is not active."
            )
        provider_id = get_document_source_provider().strip().lower()
        resolver = get_document_source_development_credential_resolver(provider_id)
        cached_entry = self._cached.get(provider_id)
        cached = (
            cached_entry[1] if cached_entry and cached_entry[0] is resolver else None
        )
        if cached is not None and self._cache_is_usable(cached):
            return cached

        async with self._lock:
            cached_entry = self._cached.get(provider_id)
            cached = (
                cached_entry[1]
                if cached_entry and cached_entry[0] is resolver
                else None
            )
            if cached is not None and self._cache_is_usable(cached):
                return cached
            try:
                credentials = await asyncio.wait_for(
                    resolver(),
                    timeout=get_document_source_request_timeout_seconds(),
                )
            except DevCuratorCredentialUnavailable:
                raise
            except Exception as exc:
                logger.warning(
                    "Development document-source curator authentication failed",
                    extra={"failure_type": type(exc).__name__},
                )
                raise DevCuratorCredentialUnavailable(
                    "Development document-source curator credentials are unavailable."
                ) from None
            if (
                not isinstance(credentials, DevCuratorCredentials)
                or not isinstance(credentials.token, str)
                or not credentials.token.strip()
                or not isinstance(credentials.claims, Mapping)
                or not isinstance(credentials.expires_at, (int, float))
                or not math.isfinite(credentials.expires_at)
                or credentials.expires_at <= time.time()
            ):
                raise DevCuratorCredentialUnavailable(
                    "Development document-source resolver returned invalid credentials."
                )
            self._cached[provider_id] = (resolver, credentials)
            return credentials


_credential_service = DevCuratorCredentialService()


async def get_dev_curator_credentials() -> DevCuratorCredentials:
    """Return the current worker's renewable dev curator credentials."""

    return await _credential_service.get_credentials()

"""Provider-neutral renewal and caching of the development document-source reader.

In login-free development the provider is called with a dedicated machine
reader bearer supplied by the package. The bearer carries no curator identity:
imports are still authorized from the requesting (development) user's groups.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time

from src.config import is_dev_mode
from src.lib.packages.document_source_provider_models import (
    DevelopmentReaderCredentials,
    DevelopmentReaderUnavailable,
    DevelopmentReaderResolver,
)
from src.lib.document_sources.registry import (
    get_document_source_development_reader_resolver,
)
from src.lib.openai_agents.config import (
    get_document_source_dev_reader_refresh_skew_seconds,
    get_document_source_import_enabled,
    get_document_source_import_timeout_seconds,
    get_document_source_provider,
    get_document_source_request_timeout_seconds,
)

logger = logging.getLogger(__name__)


def development_reader_required() -> bool:
    """Return whether this process/request path uses the development reader."""

    return (
        is_dev_mode()
        and get_document_source_import_enabled()
        and get_document_source_provider().strip().lower() != "local_pdf"
    )


class DevelopmentReaderService:
    """Per-worker, lock-protected cache for validated development reader bearers."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._cached: dict[
            str, tuple[DevelopmentReaderResolver, DevelopmentReaderCredentials]
        ] = {}

    def _cache_is_usable(self, credentials: DevelopmentReaderCredentials) -> bool:
        required_lifetime = max(
            float(get_document_source_dev_reader_refresh_skew_seconds()),
            get_document_source_import_timeout_seconds(),
        )
        return credentials.expires_at > time.time() + required_lifetime

    async def get_credentials(self) -> DevelopmentReaderCredentials:
        """Return cached credentials or renew them once for concurrent callers."""

        if not development_reader_required():
            raise DevelopmentReaderUnavailable(
                "Development document-source reader is not active."
            )
        provider_id = get_document_source_provider().strip().lower()
        resolver = get_document_source_development_reader_resolver(provider_id)
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
            except DevelopmentReaderUnavailable:
                raise
            except Exception as exc:
                logger.warning(
                    "Development document-source reader authentication failed",
                    extra={"failure_type": type(exc).__name__},
                )
                raise DevelopmentReaderUnavailable(
                    "Development document-source reader credentials are unavailable."
                ) from None
            if (
                not isinstance(credentials, DevelopmentReaderCredentials)
                or not isinstance(credentials.token, str)
                or not credentials.token.strip()
                or not isinstance(credentials.expires_at, (int, float))
                or not math.isfinite(credentials.expires_at)
                or credentials.expires_at <= time.time()
            ):
                raise DevelopmentReaderUnavailable(
                    "Development document-source resolver returned invalid credentials."
                )
            self._cached[provider_id] = (resolver, credentials)
            return credentials


_credential_service = DevelopmentReaderService()


async def get_development_reader_credentials() -> DevelopmentReaderCredentials:
    """Return the current worker's renewable development reader bearer."""

    return await _credential_service.get_credentials()

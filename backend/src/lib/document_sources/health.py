"""Document-source provider health/readiness helpers."""

from __future__ import annotations

import logging

from src.lib.document_sources.models import (
    DocumentSourceConfigError,
    DocumentSourceHealth,
)
from src.lib.document_sources.registry import (
    LOCAL_PDF_PROVIDER_ID,
    get_configured_document_source_provider,
)
from src.lib.openai_agents.config import (
    get_abc_literature_import_enabled,
    get_document_source_provider,
)

logger = logging.getLogger(__name__)

_DISABLED_MESSAGE = "Document-source import disabled; local PDF upload remains active"
_LOCAL_PDF_MESSAGE = "Using local PDF upload flow"
_CONFIG_ERROR_MESSAGE = "Document-source provider misconfigured"
_READY_MESSAGE = "Document-source provider ready"
_UNAVAILABLE_MESSAGE = "Document-source provider unavailable"


async def check_configured_document_source_health() -> DocumentSourceHealth:
    """Return sanitized health for the configured document source.

    External provider checks are strict only when ABC Literature import is
    enabled. The default local PDF flow remains handled by the existing upload
    stack and does not require a provider health check.
    """

    provider_id = get_document_source_provider().strip().lower()
    import_enabled = get_abc_literature_import_enabled()

    if not import_enabled:
        return DocumentSourceHealth(
            provider=provider_id,
            ok=True,
            message=_DISABLED_MESSAGE,
            metadata={"enabled": False},
        )

    if provider_id == LOCAL_PDF_PROVIDER_ID:
        return DocumentSourceHealth(
            provider=provider_id,
            ok=True,
            message=_LOCAL_PDF_MESSAGE,
            metadata={"enabled": True},
        )

    try:
        provider = get_configured_document_source_provider(provider_id)
    except DocumentSourceConfigError as exc:
        logger.warning("Document-source provider configuration failed: %s", exc)
        return DocumentSourceHealth(
            provider=provider_id,
            ok=False,
            message=_CONFIG_ERROR_MESSAGE,
            metadata={"enabled": True, "reason": "configuration"},
        )

    async with provider:
        health = await provider.health()
    if not health.ok:
        logger.warning(
            "Document-source provider health check failed for %s: %s",
            health.provider,
            health.message,
        )

    return DocumentSourceHealth(
        provider=health.provider,
        ok=health.ok,
        message=_READY_MESSAGE if health.ok else _UNAVAILABLE_MESSAGE,
        metadata={**health.metadata, "enabled": True},
    )

"""Register Alliance document-source providers with the neutral runtime."""

from __future__ import annotations

import logging
import os

from src.lib.packages import (
    DocumentSourceProviderRegistration,
    DocumentSourceProviderPresentation,
)

from agr_ai_curation_alliance.document_sources import ABC_LITERATURE_PROVIDER_ID


logger = logging.getLogger(__name__)


def _document_source_request_timeout_seconds() -> float:
    """Read the shared operational timeout without importing backend config."""

    raw_timeout = os.getenv("DOCUMENT_SOURCE_REQUEST_TIMEOUT_SECONDS")
    if raw_timeout is None:
        return 10.0
    try:
        timeout_seconds = float(raw_timeout)
    except ValueError:
        logger.warning(
            "Invalid float value for DOCUMENT_SOURCE_REQUEST_TIMEOUT_SECONDS: %s, "
            "using default 10.0",
            raw_timeout,
        )
        timeout_seconds = 10.0
    return max(0.1, timeout_seconds)


def _build_abc_literature_client_config():
    """Build ABC client configuration entirely from Alliance package settings."""

    from agr_ai_curation_alliance.literature.client import (
        ABCLiteratureAuthMode,
        ABCLiteratureClientConfig,
        ABCLiteratureConfigError,
    )

    base_url = os.getenv("ABC_LITERATURE_API_BASE_URL", "").strip()
    if not base_url:
        raise ABCLiteratureConfigError("ABC_LITERATURE_API_BASE_URL is required")

    raw_auth_mode = os.getenv("ABC_LITERATURE_AUTH_MODE", "none").strip().lower()
    try:
        auth_mode = ABCLiteratureAuthMode(raw_auth_mode)
    except ValueError as exc:
        allowed = ", ".join(mode.value for mode in ABCLiteratureAuthMode)
        raise ABCLiteratureConfigError(
            f"Unsupported ABC_LITERATURE_AUTH_MODE {raw_auth_mode!r}; "
            f"expected one of {allowed}"
        ) from exc

    return ABCLiteratureClientConfig(
        base_url=base_url,
        auth_mode=auth_mode,
        timeout_seconds=_document_source_request_timeout_seconds(),
        bearer_token=os.getenv("ABC_LITERATURE_BEARER_TOKEN"),
        cognito_token_url=os.getenv("ABC_LITERATURE_COGNITO_TOKEN_URL"),
        cognito_client_id=os.getenv("ABC_LITERATURE_COGNITO_CLIENT_ID"),
        cognito_client_secret=os.getenv("ABC_LITERATURE_COGNITO_CLIENT_SECRET"),
        cognito_scope=os.getenv("ABC_LITERATURE_COGNITO_SCOPE"),
    )


def _create_abc_literature_provider():
    from src.lib.document_sources.models import DocumentSourceConfigError
    from agr_ai_curation_alliance.literature.client import (
        ABCLiteratureClient,
        ABCLiteratureConfigError,
    )

    from agr_ai_curation_alliance.document_sources.abc_literature import (
        ABCLiteratureDocumentSourceProvider,
    )

    try:
        client = ABCLiteratureClient(_build_abc_literature_client_config())
        return ABCLiteratureDocumentSourceProvider(client)
    except ABCLiteratureConfigError as exc:
        raise DocumentSourceConfigError(str(exc)) from exc


def _resolve_abc_literature_development_token() -> str | None:
    if os.getenv("ABC_LITERATURE_AUTH_MODE", "none").strip().lower() != "static_bearer":
        return None
    token = os.getenv("ABC_LITERATURE_BEARER_TOKEN", "").strip()
    return token or None


def get_document_source_provider_registrations(
) -> tuple[DocumentSourceProviderRegistration, ...]:
    """Return Alliance-owned provider registrations without invoking callbacks."""

    return (
        DocumentSourceProviderRegistration(
            provider_id=ABC_LITERATURE_PROVIDER_ID,
            factory=_create_abc_literature_provider,
            development_token_resolver=_resolve_abc_literature_development_token,
            presentation=DocumentSourceProviderPresentation(
                display_label="ABC Literature",
                identifier_help_label=(
                    "PMID, PubMed ID, AGRKB, or ABC identifiers; comma or newline "
                    "separated."
                ),
                identifier_examples=(
                    "PMID:23970418",
                    "PubMed ID 23970418",
                    "AGRKB:101000000055784",
                ),
                reference_label_priority=(
                    "external_ids.fbrf",
                    "reference_curie",
                    "reference_id",
                    "external_ids.pmid",
                    "external_ids.pmcid",
                    "external_ids.doi",
                    "source_md5",
                ),
            ),
            capabilities={
                "identifier_import": True,
                "checksum_lookup": True,
                "conversion_requests": True,
                "provider_pdf_proxy": True,
            },
        ),
    )

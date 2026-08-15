"""Provider-neutral document-source registration and resolution."""

from __future__ import annotations

from typing import Any

from src.lib.document_sources.models import (
    DocumentSourceConfigError,
    DocumentSourceProvider,
)
from src.lib.openai_agents.config import get_document_source_provider
from src.lib.packages.document_source_provider_loader import (
    LoadedDocumentSourceProviderRegistration,
    load_document_source_provider_catalog,
)


LOCAL_PDF_PROVIDER_ID = "local_pdf"


def _registered_provider(
    provider_id: str,
) -> LoadedDocumentSourceProviderRegistration | None:
    return load_document_source_provider_catalog().get(provider_id)


def get_document_source_provider_metadata(provider_id: str) -> dict[str, Any] | None:
    """Return non-secret presentation metadata owned by a registered provider."""

    registered = _registered_provider(provider_id.strip().lower())
    if registered is None:
        return None
    return registered.registration.presentation.as_public_dict()


def _resolve_provider_id(provider_id: str | None) -> str:
    selected_provider_id = (provider_id or get_document_source_provider()).strip().lower()
    if selected_provider_id == LOCAL_PDF_PROVIDER_ID:
        return selected_provider_id
    if _registered_provider(selected_provider_id) is None:
        registered_providers = sorted(
            load_document_source_provider_catalog().registrations,
            key=lambda item: item.registration.provider_id,
        )
        registered_detail = (
            "; ".join(
                f"{item.registration.provider_id} from {item.source.describe()}"
                for item in registered_providers
            )
            if registered_providers
            else "none"
        )
        raise DocumentSourceConfigError(
            f"Unsupported DOCUMENT_SOURCE_PROVIDER: {selected_provider_id}; "
            f"registered external providers: {registered_detail}"
        )
    return selected_provider_id


def get_configured_document_source_dev_mode_static_curator_token(
    provider_id: str | None = None,
) -> str | None:
    """Resolve configured dev-auth token state without constructing a provider."""

    selected_provider_id = _resolve_provider_id(provider_id)
    if selected_provider_id == LOCAL_PDF_PROVIDER_ID:
        return None

    loaded = _registered_provider(selected_provider_id)
    if loaded is None:  # Defensive: _resolve_provider_id already validates this.
        raise DocumentSourceConfigError(
            f"Document-source provider registration disappeared: {selected_provider_id}"
        )
    resolver = loaded.registration.development_token_resolver
    if resolver is None:
        return None
    token = resolver()
    if token is None:
        return None
    if not isinstance(token, str):
        raise DocumentSourceConfigError(
            f"Development token resolver for '{selected_provider_id}' from "
            f"{loaded.source.describe()} returned a non-string value"
        )
    return token.strip() or None


def get_configured_document_source_provider(
    provider_id: str | None = None,
) -> DocumentSourceProvider:
    """Create the configured external document-source provider.

    ``local_pdf`` remains handled by the existing upload/extraction flow and is
    not modeled as an external provider.
    """

    selected_provider_id = _resolve_provider_id(provider_id)
    if selected_provider_id == LOCAL_PDF_PROVIDER_ID:
        raise DocumentSourceConfigError(
            "local_pdf is handled by the existing upload flow, not an external "
            "document-source provider"
        )

    loaded = _registered_provider(selected_provider_id)
    if loaded is None:  # Defensive: _resolve_provider_id already validates this.
        raise DocumentSourceConfigError(
            f"Document-source provider registration disappeared: {selected_provider_id}"
        )
    try:
        provider = loaded.registration.factory()
    except DocumentSourceConfigError as exc:
        raise DocumentSourceConfigError(
            f"Document-source provider configuration failed for "
            f"{loaded.source.describe()}: {exc}"
        ) from exc
    actual_provider_id = str(getattr(provider, "provider_id", "")).strip().lower()
    if actual_provider_id != selected_provider_id:
        raise DocumentSourceConfigError(
            f"Factory for document-source provider '{selected_provider_id}' returned "
            f"provider_id {actual_provider_id or '<missing>'!r}; source: "
            f"{loaded.source.describe()}"
        )
    return provider

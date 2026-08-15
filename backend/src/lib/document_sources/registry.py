"""Provider-neutral document-source provider resolution."""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING, Any

from src.lib.document_sources.models import (
    DocumentSourceConfigError,
    DocumentSourceProvider,
)
from src.lib.openai_agents.config import get_document_source_provider

if TYPE_CHECKING:
    from src.lib.packages.document_source_provider_registry import (
        DocumentSourceProviderRegistry,
        RegisteredDocumentSourceProvider,
    )


LOCAL_PDF_PROVIDER_ID = "local_pdf"


@lru_cache(maxsize=1)
def get_document_source_provider_registry() -> DocumentSourceProviderRegistry:
    """Return the validated registry loaded from installed package exports."""

    from src.lib.packages.document_source_provider_registry import (
        load_document_source_provider_registry,
    )

    return load_document_source_provider_registry()


def get_document_source_provider_metadata(provider_id: str) -> dict[str, Any] | None:
    """Return registered non-secret presentation metadata for one provider."""

    registered = get_document_source_provider_registry().get(provider_id)
    if registered is None or registered.registration.presentation is None:
        return None

    presentation = registered.registration.presentation
    return {
        "display_label": presentation.display_label,
        "reference_label_priority": list(presentation.reference_label_priority),
    }


def get_document_source_provider_capabilities(provider_id: str) -> dict[str, bool] | None:
    """Return a copy of registered non-secret capability metadata."""

    registered = get_document_source_provider_registry().get(provider_id)
    if registered is None:
        return None
    return dict(registered.registration.capabilities)


def validate_configured_document_source_provider(
    registry: DocumentSourceProviderRegistry,
    provider_id: str | None = None,
) -> None:
    """Validate that the configured mode is built in or package registered."""

    selected_provider_id = _configured_provider_id(provider_id)
    if selected_provider_id == LOCAL_PDF_PROVIDER_ID:
        return
    if registry.get(selected_provider_id) is None:
        raise _unknown_provider_error(selected_provider_id, registry)


def get_configured_document_source_dev_mode_static_curator_token(
    provider_id: str | None = None,
) -> str | None:
    """Resolve optional development auth through the selected registration."""

    selected_provider_id = _configured_provider_id(provider_id)
    if selected_provider_id == LOCAL_PDF_PROVIDER_ID:
        return None

    registered = _resolve_external_provider(selected_provider_id)
    resolver = registered.registration.development_token_resolver
    if resolver is None:
        return None
    try:
        token = resolver()
    except Exception as exc:
        raise DocumentSourceConfigError(
            "Document-source development-token resolver failed for "
            f"{registered.source.describe(selected_provider_id)} "
            f"({type(exc).__name__})"
        ) from exc
    if token is not None and not isinstance(token, str):
        raise DocumentSourceConfigError(
            "Document-source development-token resolver returned an invalid value for "
            f"{registered.source.describe(selected_provider_id)}"
        )
    return token


def get_configured_document_source_provider(
    provider_id: str | None = None,
) -> DocumentSourceProvider:
    """Construct the configured package-owned external provider."""

    selected_provider_id = _configured_provider_id(provider_id)
    if selected_provider_id == LOCAL_PDF_PROVIDER_ID:
        raise DocumentSourceConfigError(
            "local_pdf is handled by the existing upload flow, not an external "
            "document-source provider"
        )

    registered = _resolve_external_provider(selected_provider_id)
    try:
        provider = registered.registration.factory()
    except DocumentSourceConfigError:
        raise
    except Exception as exc:
        raise DocumentSourceConfigError(
            "Document-source provider factory failed for "
            f"{registered.source.describe(selected_provider_id)} "
            f"({type(exc).__name__})"
        ) from exc

    actual_provider_id = getattr(provider, "provider_id", None)
    if actual_provider_id != selected_provider_id:
        raise DocumentSourceConfigError(
            "Document-source provider factory returned a mismatched provider_id; "
            f"expected '{selected_provider_id}' for "
            f"{registered.source.describe(selected_provider_id)}"
        )
    return provider


def _configured_provider_id(provider_id: str | None) -> str:
    selected_provider_id = provider_id or get_document_source_provider()
    normalized = selected_provider_id.strip().lower()
    if not normalized:
        raise DocumentSourceConfigError("DOCUMENT_SOURCE_PROVIDER must not be empty")
    return normalized


def _resolve_external_provider(
    provider_id: str,
) -> RegisteredDocumentSourceProvider:
    registry = get_document_source_provider_registry()
    registered = registry.get(provider_id)
    if registered is None:
        raise _unknown_provider_error(provider_id, registry)
    return registered


def _unknown_provider_error(
    provider_id: str,
    registry: DocumentSourceProviderRegistry,
) -> DocumentSourceConfigError:
    registered_ids = ", ".join(sorted(registry.providers_by_id)) or "none"
    return DocumentSourceConfigError(
        f"Unsupported DOCUMENT_SOURCE_PROVIDER: {provider_id}. "
        f"Registered external providers: {registered_ids}; "
        f"built-in modes: {LOCAL_PDF_PROVIDER_ID}"
    )

"""Document source provider registry."""

from __future__ import annotations

from src.lib.document_sources.models import (
    DocumentSourceConfigError,
    DocumentSourceProvider,
)
from src.lib.literature.client import ABCLiteratureConfigError
from src.lib.openai_agents.config import get_document_source_provider


LOCAL_PDF_PROVIDER_ID = "local_pdf"
ABC_LITERATURE_PROVIDER_ID = "abc_literature"


def _resolve_provider_id(provider_id: str | None) -> str:
    selected_provider_id = (
        provider_id or get_document_source_provider()
    ).strip().lower()
    if selected_provider_id not in {
        ABC_LITERATURE_PROVIDER_ID,
        LOCAL_PDF_PROVIDER_ID,
    }:
        raise DocumentSourceConfigError(
            f"Unsupported DOCUMENT_SOURCE_PROVIDER: {selected_provider_id}"
        )
    return selected_provider_id


def get_configured_document_source_dev_mode_static_curator_token(
    provider_id: str | None = None,
) -> str | None:
    """Read configured dev-auth token state without constructing a provider."""

    selected_provider_id = _resolve_provider_id(provider_id)
    if selected_provider_id == ABC_LITERATURE_PROVIDER_ID:
        from src.lib.document_sources.providers.abc_literature import (
            get_dev_mode_static_curator_token,
        )

        return get_dev_mode_static_curator_token()

    return None


def get_configured_document_source_provider(
    provider_id: str | None = None,
) -> DocumentSourceProvider:
    """Create the configured external document-source provider.

    ``local_pdf`` remains handled by the existing upload/extraction flow and is
    not modeled as an external provider yet.
    """

    selected_provider_id = _resolve_provider_id(provider_id)
    if selected_provider_id == ABC_LITERATURE_PROVIDER_ID:
        from src.lib.document_sources.providers.abc_literature import (
            ABCLiteratureDocumentSourceProvider,
        )

        try:
            return ABCLiteratureDocumentSourceProvider.from_env()
        except ABCLiteratureConfigError as exc:
            raise DocumentSourceConfigError(str(exc)) from exc

    raise DocumentSourceConfigError(
        "local_pdf is handled by the existing upload flow, not an external "
        "document-source provider"
    )

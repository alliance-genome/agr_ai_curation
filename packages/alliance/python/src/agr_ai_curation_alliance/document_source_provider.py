# pyright: reportMissingImports=false
"""Alliance document-source registration exported by the package manifest."""

from src.lib.document_sources.models import DocumentSourceConfigError
from src.lib.document_sources.registration import (
    DocumentSourceProviderPresentation,
    DocumentSourceProviderRegistration,
)
from agr_ai_curation_alliance.document_sources.abc_literature import (
    ABCLiteratureDocumentSourceProvider,
    get_dev_mode_static_curator_token,
)
from agr_ai_curation_alliance.literature.client import ABCLiteratureConfigError


def _create_abc_literature_provider() -> ABCLiteratureDocumentSourceProvider:
    try:
        return ABCLiteratureDocumentSourceProvider.from_env()
    except ABCLiteratureConfigError as exc:
        raise DocumentSourceConfigError(str(exc)) from exc


DOCUMENT_SOURCE_PROVIDER_REGISTRATION = DocumentSourceProviderRegistration(
    provider_id="abc_literature",
    factory=_create_abc_literature_provider,
    development_token_resolver=get_dev_mode_static_curator_token,
    presentation=DocumentSourceProviderPresentation(
        display_label="ABC Literature",
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
        "checksum_lookup": True,
        "external_import": True,
    },
)

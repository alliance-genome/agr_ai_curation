"""Synthetic provider registration used by project-neutral registry tests."""

from typing import cast

from src.lib.document_sources.models import DocumentSourceProvider
from src.lib.document_sources.registration import (
    DocumentSourceProviderPresentation,
    DocumentSourceProviderRegistration,
)


class ExampleLiteratureProvider:
    provider_id = "example_literature"


def _create_example_provider() -> DocumentSourceProvider:
    return cast(DocumentSourceProvider, ExampleLiteratureProvider())


DOCUMENT_SOURCE_PROVIDER_REGISTRATION = DocumentSourceProviderRegistration(
    provider_id="example_literature",
    factory=_create_example_provider,
    presentation=DocumentSourceProviderPresentation(
        display_label="Example Literature",
        reference_label_priority=("reference_id",),
    ),
    capabilities={
        "checksum_lookup": False,
        "external_import": True,
    },
)

"""Synthetic non-Alliance document-source provider package fixture."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, cast

from src.lib.packages import (
    DocumentSourceProviderPresentation,
    DocumentSourceProviderRegistration,
)

if TYPE_CHECKING:
    from src.lib.document_sources.models import DocumentSourceProvider


CALLBACK_CALLS = {"factory": 0, "development_token_resolver": 0}


class ExampleLiteratureProvider:
    provider_id = "example_literature"


def _create_provider() -> ExampleLiteratureProvider:
    CALLBACK_CALLS["factory"] += 1
    return ExampleLiteratureProvider()


def _resolve_development_token() -> str | None:
    CALLBACK_CALLS["development_token_resolver"] += 1
    return "fixture-development-token"


def get_document_source_provider_registrations(
) -> tuple[DocumentSourceProviderRegistration, ...]:
    return (
        DocumentSourceProviderRegistration(
            provider_id="example_literature",
            factory=cast("Callable[[], DocumentSourceProvider]", _create_provider),
            development_token_resolver=_resolve_development_token,
            presentation=DocumentSourceProviderPresentation(
                display_label="Example Literature",
                reference_label_priority=("reference_curie", "reference_id"),
            ),
            capabilities={
                "identifier_import": True,
                "checksum_lookup": False,
                "conversion_requests": False,
            },
        ),
    )

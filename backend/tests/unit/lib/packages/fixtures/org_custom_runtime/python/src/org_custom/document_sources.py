"""Synthetic non-Alliance document-source provider package fixture."""

from __future__ import annotations

import time

from src.lib.packages.document_source_provider_models import DevCuratorCredentials

from typing import TYPE_CHECKING, Callable, cast

from src.lib.packages import (
    DocumentSourceProviderPresentation,
    DocumentSourceProviderRegistration,
)

if TYPE_CHECKING:
    from src.lib.document_sources.models import DocumentSourceProvider


CALLBACK_CALLS = {"factory": 0, "development_credential_resolver": 0}


class ExampleLiteratureProvider:
    provider_id = "example_literature"


def _create_provider() -> ExampleLiteratureProvider:
    CALLBACK_CALLS["factory"] += 1
    return ExampleLiteratureProvider()


async def _resolve_development_credentials() -> DevCuratorCredentials:
    CALLBACK_CALLS["development_credential_resolver"] += 1
    return DevCuratorCredentials(
        token="fixture-development-token",
        claims={"sub": "custom-curator", "groups": ["custom-staff"]},
        expires_at=time.time() + 3600,
    )


def get_document_source_provider_registrations(
) -> tuple[DocumentSourceProviderRegistration, ...]:
    return (
        DocumentSourceProviderRegistration(
            provider_id="example_literature",
            factory=cast("Callable[[], DocumentSourceProvider]", _create_provider),
            development_credential_resolver=_resolve_development_credentials,
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

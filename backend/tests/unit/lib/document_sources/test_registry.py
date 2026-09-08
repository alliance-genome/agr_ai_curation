"""Tests for provider-neutral package registration resolution errors."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Callable, cast

import pytest

from src.lib.packages.document_source_provider_models import (
    DevelopmentCredentialResolver,
    DevCuratorCredentialUnavailable,
)
from src.lib.document_sources import registry as document_source_registry
from src.lib.document_sources.models import (
    DocumentSourceConfigError,
    DocumentSourceProvider,
)
from src.lib.packages import (
    DocumentSourceProviderCatalog,
    DocumentSourceProviderPresentation,
    DocumentSourceProviderRegistration,
    DocumentSourceProviderSource,
    LoadedDocumentSourceProviderRegistration,
)


def _catalog(
    tmp_path,
    *,
    factory: Callable[[], DocumentSourceProvider],
    development_credential_resolver: DevelopmentCredentialResolver | None = None,
) -> DocumentSourceProviderCatalog:
    source = DocumentSourceProviderSource(
        package_id="org.example",
        manifest_path=tmp_path / "org.example" / "package.yaml",
        export_name="example_source",
        module_path=tmp_path / "org.example" / "document_sources.py",
    )
    registration = DocumentSourceProviderRegistration(
        provider_id="example_source",
        factory=factory,
        development_credential_resolver=development_credential_resolver,
        presentation=DocumentSourceProviderPresentation(display_label="Example Source"),
        capabilities={"identifier_import": True},
    )
    return DocumentSourceProviderCatalog(
        registrations=(
            LoadedDocumentSourceProviderRegistration(
                registration=registration,
                source=source,
            ),
        )
    )


def _set_catalog(monkeypatch, catalog: DocumentSourceProviderCatalog) -> None:
    monkeypatch.setattr(
        document_source_registry,
        "load_document_source_provider_catalog",
        lambda: catalog,
    )


def _unused_factory() -> DocumentSourceProvider:
    raise AssertionError("factory must not run")


def test_provider_presentation_exposes_paired_identifier_guidance() -> None:
    presentation = DocumentSourceProviderPresentation(
        display_label="Example Source",
        identifier_help_label="Use example identifiers.",
        identifier_examples=("SOURCE:1", "SOURCE:2"),
    )

    assert presentation.as_public_dict() == {
        "display_label": "Example Source",
        "reference_label_priority": [],
        "identifier_help_label": "Use example identifiers.",
        "identifier_examples": ["SOURCE:1", "SOURCE:2"],
    }


def test_provider_presentation_rejects_partial_identifier_guidance() -> None:
    with pytest.raises(ValueError, match="must be configured together"):
        DocumentSourceProviderPresentation(
            display_label="Example Source",
            identifier_help_label="Use example identifiers.",
        )


def test_unknown_provider_lists_registered_package_export_provenance(
    monkeypatch,
    tmp_path,
) -> None:
    catalog = _catalog(tmp_path, factory=_unused_factory)
    _set_catalog(monkeypatch, catalog)

    with pytest.raises(DocumentSourceConfigError) as exc_info:
        document_source_registry.get_configured_document_source_provider("missing")

    message = str(exc_info.value)
    assert "Unsupported DOCUMENT_SOURCE_PROVIDER: missing" in message
    assert "example_source from package 'org.example'" in message
    assert str(tmp_path / "org.example" / "package.yaml") in message
    assert "export 'example_source'" in message
    assert str(tmp_path / "org.example" / "document_sources.py") in message


@pytest.mark.parametrize("provider_id", ["example_source", "missing", "local_pdf"])
@pytest.mark.asyncio
async def test_missing_development_resolver_fails_closed(
    monkeypatch, tmp_path, provider_id
):
    from src.lib.document_sources import access, dev_curator_auth

    _set_catalog(monkeypatch, _catalog(tmp_path, factory=_unused_factory))
    with pytest.raises(
        DevCuratorCredentialUnavailable, match="resolver is unavailable"
    ):
        document_source_registry.get_document_source_development_credential_resolver(
            provider_id
        )
    monkeypatch.setattr(
        dev_curator_auth, "renewable_dev_curator_auth_required", lambda: True
    )
    monkeypatch.setattr(access, "renewable_dev_curator_auth_required", lambda: True)
    monkeypatch.setattr(
        dev_curator_auth, "get_document_source_provider", lambda: provider_id
    )
    monkeypatch.setattr(
        dev_curator_auth,
        "_credential_service",
        dev_curator_auth.DevCuratorCredentialService(),
    )
    with pytest.raises(
        DevCuratorCredentialUnavailable, match="resolver is unavailable"
    ):
        await access.build_document_source_request_context(
            request=None, user_claims={"groups": ["staff"]}
        )


def test_factory_provider_id_mismatch_includes_registration_provenance(
    monkeypatch,
    tmp_path,
) -> None:
    factory = cast(
        Callable[[], DocumentSourceProvider],
        lambda: SimpleNamespace(provider_id="wrong_source"),
    )
    catalog = _catalog(tmp_path, factory=factory)
    _set_catalog(monkeypatch, catalog)

    with pytest.raises(DocumentSourceConfigError) as exc_info:
        document_source_registry.get_configured_document_source_provider(
            "example_source"
        )

    message = str(exc_info.value)
    assert "returned provider_id 'wrong_source'" in message
    assert "package 'org.example'" in message
    assert "export 'example_source'" in message


def test_factory_configuration_error_includes_registration_provenance(
    monkeypatch,
    tmp_path,
) -> None:
    def fail_factory() -> DocumentSourceProvider:
        raise DocumentSourceConfigError("EXAMPLE_SOURCE_URL is required")

    catalog = _catalog(tmp_path, factory=fail_factory)
    _set_catalog(monkeypatch, catalog)

    with pytest.raises(DocumentSourceConfigError) as exc_info:
        document_source_registry.get_configured_document_source_provider(
            "example_source"
        )

    message = str(exc_info.value)
    assert "EXAMPLE_SOURCE_URL is required" in message
    assert "package 'org.example'" in message
    assert "export 'example_source'" in message

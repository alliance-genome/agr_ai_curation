"""Tests for provider-neutral package registration resolution errors."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Callable, cast

import pytest

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
    development_token_resolver: Callable[[], str | None] | None = None,
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
        development_token_resolver=development_token_resolver,
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


def test_invalid_development_token_result_includes_registration_provenance(
    monkeypatch,
    tmp_path,
) -> None:
    invalid_resolver = cast(Callable[[], str | None], lambda: 123)
    catalog = _catalog(
        tmp_path,
        factory=_unused_factory,
        development_token_resolver=invalid_resolver,
    )
    _set_catalog(monkeypatch, catalog)

    with pytest.raises(DocumentSourceConfigError) as exc_info:
        document_source_registry.get_configured_document_source_dev_mode_static_curator_token(
            "example_source"
        )

    message = str(exc_info.value)
    assert "returned a non-string value" in message
    assert "package 'org.example'" in message
    assert "export 'example_source'" in message


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
        document_source_registry.get_configured_document_source_provider("example_source")

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
        document_source_registry.get_configured_document_source_provider("example_source")

    message = str(exc_info.value)
    assert "EXAMPLE_SOURCE_URL is required" in message
    assert "package 'org.example'" in message
    assert "export 'example_source'" in message

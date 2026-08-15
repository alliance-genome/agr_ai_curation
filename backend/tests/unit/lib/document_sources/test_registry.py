"""Package-driven document-source provider registry contracts."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

import pytest

from src.lib.document_sources import registry as document_source_registry
from src.lib.document_sources.models import DocumentSourceConfigError
from src.lib.packages.document_source_provider_registry import (
    DocumentSourceProviderRegistryValidationError,
    load_document_source_provider_registry,
)

def _find_repo_root(start: Path) -> Path:
    for candidate in (start.resolve().parent, *start.resolve().parents):
        if (candidate / "backend").is_dir() and (candidate / "packages").is_dir():
            return candidate
    raise RuntimeError(f"Could not locate repository root from {start}")


REPO_ROOT = _find_repo_root(Path(__file__))
ORG_CUSTOM_FIXTURE = (
    REPO_ROOT
    / "backend"
    / "tests"
    / "unit"
    / "lib"
    / "packages"
    / "fixtures"
    / "org_custom_runtime"
)


def _write_provider_package(
    packages_dir: Path,
    *,
    package_id: str,
    provider_id: str,
    module_text: str,
    export_name: str | None = None,
) -> Path:
    package_dir = packages_dir / package_id
    module_path = package_dir / "python" / "src" / "provider_export.py"
    module_path.parent.mkdir(parents=True)
    module_path.write_text(module_text, encoding="utf-8")
    (package_dir / "package.yaml").write_text(
        "\n".join(
            [
                f"package_id: {package_id}",
                f"display_name: {package_id}",
                "version: 1.0.0",
                "package_api_version: 1.0.0",
                "min_runtime_version: 1.0.0",
                "max_runtime_version: 2.0.0",
                "python_package_root: python/src",
                "requirements_file: requirements/runtime.txt",
                "exports:",
                "  - kind: document_source_provider",
                f"    name: {export_name or provider_id}",
                "    path: python/src/provider_export.py",
                f"    description: Synthetic export for {provider_id}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return package_dir


def _valid_registration_module(provider_id: str) -> str:
    return f'''\
from src.lib.document_sources.registration import (
    DocumentSourceProviderPresentation,
    DocumentSourceProviderRegistration,
)

class SyntheticProvider:
    provider_id = "{provider_id}"

def create_provider():
    return SyntheticProvider()

DOCUMENT_SOURCE_PROVIDER_REGISTRATION = DocumentSourceProviderRegistration(
    provider_id="{provider_id}",
    factory=create_provider,
    presentation=DocumentSourceProviderPresentation(display_label="Synthetic"),
    capabilities={{"external_import": True}},
)
'''


def test_synthetic_non_alliance_provider_loads_and_resolves(tmp_path, monkeypatch):
    packages_dir = tmp_path / "packages"
    shutil.copytree(ORG_CUSTOM_FIXTURE, packages_dir / "org.custom")

    registry = load_document_source_provider_registry(
        packages_dir,
        runtime_version="1.5.0",
        supported_package_api_version="1.0.0",
    )

    registered = registry.get("example_literature")
    assert registered is not None
    assert registered.source.package_id == "org.custom"
    assert registered.source.export_name == "example_literature"
    assert registered.registration.presentation is not None
    assert registered.registration.presentation.display_label == "Example Literature"
    assert dict(registered.registration.capabilities) == {
        "checksum_lookup": False,
        "external_import": True,
    }

    monkeypatch.setattr(
        document_source_registry,
        "get_document_source_provider_registry",
        lambda: registry,
    )
    provider = document_source_registry.get_configured_document_source_provider(
        "example_literature"
    )
    assert provider.provider_id == "example_literature"
    assert document_source_registry.get_document_source_provider_metadata(
        "example_literature"
    ) == {
        "display_label": "Example Literature",
        "reference_label_priority": ["reference_id"],
    }


@pytest.mark.asyncio
async def test_alliance_package_owns_abc_registration(monkeypatch):
    registry = load_document_source_provider_registry(REPO_ROOT / "packages")
    registered = registry.get("abc_literature")

    assert registered is not None
    assert registered.source.package_id == "agr.alliance"
    assert registered.source.manifest_path == (
        REPO_ROOT / "packages" / "alliance" / "package.yaml"
    )
    assert registered.source.export_name == "abc_literature"
    assert registered.registration.presentation is not None
    assert registered.registration.presentation.display_label == "ABC Literature"
    assert dict(registered.registration.capabilities) == {
        "checksum_lookup": True,
        "external_import": True,
    }

    monkeypatch.setenv("ABC_LITERATURE_API_BASE_URL", "https://literature.example/api")
    monkeypatch.setattr(
        document_source_registry,
        "get_document_source_provider_registry",
        lambda: registry,
    )
    provider = document_source_registry.get_configured_document_source_provider(
        "abc_literature"
    )
    assert provider.__class__.__module__.startswith(
        "agr_ai_curation_alliance.document_sources"
    )
    await provider.aclose()


def test_provider_factories_are_not_invoked_during_registry_enumeration(tmp_path):
    packages_dir = tmp_path / "packages"
    _write_provider_package(
        packages_dir,
        package_id="org.lazy",
        provider_id="lazy_provider",
        module_text='''\
from src.lib.document_sources.registration import DocumentSourceProviderRegistration

def fail_if_called():
    raise AssertionError("factory must stay lazy")

DOCUMENT_SOURCE_PROVIDER_REGISTRATION = DocumentSourceProviderRegistration(
    provider_id="lazy_provider",
    factory=fail_if_called,
)
''',
    )

    registry = load_document_source_provider_registry(packages_dir)

    assert registry.get("lazy_provider") is not None


def test_missing_registration_reports_package_export_and_path(tmp_path):
    packages_dir = tmp_path / "packages"
    package_dir = _write_provider_package(
        packages_dir,
        package_id="org.missing",
        provider_id="missing_provider",
        export_name="missing_provider",
        module_text="NOT_A_REGISTRATION = object()\n",
    )

    with pytest.raises(DocumentSourceProviderRegistryValidationError) as exc_info:
        load_document_source_provider_registry(packages_dir)

    message = str(exc_info.value)
    assert "org.missing" in message
    assert str(package_dir / "package.yaml") in message
    assert "missing_provider" in message
    assert "DOCUMENT_SOURCE_PROVIDER_REGISTRATION" in message


def test_malformed_registration_does_not_render_record_contents(tmp_path):
    packages_dir = tmp_path / "packages"
    _write_provider_package(
        packages_dir,
        package_id="org.malformed",
        provider_id="malformed_provider",
        module_text=(
            'DOCUMENT_SOURCE_PROVIDER_REGISTRATION = {"provider_id": '
            '"malformed_provider", "credential": "do-not-leak"}\n'
        ),
    )

    with pytest.raises(DocumentSourceProviderRegistryValidationError) as exc_info:
        load_document_source_provider_registry(packages_dir)

    message = str(exc_info.value)
    assert "org.malformed" in message
    assert "malformed_provider" in message
    assert "do-not-leak" not in message


def test_provider_import_error_is_sanitized_and_has_provenance(tmp_path):
    packages_dir = tmp_path / "packages"
    _write_provider_package(
        packages_dir,
        package_id="org.broken",
        provider_id="broken_provider",
        module_text='raise RuntimeError("do-not-leak")\n',
        export_name="broken_provider",
    )

    with pytest.raises(DocumentSourceProviderRegistryValidationError) as exc_info:
        load_document_source_provider_registry(packages_dir)

    message = str(exc_info.value)
    assert "org.broken" in message
    assert "broken_provider" in message
    assert "RuntimeError" in message
    assert "do-not-leak" not in message
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None
    assert "do-not-leak" not in "".join(
        traceback.format_exception(exc_info.value)
    )


def test_provider_factory_error_is_sanitized_without_chained_secrets(
    tmp_path,
    monkeypatch,
):
    packages_dir = tmp_path / "packages"
    _write_provider_package(
        packages_dir,
        package_id="org.factory_failure",
        provider_id="factory_failure",
        module_text='''\
from src.lib.document_sources.models import DocumentSourceConfigError
from src.lib.document_sources.registration import DocumentSourceProviderRegistration

def create_provider():
    secret = "factory-do-not-leak"
    raise DocumentSourceConfigError(secret)

DOCUMENT_SOURCE_PROVIDER_REGISTRATION = DocumentSourceProviderRegistration(
    provider_id="factory_failure",
    factory=create_provider,
)
''',
    )
    registry = load_document_source_provider_registry(packages_dir)
    monkeypatch.setattr(
        document_source_registry,
        "get_document_source_provider_registry",
        lambda: registry,
    )

    with pytest.raises(DocumentSourceConfigError) as exc_info:
        document_source_registry.get_configured_document_source_provider(
            "factory_failure"
        )

    message = str(exc_info.value)
    assert "org.factory_failure" in message
    assert "factory_failure" in message
    assert "factory-do-not-leak" not in message
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None
    assert "factory-do-not-leak" not in "".join(
        traceback.format_exception(exc_info.value)
    )


def test_development_token_resolver_error_is_sanitized_without_chained_secrets(
    tmp_path,
    monkeypatch,
):
    packages_dir = tmp_path / "packages"
    _write_provider_package(
        packages_dir,
        package_id="org.resolver_failure",
        provider_id="resolver_failure",
        module_text='''\
from src.lib.document_sources.registration import DocumentSourceProviderRegistration

class SyntheticProvider:
    provider_id = "resolver_failure"

def create_provider():
    return SyntheticProvider()

def resolve_development_token():
    secret = "resolver-do-not-leak"
    raise RuntimeError(secret)

DOCUMENT_SOURCE_PROVIDER_REGISTRATION = DocumentSourceProviderRegistration(
    provider_id="resolver_failure",
    factory=create_provider,
    development_token_resolver=resolve_development_token,
)
''',
    )
    registry = load_document_source_provider_registry(packages_dir)
    monkeypatch.setattr(
        document_source_registry,
        "get_document_source_provider_registry",
        lambda: registry,
    )

    with pytest.raises(DocumentSourceConfigError) as exc_info:
        document_source_registry.get_configured_document_source_dev_mode_static_curator_token(
            "resolver_failure"
        )

    message = str(exc_info.value)
    assert "org.resolver_failure" in message
    assert "resolver_failure" in message
    assert "resolver-do-not-leak" not in message
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None
    assert "resolver-do-not-leak" not in "".join(
        traceback.format_exception(exc_info.value)
    )


def test_registration_provider_id_must_match_manifest_export_name(tmp_path):
    packages_dir = tmp_path / "packages"
    _write_provider_package(
        packages_dir,
        package_id="org.mismatch",
        provider_id="actual_provider",
        export_name="declared_provider",
        module_text=_valid_registration_module("actual_provider"),
    )

    with pytest.raises(DocumentSourceProviderRegistryValidationError) as exc_info:
        load_document_source_provider_registry(packages_dir)

    message = str(exc_info.value)
    assert "org.mismatch" in message
    assert "actual_provider" in message
    assert "declared_provider" in message
    assert "provider ID as its export name" in message


def test_registry_rejects_reserved_local_pdf_registration_with_provenance(tmp_path):
    packages_dir = tmp_path / "packages"
    package_dir = _write_provider_package(
        packages_dir,
        package_id="org.reserved",
        provider_id="local_pdf",
        module_text=_valid_registration_module("local_pdf"),
    )

    with pytest.raises(DocumentSourceProviderRegistryValidationError) as exc_info:
        load_document_source_provider_registry(packages_dir)

    message = str(exc_info.value)
    assert "local_pdf" in message
    assert "reserved for the built-in local upload flow" in message
    assert "org.reserved" in message
    assert str(package_dir / "package.yaml") in message
    assert "export 'local_pdf'" in message
    assert str(package_dir / "python" / "src" / "provider_export.py") in message

    registry = load_document_source_provider_registry(
        packages_dir,
        fail_on_validation_error=False,
    )
    assert registry.get("local_pdf") is None


def test_duplicate_provider_ids_report_both_package_provenances(tmp_path):
    packages_dir = tmp_path / "packages"
    for package_id in ("org.first", "org.second"):
        _write_provider_package(
            packages_dir,
            package_id=package_id,
            provider_id="shared_provider",
            module_text=_valid_registration_module("shared_provider"),
        )

    with pytest.raises(DocumentSourceProviderRegistryValidationError) as exc_info:
        load_document_source_provider_registry(packages_dir)

    message = str(exc_info.value)
    assert "shared_provider" in message
    assert "org.first" in message
    assert "export 'shared_provider'" in message
    assert "org.second" in message
    assert message.count("package.yaml") == 2


def test_unknown_provider_error_lists_registered_ids_without_factory_details(
    tmp_path,
    monkeypatch,
):
    packages_dir = tmp_path / "packages"
    _write_provider_package(
        packages_dir,
        package_id="org.example",
        provider_id="example_provider",
        module_text=_valid_registration_module("example_provider"),
    )
    registry = load_document_source_provider_registry(packages_dir)
    monkeypatch.setattr(
        document_source_registry,
        "get_document_source_provider_registry",
        lambda: registry,
    )

    with pytest.raises(DocumentSourceConfigError) as exc_info:
        document_source_registry.get_configured_document_source_provider("unknown")

    message = str(exc_info.value)
    assert "unknown" in message
    assert "example_provider" in message
    assert "local_pdf" in message


def test_core_only_registry_does_not_import_alliance_provider_modules():
    script = '''\
import shutil
import sys
import tempfile
from pathlib import Path
from src.lib.packages.document_source_provider_registry import load_document_source_provider_registry

repo_root = Path.cwd().parent
with tempfile.TemporaryDirectory() as temp_dir:
    packages_dir = Path(temp_dir) / "packages"
    shutil.copytree(repo_root / "packages" / "core", packages_dir / "core")
    registry = load_document_source_provider_registry(packages_dir)
    assert registry.providers == ()
assert not any(name.startswith("agr_ai_curation_alliance") for name in sys.modules)
'''
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "backend")

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT / "backend",
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr

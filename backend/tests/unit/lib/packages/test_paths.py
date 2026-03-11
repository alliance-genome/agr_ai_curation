"""Unit tests for runtime package path helpers."""

from pathlib import Path

import pytest

import src.config as runtime_config
from src.lib.packages import paths


@pytest.fixture(autouse=True)
def _clear_runtime_path_env(monkeypatch):
    for variable in (
        "AGR_RUNTIME_ROOT",
        "AGR_RUNTIME_CONFIG_DIR",
        "AGR_RUNTIME_PACKAGES_DIR",
        "AGR_RUNTIME_STATE_DIR",
        "AGR_RUNTIME_OVERRIDES_PATH",
        "PDF_STORAGE_PATH",
        "PDFX_JSON_STORAGE_PATH",
        "PROCESSED_JSON_STORAGE_PATH",
        "FILE_OUTPUT_STORAGE_PATH",
        "IDENTIFIER_PREFIX_STATE_DIR",
        "IDENTIFIER_PREFIX_FILE_PATH",
    ):
        monkeypatch.delenv(variable, raising=False)


def test_default_runtime_layout_does_not_depend_on_repo_checkout():
    assert paths.get_runtime_root() == Path("/runtime")
    assert paths.get_runtime_config_dir() == Path("/runtime/config")
    assert paths.get_runtime_packages_dir() == Path("/runtime/packages")
    assert paths.get_runtime_state_dir() == Path("/runtime/state")
    assert paths.get_runtime_overrides_path() == Path("/runtime/config/overrides.yaml")
    assert paths.get_pdf_storage_dir() == Path("/runtime/state/pdf_storage")
    assert paths.get_pdfx_json_storage_dir() == Path("/runtime/state/pdf_storage/pdfx_json")
    assert paths.get_processed_json_storage_dir() == Path("/runtime/state/pdf_storage/processed_json")
    assert paths.get_file_output_dir() == Path("/runtime/state/file_outputs")
    assert paths.get_identifier_prefix_state_dir() == Path("/runtime/state/identifier_prefixes")
    assert paths.get_identifier_prefix_file_path() == Path(
        "/runtime/state/identifier_prefixes/identifier_prefixes.json"
    )


def test_runtime_layout_honors_rooted_relative_overrides(monkeypatch, tmp_path):
    runtime_root = tmp_path / "runtime-host"
    monkeypatch.setenv("AGR_RUNTIME_ROOT", str(runtime_root))
    monkeypatch.setenv("AGR_RUNTIME_CONFIG_DIR", "config-live")
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", "published")
    monkeypatch.setenv("AGR_RUNTIME_STATE_DIR", "mutable")
    monkeypatch.setenv("AGR_RUNTIME_OVERRIDES_PATH", "deployment/overrides.yaml")
    monkeypatch.setenv("PDF_STORAGE_PATH", "pdfs")
    monkeypatch.setenv("PDFX_JSON_STORAGE_PATH", "raw-json")
    monkeypatch.setenv("PROCESSED_JSON_STORAGE_PATH", "processed-json")
    monkeypatch.setenv("FILE_OUTPUT_STORAGE_PATH", "exports")
    monkeypatch.setenv("IDENTIFIER_PREFIX_STATE_DIR", "prefix-state")
    monkeypatch.setenv("IDENTIFIER_PREFIX_FILE_PATH", "active/prefixes.json")

    assert paths.get_runtime_config_dir() == runtime_root / "config-live"
    assert paths.get_runtime_packages_dir() == runtime_root / "published"
    assert paths.get_runtime_package_dir("agr.base") == runtime_root / "published" / "agr.base"
    assert paths.get_runtime_state_dir() == runtime_root / "mutable"
    assert paths.get_runtime_overrides_path() == runtime_root / "config-live" / "deployment/overrides.yaml"
    assert paths.get_pdf_storage_dir() == runtime_root / "mutable" / "pdfs"
    assert paths.get_pdfx_json_storage_dir() == runtime_root / "mutable" / "pdfs/raw-json"
    assert paths.get_processed_json_storage_dir() == runtime_root / "mutable" / "pdfs/processed-json"
    assert paths.get_file_output_dir() == runtime_root / "mutable" / "exports"
    assert paths.get_identifier_prefix_state_dir() == runtime_root / "mutable" / "prefix-state"
    assert paths.get_identifier_prefix_file_path() == runtime_root / "mutable" / "prefix-state/active/prefixes.json"


def test_package_contract_file_helpers_build_expected_relative_paths(tmp_path):
    package_dir = tmp_path / "packages" / "agr.base"

    assert paths.get_package_manifest_path(package_dir) == package_dir / "package.yaml"
    assert paths.get_tool_bindings_path(package_dir) == package_dir / "tools" / "bindings.yaml"


def test_runtime_config_storage_helpers_delegate_to_package_paths(monkeypatch, tmp_path):
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("AGR_RUNTIME_ROOT", str(runtime_root))

    assert runtime_config.get_pdf_storage_path() == runtime_root / "state" / "pdf_storage"
    assert runtime_config.get_pdfx_json_storage_path() == runtime_root / "state" / "pdf_storage" / "pdfx_json"
    assert runtime_config.get_processed_json_storage_path() == runtime_root / "state" / "pdf_storage" / "processed_json"
    assert runtime_config.get_file_output_storage_path() == runtime_root / "state" / "file_outputs"


def test_relative_runtime_override_rejects_parent_directory_traversal(monkeypatch, tmp_path):
    monkeypatch.setenv("AGR_RUNTIME_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setenv("AGR_RUNTIME_STATE_DIR", "../escape")

    with pytest.raises(ValueError, match="must not traverse parent directories"):
        paths.get_runtime_state_dir()

"""Runtime path contract for modular package distribution.

The public container contract assumes a writable runtime root mounted at
``/runtime`` by default:

    /runtime
      config/
        overrides.yaml
      packages/
        <package_id>/
          package.yaml
          tools/bindings.yaml
      state/
        pdf_storage/
          pdfx_json/
          processed_json/
        file_outputs/
        identifier_prefixes/

These helpers resolve runtime locations without assuming a repository checkout
is mounted inside the container. Each path can be overridden via environment
variables when a deployment needs a different layout.
"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_RUNTIME_ROOT = Path("/runtime")
DEFAULT_CONFIG_DIRNAME = "config"
DEFAULT_PACKAGES_DIRNAME = "packages"
DEFAULT_STATE_DIRNAME = "state"
DEFAULT_OVERRIDES_FILENAME = "overrides.yaml"
DEFAULT_PACKAGE_MANIFEST_FILENAME = "package.yaml"
DEFAULT_TOOL_BINDINGS_RELATIVE_PATH = Path("tools") / "bindings.yaml"
DEFAULT_PDF_STORAGE_DIRNAME = "pdf_storage"
DEFAULT_PDFX_JSON_DIRNAME = "pdfx_json"
DEFAULT_PROCESSED_JSON_DIRNAME = "processed_json"
DEFAULT_FILE_OUTPUT_DIRNAME = "file_outputs"
DEFAULT_IDENTIFIER_PREFIX_STATE_DIRNAME = "identifier_prefixes"
DEFAULT_IDENTIFIER_PREFIX_FILENAME = "identifier_prefixes.json"


def _normalize_path(path: Path) -> Path:
    """Normalize a path without requiring it to exist."""
    return path.expanduser().resolve(strict=False)


def _resolve_optional_path(
    raw_value: str | None,
    *,
    parent: Path,
    default_name: str | Path,
) -> Path:
    """Resolve an override path relative to its owning runtime directory."""
    if raw_value is None or not raw_value.strip():
        return _normalize_path(parent / default_name)

    candidate = Path(raw_value)
    if candidate.is_absolute():
        return _normalize_path(candidate)

    return _normalize_path(parent / candidate)


def get_runtime_root() -> Path:
    """Return the root directory mounted for runtime config, packages, and state."""
    raw_value = os.getenv("AGR_RUNTIME_ROOT")
    if raw_value and raw_value.strip():
        return _normalize_path(Path(raw_value))
    return _normalize_path(DEFAULT_RUNTIME_ROOT)


def get_runtime_config_dir() -> Path:
    """Return the runtime config directory."""
    return _resolve_optional_path(
        os.getenv("AGR_RUNTIME_CONFIG_DIR"),
        parent=get_runtime_root(),
        default_name=DEFAULT_CONFIG_DIRNAME,
    )


def get_runtime_packages_dir() -> Path:
    """Return the directory that contains installed runtime packages."""
    return _resolve_optional_path(
        os.getenv("AGR_RUNTIME_PACKAGES_DIR"),
        parent=get_runtime_root(),
        default_name=DEFAULT_PACKAGES_DIRNAME,
    )


def get_runtime_package_dir(package_id: str) -> Path:
    """Return the runtime directory for a specific package ID."""
    return _normalize_path(get_runtime_packages_dir() / package_id)


def get_runtime_state_dir() -> Path:
    """Return the writable runtime state directory."""
    return _resolve_optional_path(
        os.getenv("AGR_RUNTIME_STATE_DIR"),
        parent=get_runtime_root(),
        default_name=DEFAULT_STATE_DIRNAME,
    )


def get_runtime_overrides_path() -> Path:
    """Return the deployment override file used for collision resolution."""
    return _resolve_optional_path(
        os.getenv("AGR_RUNTIME_OVERRIDES_PATH"),
        parent=get_runtime_config_dir(),
        default_name=DEFAULT_OVERRIDES_FILENAME,
    )


def get_pdf_storage_dir() -> Path:
    """Return the directory for original PDF uploads."""
    return _resolve_optional_path(
        os.getenv("PDF_STORAGE_PATH"),
        parent=get_runtime_state_dir(),
        default_name=DEFAULT_PDF_STORAGE_DIRNAME,
    )


def get_pdfx_json_storage_dir() -> Path:
    """Return the directory for raw PDF extraction JSON outputs."""
    return _resolve_optional_path(
        os.getenv("PDFX_JSON_STORAGE_PATH"),
        parent=get_pdf_storage_dir(),
        default_name=DEFAULT_PDFX_JSON_DIRNAME,
    )


def get_processed_json_storage_dir() -> Path:
    """Return the directory for processed extraction JSON outputs."""
    return _resolve_optional_path(
        os.getenv("PROCESSED_JSON_STORAGE_PATH"),
        parent=get_pdf_storage_dir(),
        default_name=DEFAULT_PROCESSED_JSON_DIRNAME,
    )


def get_file_output_dir() -> Path:
    """Return the directory for generated CSV/TSV/JSON file outputs."""
    return _resolve_optional_path(
        os.getenv("FILE_OUTPUT_STORAGE_PATH"),
        parent=get_runtime_state_dir(),
        default_name=DEFAULT_FILE_OUTPUT_DIRNAME,
    )


def get_identifier_prefix_state_dir() -> Path:
    """Return the directory reserved for deployment-managed identifier prefix state."""
    return _resolve_optional_path(
        os.getenv("IDENTIFIER_PREFIX_STATE_DIR"),
        parent=get_runtime_state_dir(),
        default_name=DEFAULT_IDENTIFIER_PREFIX_STATE_DIRNAME,
    )


def get_identifier_prefix_file_path() -> Path:
    """Return the identifier prefix JSON file path."""
    return _resolve_optional_path(
        os.getenv("IDENTIFIER_PREFIX_FILE_PATH"),
        parent=get_identifier_prefix_state_dir(),
        default_name=DEFAULT_IDENTIFIER_PREFIX_FILENAME,
    )


def get_package_manifest_path(package_dir: Path) -> Path:
    """Return the manifest file path for a package directory."""
    return _normalize_path(package_dir / DEFAULT_PACKAGE_MANIFEST_FILENAME)


def get_tool_bindings_path(package_dir: Path) -> Path:
    """Return the tool bindings file path for a package directory."""
    return _normalize_path(package_dir / DEFAULT_TOOL_BINDINGS_RELATIVE_PATH)

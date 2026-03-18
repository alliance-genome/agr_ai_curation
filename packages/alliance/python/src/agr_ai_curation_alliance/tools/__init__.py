"""Package-local exports for the AGR Alliance toolset."""

from __future__ import annotations

from importlib import import_module
from typing import Any

_LAZY_EXPORTS = {
    "agr_curation_query": (".agr_curation", "agr_curation_query"),
    "alliance_api_call": (".rest", "alliance_api_call"),
    "chebi_api_call": (".rest", "chebi_api_call"),
    "create_curation_db_sql_tool": (".sql", "create_curation_db_sql_tool"),
    "create_read_section_tool": (".documents", "create_read_section_tool"),
    "create_read_subsection_tool": (".documents", "create_read_subsection_tool"),
    "create_search_document_tool": (".documents", "create_search_document_tool"),
    "go_api_call": (".rest", "go_api_call"),
    "quickgo_api_call": (".rest", "quickgo_api_call"),
    "save_csv_file": (".file_output", "save_csv_file"),
    "save_json_file": (".file_output", "save_json_file"),
    "save_tsv_file": (".file_output", "save_tsv_file"),
}

__all__ = list(_LAZY_EXPORTS)


def __getattr__(name: str) -> Any:
    """Resolve public tool exports on first access instead of package import."""
    if name not in _LAZY_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module_name, attribute_name = _LAZY_EXPORTS[name]
    value = getattr(import_module(module_name, __name__), attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Expose lazy exports to interactive callers and star imports."""
    return sorted(set(globals()) | set(__all__))

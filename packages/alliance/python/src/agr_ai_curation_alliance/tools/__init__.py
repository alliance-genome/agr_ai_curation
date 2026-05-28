"""Package-local exports for the AGR Alliance toolset."""

from __future__ import annotations

from importlib import import_module
from typing import Any

_LAZY_EXPORTS = {
    "agr_curation_query": (".agr_curation", "agr_curation_query"),
    "agr_literature_reference_lookup": (
        ".literature_references",
        "agr_literature_reference_lookup",
    ),
    "alliance_api_call": (".rest", "alliance_api_call"),
    "chebi_api_call": (".rest", "chebi_api_call"),
    "create_attach_evidence_to_object_tool": (
        ".documents",
        "create_attach_evidence_to_object_tool",
    ),
    "create_curation_db_sql_tool": (".sql", "create_curation_db_sql_tool"),
    "create_detach_evidence_from_object_tool": (
        ".documents",
        "create_detach_evidence_from_object_tool",
    ),
    "create_discard_recorded_evidence_tool": (
        ".documents",
        "create_discard_recorded_evidence_tool",
    ),
    "create_get_recorded_evidence_tool": (
        ".documents",
        "create_get_recorded_evidence_tool",
    ),
    "create_list_recorded_evidence_tool": (
        ".documents",
        "create_list_recorded_evidence_tool",
    ),
    "create_record_evidence_tool": (".documents", "create_record_evidence_tool"),
    "create_read_chunk_tool": (".documents", "create_read_chunk_tool"),
    "create_read_section_tool": (".documents", "create_read_section_tool"),
    "create_read_subsection_tool": (".documents", "create_read_subsection_tool"),
    "create_search_document_tool": (".documents", "create_search_document_tool"),
    "create_update_recorded_evidence_metadata_tool": (
        ".documents",
        "create_update_recorded_evidence_metadata_tool",
    ),
    "get_domain_field_term_options": (
        ".agr_curation",
        "get_domain_field_term_options",
    ),
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

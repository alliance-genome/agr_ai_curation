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
    "discard_gene_expression_observation": (
        ".agr_curation",
        "discard_gene_expression_observation",
    ),
    "finalize_gene_expression_extraction": (
        ".agr_curation",
        "finalize_gene_expression_extraction",
    ),
    "find_staged_gene_expression_observations": (
        ".agr_curation",
        "find_staged_gene_expression_observations",
    ),
    "inspect_ontology_term": (".agr_curation", "inspect_ontology_term"),
    "list_staged_gene_expression_observations": (
        ".agr_curation",
        "list_staged_gene_expression_observations",
    ),
    "patch_gene_expression_observation": (
        ".agr_curation",
        "patch_gene_expression_observation",
    ),
    "resolve_domain_field_term": (".agr_curation", "resolve_domain_field_term"),
    "search_domain_field_terms": (".agr_curation", "search_domain_field_terms"),
    "stage_gene_expression_observation": (
        ".agr_curation",
        "stage_gene_expression_observation",
    ),
    "go_api_call": (".rest", "go_api_call"),
    "quickgo_api_call": (".rest", "quickgo_api_call"),
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

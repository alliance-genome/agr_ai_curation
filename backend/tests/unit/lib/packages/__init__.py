"""Unit tests for runtime package contract helpers."""

from pathlib import Path

SHIPPED_TOOLS_PACKAGE_EXPORTS = (
    "agr_curation_query",
    "agr_literature_reference_lookup",
    "alliance_api_call",
    "chebi_api_call",
    "create_attach_evidence_to_object_tool",
    "create_curation_db_sql_tool",
    "create_detach_evidence_from_object_tool",
    "create_discard_recorded_evidence_tool",
    "create_get_recorded_evidence_tool",
    "create_list_recorded_evidence_tool",
    "create_record_evidence_tool",
    "create_read_chunk_tool",
    "create_read_section_tool",
    "create_read_subsection_tool",
    "create_search_document_tool",
    "create_update_recorded_evidence_metadata_tool",
    "discard_gene_expression_observation",
    "finalize_gene_expression_extraction",
    "inspect_ontology_term",
    "list_staged_gene_expression_observations",
    "patch_gene_expression_observation",
    "resolve_domain_field_term",
    "search_domain_field_terms",
    "stage_gene_expression_observation",
    "go_api_call",
    "quickgo_api_call",
    "save_csv_file",
    "save_json_file",
    "save_tsv_file",
)


def find_repo_root(start: Path) -> Path:
    """Resolve the repository root by walking upward to a known sentinel."""
    current = start.resolve()
    if current.is_file():
        current = current.parent

    for candidate in (current, *current.parents):
        if (candidate / "docker-compose.test.yml").exists():
            return candidate
        if (candidate / "backend").is_dir() and (candidate / "packages").is_dir():
            return candidate

    raise RuntimeError(f"Could not locate repository root from {start}")

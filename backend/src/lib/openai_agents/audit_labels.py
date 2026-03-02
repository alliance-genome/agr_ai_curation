"""Shared helpers for audit-friendly tool labels."""

from typing import Dict, Optional


BUILTIN_SPECIALIST_DISPLAY_NAMES: Dict[str, str] = {
    "ask_pdf_specialist": "General PDF Extraction Agent",
    "ask_gene_specialist": "Gene Validation Agent",
    "ask_gene_extractor_specialist": "Gene Extraction Agent",
    "ask_allele_specialist": "Allele Validation Agent",
    "ask_allele_extractor_specialist": "Allele/Variant Extraction Agent",
    "ask_disease_specialist": "Disease Ontology Agent",
    "ask_disease_extractor_specialist": "Disease Extraction Agent",
    "ask_chemical_specialist": "Chemical Ontology Agent",
    "ask_chemical_extractor_specialist": "Chemical Extraction Agent",
    "ask_gene_expression_specialist": "Gene Expression Extractor",
    "ask_phenotype_extractor_specialist": "Phenotype Extraction Agent",
    "ask_gene_ontology_specialist": "Gene Ontology Agent",
    "ask_go_annotations_specialist": "GO Annotations Agent",
    "ask_orthologs_specialist": "Orthologs Agent",
    "ask_ontology_mapping_specialist": "Ontology Mapping Agent",
    "ask_chat_output_specialist": "Chat Output Agent",
    "ask_csv_formatter_specialist": "CSV File Formatter",
    "ask_tsv_formatter_specialist": "TSV File Formatter",
    "ask_json_formatter_specialist": "JSON File Formatter",
}

INTERNAL_TOOL_DISPLAY_NAMES: Dict[str, str] = {
    "search_document": "Search Document",
    "read_section": "Read Section",
    "read_subsection": "Read Subsection",
    "agr_curation_query": "AGR Curation Query",
    "sql_query": "SQL Query",
    "alliance_api_call": "Alliance API",
    "rest_api_call": "REST API",
    "quickgo_api_call": "QuickGO API",
    "go_api_call": "GO Annotations API",
    "save_csv_file": "Save CSV File",
    "save_tsv_file": "Save TSV File",
    "save_json_file": "Save JSON File",
    "export_to_file": "Export to File",
}


def _ensure_non_empty_label(tool_name: str, label: Optional[str]) -> str:
    """Return a non-empty label string for audit payloads."""
    cleaned = (label or "").strip()
    if cleaned:
        return cleaned
    return f"[Missing tool label] {tool_name}"


def resolve_tool_display_name(tool_name: str, custom_display_names: Optional[Dict[str, str]] = None) -> str:
    """Resolve the best user-facing display name for a tool call."""
    custom = custom_display_names or {}
    label = (
        custom.get(tool_name)
        or BUILTIN_SPECIALIST_DISPLAY_NAMES.get(tool_name)
        or INTERNAL_TOOL_DISPLAY_NAMES.get(tool_name)
        or tool_name
    )
    return _ensure_non_empty_label(tool_name, label)


def build_tool_start_friendly_name(tool_name: str, custom_display_names: Optional[Dict[str, str]] = None) -> str:
    """Build consistent TOOL_START friendly label."""
    is_specialist = tool_name.startswith("ask_") and tool_name.endswith("_specialist")
    if is_specialist:
        display = resolve_tool_display_name(tool_name, custom_display_names)
        return _ensure_non_empty_label(tool_name, f"Calling {display}...")
    display = resolve_tool_display_name(tool_name, custom_display_names)
    return _ensure_non_empty_label(tool_name, f"Calling {display}...")


def build_tool_complete_friendly_name(tool_name: str, custom_display_names: Optional[Dict[str, str]] = None) -> str:
    """Build consistent TOOL_COMPLETE friendly label."""
    display = resolve_tool_display_name(tool_name, custom_display_names)
    return _ensure_non_empty_label(tool_name, f"{display} complete")


def build_specialist_internal_friendly_name(
    specialist_name: Optional[str],
    tool_name: str,
    *,
    complete: bool = False,
) -> str:
    """Build friendly labels for specialist-internal tool events."""
    specialist = (specialist_name or "").strip()
    display_tool_name = resolve_tool_display_name(tool_name)
    if not specialist:
        base = _ensure_non_empty_label(tool_name, display_tool_name)
    else:
        base = f"{specialist}: {display_tool_name}"
    if complete:
        return f"{base} complete"
    return base

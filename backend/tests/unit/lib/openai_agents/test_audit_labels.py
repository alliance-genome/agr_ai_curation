"""Unit tests for shared audit label helpers."""

from src.lib.openai_agents.audit_labels import (
    resolve_tool_display_name,
    build_tool_start_friendly_name,
    build_tool_complete_friendly_name,
    build_specialist_internal_friendly_name,
)


def test_resolve_tool_display_name_uses_builtin_map():
    assert resolve_tool_display_name("ask_pdf_specialist") == "General PDF Extraction Agent"


def test_resolve_tool_display_name_uses_builtin_phenotype_map():
    assert resolve_tool_display_name("ask_phenotype_extractor_specialist") == "Phenotype Extraction Agent"


def test_resolve_tool_display_name_uses_builtin_gene_extractor_map():
    assert (
        resolve_tool_display_name("ask_gene_extractor_specialist")
        == "Gene Extraction Agent"
    )


def test_resolve_tool_display_name_uses_builtin_allele_extractor_map():
    assert (
        resolve_tool_display_name("ask_allele_extractor_specialist")
        == "Allele/Variant Extraction Agent"
    )


def test_resolve_tool_display_name_uses_builtin_disease_extractor_map():
    assert (
        resolve_tool_display_name("ask_disease_extractor_specialist")
        == "Disease Extraction Agent"
    )


def test_resolve_tool_display_name_uses_builtin_chemical_extractor_map():
    assert (
        resolve_tool_display_name("ask_chemical_extractor_specialist")
        == "Chemical Extraction Agent"
    )


def test_resolve_tool_display_name_marks_missing_label():
    assert (
        resolve_tool_display_name("ask_ca_custom_specialist", {"ask_ca_custom_specialist": "  "})
        == "[Missing tool label] ask_ca_custom_specialist"
    )


def test_build_tool_start_friendly_name_for_specialist():
    assert build_tool_start_friendly_name("ask_gene_specialist") == "Calling Gene Validation Agent..."


def test_build_tool_start_friendly_name_for_internal_tool():
    assert build_tool_start_friendly_name("sql_query") == "Calling SQL Query..."


def test_build_tool_complete_friendly_name():
    assert build_tool_complete_friendly_name("ask_gene_specialist") == "Gene Validation Agent complete"


def test_build_specialist_internal_friendly_name_marks_missing_specialist():
    assert build_specialist_internal_friendly_name("", "search_document") == "Search Document"

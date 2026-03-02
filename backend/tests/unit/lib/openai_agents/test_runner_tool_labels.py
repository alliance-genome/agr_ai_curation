"""Tests for custom tool label extraction in streaming runner."""

from types import SimpleNamespace

import pytest

from src.lib.openai_agents.runner import (
    _build_custom_tool_display_names,
    _resolve_tool_display_name,
    _build_tool_start_friendly_name,
    _build_tool_complete_friendly_name,
)


def test_build_custom_tool_display_names_maps_custom_tool_from_description():
    agent = SimpleNamespace(
        tools=[
            SimpleNamespace(
                name="ask_ca_7fffddac_c7ad_4ee3_b641_97b6c652fc5b_specialist",
                description="Ask the Gene Validation Agent (Custom)",
            ),
        ]
    )

    labels = _build_custom_tool_display_names(agent)
    assert labels == {
        "ask_ca_7fffddac_c7ad_4ee3_b641_97b6c652fc5b_specialist": "Gene Validation Agent (Custom)"
    }


def test_build_custom_tool_display_names_ignores_non_custom_tools():
    agent = SimpleNamespace(
        tools=[
            SimpleNamespace(name="ask_pdf_specialist", description="Ask the PDF Specialist"),
            SimpleNamespace(name="search_document", description="Search document"),
        ]
    )

    labels = _build_custom_tool_display_names(agent)
    assert labels == {}


def test_resolve_tool_display_name_prefers_custom_names():
    resolved = _resolve_tool_display_name(
        "ask_ca_abc123_specialist",
        {"ask_ca_abc123_specialist": "Gene Validation Agent (Custom)"},
    )
    assert resolved == "Gene Validation Agent (Custom)"


def test_resolve_tool_display_name_uses_builtin_specialist_labels():
    resolved = _resolve_tool_display_name("ask_pdf_specialist", {})
    assert resolved == "General PDF Extraction Agent"


@pytest.mark.parametrize(
    ("tool_name", "expected_label"),
    [
        ("ask_gene_specialist", "Gene Validation Agent"),
        ("ask_gene_extractor_specialist", "Gene Extraction Agent"),
        ("ask_allele_specialist", "Allele Validation Agent"),
        ("ask_allele_extractor_specialist", "Allele/Variant Extraction Agent"),
        ("ask_disease_specialist", "Disease Ontology Agent"),
        ("ask_disease_extractor_specialist", "Disease Extraction Agent"),
        ("ask_chemical_specialist", "Chemical Ontology Agent"),
        ("ask_chemical_extractor_specialist", "Chemical Extraction Agent"),
        ("ask_gene_expression_specialist", "Gene Expression Extractor"),
        ("ask_phenotype_extractor_specialist", "Phenotype Extraction Agent"),
        ("ask_gene_ontology_specialist", "Gene Ontology Agent"),
        ("ask_go_annotations_specialist", "GO Annotations Agent"),
        ("ask_orthologs_specialist", "Orthologs Agent"),
        ("ask_ontology_mapping_specialist", "Ontology Mapping Agent"),
        ("ask_chat_output_specialist", "Chat Output Agent"),
        ("ask_csv_formatter_specialist", "CSV File Formatter"),
        ("ask_tsv_formatter_specialist", "TSV File Formatter"),
        ("ask_json_formatter_specialist", "JSON File Formatter"),
    ],
)
def test_resolve_tool_display_name_uses_canonical_builtin_names(tool_name, expected_label):
    assert _resolve_tool_display_name(tool_name, {}) == expected_label


def test_resolve_tool_display_name_marks_missing_custom_label():
    resolved = _resolve_tool_display_name(
        "ask_ca_empty_specialist",
        {"ask_ca_empty_specialist": "   "},
    )
    assert resolved == "[Missing tool label] ask_ca_empty_specialist"


def test_build_tool_start_friendly_name_formats_specialist_label():
    label = _build_tool_start_friendly_name("ask_gene_specialist", {})
    assert label == "Calling Gene Validation Agent..."


def test_build_tool_complete_friendly_name_formats_specialist_label():
    label = _build_tool_complete_friendly_name("ask_gene_specialist", {})
    assert label == "Gene Validation Agent complete"


def test_build_tool_start_friendly_name_humanizes_internal_tool_labels():
    label = _build_tool_start_friendly_name("search_document", {})
    assert label == "Calling Search Document..."

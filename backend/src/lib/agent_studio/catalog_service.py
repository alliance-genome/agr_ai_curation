"""
Prompt Catalog Service.

Retrieves agent prompts from the database for display in the Prompt Explorer.
Prompts are loaded at startup via the prompt cache and organized by category.

The catalog is organized by category (Routing, Extraction, Validation)
and includes both base prompts and MOD-specific rules.

**Database-backed**: All prompts now come from the prompt_templates table
via src.lib.prompts.cache. File parsing has been removed.

**Agent Registry**: Also provides metadata for flow execution and UI views.
Runtime instantiation resolves directly from unified DB-backed agent records.
"""

import logging
import os
from typing import Any, Dict, List, Optional
from datetime import datetime
import re
from dataclasses import dataclass

from agents import Agent
from src.lib.config.agent_loader import get_agent_definition, get_agent_by_folder

# Config-driven registry builder (loads metadata from YAML definitions)
from .registry_builder import build_agent_registry, AGENT_DOCUMENTATION

from .models import (
    PromptInfo,
    AgentPrompts,
    PromptCatalog,
    MODRuleInfo,
    AgentDocumentation,
    AgentCapability,
    DataSourceInfo,
)

logger = logging.getLogger(__name__)
def get_prompt_key_for_agent(registry_agent_id: str) -> str:
    """Resolve a registry agent ID/alias to canonical prompt cache key (folder name)."""
    if registry_agent_id == "task_input":
        return "task_input"

    # Canonical key is the folder name in config/agents/*.
    by_folder = get_agent_by_folder(registry_agent_id)
    if by_folder:
        return by_folder.folder_name

    by_agent_id = get_agent_definition(registry_agent_id)
    if by_agent_id:
        return by_agent_id.folder_name

    entry = AGENT_REGISTRY.get(registry_agent_id)
    if entry:
        supervisor = entry.get("supervisor", {})
        tool_name = supervisor.get("tool_name")
        if isinstance(tool_name, str) and tool_name.startswith("ask_") and tool_name.endswith("_specialist"):
            return tool_name[len("ask_"):-len("_specialist")]

    raise ValueError(f"Unknown agent_id: {registry_agent_id}")


def _convert_documentation(doc_dict: Optional[Dict[str, Any]]) -> Optional[AgentDocumentation]:
    """Convert a documentation dict from AGENT_REGISTRY to Pydantic models.

    Args:
        doc_dict: Documentation dict from AGENT_REGISTRY, or None

    Returns:
        AgentDocumentation model or None if no documentation
    """
    if not doc_dict:
        return None

    # Convert capabilities
    capabilities = []
    for cap in doc_dict.get("capabilities", []):
        capabilities.append(AgentCapability(
            name=cap["name"],
            description=cap["description"],
            example_query=cap.get("example_query"),
            example_result=cap.get("example_result"),
        ))

    # Convert data sources
    data_sources = []
    for ds in doc_dict.get("data_sources", []):
        data_sources.append(DataSourceInfo(
            name=ds["name"],
            description=ds["description"],
            species_supported=ds.get("species_supported"),
            data_types=ds.get("data_types"),
        ))

    return AgentDocumentation(
        summary=doc_dict.get("summary", ""),
        capabilities=capabilities,
        data_sources=data_sources,
        limitations=doc_dict.get("limitations", []),
    )


# Agent metadata registry - built dynamically from YAML configurations.
# Source of truth: config/agents/*/agent.yaml
# Factory functions: discovered via convention (create_{folder}_agent)
AGENT_REGISTRY = build_agent_registry()


# Tool metadata registry - provides detailed documentation about each tool
# available to agents, including parameters, methods, and usage examples.
TOOL_REGISTRY: Dict[str, Dict[str, Any]] = {
    # AGR Curation Database Query Tool (multi-method tool)
    "agr_curation_query": {
        "name": "AGR Curation Query",
        "description": "Query the Alliance Genome Resources Curation Database for genes, alleles, and ontology terms.",
        "category": "Database",
        "source_file": "backend/src/lib/openai_agents/tools/agr_curation.py",
        "documentation": {
            "summary": "A unified tool for querying the Alliance Curation Database. Different agents use different methods of this tool based on their specialization.",
            "parameters": [
                {
                    "name": "method",
                    "type": "string",
                    "required": True,
                    "description": "The query method to execute. Determines what type of data to retrieve.",
                },
                {
                    "name": "gene_symbol",
                    "type": "string",
                    "required": False,
                    "description": "Gene symbol to search for (e.g., 'daf-2', 'Brca1').",
                },
                {
                    "name": "gene_symbols",
                    "type": "array[string]",
                    "required": False,
                    "description": "List of gene symbols for bulk lookup (e.g., ['crb', 'ninaE', 'Rh1']).",
                },
                {
                    "name": "gene_id",
                    "type": "string",
                    "required": False,
                    "description": "Gene CURIE for direct lookup (e.g., 'WB:WBGene00000898').",
                },
                {
                    "name": "allele_symbol",
                    "type": "string",
                    "required": False,
                    "description": "Allele symbol to search for (e.g., 'e1370', 'tm1Gldn').",
                },
                {
                    "name": "allele_symbols",
                    "type": "array[string]",
                    "required": False,
                    "description": "List of allele symbols for bulk lookup.",
                },
                {
                    "name": "allele_id",
                    "type": "string",
                    "required": False,
                    "description": "Allele CURIE for direct lookup (e.g., 'WB:WBVar00143949').",
                },
                {
                    "name": "data_provider",
                    "type": "string",
                    "required": False,
                    "description": "Filter by MOD: MGI, FB, WB, ZFIN, RGD, SGD, HGNC.",
                },
                {
                    "name": "limit",
                    "type": "integer",
                    "required": False,
                    "description": "Maximum results to return (default: 100, max: 500).",
                },
            ],
        },
        "methods": {
            "search_genes": {
                "name": "Search Genes",
                "description": "Search for genes by symbol using LIKE matching (supports partial matches).",
                "required_params": ["gene_symbol"],
                "optional_params": ["data_provider", "limit", "include_synonyms"],
                "example": {
                    "method": "search_genes",
                    "gene_symbol": "daf",
                    "data_provider": "WB",
                    "limit": 10,
                },
            },
            "search_genes_bulk": {
                "name": "Search Genes (Bulk)",
                "description": "Bulk gene symbol search in one tool call (list-in/list-out).",
                "required_params": ["gene_symbols"],
                "optional_params": ["data_provider", "limit", "include_synonyms"],
                "example": {
                    "method": "search_genes_bulk",
                    "gene_symbols": ["crb", "ninaE", "Rh1"],
                    "data_provider": "FB",
                    "limit": 10,
                },
            },
            "get_gene_by_exact_symbol": {
                "name": "Get Gene by Exact Symbol",
                "description": "Find a gene by its exact official symbol (SQL IN clause - requires exact match).",
                "required_params": ["gene_symbol"],
                "optional_params": ["data_provider"],
                "example": {
                    "method": "get_gene_by_exact_symbol",
                    "gene_symbol": "daf-2",
                    "data_provider": "WB",
                },
            },
            "get_gene_by_id": {
                "name": "Get Gene by ID",
                "description": "Retrieve detailed gene information by CURIE.",
                "required_params": ["gene_id"],
                "optional_params": [],
                "example": {
                    "method": "get_gene_by_id",
                    "gene_id": "WB:WBGene00000898",
                },
            },
            "search_alleles": {
                "name": "Search Alleles",
                "description": "Search for alleles by symbol using LIKE matching (supports partial matches).",
                "required_params": ["allele_symbol"],
                "optional_params": ["data_provider", "limit", "include_synonyms"],
                "example": {
                    "method": "search_alleles",
                    "allele_symbol": "tm1",
                    "data_provider": "WB",
                    "limit": 10,
                },
            },
            "search_alleles_bulk": {
                "name": "Search Alleles (Bulk)",
                "description": "Bulk allele symbol search in one tool call (list-in/list-out).",
                "required_params": ["allele_symbols"],
                "optional_params": ["data_provider", "limit", "include_synonyms"],
                "example": {
                    "method": "search_alleles_bulk",
                    "allele_symbols": ["tm1", "e1370", "n765"],
                    "data_provider": "WB",
                    "limit": 10,
                },
            },
            "get_allele_by_exact_symbol": {
                "name": "Get Allele by Exact Symbol",
                "description": "Find an allele by its exact official symbol. Handles paper notation (Gene<allele>) to database format (Gene<sup>allele</sup>) conversion.",
                "required_params": ["allele_symbol"],
                "optional_params": ["data_provider"],
                "example": {
                    "method": "get_allele_by_exact_symbol",
                    "allele_symbol": "e1370",
                    "data_provider": "WB",
                },
            },
            "get_allele_by_id": {
                "name": "Get Allele by ID",
                "description": "Retrieve detailed allele information by CURIE.",
                "required_params": ["allele_id"],
                "optional_params": [],
                "example": {
                    "method": "get_allele_by_id",
                    "allele_id": "WB:WBVar00143949",
                },
            },
            "search_anatomy_terms": {
                "name": "Search Anatomy Terms",
                "description": "Search species-specific anatomy ontology terms.",
                "required_params": ["term", "data_provider"],
                "optional_params": ["exact_match", "include_synonyms", "limit"],
                "example": {
                    "method": "search_anatomy_terms",
                    "term": "body wall muscle",
                    "data_provider": "WB",
                },
            },
            "search_life_stage_terms": {
                "name": "Search Life Stage Terms",
                "description": "Search species-specific developmental stage ontology terms.",
                "required_params": ["term", "data_provider"],
                "optional_params": ["exact_match", "include_synonyms", "limit"],
                "example": {
                    "method": "search_life_stage_terms",
                    "term": "adult",
                    "data_provider": "WB",
                },
            },
            "search_go_terms": {
                "name": "Search GO Terms",
                "description": "Search Gene Ontology terms by name or keyword.",
                "required_params": ["term"],
                "optional_params": ["go_aspect", "exact_match", "include_synonyms", "limit"],
                "example": {
                    "method": "search_go_terms",
                    "term": "kinase activity",
                    "go_aspect": "molecular_function",
                },
            },
            "get_species": {
                "name": "Get Species",
                "description": "List all supported species/organisms.",
                "required_params": [],
                "optional_params": [],
                "example": {
                    "method": "get_species",
                },
            },
            "get_data_providers": {
                "name": "Get Data Providers",
                "description": "List all MOD data providers with their taxon mappings.",
                "required_params": [],
                "optional_params": [],
                "example": {
                    "method": "get_data_providers",
                },
            },
        },
        # Map agents to the methods they use
        "agent_methods": {
            "gene": {
                "agent_name": "Gene Validation Agent",
                "methods": ["search_genes", "search_genes_bulk", "get_gene_by_exact_symbol", "get_gene_by_id"],
                "description": "The Gene Agent uses these methods to validate gene identifiers and retrieve gene information.",
            },
            "allele": {
                "agent_name": "Allele Validation Agent",
                "methods": ["search_alleles", "search_alleles_bulk", "get_allele_by_exact_symbol", "get_allele_by_id"],
                "description": "The Allele Agent uses these methods to validate allele/variant identifiers.",
            },
            "gene_expression": {
                "agent_name": "Gene Expression Extractor",
                "methods": ["search_genes", "search_genes_bulk", "get_gene_by_exact_symbol"],
                "description": "The Gene Expression agent validates gene names found during PDF extraction.",
            },
            "gene_ontology": {
                "agent_name": "Gene Ontology Agent",
                "methods": ["search_go_terms"],
                "description": "The GO Agent searches for Gene Ontology terms.",
            },
            "ontology_mapping": {
                "agent_name": "Ontology Mapping Agent",
                "methods": ["search_anatomy_terms", "search_life_stage_terms", "search_go_terms"],
                "description": "The Ontology Mapping agent maps free-text labels to ontology term IDs.",
            },
        },
    },

    # Alliance Genome Orthology API Tool
    "alliance_api_call": {
        "name": "Alliance Orthology API",
        "description": "Query the Alliance of Genome Resources API for orthology relationships.",
        "category": "API",
        "source_file": "backend/src/lib/openai_agents/tools/rest_api.py",
        "documentation": {
            "summary": "Queries orthology relationships between genes across species using the Alliance of Genome Resources API.",
            "parameters": [
                {
                    "name": "url",
                    "type": "string",
                    "required": True,
                    "description": "Full URL to query (must be on alliancegenome.org domain).",
                },
                {
                    "name": "method",
                    "type": "string",
                    "required": False,
                    "description": "HTTP method (default: GET).",
                },
                {
                    "name": "headers_json",
                    "type": "string",
                    "required": False,
                    "description": "Optional JSON string for request headers.",
                },
                {
                    "name": "body_json",
                    "type": "string",
                    "required": False,
                    "description": "Optional JSON string for request body.",
                },
            ],
        },
        "methods": None,
        "agent_methods": None,
    },

    # PDF Document Search Tools
    "search_document": {
        "name": "Search Document",
        "description": "Search uploaded PDF documents using hybrid semantic and keyword search.",
        "category": "PDF Extraction",
        "source_file": "backend/src/lib/openai_agents/tools/weaviate_search.py",
        "documentation": {
            "summary": "Finds relevant passages in the uploaded PDF using vector similarity search combined with keyword matching.",
            "parameters": [
                {
                    "name": "query",
                    "type": "string",
                    "required": True,
                    "description": "Search query text (semantic + keyword matching).",
                },
                {
                    "name": "limit",
                    "type": "integer",
                    "required": False,
                    "description": "Maximum number of results (default: 5).",
                },
                {
                    "name": "section_keywords",
                    "type": "array",
                    "required": False,
                    "description": "Filter to specific sections (e.g., ['Methods', 'Results']).",
                },
            ],
        },
        "methods": None,  # Single-method tool
        "agent_methods": None,
    },
    "read_section": {
        "name": "Read Section",
        "description": "Read the full text of a specific document section.",
        "category": "PDF Extraction",
        "source_file": "backend/src/lib/openai_agents/tools/weaviate_search.py",
        "documentation": {
            "summary": "Retrieves the complete text content of a named section from the PDF.",
            "parameters": [
                {
                    "name": "section_name",
                    "type": "string",
                    "required": True,
                    "description": "Name of the section to read (e.g., 'Methods', 'Introduction').",
                },
            ],
        },
        "methods": None,
        "agent_methods": None,
    },
    "read_subsection": {
        "name": "Read Subsection",
        "description": "Read the full text of a specific subsection within a section.",
        "category": "PDF Extraction",
        "source_file": "backend/src/lib/openai_agents/tools/weaviate_search.py",
        "documentation": {
            "summary": "Retrieves content from a specific subsection (e.g., 'Strain construction' within Methods).",
            "parameters": [
                {
                    "name": "section_name",
                    "type": "string",
                    "required": True,
                    "description": "Parent section name.",
                },
                {
                    "name": "subsection_name",
                    "type": "string",
                    "required": True,
                    "description": "Subsection name to read.",
                },
            ],
        },
        "methods": None,
        "agent_methods": None,
    },

    # Curation Database SQL Tool (Disease Agent)
    "curation_db_sql": {
        "name": "Curation Database SQL",
        "description": "Query the Alliance Curation Database for disease ontology information.",
        "category": "Database",
        "source_file": "backend/src/lib/openai_agents/tools/sql_query.py",
        "documentation": {
            "summary": "Executes SQL queries against the Alliance Curation Database to look up Disease Ontology (DOID) terms and relationships.",
            "parameters": [
                {
                    "name": "query",
                    "type": "string",
                    "required": True,
                    "description": "SQL query to execute against the curation database.",
                },
            ],
        },
        "methods": None,
        "agent_methods": None,
    },

    # ChEBI API Tool
    "chebi_api_call": {
        "name": "ChEBI API",
        "description": "Query the ChEBI API for chemical compound identifiers.",
        "category": "API",
        "source_file": "backend/src/lib/openai_agents/tools/rest_api.py",
        "documentation": {
            "summary": "Queries the ChEBI API at EBI to look up chemical compound identifiers and ontology information.",
            "parameters": [
                {
                    "name": "url",
                    "type": "string",
                    "required": True,
                    "description": "Full URL to query (must be on ebi.ac.uk domain).",
                },
                {
                    "name": "method",
                    "type": "string",
                    "required": False,
                    "description": "HTTP method (default: GET).",
                },
                {
                    "name": "headers_json",
                    "type": "string",
                    "required": False,
                    "description": "Optional JSON string for request headers.",
                },
                {
                    "name": "body_json",
                    "type": "string",
                    "required": False,
                    "description": "Optional JSON string for request body.",
                },
            ],
        },
        "methods": None,
        "agent_methods": None,
    },

    # QuickGO Gene Ontology API Tool
    "quickgo_api_call": {
        "name": "QuickGO API",
        "description": "Query the QuickGO API for Gene Ontology term information.",
        "category": "API",
        "source_file": "backend/src/lib/openai_agents/tools/rest_api.py",
        "documentation": {
            "summary": "Queries the QuickGO API to retrieve Gene Ontology (GO) term details including names, definitions, and relationships.",
            "parameters": [
                {
                    "name": "url",
                    "type": "string",
                    "required": True,
                    "description": "Full URL to query (must be on ebi.ac.uk domain).",
                },
                {
                    "name": "method",
                    "type": "string",
                    "required": False,
                    "description": "HTTP method (default: GET).",
                },
                {
                    "name": "headers_json",
                    "type": "string",
                    "required": False,
                    "description": "Optional JSON string for request headers.",
                },
                {
                    "name": "body_json",
                    "type": "string",
                    "required": False,
                    "description": "Optional JSON string for request body.",
                },
            ],
        },
        "methods": None,
        "agent_methods": None,
    },

    # QuickGO Annotations API Tool
    "go_api_call": {
        "name": "GO Annotations API",
        "description": "Query the QuickGO API for Gene Ontology annotations.",
        "category": "API",
        "source_file": "backend/src/lib/openai_agents/tools/rest_api.py",
        "documentation": {
            "summary": "Queries the QuickGO API to retrieve GO annotations for genes, including evidence codes and qualifiers.",
            "parameters": [
                {
                    "name": "url",
                    "type": "string",
                    "required": True,
                    "description": "Full URL to query (must be on ebi.ac.uk domain).",
                },
                {
                    "name": "method",
                    "type": "string",
                    "required": False,
                    "description": "HTTP method (default: GET).",
                },
                {
                    "name": "headers_json",
                    "type": "string",
                    "required": False,
                    "description": "Optional JSON string for request headers.",
                },
                {
                    "name": "body_json",
                    "type": "string",
                    "required": False,
                    "description": "Optional JSON string for request body.",
                },
            ],
        },
        "methods": None,
        "agent_methods": None,
    },

    # Supervisor Transfer Tools
    "transfer_to_pdf_specialist": {
        "name": "Transfer to PDF Specialist",
        "description": "Route query to PDF Specialist agent for document extraction.",
        "category": "Routing",
        "source_file": "backend/src/lib/openai_agents/agents/supervisor_agent.py",
        "documentation": {
            "summary": "Internal supervisor tool for routing document-related queries to the PDF specialist.",
            "parameters": [],
        },
        "methods": None,
        "agent_methods": None,
    },
    "transfer_to_gene_agent": {
        "name": "Transfer to Gene Agent",
        "description": "Route query to Gene Validation Agent.",
        "category": "Routing",
        "source_file": "backend/src/lib/openai_agents/agents/supervisor_agent.py",
        "documentation": {
            "summary": "Internal supervisor tool for routing gene lookup queries.",
            "parameters": [],
        },
        "methods": None,
        "agent_methods": None,
    },
    "transfer_to_allele_agent": {
        "name": "Transfer to Allele Agent",
        "description": "Route query to Allele Validation Agent.",
        "category": "Routing",
        "source_file": "backend/src/lib/openai_agents/agents/supervisor_agent.py",
        "documentation": {
            "summary": "Internal supervisor tool for routing allele/variant lookup queries.",
            "parameters": [],
        },
        "methods": None,
        "agent_methods": None,
    },
    "transfer_to_disease_agent": {
        "name": "Transfer to Disease Agent",
        "description": "Route query to Disease Ontology Agent.",
        "category": "Routing",
        "source_file": "backend/src/lib/openai_agents/agents/supervisor_agent.py",
        "documentation": {
            "summary": "Internal supervisor tool for routing disease term queries.",
            "parameters": [],
        },
        "methods": None,
        "agent_methods": None,
    },
    "transfer_to_chemical_agent": {
        "name": "Transfer to Chemical Agent",
        "description": "Route query to Chemical Ontology Agent.",
        "category": "Routing",
        "source_file": "backend/src/lib/openai_agents/agents/supervisor_agent.py",
        "documentation": {
            "summary": "Internal supervisor tool for routing chemical compound queries.",
            "parameters": [],
        },
        "methods": None,
        "agent_methods": None,
    },
    "transfer_to_go_agent": {
        "name": "Transfer to GO Agent",
        "description": "Route query to Gene Ontology Agent.",
        "category": "Routing",
        "source_file": "backend/src/lib/openai_agents/agents/supervisor_agent.py",
        "documentation": {
            "summary": "Internal supervisor tool for routing GO term queries.",
            "parameters": [],
        },
        "methods": None,
        "agent_methods": None,
    },
    "transfer_to_go_annotations_agent": {
        "name": "Transfer to GO Annotations Agent",
        "description": "Route query to GO Annotations Agent.",
        "category": "Routing",
        "source_file": "backend/src/lib/openai_agents/agents/supervisor_agent.py",
        "documentation": {
            "summary": "Internal supervisor tool for routing GO annotation queries.",
            "parameters": [],
        },
        "methods": None,
        "agent_methods": None,
    },
    "transfer_to_orthologs_agent": {
        "name": "Transfer to Orthologs Agent",
        "description": "Route query to Orthologs Agent.",
        "category": "Routing",
        "source_file": "backend/src/lib/openai_agents/agents/supervisor_agent.py",
        "documentation": {
            "summary": "Internal supervisor tool for routing orthology queries.",
            "parameters": [],
        },
        "methods": None,
        "agent_methods": None,
    },

    # File Output Tools
    "save_csv_file": {
        "name": "Save CSV File",
        "description": "Save data as a downloadable CSV file.",
        "category": "Output",
        "source_file": "backend/src/lib/openai_agents/tools/file_output_tools.py",
        "documentation": {
            "summary": "Creates a CSV file from structured data and returns a download link.",
            "parameters": [
                {
                    "name": "filename",
                    "type": "string",
                    "required": True,
                    "description": "Output filename (without extension).",
                },
                {
                    "name": "data",
                    "type": "array",
                    "required": True,
                    "description": "Array of objects to convert to CSV rows.",
                },
            ],
        },
        "methods": None,
        "agent_methods": None,
    },
    "save_tsv_file": {
        "name": "Save TSV File",
        "description": "Save data as a downloadable TSV file.",
        "category": "Output",
        "source_file": "backend/src/lib/openai_agents/tools/file_output_tools.py",
        "documentation": {
            "summary": "Creates a TSV file from structured data and returns a download link.",
            "parameters": [
                {
                    "name": "filename",
                    "type": "string",
                    "required": True,
                    "description": "Output filename (without extension).",
                },
                {
                    "name": "data",
                    "type": "array",
                    "required": True,
                    "description": "Array of objects to convert to TSV rows.",
                },
            ],
        },
        "methods": None,
        "agent_methods": None,
    },
    "save_json_file": {
        "name": "Save JSON File",
        "description": "Save data as a downloadable JSON file.",
        "category": "Output",
        "source_file": "backend/src/lib/openai_agents/tools/file_output_tools.py",
        "documentation": {
            "summary": "Creates a JSON file from structured data and returns a download link.",
            "parameters": [
                {
                    "name": "filename",
                    "type": "string",
                    "required": True,
                    "description": "Output filename (without extension).",
                },
                {
                    "name": "data",
                    "type": "any",
                    "required": True,
                    "description": "Data to serialize as JSON.",
                },
            ],
        },
        "methods": None,
        "agent_methods": None,
    },
}


# =============================================================================
# Tool Overrides for Hybrid Registry
# =============================================================================
# Manual overrides for rich documentation that can't be introspected.
# This is merged with auto-introspected tool metadata in get_tool_registry().

TOOL_OVERRIDES: Dict[str, Dict[str, Any]] = {
    "agr_curation_query": {
        "category": "Database",
        "documentation": {
            "example_queries": [
                "Find gene daf-2 in WormBase",
                "Get allele information for e1370",
            ],
        },
    },
    "search_document": {
        "category": "Document",
    },
}


def get_tool_registry() -> Dict[str, Dict[str, Any]]:
    """
    Build tool registry: introspection + manual overrides.

    Scans tool modules for @function_tool decorated functions,
    extracts metadata via introspection, then merges manual
    overrides for rich documentation.

    Returns:
        Dict mapping tool_id to metadata dict
    """
    from src.lib.openai_agents.tools import agr_curation
    from src.lib.openai_agents.tools import weaviate_search
    from .tool_introspection import introspect_tool

    # List of tool modules to scan
    tool_modules = [agr_curation, weaviate_search]

    registry: Dict[str, Dict[str, Any]] = {}

    for module in tool_modules:
        # Find all function_tool decorated functions (they have params_json_schema)
        for name in dir(module):
            obj = getattr(module, name)
            # FunctionTool objects have params_json_schema attribute
            if hasattr(obj, 'params_json_schema') and hasattr(obj, 'description'):
                try:
                    metadata = introspect_tool(obj)
                    tool_dict = {
                        "name": metadata.name,
                        "description": metadata.description,
                        "parameters": metadata.parameters,
                        "source_file": metadata.source_file,
                    }

                    # Apply manual overrides
                    if metadata.name in TOOL_OVERRIDES:
                        tool_dict.update(TOOL_OVERRIDES[metadata.name])

                    registry[metadata.name] = tool_dict
                except Exception as e:
                    logger.warning('Failed to introspect %s: %s', name, e)

    return registry


# =============================================================================
# Method-Level Tool Entries
# =============================================================================
# These entries provide first-class access to individual methods of multi-method
# tools like agr_curation_query. When displayed in the UI, users see these
# descriptive method names instead of the underlying tool mechanism.

def _generate_method_tool_entries() -> Dict[str, Dict[str, Any]]:
    """
    Generate first-class tool entries for methods of multi-method tools.

    This creates entries like 'search_genes', 'get_allele_by_id' that reference
    their parent tool (agr_curation_query) but present method-specific metadata.
    Uses rich parameter descriptions from the parent tool where available.
    """
    entries = {}

    for tool_id, tool_info in TOOL_REGISTRY.items():
        methods = tool_info.get("methods")
        if not methods:
            continue

        # Build a lookup dict for parameter descriptions from parent tool
        parent_params: Dict[str, Dict[str, Any]] = {}
        if tool_info.get("documentation") and tool_info["documentation"].get("parameters"):
            for param in tool_info["documentation"]["parameters"]:
                parent_params[param["name"]] = param

        for method_id, method_info in methods.items():
            # Build parameters with rich descriptions from parent where available
            params = []
            for p in method_info.get("required_params", []):
                if p in parent_params:
                    params.append({**parent_params[p], "required": True})
                else:
                    params.append({"name": p, "type": "string", "required": True, "description": f"Required parameter: {p}"})

            for p in method_info.get("optional_params", []):
                if p in parent_params:
                    params.append({**parent_params[p], "required": False})
                else:
                    params.append({"name": p, "type": "string", "required": False, "description": f"Optional parameter: {p}"})

            entries[method_id] = {
                "name": method_info["name"],
                "description": method_info["description"],
                "category": tool_info["category"],
                "source_file": tool_info["source_file"],
                "parent_tool": tool_id,  # Reference to the parent tool
                "documentation": {
                    "summary": method_info["description"],
                    "parameters": params,
                },
                "example": method_info.get("example", {}),
                "methods": None,  # Method-level tools don't have sub-methods
                "agent_methods": None,
            }

    return entries

# Add method-level entries to a separate registry for lookup
METHOD_TOOL_ENTRIES = _generate_method_tool_entries()


@dataclass(frozen=True)
class ToolExecutionContext:
    """Context used to resolve runtime tool factories deterministically."""

    document_id: Optional[str] = None
    user_id: Optional[str] = None
    database_url: Optional[str] = None
    tool_tracker: Optional[Any] = None


def _resolve_agr_curation_tool(context: ToolExecutionContext) -> Any:
    from dataclasses import replace
    from src.lib.openai_agents.tools.agr_curation import agr_curation_query

    if not context.tool_tracker:
        return agr_curation_query

    # Wrap with ToolCallTracker recording so the output guardrail
    # sees the call count.  Uses dataclasses.replace to create an
    # independent copy of the FunctionTool — the module-level
    # singleton is never mutated.
    tracker = context.tool_tracker
    original_invoke = agr_curation_query.on_invoke_tool

    async def _tracked_invoke(ctx, input_str):
        tracker.record_call("agr_curation_query")
        return await original_invoke(ctx, input_str)

    return replace(agr_curation_query, on_invoke_tool=_tracked_invoke)


def _resolve_search_document_tool(context: ToolExecutionContext) -> Any:
    from src.lib.openai_agents.tools.weaviate_search import create_search_tool

    return create_search_tool(
        document_id=context.document_id,
        user_id=context.user_id,
        tracker=context.tool_tracker,
    )


def _resolve_read_section_tool(context: ToolExecutionContext) -> Any:
    from src.lib.openai_agents.tools.weaviate_search import create_read_section_tool

    return create_read_section_tool(
        document_id=context.document_id,
        user_id=context.user_id,
        tracker=context.tool_tracker,
    )


def _resolve_read_subsection_tool(context: ToolExecutionContext) -> Any:
    from src.lib.openai_agents.tools.weaviate_search import create_read_subsection_tool

    return create_read_subsection_tool(
        document_id=context.document_id,
        user_id=context.user_id,
        tracker=context.tool_tracker,
    )


def _resolve_curation_db_sql_tool(context: ToolExecutionContext) -> Any:
    from src.lib.openai_agents.tools.sql_query import create_sql_query_tool

    return create_sql_query_tool(context.database_url, tool_name="curation_db_sql")


def _resolve_chebi_api_tool(_context: ToolExecutionContext) -> Any:
    from src.lib.openai_agents.tools.rest_api import create_rest_api_tool

    return create_rest_api_tool(
        allowed_domains=["ebi.ac.uk", "www.ebi.ac.uk"],
        tool_name="chebi_api_call",
        tool_description="Query ChEBI chemical database API (ebi.ac.uk only)",
    )


def _resolve_quickgo_api_tool(_context: ToolExecutionContext) -> Any:
    from src.lib.openai_agents.tools.rest_api import create_rest_api_tool

    return create_rest_api_tool(
        allowed_domains=["ebi.ac.uk", "www.ebi.ac.uk"],
        tool_name="quickgo_api_call",
        tool_description=(
            "Query QuickGO Gene Ontology API for GO terms, hierarchy, and relationships "
            "(ebi.ac.uk only)"
        ),
    )


def _resolve_go_api_tool(_context: ToolExecutionContext) -> Any:
    from src.lib.openai_agents.tools.rest_api import create_rest_api_tool

    return create_rest_api_tool(
        allowed_domains=["geneontology.org", "api.geneontology.org"],
        tool_name="go_api_call",
        tool_description=(
            "Query Gene Ontology API for gene annotations with evidence codes "
            "(geneontology.org only)"
        ),
    )


def _resolve_alliance_api_tool(_context: ToolExecutionContext) -> Any:
    from src.lib.openai_agents.tools.rest_api import create_rest_api_tool

    return create_rest_api_tool(
        allowed_domains=["alliancegenome.org", "www.alliancegenome.org"],
        tool_name="alliance_api_call",
        tool_description=(
            "Query Alliance of Genome Resources API for orthology data "
            "(alliancegenome.org only)"
        ),
    )


def _resolve_save_csv_tool(_context: ToolExecutionContext) -> Any:
    from src.lib.openai_agents.tools.file_output_tools import create_csv_tool

    return create_csv_tool()


def _resolve_save_tsv_tool(_context: ToolExecutionContext) -> Any:
    from src.lib.openai_agents.tools.file_output_tools import create_tsv_tool

    return create_tsv_tool()


def _resolve_save_json_tool(_context: ToolExecutionContext) -> Any:
    from src.lib.openai_agents.tools.file_output_tools import create_json_tool

    return create_json_tool()


# Declarative runtime bindings used by the unified agent builder.
TOOL_BINDINGS: Dict[str, Dict[str, Any]] = {
    "agr_curation_query": {
        "binding": "static",
        "required_context": [],
        "resolver": _resolve_agr_curation_tool,
    },
    "search_document": {
        "binding": "context_factory",
        "required_context": ["document_id", "user_id"],
        "resolver": _resolve_search_document_tool,
    },
    "read_section": {
        "binding": "context_factory",
        "required_context": ["document_id", "user_id"],
        "resolver": _resolve_read_section_tool,
    },
    "read_subsection": {
        "binding": "context_factory",
        "required_context": ["document_id", "user_id"],
        "resolver": _resolve_read_subsection_tool,
    },
    "curation_db_sql": {
        "binding": "context_factory",
        "required_context": ["database_url"],
        "resolver": _resolve_curation_db_sql_tool,
    },
    "chebi_api_call": {
        "binding": "static",
        "required_context": [],
        "resolver": _resolve_chebi_api_tool,
    },
    "quickgo_api_call": {
        "binding": "static",
        "required_context": [],
        "resolver": _resolve_quickgo_api_tool,
    },
    "go_api_call": {
        "binding": "static",
        "required_context": [],
        "resolver": _resolve_go_api_tool,
    },
    "alliance_api_call": {
        "binding": "static",
        "required_context": [],
        "resolver": _resolve_alliance_api_tool,
    },
    "save_csv_file": {
        "binding": "static",
        "required_context": [],
        "resolver": _resolve_save_csv_tool,
    },
    "save_tsv_file": {
        "binding": "static",
        "required_context": [],
        "resolver": _resolve_save_tsv_tool,
    },
    "save_json_file": {
        "binding": "static",
        "required_context": [],
        "resolver": _resolve_save_json_tool,
    },
}


def _canonicalize_tool_id(tool_id: str) -> str:
    """Map method-level tool aliases back to concrete runtime tool IDs."""
    method_entry = METHOD_TOOL_ENTRIES.get(tool_id)
    parent_tool = method_entry.get("parent_tool") if method_entry else None
    if isinstance(parent_tool, str) and parent_tool:
        return parent_tool
    return tool_id


def resolve_tools(tool_ids: List[str], execution_context: ToolExecutionContext) -> List[Any]:
    """Resolve DB tool IDs to runtime tool instances using explicit binding metadata."""
    resolved_tools: List[Any] = []
    seen_tool_ids: set[str] = set()

    for raw_tool_id in tool_ids:
        tool_id = _canonicalize_tool_id(raw_tool_id)
        if tool_id in seen_tool_ids:
            continue
        seen_tool_ids.add(tool_id)

        binding = TOOL_BINDINGS.get(tool_id)
        if binding is None:
            raise ValueError(f"Unknown tool binding '{tool_id}'")

        required_context = list(binding.get("required_context", []))
        missing_context = [
            key for key in required_context if getattr(execution_context, key, None) in (None, "")
        ]
        if missing_context:
            missing_text = ", ".join(missing_context)
            raise ValueError(
                f"Tool '{tool_id}' requires execution context: {missing_text}"
            )

        resolver = binding.get("resolver")
        if not callable(resolver):
            raise ValueError(f"Tool '{tool_id}' has invalid binding resolver")

        instance = resolver(execution_context)
        if instance is None:
            raise ValueError(f"Tool '{tool_id}' resolver returned no tool instance")

        resolved_tools.append(instance)

    return resolved_tools


_DOCUMENT_TOOL_IDS = {"search_document", "read_section", "read_subsection"}
_FORMATTER_TOOL_IDS = {"save_csv_file", "save_tsv_file", "save_json_file"}
# AGR validation agents should not answer without at least one DB query attempt.
# We enforce this similarly to document-tool agents.
_AGR_DB_QUERY_TOOL_IDS = {"agr_curation_query"}


def _canonical_tool_ids(tool_ids: List[str]) -> List[str]:
    """Canonicalize and de-duplicate tool IDs while preserving order."""
    canonical: List[str] = []
    seen: set[str] = set()
    for raw_tool_id in tool_ids:
        tool_id = _canonicalize_tool_id(raw_tool_id)
        if tool_id in seen:
            continue
        seen.add(tool_id)
        canonical.append(tool_id)
    return canonical


def _required_context_for_tool_ids(tool_ids: List[str]) -> List[str]:
    """Collect required execution-context keys implied by tool bindings."""
    required: set[str] = set()
    for tool_id in _canonical_tool_ids(tool_ids):
        binding = TOOL_BINDINGS.get(tool_id)
        if binding:
            required.update(binding.get("required_context", []))
    return sorted(required)


def _uses_document_tools(tool_ids: List[str]) -> bool:
    """Whether a tool set requires document-scoped context."""
    return bool(set(_canonical_tool_ids(tool_ids)) & _DOCUMENT_TOOL_IDS)


def expand_tools_for_agent(agent_id: str, tools: List[str]) -> List[str]:
    """
    Expand multi-method tools into their individual method names for an agent.

    For agents that use multi-method tools like agr_curation_query, this replaces
    the tool name with the specific method names that agent uses. This makes the
    tool list more intuitive for users.

    Example:
        expand_tools_for_agent("gene", ["agr_curation_query"])
        -> ["search_genes", "get_gene_by_exact_symbol", "get_gene_by_id"]

    Args:
        agent_id: Agent identifier (e.g., 'gene', 'allele')
        tools: Original list of tool IDs

    Returns:
        Expanded list with multi-method tools replaced by their method names
    """
    expanded = []

    for tool_id in tools:
        tool = TOOL_REGISTRY.get(tool_id)
        if not tool:
            # Unknown tool, keep as-is
            expanded.append(tool_id)
            continue

        agent_methods = tool.get("agent_methods")
        if agent_methods and agent_id in agent_methods:
            # Replace with the individual method names for this agent
            method_names = agent_methods[agent_id].get("methods", [])
            expanded.extend(method_names)
        else:
            # Not a multi-method tool or agent not in mapping, keep original
            expanded.append(tool_id)

    return expanded


def get_tool_details(tool_id: str) -> Optional[Dict[str, Any]]:
    """
    Get detailed information about a specific tool or method.

    Args:
        tool_id: Tool identifier (e.g., 'agr_curation_query', 'search_document')
                 or method identifier (e.g., 'search_genes', 'get_allele_by_id')

    Returns:
        Tool metadata dict or None if not found
    """
    # First check main registry
    if tool_id in TOOL_REGISTRY:
        return TOOL_REGISTRY[tool_id]

    # Then check method-level entries
    if tool_id in METHOD_TOOL_ENTRIES:
        return METHOD_TOOL_ENTRIES[tool_id]

    return None


def get_all_tools() -> Dict[str, Dict[str, Any]]:
    """
    Get all tools from the registry, including method-level entries.

    Returns:
        Combined dict of TOOL_REGISTRY and METHOD_TOOL_ENTRIES
    """
    # Combine both registries, with method entries available for lookup
    combined = dict(TOOL_REGISTRY)
    combined.update(METHOD_TOOL_ENTRIES)
    return combined


def get_tool_for_agent(tool_id: str, agent_id: str) -> Optional[Dict[str, Any]]:
    """
    Get tool details with agent-specific method information highlighted.

    For multi-method tools like agr_curation_query, this returns the tool
    with agent-specific method usage highlighted.

    For method-level tools (like search_genes), returns the method details directly.

    Args:
        tool_id: Tool identifier or method identifier
        agent_id: Agent identifier (e.g., 'gene', 'allele')

    Returns:
        Tool metadata with agent-specific context, or None if not found
    """
    # First check if it's a method-level tool
    if tool_id in METHOD_TOOL_ENTRIES:
        return METHOD_TOOL_ENTRIES[tool_id]

    tool = TOOL_REGISTRY.get(tool_id)
    if not tool:
        return None

    # Make a copy to avoid modifying the original
    result = dict(tool)

    # Add agent-specific method context if available
    agent_methods = tool.get("agent_methods")
    if agent_methods and agent_id in agent_methods:
        result["agent_context"] = agent_methods[agent_id]
        # Filter methods to only show those used by this agent
        if tool.get("methods"):
            agent_method_list = agent_methods[agent_id].get("methods", [])
            result["relevant_methods"] = {
                method_id: method_info
                for method_id, method_info in tool["methods"].items()
                if method_id in agent_method_list
            }

    return result


def _build_catalog() -> PromptCatalog:
    """
    Build the complete prompt catalog from database prompts.

    Uses the prompt cache (loaded at startup) to get prompt content
    and version metadata. Static metadata (category, tools) comes
    from AGENT_REGISTRY.

    Returns:
        PromptCatalog with all agents organized by category
    """
    from src.lib.prompts.cache import get_all_active_prompts, is_initialized

    # Check if cache is initialized
    if not is_initialized():
        logger.warning("Prompt cache not initialized - returning empty catalog")
        return PromptCatalog(
            categories=[],
            total_agents=0,
            available_mods=[],
            last_updated=datetime.utcnow(),
        )

    # Get all active prompts from cache
    all_prompts = get_all_active_prompts()

    # Group prompts by agent_name for easy lookup
    # Key format: agent_name:prompt_type:mod_id_or_base
    prompts_by_agent: Dict[str, Dict[str, Any]] = {}
    for cache_key, prompt in all_prompts.items():
        parts = cache_key.split(":")
        if len(parts) < 3:
            continue
        agent_name, prompt_type, mod_key = parts[0], parts[1], parts[2]

        if agent_name not in prompts_by_agent:
            prompts_by_agent[agent_name] = {"system": None, "group_rules": {}}

        if prompt_type == "system" and mod_key == "base":
            prompts_by_agent[agent_name]["system"] = prompt
        elif prompt_type in {"group_rules", "mod_rules"} and mod_key != "base":
            # Support legacy mod_rules keys during migration.
            prompts_by_agent[agent_name]["group_rules"][mod_key] = prompt

    # Build catalog by combining AGENT_REGISTRY metadata with database prompts
    categories_map: Dict[str, List[PromptInfo]] = {}
    available_mods = set()

    for agent_id, config in AGENT_REGISTRY.items():
        agent_prompts = prompts_by_agent.get(agent_id, {})
        system_prompt = agent_prompts.get("system")

        # Special case: non-agent entries (task_input) don't need database prompts
        if agent_id == "task_input":
            # Resolve show_in_palette from frontend config (defaults to True)
            frontend_config = config.get("frontend", {})
            show_in_palette = frontend_config.get("show_in_palette", True)

            # Create PromptInfo with no base prompt for display-only entries
            prompt_info = PromptInfo(
                agent_id=agent_id,
                agent_name=config["name"],
                description=config["description"],
                base_prompt="",  # No prompt for non-agent entries
                source_file="built-in",
                has_mod_rules=False,
                mod_rules={},
                tools=expand_tools_for_agent(agent_id, config.get("tools", [])),
                subcategory=config.get("subcategory"),
                show_in_palette=show_in_palette,
                documentation=_convert_documentation(config.get("documentation")),
                prompt_id=None,
                prompt_version=None,
                created_at=None,
                created_by=None,
            )
            category = config["category"]
            if category not in categories_map:
                categories_map[category] = []
            categories_map[category].append(prompt_info)
            continue

        if not system_prompt:
            logger.warning('Skipping %s: no system prompt found in database', agent_id)
            continue

        # Build MOD rules dict from database prompts
        mod_rules: Dict[str, MODRuleInfo] = {}
        for mod_id, prompt in agent_prompts.get("group_rules", {}).items():
            available_mods.add(mod_id)
            mod_rules[mod_id] = MODRuleInfo(
                mod_id=mod_id,
                content=prompt.content,
                source_file=prompt.source_file or "database",
                description=prompt.description,
                # Version metadata
                prompt_id=str(prompt.id) if prompt.id else None,
                prompt_version=prompt.version,
                created_at=prompt.created_at,
                created_by=prompt.created_by,
            )

        # Resolve show_in_palette from frontend config (defaults to True)
        frontend_config = config.get("frontend", {})
        show_in_palette = frontend_config.get("show_in_palette", True)

        # Create PromptInfo with version metadata
        prompt_info = PromptInfo(
            agent_id=agent_id,
            agent_name=config["name"],
            description=config["description"],
            base_prompt=system_prompt.content,
            source_file=system_prompt.source_file or "database",
            has_mod_rules=bool(mod_rules),
            mod_rules=mod_rules,
            tools=expand_tools_for_agent(agent_id, config.get("tools", [])),
            subcategory=config.get("subcategory"),
            show_in_palette=show_in_palette,
            documentation=_convert_documentation(config.get("documentation")),
            # Version metadata from database
            prompt_id=str(system_prompt.id) if system_prompt.id else None,
            prompt_version=system_prompt.version,
            created_at=system_prompt.created_at,
            created_by=system_prompt.created_by,
        )

        # Add to category
        category = config["category"]
        if category not in categories_map:
            categories_map[category] = []
        categories_map[category].append(prompt_info)

    # Convert to AgentPrompts list
    categories = [
        AgentPrompts(category=cat, agents=agents)
        for cat, agents in sorted(categories_map.items())
    ]

    return PromptCatalog(
        categories=categories,
        total_agents=sum(len(cat.agents) for cat in categories),
        available_mods=sorted(available_mods),
        last_updated=datetime.utcnow(),
    )


class PromptCatalogService:
    """
    Service for accessing the prompt catalog.

    The catalog is built from the prompt cache (database-backed) and
    combines static metadata from AGENT_REGISTRY with prompt content
    and version info from the prompt_templates table.

    Use refresh() to rebuild after prompt cache updates.
    """

    def __init__(self):
        self._catalog: Optional[PromptCatalog] = None

    @property
    def catalog(self) -> PromptCatalog:
        """Get the prompt catalog, building it if necessary."""
        if self._catalog is None:
            self._catalog = _build_catalog()
            logger.info(
                f"Built prompt catalog: {self._catalog.total_agents} agents, "
                f"{len(self._catalog.available_mods)} MODs"
            )
        return self._catalog

    def refresh(self) -> PromptCatalog:
        """Force rebuild of the catalog."""
        self._catalog = _build_catalog()
        logger.info("Refreshed prompt catalog")
        return self._catalog

    def get_agent(self, agent_id: str) -> Optional[PromptInfo]:
        """Get a specific agent's prompt info by ID."""
        for category in self.catalog.categories:
            for agent in category.agents:
                if agent.agent_id == agent_id:
                    return agent
        return None

    def get_agents_by_category(self, category: str) -> List[PromptInfo]:
        """Get all agents in a specific category."""
        for cat in self.catalog.categories:
            if cat.category == category:
                return cat.agents
        return []

    def get_combined_prompt(self, agent_id: str, mod_id: str) -> Optional[str]:
        """
        Get the combined prompt for an agent with MOD rules injected.

        Args:
            agent_id: Agent identifier
            mod_id: MOD identifier (e.g., "WB", "FB")

        Returns:
            Combined prompt string, or None if agent/MOD not found
        """
        agent = self.get_agent(agent_id)
        if not agent:
            return None

        if not agent.has_mod_rules or mod_id not in agent.mod_rules:
            return agent.base_prompt

        # Inject MOD rules into base prompt
        mod_rule = agent.mod_rules[mod_id]
        combined = f"""{agent.base_prompt}

## MOD-SPECIFIC RULES

The following rules are specific to {mod_id}:

{mod_rule.content}

## END MOD-SPECIFIC RULES
"""
        return combined


# Singleton instance
_catalog_service: Optional[PromptCatalogService] = None


def get_prompt_catalog() -> PromptCatalogService:
    """Get the singleton PromptCatalogService instance."""
    global _catalog_service
    if _catalog_service is None:
        _catalog_service = PromptCatalogService()
    return _catalog_service


# =============================================================================
# Agent Factory Functions (for Flow Execution)
# =============================================================================

_REASONING_LEVEL_PATTERN = re.compile(r"^(minimal|low|medium|high)$")


def _coerce_db_user_id(raw_user_id: Any) -> Optional[int]:
    """Best-effort conversion for runtime user IDs passed via kwargs."""
    if isinstance(raw_user_id, int):
        return raw_user_id
    if isinstance(raw_user_id, str):
        stripped = raw_user_id.strip()
        if stripped.isdigit():
            try:
                return int(stripped)
            except ValueError:
                return None
    return None


def _build_tool_execution_context(
    kwargs: Dict[str, Any],
    *,
    tool_tracker: Optional[Any] = None,
) -> ToolExecutionContext:
    """Build tool-resolution context from runtime kwargs + environment."""
    raw_user_id = kwargs.get("user_id")
    user_id = str(raw_user_id) if raw_user_id not in (None, "") else None

    raw_document_id = kwargs.get("document_id")
    document_id = str(raw_document_id) if raw_document_id not in (None, "") else None

    raw_database_url = kwargs.get("database_url")
    if isinstance(raw_database_url, str) and raw_database_url.strip():
        database_url = raw_database_url.strip()
    else:
        env_database_url = os.getenv("CURATION_DB_URL", "").strip()
        database_url = env_database_url or None

    return ToolExecutionContext(
        document_id=document_id,
        user_id=user_id,
        database_url=database_url,
        tool_tracker=tool_tracker,
    )


def _inject_group_rules_with_overrides(
    *,
    base_prompt: str,
    group_ids: List[str],
    component_name: str,
    mod_overrides: Optional[Dict[str, str]] = None,
    injection_marker: str = "## GROUP-SPECIFIC RULES",
) -> str:
    """Inject group rules using DB overrides first, then cached rule prompts."""
    from src.lib.prompts.cache import get_prompt_optional

    normalized_groups: List[str] = []
    for raw_group in group_ids:
        group_id = str(raw_group or "").strip().upper()
        if group_id and group_id not in normalized_groups:
            normalized_groups.append(group_id)
    if not normalized_groups:
        return base_prompt

    normalized_overrides: Dict[str, str] = {}
    for raw_group, raw_content in (mod_overrides or {}).items():
        group_id = str(raw_group or "").strip().upper()
        content = str(raw_content or "").strip()
        if group_id and content:
            normalized_overrides[group_id] = content

    collected_groups: List[str] = []
    collected_content: List[str] = []

    for group_id in normalized_groups:
        override_content = normalized_overrides.get(group_id)
        if override_content:
            collected_groups.append(group_id)
            collected_content.append(override_content)
            continue

        rule_prompt = get_prompt_optional(
            component_name,
            prompt_type="group_rules",
            group_id=group_id,
        ) or get_prompt_optional(
            component_name,
            prompt_type="mod_rules",
            group_id=group_id,
        )
        if rule_prompt:
            collected_groups.append(group_id)
            collected_content.append(rule_prompt.content)

    if not collected_content:
        logger.warning(
            "[CatalogService] No group rules found for %s/%s",
            component_name,
            normalized_groups,
        )
        return base_prompt

    group_list = ", ".join(collected_groups)
    formatted_rules = "\n".join(collected_content)
    injection_block = f"""
{injection_marker}

The following rules are specific to the organization group(s) you are working with: {group_list}
Apply these rules when searching for and interpreting results.

{formatted_rules}

## END GROUP-SPECIFIC RULES
"""
    if injection_marker in base_prompt:
        return base_prompt.replace(injection_marker, injection_block)
    return base_prompt + "\n" + injection_block


def _build_runtime_instructions(
    db_agent: Any,
    runtime_kwargs: Dict[str, Any],
    *,
    output_schema: Optional[Any],
    canonical_tool_ids: List[str],
) -> str:
    """Build final instructions from DB spec + runtime context injections."""
    from src.lib.openai_agents.prompt_utils import (
        format_document_context_for_prompt,
        inject_structured_output_instruction,
    )

    instructions = str(getattr(db_agent, "instructions", "") or "")

    # Inject group-specific rules when configured and requested at runtime.
    active_groups = list(runtime_kwargs.get("active_groups", []) or [])
    if bool(getattr(db_agent, "group_rules_enabled", False)) and active_groups:
        component_name = (
            getattr(db_agent, "group_rules_component", None)
            or getattr(db_agent, "template_source", None)
            or db_agent.agent_key
        )
        try:
            instructions = _inject_group_rules_with_overrides(
                base_prompt=instructions,
                group_ids=active_groups,
                component_name=component_name,
                mod_overrides=dict(getattr(db_agent, "mod_prompt_overrides", {}) or {}),
            )
        except Exception:
            logger.exception(
                "[CatalogService] Failed group-rules injection for '%s'",
                db_agent.agent_key,
            )
            raise ValueError(
                f"Failed group-rules injection for agent '{db_agent.agent_key}'"
            )

    # Inject document structure context for document-aware tools.
    if bool(set(canonical_tool_ids) & _DOCUMENT_TOOL_IDS):
        context_text, _structure_info = format_document_context_for_prompt(
            hierarchy=runtime_kwargs.get("hierarchy"),
            sections=runtime_kwargs.get("sections"),
            abstract=runtime_kwargs.get("abstract"),
        )
        if context_text:
            instructions += context_text

        document_name = runtime_kwargs.get("document_name")
        if document_name:
            instructions = (
                f'You are helping the user with the document: "{document_name}"\n\n'
                + instructions
            )

    # Reinforce structured-output generation when output schema is configured.
    if output_schema is not None:
        instructions = inject_structured_output_instruction(
            instructions,
            output_type=output_schema,
        )

    return instructions


def _resolve_output_schema(schema_key: str) -> Optional[Any]:
    """Resolve output schema class by name from shared OpenAI agent models."""
    try:
        from src.lib.openai_agents import models as agent_models
    except Exception:
        return None

    schema = getattr(agent_models, schema_key, None)
    if schema is None:
        return None
    return schema


def validate_active_agent_output_schemas(db: Any) -> None:
    """Fail fast when active agents reference unknown output schema keys."""
    from src.models.sql.agent import Agent as DBAgent

    rows = (
        db.query(DBAgent.agent_key, DBAgent.name, DBAgent.output_schema_key)
        .filter(DBAgent.is_active == True)  # noqa: E712
        .filter(DBAgent.output_schema_key.isnot(None))
        .filter(DBAgent.output_schema_key != "")
        .order_by(DBAgent.agent_key.asc())
        .all()
    )

    unresolved: List[str] = []
    for agent_key, name, output_schema_key in rows:
        if not _resolve_output_schema(str(output_schema_key)):
            unresolved.append(
                f"{agent_key} ({name}) -> {output_schema_key}"
            )

    if unresolved:
        details = "; ".join(unresolved)
        raise RuntimeError(
            "Found active agents with unknown output schemas in agents table: "
            f"{details}"
        )


def _create_db_agent(db_agent: Any, **kwargs: Any) -> Optional[Agent]:
    """Create an agent from a row in the unified agents table."""
    from src.lib.openai_agents.guardrails import (
        ToolCallTracker,
        create_tool_required_output_guardrail,
    )
    from src.lib.openai_agents.config import (
        get_model_for_agent,
        build_model_settings,
        resolve_model_provider,
    )

    runtime_kwargs = dict(kwargs)
    requested_tool_ids = list(getattr(db_agent, "tool_ids", []) or [])
    canonical_tool_ids = _canonical_tool_ids(requested_tool_ids)

    # Resolve output schema override when present.
    output_schema_key = getattr(db_agent, "output_schema_key", None)
    output_schema: Optional[Any] = None
    if output_schema_key:
        output_schema = _resolve_output_schema(output_schema_key)
        if output_schema is None:
            raise ValueError(
                f"Unknown output schema '{output_schema_key}' for agent '{db_agent.agent_key}'"
            )

    # Resolve tools from explicit binding metadata (no runtime fallbacks).
    output_guardrails: List[Any] = []
    if requested_tool_ids:
        tool_tracker: Optional[ToolCallTracker] = None
        has_document_tools = bool(set(canonical_tool_ids) & _DOCUMENT_TOOL_IDS)
        has_agr_db_query_tools = bool(set(canonical_tool_ids) & _AGR_DB_QUERY_TOOL_IDS)

        if has_document_tools or has_agr_db_query_tools:
            tool_tracker = ToolCallTracker()

        if has_document_tools:
            output_guardrails.append(
                create_tool_required_output_guardrail(
                    tracker=tool_tracker,
                    minimum_calls=1,
                    error_message=(
                        "You must search or read the document before answering. "
                        "Use search_document, read_section, or read_subsection first."
                    ),
                )
            )
        elif has_agr_db_query_tools:
            output_guardrails.append(
                create_tool_required_output_guardrail(
                    tracker=tool_tracker,
                    minimum_calls=1,
                    error_message=(
                        "You must query the AGR Curation Database before answering. "
                        "Use agr_curation_query first."
                    ),
                )
            )
        execution_context = _build_tool_execution_context(
            runtime_kwargs,
            tool_tracker=tool_tracker,
        )
        tools = resolve_tools(requested_tool_ids, execution_context)
    else:
        tools = []

    instructions = _build_runtime_instructions(
        db_agent=db_agent,
        runtime_kwargs=runtime_kwargs,
        output_schema=output_schema,
        canonical_tool_ids=canonical_tool_ids,
    )

    reasoning_effort = db_agent.model_reasoning
    if isinstance(reasoning_effort, str) and not _REASONING_LEVEL_PATTERN.match(reasoning_effort):
        logger.warning(
            "[CatalogService] Ignoring invalid reasoning level '%s' for agent '%s'",
            reasoning_effort,
            db_agent.agent_key,
        )
        reasoning_effort = None

    if bool(set(canonical_tool_ids) & _FORMATTER_TOOL_IDS):
        reasoning_effort = None

    model_provider = resolve_model_provider(db_agent.model_id)

    model_settings = build_model_settings(
        model=db_agent.model_id,
        temperature=db_agent.model_temperature,
        reasoning_effort=reasoning_effort,
        tool_choice="auto" if tools else None,
        parallel_tool_calls=not bool(set(canonical_tool_ids) & _FORMATTER_TOOL_IDS),
        verbosity="low"
        if (output_schema is None and bool(set(canonical_tool_ids) & _DOCUMENT_TOOL_IDS))
        else None,
        provider_override=model_provider,
    )

    return Agent(
        name=db_agent.name,
        instructions=instructions,
        model=get_model_for_agent(db_agent.model_id, provider_override=model_provider),
        model_settings=model_settings,
        tools=tools,
        output_type=output_schema,
        output_guardrails=output_guardrails,
    )


def _get_db_agent_row(agent_id: str, kwargs: Dict[str, Any]) -> Optional[Any]:
    """Look up an active agent row by key from the unified agents table."""
    from src.models.sql.database import SessionLocal
    from src.lib.agent_studio.agent_service import get_agent_by_key

    db_user_id = _coerce_db_user_id(kwargs.get("db_user_id"))
    if db_user_id is None:
        db_user_id = _coerce_db_user_id(kwargs.get("user_id"))

    db = SessionLocal()
    try:
        return get_agent_by_key(db, agent_id, user_id=db_user_id)
    except Exception:
        logger.exception("[CatalogService] Failed DB lookup for agent '%s'", agent_id)
        return None
    finally:
        db.close()


def get_agent_by_id(agent_id: str, **kwargs: Any) -> Agent:
    """Create an agent by ID using the unified agents table only."""
    db_agent = _get_db_agent_row(agent_id, kwargs)
    if db_agent is None:
        raise ValueError(
            f"Unknown agent_id: {agent_id}. "
            "Agent must exist in the unified agents table."
        )

    built = _create_db_agent(db_agent, **kwargs)
    if built is None:
        raise ValueError(
            f"Agent '{agent_id}' exists but could not be built from unified spec. "
            "Check unified runtime spec fields."
        )

    return built


def get_agent_metadata(agent_id: str, **kwargs: Any) -> Dict[str, Any]:
    """Get metadata about a unified agent (display name, requirements, etc.).

    Args:
        agent_id: Unified agent key from `agents.agent_key`.

    Returns:
        Dictionary with agent metadata:
            - agent_id: The agent's catalog ID
            - display_name: Human-readable name
            - requires_document: Whether the agent needs a document context
            - required_params: List of required parameter names

    Raises:
        ValueError: If agent_id is not found in the unified agents table
    """
    db_agent = _get_db_agent_row(agent_id, dict(kwargs))
    if db_agent is not None:
        tool_ids = list(getattr(db_agent, "tool_ids", []) or [])
        required_params = _required_context_for_tool_ids(tool_ids)
        requires_document = "document_id" in required_params
        return {
            "agent_id": agent_id,
            "display_name": db_agent.name,
            "description": db_agent.description,
            "requires_document": requires_document,
            "required_params": required_params,
        }

    if agent_id == "task_input":
        return {
            "agent_id": agent_id,
            "display_name": "Initial Instructions",
            "description": "Define the curator's task that starts the flow",
            "requires_document": False,
            "required_params": [],
        }

    raise ValueError(
        f"Unknown agent_id: {agent_id}. "
        "Agent metadata is only available for unified agents table records."
    )


def list_available_agents(db_user_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """List active unified agents with metadata.

    Args:
        db_user_id: Optional DB user ID to apply private/project visibility.
            When omitted, only system agents are returned.
    """
    from src.models.sql.agent import Agent as AgentRecord
    from src.models.sql.database import SessionLocal

    db = SessionLocal()
    try:
        keys = [
            row[0]
            for row in db.query(AgentRecord.agent_key).filter(
                AgentRecord.is_active == True  # noqa: E712
            ).all()
        ]
    finally:
        db.close()

    metadata_kwargs: Dict[str, Any] = {}
    if db_user_id is not None:
        metadata_kwargs["db_user_id"] = db_user_id

    visible: List[Dict[str, Any]] = []
    for agent_id in keys:
        try:
            visible.append(get_agent_metadata(agent_id, **metadata_kwargs))
        except ValueError:
            continue
    return visible

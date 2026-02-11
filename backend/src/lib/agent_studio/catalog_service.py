"""
Prompt Catalog Service.

Retrieves agent prompts from the database for display in the Prompt Explorer.
Prompts are loaded at startup via the prompt cache and organized by category.

The catalog is organized by category (Routing, Extraction, Validation)
and includes both base prompts and MOD-specific rules.

**Database-backed**: All prompts now come from the prompt_templates table
via src.lib.prompts.cache. File parsing has been removed.

**Agent Registry**: Also provides agent instantiation for flow execution.
The registry maps agent IDs to factory functions with parameter metadata.
"""

import inspect
import logging
from typing import Any, Dict, List, Optional
from datetime import datetime

from agents import Agent
from src.lib.config.agent_loader import get_agent_definition, get_agent_by_folder

# Config-driven registry builder (loads from YAML + convention-based factory discovery)
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


class MissingRequiredParamError(ValueError):
    """Raised when a required parameter is missing for an agent factory."""
    pass


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
                "methods": ["search_genes", "get_gene_by_exact_symbol", "get_gene_by_id"],
                "description": "The Gene Agent uses these methods to validate gene identifiers and retrieve gene information.",
            },
            "allele": {
                "agent_name": "Allele Validation Agent",
                "methods": ["search_alleles", "get_allele_by_exact_symbol", "get_allele_by_id"],
                "description": "The Allele Agent uses these methods to validate allele/variant identifiers.",
            },
            "gene_expression": {
                "agent_name": "Gene Expression Extractor",
                "methods": ["search_genes", "get_gene_by_exact_symbol"],
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
        "source_file": "backend/src/lib/openai_agents/agents/orthologs_agent.py",
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
        "source_file": "backend/src/lib/openai_agents/agents/disease_agent.py",
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
        "source_file": "backend/src/lib/openai_agents/agents/chemical_agent.py",
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
        "source_file": "backend/src/lib/openai_agents/agents/gene_ontology_agent.py",
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
        "source_file": "backend/src/lib/openai_agents/agents/go_annotations_agent.py",
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
                    logger.warning(f"Failed to introspect {name}: {e}")

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

        # Special case: non-agent entries (like task_input) don't need database prompts
        if config.get("factory") is None:
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
            logger.warning(f"Skipping {agent_id}: no system prompt found in database")
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

def get_agent_by_id(agent_id: str, **kwargs: Any) -> Agent:
    """Create an agent by ID, passing only the parameters each factory accepts.

    This function provides a unified interface for flow execution while
    respecting the varying signatures of existing agent factories.

    Args:
        agent_id: Catalog ID (e.g., 'pdf', 'gene', 'disease')
        **kwargs: All available context. Common parameters include:
            - document_id: For document-aware agents (pdf, gene_expression)
            - user_id: For Weaviate tenant isolation
            - document_name, sections, hierarchy, abstract: PDF context
            - active_groups: List of active group IDs (e.g., ['SGD', 'MGI'])
            - format_type: For formatter agent

    Returns:
        Configured Agent instance

    Raises:
        ValueError: If agent_id is not in the registry
        MissingRequiredParamError: If required parameters are missing

    Example:
        # Flow executor passes all available context
        context = {
            "document_id": "doc123",
            "user_id": "user456",
            "active_groups": ["SGD", "MGI"],
        }

        # Registry filters to only what each factory needs:
        gene_agent = get_agent_by_id("gene", **context)
        # -> create_gene_agent(active_groups=["SGD", "MGI"])

        disease_agent = get_agent_by_id("disease", **context)
        # -> create_disease_agent()  # No params needed
    """
    if agent_id.startswith("ca_"):
        from src.lib.agent_studio.custom_agent_service import get_custom_agent_runtime_info
        from src.lib.prompts.context import PromptOverride, set_prompt_override, clear_prompt_override

        runtime_info = get_custom_agent_runtime_info(agent_id)
        if not runtime_info:
            raise ValueError(f"Unknown custom agent_id: {agent_id}")
        if not runtime_info.parent_exists:
            raise ValueError(
                f"Custom agent '{agent_id}' cannot run because parent agent "
                f"'{runtime_info.parent_agent_key}' is unavailable"
            )

        parent_kwargs = dict(kwargs)
        if not runtime_info.include_mod_rules:
            parent_kwargs["active_groups"] = []

        set_prompt_override(PromptOverride(
            content=runtime_info.custom_prompt,
            agent_name=runtime_info.parent_agent_key,
            custom_agent_id=str(runtime_info.custom_agent_uuid),
            mod_overrides=runtime_info.mod_prompt_overrides,
        ))
        try:
            return get_agent_by_id(runtime_info.parent_agent_key, **parent_kwargs)
        finally:
            clear_prompt_override()

    entry = AGENT_REGISTRY.get(agent_id)
    if not entry:
        valid_ids = list(AGENT_REGISTRY.keys())
        raise ValueError(f"Unknown agent_id: {agent_id}. Valid IDs: {valid_ids}")

    # Special case: non-agent entries (like task_input) don't have factories
    factory = entry.get("factory")
    if factory is None:
        raise ValueError(
            f"Agent '{agent_id}' is not an executable agent (no factory). "
            "This entry is for display purposes only (e.g., flow input nodes)."
        )

    # Validate required parameters before calling factory
    required_params = entry.get("required_params", [])
    missing = [p for p in required_params if p not in kwargs or kwargs[p] is None]
    if missing:
        raise MissingRequiredParamError(
            f"Agent '{agent_id}' requires: {', '.join(missing)}"
        )

    # Introspect factory signature to filter kwargs
    sig = inspect.signature(factory)
    valid_params = set(sig.parameters.keys())
    filtered_kwargs = {k: v for k, v in kwargs.items() if k in valid_params}

    return factory(**filtered_kwargs)


def get_agent_metadata(agent_id: str) -> Dict[str, Any]:
    """Get metadata about an agent (display name, requirements, etc.).

    Args:
        agent_id: Catalog ID (e.g., 'pdf', 'gene', 'disease')

    Returns:
        Dictionary with agent metadata:
            - agent_id: The agent's catalog ID
            - display_name: Human-readable name
            - requires_document: Whether the agent needs a document context
            - required_params: List of required parameter names

    Raises:
        ValueError: If agent_id is not in the registry
    """
    if agent_id.startswith("ca_"):
        from src.lib.agent_studio.custom_agent_service import get_custom_agent_runtime_info

        runtime_info = get_custom_agent_runtime_info(agent_id)
        if not runtime_info:
            raise ValueError(f"Unknown agent_id: {agent_id}")
        return {
            "agent_id": agent_id,
            "display_name": runtime_info.display_name,
            "requires_document": runtime_info.requires_document,
            "required_params": ["document_id"] if runtime_info.requires_document else [],
        }

    entry = AGENT_REGISTRY.get(agent_id)
    if not entry:
        raise ValueError(f"Unknown agent_id: {agent_id}")
    return {
        "agent_id": agent_id,
        "display_name": entry["name"],
        "requires_document": entry.get("requires_document", False),
        "required_params": entry.get("required_params", []),
    }


def list_available_agents() -> List[Dict[str, Any]]:
    """List all available agents with their metadata.

    Returns:
        List of agent metadata dictionaries, one per agent in the registry.
        Each dictionary contains: agent_id, display_name, requires_document,
        required_params.
    """
    return [get_agent_metadata(agent_id) for agent_id in AGENT_REGISTRY]

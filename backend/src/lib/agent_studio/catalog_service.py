"""
Prompt Catalog Service.

Retrieves agent prompts from the database for display in the Prompt Explorer.
Prompts are loaded at startup via the prompt cache and organized by category.

The catalog is organized by category (Routing, Extraction, Validation)
and includes both base prompts and group-specific rules.

**Database-backed**: All prompts now come from the prompt_templates table
via src.lib.prompts.cache. File parsing has been removed.

**Agent Registry**: Also provides metadata for flow execution and UI views.
Runtime instantiation resolves directly from unified DB-backed agent records.
"""

import asyncio
import errno
import importlib
import inspect
import json
import logging
import os
import sys
from pathlib import Path
from functools import lru_cache
from types import SimpleNamespace
from typing import Any, Callable, Dict, Iterator, List, Optional
from datetime import datetime
import re
from dataclasses import dataclass, replace

from agents import Agent
from src.lib.config.agent_loader import get_agent_definition, get_agent_by_folder

# Config-driven registry builder (loads metadata from YAML definitions)
from .registry_builder import build_agent_registry

from .models import (
    PromptInfo,
    AgentPrompts,
    PromptCatalog,
    GroupRuleInfo,
    AgentDocumentation,
    AgentCapability,
    DataSourceInfo,
)

logger = logging.getLogger(__name__)
_HOST_RUNTIME_SRC_DIR = Path(__file__).resolve().parents[2]
_HOST_RUNTIME_ROOT_DIR = _HOST_RUNTIME_SRC_DIR.parent
_RECORD_EVIDENCE_RUNTIME_NOTE = (
    "EVIDENCE VERIFICATION RULES:\n"
    "- Call `record_evidence` once for each distinct evidence quote you intend to keep.\n"
    "- Use multiple evidence records when one quote alone does not fully support the retained item or claim.\n"
    "- Prefer complementary quotes when different passages establish different parts of the support (for example identity, condition, effect, or scope).\n"
    "- Each claimed quote should be a single contiguous excerpt. A short multi-sentence passage is fine when that is the tightest support, but do not stitch together disconnected text.\n"
    "- Pass the entity label, the exact `chunk_id` from prior document search/read results, and the quote you believe appears in that chunk.\n"
    "- If the tool returns `not_found`, inspect the returned chunk preview, retry once with corrected text from that chunk when appropriate, and drop the evidence if it still does not verify.\n"
    "- Only persist evidence records that came back `verified`.\n"
)
_INLINE_PACKAGE_TOOL_IDS = frozenset({
    "record_evidence",
    "search_document",
    "read_section",
    "read_subsection",
})


def _is_thread_exhaustion_error(exc: BaseException) -> bool:
    """Recognize thread-creation failures across Python/runtime variants."""

    if isinstance(exc, OSError) and exc.errno == errno.EAGAIN:
        return True

    message = str(exc).lower()
    return "can't start new thread" in message or "cannot start new thread" in message


def get_prompt_key_for_agent(registry_agent_id: str) -> str:
    """Resolve a registry agent ID/alias to canonical prompt cache key (folder name)."""
    if registry_agent_id == "task_input":
        return "task_input"

    # Canonical key is the resolved agent bundle folder name.
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


# Agent metadata registry - built dynamically from layered YAML configurations.
# Source of truth: runtime packages plus config/agents overrides
# Factory functions: discovered via convention (create_{folder}_agent)
AGENT_REGISTRY = build_agent_registry()


# Tool metadata registry - provides detailed documentation about each tool
# available to agents, including parameters, methods, and usage examples.
CURATED_TOOL_REGISTRY: Dict[str, Dict[str, Any]] = {
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
                    "description": "Filter by group/provider: MGI, FB, WB, ZFIN, RGD, SGD, HGNC.",
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
                "description": "List all Alliance group data providers with their taxon mappings.",
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
    "record_evidence": {
        "name": "Record Evidence",
        "description": "Verify one contiguous claimed quote against a specific PDF chunk before persisting evidence.",
        "category": "PDF Extraction",
        "source_file": "backend/src/lib/openai_agents/tools/record_evidence.py",
        "documentation": {
            "summary": "Checks whether a claimed quote appears in a known chunk and returns a verified verbatim quote plus locator metadata when it does. Use separate calls for multiple complementary quotes when one quote is not enough.",
            "parameters": [
                {
                    "name": "entity",
                    "type": "string",
                    "required": True,
                    "description": "Entity label associated with this evidence record.",
                },
                {
                    "name": "chunk_id",
                    "type": "string",
                    "required": True,
                    "description": "Chunk identifier returned by search_document or read-section tools.",
                },
                {
                    "name": "claimed_quote",
                    "type": "string",
                    "required": True,
                    "description": "One contiguous quote to verify against the target chunk. It may be one sentence or a short multi-sentence passage when that is the strongest support.",
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
        "name": "Transfer to General PDF Extraction Agent",
        "description": "Route query to the general PDF extraction agent for document extraction.",
        "category": "Routing",
        "source_file": "backend/src/lib/openai_agents/agents/supervisor_agent.py",
        "documentation": {
            "summary": "Internal supervisor tool for routing document-related queries to the general PDF extraction agent.",
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
            "summary": "Creates a CSV file from structured row data and returns a download link.",
            "parameters": [
                {
                    "name": "data_json",
                    "type": "string",
                    "required": True,
                    "description": "JSON array string of row objects to convert to CSV.",
                },
                {
                    "name": "filename",
                    "type": "string",
                    "required": True,
                    "description": "Output filename (without extension).",
                },
                {
                    "name": "columns",
                    "type": "string",
                    "required": False,
                    "description": "Optional JSON array string listing column names in output order.",
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


_REPO_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_CATALOG_CONTEXT = {
    "document_id": "tool-catalog-document-id",
    "user_id": "tool-catalog-user-id",
    "database_url": "postgresql://tool-catalog.example/db",
}


def _resolve_packages_dir() -> Path:
    """Use the runtime packages mount when present, otherwise the repo packages dir."""
    from src.lib.packages.paths import get_runtime_packages_dir

    runtime_packages_dir = get_runtime_packages_dir()
    if runtime_packages_dir.exists():
        return runtime_packages_dir
    return _REPO_ROOT / "packages"


class _LazyDictProxy(dict):
    """Lazy dict wrapper for runtime registries that are expensive to build."""

    def __init__(self, loader: Callable[[], Dict[str, Dict[str, Any]]]) -> None:
        super().__init__()
        self._loader = loader
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        super().clear()
        super().update(self._loader())
        self._loaded = True

    def reset(self) -> None:
        self._loaded = False
        super().clear()

    def __getitem__(self, key: str) -> Dict[str, Any]:
        self._ensure_loaded()
        return super().__getitem__(key)

    def __iter__(self) -> Iterator[str]:
        self._ensure_loaded()
        return super().__iter__()

    def __len__(self) -> int:
        self._ensure_loaded()
        return super().__len__()

    def __contains__(self, key: object) -> bool:
        self._ensure_loaded()
        return super().__contains__(key)

    def get(self, key: str, default: Optional[Any] = None) -> Optional[Dict[str, Any]]:
        self._ensure_loaded()
        return super().get(key, default)

    def items(self):
        self._ensure_loaded()
        return super().items()

    def keys(self):
        self._ensure_loaded()
        return super().keys()

    def values(self):
        self._ensure_loaded()
        return super().values()

    def copy(self) -> Dict[str, Dict[str, Any]]:
        self._ensure_loaded()
        return dict(super().items())

    def __repr__(self) -> str:
        self._ensure_loaded()
        return super().__repr__()


@lru_cache(maxsize=1)
def _load_package_tool_registry():
    """Load the merged package-backed tool registry for live runtime/catalog use.

    Tests should patch this boundary directly, or call
    clear_package_tool_runtime_caches() after patching deeper loader dependencies.
    """
    from src.lib.packages.paths import get_runtime_overrides_path
    from src.lib.packages.tool_registry import load_tool_registry

    overrides_path = get_runtime_overrides_path()
    load_kwargs: Dict[str, Any] = {}
    if overrides_path.exists():
        load_kwargs["overrides_path"] = overrides_path

    return load_tool_registry(_resolve_packages_dir(), **load_kwargs)


def _get_package_tool_binding(tool_id: str):
    """Resolve one merged package tool binding by runtime tool ID."""
    return _load_package_tool_registry().get(tool_id)


@lru_cache(maxsize=1)
def _get_package_tool_runner():
    """Create a package tool runner bound to the merged runtime registry."""
    from src.lib.packages.package_runner import PackageToolRunner

    return PackageToolRunner(tool_registry=_load_package_tool_registry())


def _extend_sys_path_for_package(package: Any) -> None:
    """Make one loaded package and public runtime helpers importable."""
    python_package_root = (
        package.package_path / package.manifest.python_package_root
    ).expanduser().resolve(strict=False)
    for candidate in (
        _HOST_RUNTIME_SRC_DIR,
        python_package_root.parent,
        python_package_root,
        package.package_path,
    ):
        candidate_text = str(candidate)
        if candidate_text not in sys.path:
            sys.path.insert(0, candidate_text)

    host_runtime_root_text = str(_HOST_RUNTIME_ROOT_DIR)
    if host_runtime_root_text not in sys.path:
        # Keep the backend package root available for public runtime helpers that
        # lazily import ``src.lib.*`` modules, without outranking package-local paths.
        sys.path.append(host_runtime_root_text)


def _get_loaded_package_for_binding(binding: Any) -> Any:
    """Look up the loaded package that owns one merged tool binding."""
    package = _load_package_tool_registry().package_registry.get_package(
        binding.source.package_id
    )
    if package is None:
        raise ValueError(
            f"Package '{binding.source.package_id}' is not available for tool '{binding.tool_id}'"
        )
    return package


def _import_package_binding_target(binding: Any) -> Any:
    """Import the package-declared callable or factory for one binding."""
    package = _get_loaded_package_for_binding(binding)
    _extend_sys_path_for_package(package)

    module_name, attribute_name = binding.import_path.split(":", 1)
    module = importlib.import_module(module_name)
    return getattr(module, attribute_name)


def _binding_context_payload(
    binding: Any,
    execution_context: Optional["ToolExecutionContext"] = None,
) -> Dict[str, Any]:
    """Build the context payload used for factories and runner execution."""
    if execution_context is None:
        values = dict(_DEFAULT_CATALOG_CONTEXT)
    else:
        values = {
            "document_id": execution_context.document_id,
            "user_id": execution_context.user_id,
            "database_url": execution_context.database_url,
        }

    required_context = set(binding.required_context)
    return {
        key: value
        for key, value in values.items()
        if key in required_context and value not in (None, "")
    }


def _instantiate_package_tool(
    binding: Any,
    *,
    execution_context: Optional["ToolExecutionContext"] = None,
) -> Any:
    """Instantiate the package-exported SDK tool for metadata/runtime wrapping."""
    imported = _import_package_binding_target(binding)
    if binding.import_attribute_kind == "callable_factory":
        if not callable(imported):
            raise TypeError(f"Imported factory '{binding.import_path}' is not callable")
        return imported(_binding_context_payload(binding, execution_context))
    return imported


def _should_execute_package_tool_inline(binding: Any) -> bool:
    """Return whether one package-backed tool should execute in the host runtime."""
    return getattr(binding, "tool_id", None) in _INLINE_PACKAGE_TOOL_IDS


def _decode_tool_input(tool_id: str, input_str: str) -> Dict[str, Any]:
    """Decode the SDK tool input payload into kwargs for the package runner."""
    raw_payload = (input_str or "").strip()
    if not raw_payload:
        return {}

    try:
        parsed = json.loads(raw_payload)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Tool '{tool_id}' received invalid JSON input: {exc}"
        ) from exc

    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        raise ValueError(
            f"Tool '{tool_id}' input must decode to a JSON object"
        )
    return parsed


def _resolve_package_tool(tool_id: str, execution_context: "ToolExecutionContext") -> Any:
    """Wrap one package-backed tool in a runtime-compatible SDK tool object."""
    binding = _get_package_tool_binding(tool_id)
    if binding is None:
        raise ValueError(f"Unknown tool binding '{tool_id}'")

    base_tool = _instantiate_package_tool(binding, execution_context=execution_context)
    if not hasattr(base_tool, "on_invoke_tool"):
        raise ValueError(
            f"Package tool '{tool_id}' does not expose on_invoke_tool"
        )

    tracker = execution_context.tool_tracker
    base_on_invoke_tool = base_tool.on_invoke_tool

    async def _runner_invoke(ctx, input_str):
        if tracker:
            tracker.record_call(tool_id)

        if _should_execute_package_tool_inline(binding):
            inline_ctx = ctx or SimpleNamespace(tool_name=tool_id)
            result = base_on_invoke_tool(inline_ctx, input_str)
            if inspect.isawaitable(result):
                return await result
            return result

        runner = _get_package_tool_runner()
        decoded_kwargs = _decode_tool_input(tool_id, input_str)
        execute_kwargs = {
            "kwargs": decoded_kwargs,
            "context": _binding_context_payload(binding, execution_context),
        }

        try:
            result = await asyncio.to_thread(
                runner.execute_tool,
                tool_id,
                **execute_kwargs,
            )
        except (RuntimeError, OSError) as exc:
            if not _is_thread_exhaustion_error(exc):
                raise
            logger.warning(
                "Falling back to inline package tool execution for %s after thread exhaustion: %s",
                tool_id,
                exc,
            )
            result = runner.execute_tool(
                tool_id,
                **execute_kwargs,
            )
        if not result.ok:
            error_message = result.error.message if result.error else "Unknown package tool error"
            raise RuntimeError(
                f"Package tool '{tool_id}' execution failed: {error_message}"
            )
        return result.result

    return replace(base_tool, on_invoke_tool=_runner_invoke)


def _tool_category_for_binding(binding: Any) -> str:
    """Infer a coarse tool category when curated metadata does not provide one."""
    if binding.tool_id in {"agr_curation_query", "curation_db_sql"}:
        return "Database"
    if binding.tool_id in {"search_document", "read_section", "read_subsection", "record_evidence"}:
        return "Document"
    if binding.tool_id.startswith("save_"):
        return "Output"
    if binding.tool_id.endswith("_api_call"):
        return "API"
    return "Tool"


def _merge_tool_metadata(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Merge curated UI metadata on top of package-backed tool metadata."""
    merged = dict(base)
    for key, value in override.items():
        if (
            key == "documentation"
            and isinstance(value, dict)
            and isinstance(merged.get("documentation"), dict)
        ):
            documentation = dict(merged["documentation"])
            documentation.update(value)
            merged["documentation"] = documentation
            continue
        merged[key] = value

    for preserved_key in (
        "source_file",
        "binding_kind",
        "required_context",
        "package_backed",
        "package_id",
        "package_version",
        "package_display_name",
        "package_export_name",
    ):
        if preserved_key in base:
            merged[preserved_key] = base[preserved_key]

    return merged


def _build_tool_registry() -> Dict[str, Dict[str, Any]]:
    """
    Build the Agent Studio tool catalog from package bindings plus curated metadata.

    Returns:
        Dict mapping tool_id to metadata dict
    """
    from .tool_introspection import introspect_tool

    registry: Dict[str, Dict[str, Any]] = {}
    for binding in _load_package_tool_registry().bindings:
        try:
            tool = _instantiate_package_tool(binding)
            metadata = introspect_tool(tool)
            parameters = [
                {"name": name, **param_info}
                for name, param_info in metadata.parameters.items()
            ]
            registry[binding.tool_id] = {
                "name": metadata.name or binding.tool_id,
                "description": binding.description or metadata.description,
                "category": _tool_category_for_binding(binding),
                "source_file": binding.source.source_file or metadata.source_file,
                "documentation": {
                    "summary": binding.description or metadata.description,
                    "parameters": parameters,
                },
                "methods": None,
                "agent_methods": None,
                "binding_kind": binding.binding_kind.value,
                "required_context": list(binding.required_context),
                "package_backed": True,
                "package_id": binding.source.package_id,
                "package_version": binding.source.package_version,
                "package_display_name": binding.source.package_display_name,
                "package_export_name": binding.source.export_name,
            }
        except Exception as exc:
            logger.warning(
                "Failed to build package-backed tool catalog entry for %s: %s",
                binding.tool_id,
                exc,
            )
            registry[binding.tool_id] = {
                "name": binding.tool_id,
                "description": binding.description,
                "category": _tool_category_for_binding(binding),
                "source_file": binding.source.source_file,
                "documentation": {
                    "summary": binding.description,
                    "parameters": [],
                },
                "methods": None,
                "agent_methods": None,
                "binding_kind": binding.binding_kind.value,
                "required_context": list(binding.required_context),
                "package_backed": True,
                "package_id": binding.source.package_id,
                "package_version": binding.source.package_version,
                "package_display_name": binding.source.package_display_name,
                "package_export_name": binding.source.export_name,
            }

    for tool_id, metadata in CURATED_TOOL_REGISTRY.items():
        if tool_id in registry:
            registry[tool_id] = _merge_tool_metadata(registry[tool_id], metadata)
        else:
            registry[tool_id] = dict(metadata)

    for tool_id, metadata in TOOL_OVERRIDES.items():
        if tool_id in registry:
            registry[tool_id] = _merge_tool_metadata(registry[tool_id], metadata)
        else:
            registry[tool_id] = dict(metadata)

    return registry


def _build_method_tool_entries() -> Dict[str, Dict[str, Any]]:
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


def _build_tool_bindings() -> Dict[str, Dict[str, Any]]:
    """Build the live runtime binding table from the merged package registry."""
    bindings: Dict[str, Dict[str, Any]] = {}
    for binding in _load_package_tool_registry().bindings:
        bindings[binding.tool_id] = {
            "binding": binding.binding_kind.value,
            "required_context": list(binding.required_context),
            "resolver": (
                lambda context, resolved_tool_id=binding.tool_id: _resolve_package_tool(
                    resolved_tool_id, context
                )
            ),
            "package_id": binding.source.package_id,
            "package_version": binding.source.package_version,
            "package_export_name": binding.source.export_name,
        }
    return bindings


TOOL_REGISTRY = _LazyDictProxy(_build_tool_registry)
METHOD_TOOL_ENTRIES = _LazyDictProxy(_build_method_tool_entries)
TOOL_BINDINGS = _LazyDictProxy(_build_tool_bindings)


def clear_package_tool_runtime_caches() -> None:
    """Reset cached package-tool loaders and lazy registries for tests/runtime refresh."""
    for cached_func in (_load_package_tool_registry, _get_package_tool_runner):
        cache_clear = getattr(cached_func, "cache_clear", None)
        if callable(cache_clear):
            cache_clear()

    for registry in (TOOL_REGISTRY, METHOD_TOOL_ENTRIES, TOOL_BINDINGS):
        reset = getattr(registry, "reset", None)
        if callable(reset):
            reset()


def get_tool_registry() -> Dict[str, Dict[str, Any]]:
    """Return a copy of the lazily materialized tool registry."""
    return TOOL_REGISTRY.copy()


# =============================================================================
# Method-Level Tool Entries
# =============================================================================
# These entries provide first-class access to individual methods of multi-method
# tools like agr_curation_query. When displayed in the UI, users see these
# descriptive method names instead of the underlying tool mechanism.

@dataclass(frozen=True)
class ToolExecutionContext:
    """Context used to resolve runtime tool factories deterministically."""

    document_id: Optional[str] = None
    user_id: Optional[str] = None
    database_url: Optional[str] = None
    tool_tracker: Optional[Any] = None

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
            available_groups=[],
            last_updated=datetime.utcnow(),
        )

    # Get all active prompts from cache
    all_prompts = get_all_active_prompts()

    # Group prompts by agent_name for easy lookup
    # Key format: agent_name:prompt_type:group_id_or_base
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
    available_groups = set()

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
                has_group_rules=False,
                group_rules={},
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

        # Build group-rules dict from database prompts
        group_rules: Dict[str, GroupRuleInfo] = {}
        for group_id, prompt in agent_prompts.get("group_rules", {}).items():
            available_groups.add(group_id)
            group_rules[group_id] = GroupRuleInfo(
                group_id=group_id,
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
            has_group_rules=bool(group_rules),
            group_rules=group_rules,
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
        available_groups=sorted(available_groups),
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
                f"{len(self._catalog.available_groups)} groups"
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

    def get_combined_prompt(self, agent_id: str, group_id: str) -> Optional[str]:
        """
        Get the combined prompt for an agent with group rules injected.

        Args:
            agent_id: Agent identifier
            group_id: Group identifier (for example "WB", "FB")

        Returns:
            Combined prompt string, or None if agent/group not found
        """
        agent = self.get_agent(agent_id)
        if not agent:
            return None

        has_group_rules = bool(getattr(agent, "has_group_rules", getattr(agent, "has_mod_rules", False)))
        group_rules = getattr(agent, "group_rules", getattr(agent, "mod_rules", {}))
        if not has_group_rules or group_id not in group_rules:
            return agent.base_prompt

        group_rule = group_rules[group_id]
        combined = f"""{agent.base_prompt}

## GROUP-SPECIFIC RULES

The following rules are specific to {group_id}:

{group_rule.content}

## END GROUP-SPECIFIC RULES
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
    group_overrides: Optional[Dict[str, str]] = None,
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
    raw_override_map = group_overrides if group_overrides is not None else mod_overrides
    for raw_group, raw_content in (raw_override_map or {}).items():
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
                group_overrides=dict(getattr(db_agent, "mod_prompt_overrides", {}) or {}),
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

    if "record_evidence" in canonical_tool_ids:
        instructions = instructions.rstrip() + "\n\n" + _RECORD_EVIDENCE_RUNTIME_NOTE

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


def _merge_registry_required_params(
    agent_id: str,
    *,
    required_params: List[str],
    requires_document: bool,
) -> tuple[List[str], bool]:
    """Merge config-owned runtime requirements for shipped agents into DB metadata."""

    registry_entry = AGENT_REGISTRY.get(agent_id) or {}
    merged_required_params: List[str] = []
    seen_params: set[str] = set()

    for value in [*required_params, *(registry_entry.get("required_params", []) or [])]:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen_params:
            continue
        seen_params.add(normalized)
        merged_required_params.append(normalized)

    merged_requires_document = bool(
        requires_document
        or registry_entry.get("requires_document", False)
        or "document_id" in merged_required_params
    )

    return merged_required_params, merged_requires_document


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
    from src.lib.config.agent_loader import get_agent_definition

    agent_definition = get_agent_definition(agent_id)
    curation_metadata = {
        "adapter_key": agent_definition.curation.adapter_key,
        "launchable": agent_definition.curation.launchable,
    } if agent_definition is not None else None
    db_agent = _get_db_agent_row(agent_id, dict(kwargs))
    if db_agent is not None:
        tool_ids = list(getattr(db_agent, "tool_ids", []) or [])
        required_params = _required_context_for_tool_ids(tool_ids)
        required_params, requires_document = _merge_registry_required_params(
            agent_id,
            required_params=required_params,
            requires_document="document_id" in required_params,
        )
        return {
            "agent_id": agent_id,
            "display_name": db_agent.name,
            "description": db_agent.description,
            "requires_document": requires_document,
            "required_params": required_params,
            "curation": curation_metadata,
        }

    if agent_id == "task_input":
        return {
            "agent_id": agent_id,
            "display_name": "Initial Instructions",
            "description": "Define the curator's task that starts the flow",
            "requires_document": False,
            "required_params": [],
            "curation": None,
        }

    if agent_definition is not None:
        return {
            "agent_id": agent_id,
            "display_name": agent_definition.name,
            "description": agent_definition.description,
            "requires_document": agent_definition.requires_document,
            "required_params": list(agent_definition.required_params),
            "curation": curation_metadata,
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

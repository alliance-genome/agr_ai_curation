"""
Tool Definitions for Prompt Explorer Diagnostic Tools.

This module registers all diagnostic tools available to Opus.
Tools are direct copies of those used by specialist agents,
giving Opus the same capabilities for trace troubleshooting.

Tool Categories:
- database: SQL query tools (curation_db_sql)
- api: REST API tools (agr_curation_query, chebi_api_call, quickgo_api_call, go_api_call)
- prompt: Prompt inspection tools (get_prompt)
"""

import logging
import os
from typing import Any, Callable, Dict, List, Optional

from agents import FunctionTool

from .registry import DiagnosticToolRegistry

logger = logging.getLogger(__name__)


def _unwrap_function_tool(tool: FunctionTool) -> Callable:
    """
    Extract the underlying function from a FunctionTool.

    The OpenAI Agents SDK's @function_tool decorator wraps functions in a
    FunctionTool object. This extracts the original callable from the closure
    chain so it can be invoked directly.

    Args:
        tool: A FunctionTool instance created by @function_tool decorator

    Returns:
        The original function that was decorated

    Raises:
        ValueError: If the underlying function cannot be extracted
    """
    try:
        # The closure chain is:
        # tool.on_invoke_tool -> _on_invoke_tool (closure[0]) -> _on_invoke_tool_impl
        # _on_invoke_tool_impl has closure[1] = original function
        impl_func = tool.on_invoke_tool.__closure__[0].cell_contents
        original_func = impl_func.__closure__[1].cell_contents
        if callable(original_func):
            return original_func
    except (AttributeError, IndexError, TypeError) as e:
        logger.error(f"Failed to unwrap FunctionTool: {e}")

    raise ValueError(
        f"Could not extract underlying function from FunctionTool '{tool.name}'. "
        "The OpenAI Agents SDK structure may have changed."
    )


def _create_agr_curation_handler():
    """
    Create handler for AGR Curation Database queries.

    This wraps the existing agr_curation_query tool from OpenAI Agents.
    """
    from src.lib.openai_agents.tools.agr_curation import agr_curation_query as agr_tool

    # Extract the underlying function from the FunctionTool wrapper
    agr_curation_query = _unwrap_function_tool(agr_tool)

    def handler(
        method: str,
        gene_symbol: Optional[str] = None,
        gene_id: Optional[str] = None,
        allele_symbol: Optional[str] = None,
        allele_id: Optional[str] = None,
        data_provider: Optional[str] = None,
        taxon_id: Optional[str] = None,
        term: Optional[str] = None,
        go_aspect: Optional[str] = None,
        exact_match: bool = False,
        include_synonyms: bool = True,
        limit: Optional[int] = None,
        force: bool = False,
        force_reason: Optional[str] = None
    ) -> Dict[str, Any]:
        """Execute AGR curation query and return result as dict."""
        result = agr_curation_query(
            method=method,
            gene_symbol=gene_symbol,
            gene_id=gene_id,
            allele_symbol=allele_symbol,
            allele_id=allele_id,
            data_provider=data_provider,
            taxon_id=taxon_id,
            term=term,
            go_aspect=go_aspect,
            exact_match=exact_match,
            include_synonyms=include_synonyms,
            limit=limit,
            force=force,
            force_reason=force_reason
        )
        return result.model_dump()

    return handler


def _create_sql_query_handler(database_url: str, tool_name: str):
    """
    Create handler for SQL query tools.

    Uses the existing sql_query tool factory from OpenAI Agents.
    """
    from src.lib.openai_agents.tools.sql_query import create_sql_query_tool

    # Create the bound tool and unwrap it
    sql_tool_wrapped = create_sql_query_tool(database_url, tool_name)
    sql_tool = _unwrap_function_tool(sql_tool_wrapped)

    def handler(query: str) -> Dict[str, Any]:
        """Execute SQL query and return result as dict."""
        result = sql_tool(query=query)
        return result.model_dump()

    return handler


def _create_rest_api_handler(allowed_domains: List[str], tool_name: str):
    """
    Create handler for REST API tools.

    Uses the existing rest_api tool factory from OpenAI Agents.
    """
    from src.lib.openai_agents.tools.rest_api import create_rest_api_tool

    # Create the bound tool and unwrap it
    rest_tool_wrapped = create_rest_api_tool(allowed_domains, tool_name)
    rest_tool = _unwrap_function_tool(rest_tool_wrapped)

    def handler(
        url: str,
        method: str = "GET",
        headers_json: Optional[str] = None,
        body_json: Optional[str] = None
    ) -> Dict[str, Any]:
        """Execute REST API call and return result as dict."""
        result = rest_tool(
            url=url,
            method=method,
            headers_json=headers_json,
            body_json=body_json
        )
        return result.model_dump()

    return handler


def _create_get_prompt_handler():
    """
    Create handler for prompt inspection tool.

    Uses the PromptCatalogService to fetch agent prompts.
    """
    from src.lib.agent_studio.catalog_service import get_prompt_catalog

    def handler(agent_id: str, mod_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Get an agent's prompt from the catalog.

        Args:
            agent_id: Agent identifier (e.g., "supervisor", "gene", "pdf")
            mod_id: Optional MOD identifier for MOD-specific rules (e.g., "WB", "FB")

        Returns:
            Dict with prompt content and metadata
        """
        catalog = get_prompt_catalog()
        agent = catalog.get_agent(agent_id)

        if not agent:
            available_agents = []
            for cat in catalog.catalog.categories:
                for a in cat.agents:
                    available_agents.append(a.agent_id)
            return {
                "status": "error",
                "message": f"Agent '{agent_id}' not found",
                "available_agents": available_agents
            }

        # Get the prompt (with MOD rules if specified)
        if mod_id:
            prompt = catalog.get_combined_prompt(agent_id, mod_id)
            has_mod_rules = mod_id in agent.mod_rules if agent.mod_rules else False
        else:
            prompt = agent.base_prompt
            has_mod_rules = False

        return {
            "status": "ok",
            "agent_id": agent_id,
            "agent_name": agent.agent_name,
            "description": agent.description,
            "prompt": prompt,
            "source_file": agent.source_file,
            "has_mod_rules": agent.has_mod_rules,
            "mod_id_applied": mod_id if has_mod_rules else None,
            "available_mods": list(agent.mod_rules.keys()) if agent.mod_rules else [],
            "tools": agent.tools
        }

    return handler


def register_all_tools(registry: DiagnosticToolRegistry) -> None:
    """
    Register all diagnostic tools with the registry.

    This is called automatically by get_diagnostic_tools_registry()
    on first access.
    """
    logger.info("Registering diagnostic tools...")

    # -------------------------------------------------------------------------
    # 1. AGR Curation Query Tool
    # -------------------------------------------------------------------------
    registry.register(
        name="agr_curation_query",
        description="""Query the Alliance Genome Resources Curation Database.

Available methods:
- search_genes: Search genes by symbol (LIKE search, partial matches)
- get_gene_by_exact_symbol: Get gene by exact symbol match
- get_gene_by_id: Get gene by CURIE (e.g., WB:WBGene00006963)
- search_alleles: Search alleles by symbol (LIKE search)
- get_allele_by_exact_symbol: Get allele by exact symbol
- get_allele_by_id: Get allele by CURIE
- get_data_providers: List data providers (MGI, FB, WB, etc.)
- search_anatomy_terms: Search anatomy ontology terms
- search_life_stage_terms: Search life stage ontology terms
- search_go_terms: Search GO terms

Use data_provider to filter by species: MGI (mouse), FB (fly), WB (worm), ZFIN (zebrafish), RGD (rat), SGD (yeast), HGNC (human).""",
        input_schema={
            "type": "object",
            "properties": {
                "method": {
                    "type": "string",
                    "description": "Query method to execute",
                    "enum": [
                        "search_genes", "get_gene_by_exact_symbol", "get_gene_by_id",
                        "search_alleles", "get_allele_by_exact_symbol", "get_allele_by_id",
                        "get_data_providers",
                        "search_anatomy_terms", "search_life_stage_terms", "search_go_terms"
                    ]
                },
                "gene_symbol": {"type": "string", "description": "Gene symbol to search"},
                "gene_id": {"type": "string", "description": "Gene CURIE for direct lookup"},
                "allele_symbol": {"type": "string", "description": "Allele symbol to search"},
                "allele_id": {"type": "string", "description": "Allele CURIE for direct lookup"},
                "data_provider": {
                    "type": "string",
                    "description": "Filter by species (MGI, FB, WB, ZFIN, RGD, SGD, HGNC)",
                    "enum": ["MGI", "FB", "WB", "ZFIN", "RGD", "SGD", "HGNC"]
                },
                "taxon_id": {"type": "string", "description": "NCBITaxon ID (alternative to data_provider)"},
                "term": {"type": "string", "description": "Search term for ontology searches"},
                "go_aspect": {
                    "type": "string",
                    "description": "GO aspect filter",
                    "enum": ["molecular_function", "biological_process", "cellular_component"]
                },
                "exact_match": {"type": "boolean", "description": "Require exact match (default: false)"},
                "include_synonyms": {"type": "boolean", "description": "Search synonyms (default: true)"},
                "limit": {"type": "integer", "description": "Max results (default: 100, max: 500)"},
                "force": {"type": "boolean", "description": "Skip symbol validation (default: false)"},
                "force_reason": {"type": "string", "description": "Reason for skipping validation (required if force=true)"}
            },
            "required": ["method"]
        },
        handler=_create_agr_curation_handler(),
        category="api",
        tags=["gene", "allele", "ontology", "alliance"]
    )
    logger.debug("Registered: agr_curation_query")

    # -------------------------------------------------------------------------
    # 2. Curation Database SQL Tool
    # -------------------------------------------------------------------------
    curation_db_url = os.getenv("CURATION_DB_URL")
    if curation_db_url:
        registry.register(
            name="curation_db_sql",
            description="""Execute read-only SQL queries against the Alliance Curation Database.

This gives you direct access to the curation database schema. Use this to:
- Explore database schema (SELECT * FROM information_schema.tables/columns)
- Run diagnostic queries to understand data patterns
- Verify query results from other tools
- Deep dive into specific records

ONLY SELECT queries are allowed. The database contains gene, allele, annotation, and ontology data.""",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "SQL SELECT query to execute"
                    }
                },
                "required": ["query"]
            },
            handler=_create_sql_query_handler(curation_db_url, "curation_db_sql"),
            category="database",
            tags=["sql", "curation", "diagnostic"]
        )
        logger.debug("Registered: curation_db_sql")
    else:
        logger.warning("CURATION_DB_URL not set - curation_db_sql tool not available")

    # -------------------------------------------------------------------------
    # 3. ChEBI API Tool
    # -------------------------------------------------------------------------
    registry.register(
        name="chebi_api_call",
        description="""Query the ChEBI (Chemical Entities of Biological Interest) REST API.

Base URL: https://www.ebi.ac.uk/chebi

Key endpoints:
- Search: GET /backend/api/public/es_search/?term={term}
- Compound details: GET /backend/api/public/compound/{chebi_id}/
- Parent terms: GET /backend/api/public/ontology/parents/{chebi_id}/
- Child terms: GET /backend/api/public/ontology/children/{chebi_id}/

ChEBI IDs are numeric (e.g., 17234 for D-glucose). Cite as CHEBI:17234.""",
        input_schema={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Full URL to query (must be on ebi.ac.uk domain)"
                },
                "method": {
                    "type": "string",
                    "description": "HTTP method (default: GET)",
                    "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"],
                    "default": "GET"
                },
                "headers_json": {
                    "type": "string",
                    "description": "Optional JSON string for request headers"
                },
                "body_json": {
                    "type": "string",
                    "description": "Optional JSON string for request body"
                }
            },
            "required": ["url"]
        },
        handler=_create_rest_api_handler(
            ["ebi.ac.uk", "www.ebi.ac.uk"],
            "chebi_api_call"
        ),
        category="api",
        tags=["chemical", "ontology", "chebi", "rest"]
    )
    logger.debug("Registered: chebi_api_call")

    # -------------------------------------------------------------------------
    # 4. QuickGO API Tool (GO Terms)
    # -------------------------------------------------------------------------
    registry.register(
        name="quickgo_api_call",
        description="""Query the QuickGO REST API for Gene Ontology term information.

Base URL: https://www.ebi.ac.uk/QuickGO/services

Key endpoints:
- Search terms: GET /ontology/go/search?query={term}
- Term info: GET /ontology/go/terms/{GO:ID}
- Complete info: GET /ontology/go/terms/{GO:ID}/complete
- Children: GET /ontology/go/terms/{GO:ID}/children
- Ancestors: GET /ontology/go/terms/{GO:ID}/ancestors
- Descendants: GET /ontology/go/terms/{GO:ID}/descendants

GO IDs use format GO:0003677. Three aspects: molecular_function, biological_process, cellular_component.""",
        input_schema={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Full URL to query (must be on ebi.ac.uk domain)"
                },
                "method": {
                    "type": "string",
                    "description": "HTTP method (default: GET)",
                    "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"],
                    "default": "GET"
                },
                "headers_json": {
                    "type": "string",
                    "description": "Optional JSON string for request headers"
                },
                "body_json": {
                    "type": "string",
                    "description": "Optional JSON string for request body"
                }
            },
            "required": ["url"]
        },
        handler=_create_rest_api_handler(
            ["ebi.ac.uk", "www.ebi.ac.uk"],
            "quickgo_api_call"
        ),
        category="api",
        tags=["go", "ontology", "quickgo", "rest"]
    )
    logger.debug("Registered: quickgo_api_call")

    # -------------------------------------------------------------------------
    # 5. GO Annotations API Tool
    # -------------------------------------------------------------------------
    registry.register(
        name="go_api_call",
        description="""Query the Gene Ontology Consortium API for gene annotations.

Base URL: https://api.geneontology.org/api

Key endpoint:
- Gene annotations: GET /bioentity/gene/{gene_id}/function

Gene IDs use Alliance format: WB:WBGene00000898, HGNC:11998, MGI:123456

Returns GO annotations with evidence codes:
- Manual (high quality): IDA, IMP, IPI, IGI, ISS
- Automatic (lower confidence): IEA, IBA

Each annotation includes: GO term, evidence code, assigned_by (curation source).""",
        input_schema={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Full URL to query (must be on geneontology.org domain)"
                },
                "method": {
                    "type": "string",
                    "description": "HTTP method (default: GET)",
                    "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"],
                    "default": "GET"
                },
                "headers_json": {
                    "type": "string",
                    "description": "Optional JSON string for request headers"
                },
                "body_json": {
                    "type": "string",
                    "description": "Optional JSON string for request body"
                }
            },
            "required": ["url"]
        },
        handler=_create_rest_api_handler(
            ["geneontology.org", "api.geneontology.org"],
            "go_api_call"
        ),
        category="api",
        tags=["go", "annotations", "gene", "rest"]
    )
    logger.debug("Registered: go_api_call")

    # -------------------------------------------------------------------------
    # 6. Get Prompt Tool
    # -------------------------------------------------------------------------
    registry.register(
        name="get_prompt",
        description="""Get an agent's prompt from the prompt catalog.

Use this to inspect what instructions any specialist agent receives.
Useful for understanding agent behavior and troubleshooting routing issues.

**Available agents:**
- supervisor: Routes queries to specialists
- pdf: Answers questions about PDF documents
- gene_expression: Extracts gene expression patterns from papers
- chat_output: Displays results in chat; csv_formatter, tsv_formatter, json_formatter: File exports
- gene, allele, disease, chemical: Database query agents
- gene_ontology, go_annotations, orthologs: GO and orthology agents
- ontology_mapping: Maps text labels to ontology IDs

**MOD-specific rules (pass mod_id to see combined prompt):**
Some agents have organism-specific rules. Use these MOD aliases:
- WB = WormBase (C. elegans / worm) - "worm prompt", "WormBase rules"
- FB = FlyBase (Drosophila / fly) - "fly prompt", "FlyBase rules"
- MGI = Mouse Genome Informatics (mouse) - "mouse prompt"
- RGD = Rat Genome Database (rat) - "rat prompt"
- SGD = Saccharomyces Genome Database (yeast) - "yeast prompt"
- ZFIN = Zebrafish Information Network (zebrafish) - "zebrafish prompt"

**Example usage:**
- "Show me the worm gene expression prompt" → get_prompt(agent_id="gene_expression", mod_id="WB")
- "What are the fly-specific rules for gene agent?" → get_prompt(agent_id="gene", mod_id="FB")
- "Show the supervisor base prompt" → get_prompt(agent_id="supervisor")""",
        input_schema={
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Agent identifier (e.g., 'supervisor', 'gene', 'gene_expression', 'pdf')"
                },
                "mod_id": {
                    "type": "string",
                    "description": "MOD identifier for organism-specific rules: WB (worm), FB (fly), MGI (mouse), RGD (rat), SGD (yeast), ZFIN (zebrafish)"
                }
            },
            "required": ["agent_id"]
        },
        handler=_create_get_prompt_handler(),
        category="prompt",
        tags=["prompt", "agent", "debugging", "mod"]
    )
    logger.debug("Registered: get_prompt")

    logger.info(f"Registered {registry.get_tool_count()} diagnostic tools")

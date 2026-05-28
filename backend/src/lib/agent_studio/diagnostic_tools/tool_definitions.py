"""
Tool Definitions for Prompt Explorer Diagnostic Tools.

This module registers all diagnostic tools available to Opus.
Tools are direct copies of those used by specialist agents,
giving Opus the same capabilities for trace troubleshooting.

Tool Categories:
- database: SQL query tools (curation_db_sql)
- api: REST API and package-registered lookup tools
- prompt: Prompt inspection tools (get_prompt)
- codebase: Read-only runtime repository inspection tools
"""

import logging
import inspect
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
    seen: set[int] = set()
    candidates: List[Callable] = []

    def _walk(obj: Any, depth: int = 0) -> None:
        if obj is None or depth > 6:
            return
        obj_id = id(obj)
        if obj_id in seen:
            return
        seen.add(obj_id)

        if callable(obj):
            candidates.append(obj)
            closure = getattr(obj, "__closure__", None)
            if closure:
                for cell in closure:
                    try:
                        _walk(cell.cell_contents, depth + 1)
                    except Exception:
                        continue

        for attr in (
            "on_invoke_tool",
            "_invoke_tool_impl",
            "_function_tool",
            "func",
            "function",
            "_func",
            "_function",
            "handler",
        ):
            if hasattr(obj, attr):
                try:
                    _walk(getattr(obj, attr), depth + 1)
                except Exception:
                    continue

        obj_dict = getattr(obj, "__dict__", None)
        if isinstance(obj_dict, dict):
            # Newer Agents SDK wrappers keep the callable inside helper/invoker
            # objects, so we need to inspect instance attributes as well.
            for value in obj_dict.values():
                if callable(value) or hasattr(value, "__dict__"):
                    _walk(value, depth + 1)

    _walk(tool)

    # Prefer exact name match (most stable signal across SDK versions).
    for fn in candidates:
        if getattr(fn, "__name__", "") == tool.name:
            return fn

    # Fall back to the first callable that is not the SDK invoke wrapper.
    for fn in candidates:
        name = getattr(fn, "__name__", "")
        if "on_invoke_tool" in name:
            continue
        try:
            params = list(inspect.signature(fn).parameters.keys())
        except Exception:
            params = []
        if params != ["ctx", "input"]:
            return fn

    raise ValueError(
        f"Could not extract underlying function from FunctionTool '{tool.name}'. "
        "The OpenAI Agents SDK structure may have changed."
    )


def _callable_handler_from_tool(tool: Any) -> Callable[..., Dict[str, Any]]:
    """Create a diagnostic handler from a package-bound callable tool."""
    base_callable = _unwrap_function_tool(tool) if isinstance(tool, FunctionTool) else tool

    def handler(**kwargs: Any) -> Dict[str, Any]:
        result = base_callable(**kwargs)
        if hasattr(result, "model_dump"):
            return result.model_dump()
        if hasattr(result, "dict"):
            return result.dict()
        if isinstance(result, dict):
            return result
        raise TypeError(
            f"Package diagnostic tool '{getattr(tool, 'name', tool)}' returned "
            f"unsupported result type {type(result).__name__}; expected dict or Pydantic model."
        )

    return handler


def _register_package_diagnostic_tools(registry: DiagnosticToolRegistry) -> None:
    """Register package-owned tools that opt into Agent Studio diagnostics."""
    from src.lib.agent_studio.catalog_service import (
        _instantiate_package_tool,
        _load_package_tool_registry,
        get_tool_registry,
    )

    tool_catalog = get_tool_registry()
    package_registry = _load_package_tool_registry()
    for binding in package_registry.bindings:
        tool_info = tool_catalog.get(binding.tool_id, {})
        agent_studio_metadata = tool_info.get("agent_studio")
        if not isinstance(agent_studio_metadata, dict):
            continue
        diagnostic = agent_studio_metadata.get("diagnostic")
        if not isinstance(diagnostic, dict) or not bool(diagnostic.get("enabled")):
            continue
        if binding.required_context:
            logger.debug("Skipping context-bound package diagnostic tool %s", binding.tool_id)
            continue

        tool = _instantiate_package_tool(binding)
        input_schema = diagnostic.get("input_schema")
        if not isinstance(input_schema, dict):
            raise ValueError(
                f"Package diagnostic tool '{binding.tool_id}' must declare "
                "agent_studio.diagnostic.input_schema."
            )
        description = str(
            diagnostic.get("description")
            or agent_studio_metadata.get("prompt_description")
            or ""
        ).strip()
        if not description:
            raise ValueError(
                f"Package diagnostic tool '{binding.tool_id}' must declare "
                "agent_studio.prompt_description or agent_studio.diagnostic.description."
            )
        category = str(diagnostic.get("category") or "").strip()
        if not category:
            raise ValueError(
                f"Package diagnostic tool '{binding.tool_id}' must declare "
                "agent_studio.diagnostic.category."
            )
        raw_tags = diagnostic.get("tags")
        if not isinstance(raw_tags, list):
            raise ValueError(
                f"Package diagnostic tool '{binding.tool_id}' must declare "
                "agent_studio.diagnostic.tags as a list."
            )

        registry.register(
            name=binding.tool_id,
            description=description,
            input_schema=input_schema,
            handler=_callable_handler_from_tool(tool),
            category=category,
            tags=list(raw_tags),
        )
        logger.debug("Registered package diagnostic tool: %s", binding.tool_id)


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

    def handler(
        agent_id: str,
        group_id: Optional[str] = None,
        mod_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get an agent's prompt from the catalog.

        Args:
            agent_id: Agent identifier (e.g., "supervisor", "gene", "pdf_extraction")
            group_id: Optional group identifier for group-specific rules (e.g., "WB", "FB")
            mod_id: Legacy alias for group_id

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

        resolved_group_id = group_id or mod_id
        bundle = catalog.get_effective_prompt_bundle(agent_id, group_id=resolved_group_id)
        if bundle is None:
            return {
                "status": "error",
                "message": f"Agent '{agent_id}' not found",
            }
        has_group_rules = bool(
            resolved_group_id and any(layer.kind == "group_rules" for layer in bundle.layers)
        )

        return {
            "status": "ok",
            "agent_id": agent_id,
            "agent_name": agent.agent_name,
            "description": agent.description,
            "prompt": bundle.render(),
            "effective_prompt_hash": bundle.hash,
            "layer_manifest": bundle.to_manifest(),
            "layers": [layer.to_manifest() for layer in bundle.layers],
            "source_file": agent.source_file,
            "has_group_rules": agent.has_group_rules,
            "group_id_applied": resolved_group_id if has_group_rules else None,
            "available_groups": list(agent.group_rules.keys()) if agent.group_rules else [],
            "tools": agent.tools
        }

    return handler


def _create_search_codebase_handler():
    """Create handler for searching the runtime repository."""
    from .codebase_tools import search_codebase

    def handler(
        query: str,
        search_mode: str = "content",
        path_glob: Optional[str] = None,
        per_file_matches: int = 1,
        limit: int = 20,
    ) -> Dict[str, Any]:
        return search_codebase(
            query=query,
            search_mode=search_mode,
            path_glob=path_glob,
            per_file_matches=per_file_matches,
            limit=limit,
        )

    return handler


def _create_read_source_file_handler():
    """Create handler for reading a repository file."""
    from .codebase_tools import read_source_file

    def handler(
        path: str,
        start_line: int = 1,
        end_line: Optional[int] = None,
    ) -> Dict[str, Any]:
        return read_source_file(
            path=path,
            start_line=start_line,
            end_line=end_line,
        )

    return handler


def _bounded_inventory_limit(value: Any, *, default: int = 100, maximum: int = 250) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(parsed, maximum))


def _tool_inventory_item(tool_id: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
    methods = metadata.get("methods")
    agent_methods = metadata.get("agent_methods")
    return {
        "tool_id": tool_id,
        "name": metadata.get("name") or tool_id,
        "description": metadata.get("description"),
        "category": metadata.get("category"),
        "source_file": metadata.get("source_file"),
        "parent_tool": metadata.get("parent_tool"),
        "method_count": len(methods) if isinstance(methods, dict) else 0,
        "agent_method_agents": sorted(agent_methods)
        if isinstance(agent_methods, dict)
        else [],
    }


def _category_counts(tools: Dict[str, Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for metadata in tools.values():
        category = str(metadata.get("category") or "uncategorized")
        counts[category] = counts.get(category, 0) + 1
    return dict(sorted(counts.items()))


def _create_get_tool_inventory_handler():
    """Create handler for read-only tool inventory inspection."""
    from src.lib.agent_studio import catalog_service

    def handler(
        agent_id: Optional[str] = None,
        category: Optional[str] = None,
        include_method_tools: bool = False,
        limit: int = 100,
    ) -> Dict[str, Any]:
        normalized_agent_id = str(agent_id).strip() if agent_id else None
        normalized_category = str(category).strip() if category else None
        bounded_limit = _bounded_inventory_limit(limit)

        if normalized_agent_id:
            agent_entry = catalog_service.AGENT_REGISTRY.get(normalized_agent_id)
            if agent_entry is None:
                return {
                    "success": False,
                    "error": f"Agent {normalized_agent_id} was not found.",
                }

            raw_tool_ids = [
                str(tool_id)
                for tool_id in agent_entry.get("tools", [])
                if str(tool_id).strip()
            ]
            expanded_tool_ids = catalog_service.expand_tools_for_agent(
                normalized_agent_id,
                raw_tool_ids,
            )
            tool_items = []
            for tool_id in expanded_tool_ids:
                metadata = catalog_service.get_tool_for_agent(
                    tool_id,
                    normalized_agent_id,
                )
                if metadata is None:
                    tool_items.append(
                        {
                            "tool_id": tool_id,
                            "name": tool_id,
                            "description": None,
                            "category": None,
                            "source_file": None,
                            "parent_tool": None,
                            "method_count": 0,
                            "agent_method_agents": [],
                        }
                    )
                    continue
                if normalized_category and metadata.get("category") != normalized_category:
                    continue
                tool_items.append(_tool_inventory_item(tool_id, metadata))

            return {
                "success": True,
                "agent_id": normalized_agent_id,
                "agent_name": agent_entry.get("name"),
                "raw_tool_ids": raw_tool_ids,
                "expanded_tool_ids": expanded_tool_ids,
                "total_tools": len(tool_items),
                "tools": tool_items[:bounded_limit],
                "truncated": len(tool_items) > bounded_limit,
                "filters": {
                    "category": normalized_category,
                    "include_method_tools": include_method_tools,
                    "limit": bounded_limit,
                },
                "instruction": (
                    "Use get_tool_details(tool_id, agent_id) for parameter schemas, "
                    "method details, agent-specific multi-method context, and live "
                    "document/evidence capabilities such as search_mode, evidence "
                    "span IDs, and active-run evidence workspace tools."
                ),
            }

        all_tools = (
            catalog_service.get_all_tools()
            if include_method_tools
            else catalog_service.get_tool_registry()
        )
        filtered_tools = {
            tool_id: metadata
            for tool_id, metadata in sorted(all_tools.items())
            if not normalized_category or metadata.get("category") == normalized_category
        }
        tool_items = [
            _tool_inventory_item(tool_id, metadata)
            for tool_id, metadata in filtered_tools.items()
        ]
        return {
            "success": True,
            "agent_id": None,
            "total_tools": len(tool_items),
            "categories": _category_counts(filtered_tools),
            "tools": tool_items[:bounded_limit],
            "truncated": len(tool_items) > bounded_limit,
            "filters": {
                "category": normalized_category,
                "include_method_tools": include_method_tools,
                "limit": bounded_limit,
            },
            "instruction": (
                "Use agent_id to inspect one agent's attached tools, or "
                "get_tool_details(tool_id, agent_id) for full metadata. For PDF "
                "evidence guidance, inspect search_document, read_chunk, "
                "record_evidence, and active-run evidence workspace tools."
            ),
        }

    return handler


def _create_get_tool_details_handler():
    """Create handler for read-only tool detail inspection."""
    from src.lib.agent_studio import catalog_service

    def handler(
        tool_id: str,
        agent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        normalized_tool_id = str(tool_id).strip()
        normalized_agent_id = str(agent_id).strip() if agent_id else None
        if not normalized_tool_id:
            return {"success": False, "error": "tool_id is required."}

        if normalized_agent_id:
            metadata = catalog_service.get_tool_for_agent(
                normalized_tool_id,
                normalized_agent_id,
            )
        else:
            metadata = catalog_service.get_tool_details(normalized_tool_id)

        if metadata is None:
            agent_suffix = (
                f" for agent {normalized_agent_id}"
                if normalized_agent_id
                else ""
            )
            return {
                "success": False,
                "error": f"Tool {normalized_tool_id}{agent_suffix} was not found.",
            }

        return {
            "success": True,
            "tool_id": normalized_tool_id,
            "agent_id": normalized_agent_id,
            "tool": metadata,
            "instruction": (
                "Use tool.documentation.parameters for call shape, methods or "
                "relevant_methods for multi-method tools, and agent_context for "
                "agent-specific allowlists. For PDF evidence tools, treat this "
                "metadata as the source of truth for search_mode, read_chunk "
                "evidence_spans[].span_id output, record_evidence span_ids "
                "input, and immutable evidence provenance behavior."
            ),
        }

    return handler


def register_all_tools(registry: DiagnosticToolRegistry) -> None:
    """
    Register all diagnostic tools with the registry.

    This is called automatically by get_diagnostic_tools_registry()
    on first access.
    """
    logger.info("Registering diagnostic tools...")

    _register_package_diagnostic_tools(registry)

    # -------------------------------------------------------------------------
    # 1. Curation Database SQL Tool
    # -------------------------------------------------------------------------
    from src.lib.database.curation_resolver import get_curation_resolver
    curation_db_url = get_curation_resolver().get_connection_url()
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
        description="""Get an agent's effective prompt from the shared prompt assembler.

Use this to inspect the flat prompt, structured layers, layer manifest, and
effective prompt hash any specialist or validator agent receives.
Useful for understanding agent behavior, troubleshooting routing issues, and
answering validator-agent inspection questions from domain-pack validation plans.

**Common prompt targets:**
- Routing/display/export: supervisor, curation_prep, chat_output, csv_formatter,
  tsv_formatter, json_formatter.
- Document/general extraction: pdf_extraction.
- Domain-envelope extractors: gene_extractor, allele_extractor,
  disease_extractor, chemical_extractor, phenotype_extractor, gene_expression
  (flow/prompt alias for the packaged gene_expression_extraction agent).
- Validator/resolver agents: gene_validation (also available as gene),
  allele_validation (allele), disease_validation (disease),
  chemical_validation (chemical), ontology_term_validation,
  controlled_vocabulary_validation, data_provider_validation,
  subject_entity_validation, reference_validation,
  experimental_condition_validation, agm_validation.
- Other lookup specialists: gene_ontology, go_annotations, orthologs.
- Validator-agent IDs returned by get_domain_pack_validation_plan are valid prompt
  inspection targets. Prefer those exact IDs when explaining active bindings.

**Group-specific rules (pass group_id to see combined prompt):**
Some agents have organism-specific rules. Use these group aliases:
- WB = WormBase (C. elegans / worm) - "worm prompt", "WormBase rules"
- FB = FlyBase (Drosophila / fly) - "fly prompt", "FlyBase rules"
- MGI = Mouse Genome Informatics (mouse) - "mouse prompt"
- RGD = Rat Genome Database (rat) - "rat prompt"
- SGD = Saccharomyces Genome Database (yeast) - "yeast prompt"
- ZFIN = Zebrafish Information Network (zebrafish) - "zebrafish prompt"

**Example usage:**
- "Show me the worm gene expression prompt" → get_prompt(agent_id="gene_expression", group_id="WB")
- "Show me the packaged gene expression extractor prompt" → get_prompt(agent_id="gene_expression_extraction")
- "What are the fly-specific rules for the gene extractor?" → get_prompt(agent_id="gene_extractor", group_id="FB")
- "Show the phenotype extractor prompt" → get_prompt(agent_id="phenotype_extractor", group_id="ZFIN")
- "Inspect controlled vocabulary validation" → get_prompt(agent_id="controlled_vocabulary_validation")
- "How does this validator work?" → read validator_bindings[].validator_agent.agent_id or validation_attachments[].validator_agent_id from get_domain_pack_validation_plan, then call get_prompt(agent_id="gene_validation")
- "Show the supervisor base prompt" → get_prompt(agent_id="supervisor")""",
        input_schema={
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Agent identifier or validator-agent ID from a validation plan (e.g., 'supervisor', 'gene_extractor', 'gene_expression', 'gene_expression_extraction', 'phenotype_extractor', 'gene_validation')"
                },
                "group_id": {
                    "type": "string",
                    "description": "Group identifier for organism-specific rules: WB (worm), FB (fly), MGI (mouse), RGD (rat), SGD (yeast), ZFIN (zebrafish)"
                },
                "mod_id": {
                    "type": "string",
                    "description": "Legacy alias for group_id. MOD identifier for organism-specific rules: WB (worm), FB (fly), MGI (mouse), RGD (rat), SGD (yeast), ZFIN (zebrafish)"
                }
            },
            "required": ["agent_id"]
        },
        handler=_create_get_prompt_handler(),
        category="prompt",
        tags=["prompt", "agent", "debugging", "mod"]
    )
    logger.debug("Registered: get_prompt")

    # -------------------------------------------------------------------------
    # 6.5. Tool Inventory / Details
    # -------------------------------------------------------------------------
    registry.register(
        name="get_tool_inventory",
        description="""Inspect the runtime tool catalog in read-only mode.

Use this when a curator asks what an agent can do, what tools are attached to a
specialist or validator, or which package tools/method-level helpers exist.
Pass agent_id to see one agent's raw and expanded tool IDs; omit it to list the
global catalog. Use get_tool_details for full schemas and method metadata.""",
        input_schema={
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Optional agent or validator-agent ID, for example gene_extractor, gene_validation, disease_validation, or ontology_term_validation.",
                },
                "category": {
                    "type": "string",
                    "description": "Optional exact category filter from the tool catalog.",
                },
                "include_method_tools": {
                    "type": "boolean",
                    "description": "Include method-level tool entries such as search_genes in addition to concrete runtime tool IDs.",
                    "default": False,
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum tools to return (default: 100, max: 250).",
                    "default": 100,
                    "minimum": 1,
                    "maximum": 250,
                },
            },
            "required": [],
        },
        handler=_create_get_tool_inventory_handler(),
        category="tooling",
        tags=["tools", "inventory", "agent", "debugging"],
    )
    logger.debug("Registered: get_tool_inventory")

    registry.register(
        name="get_tool_details",
        description="""Inspect full runtime metadata for one tool or method-level helper.

Use this after get_tool_inventory when you need parameters, source file,
documentation, available methods, or agent-specific method allowlists. Pass
agent_id to show relevant_methods and agent_context for multi-method package
tools.""",
        input_schema={
            "type": "object",
            "properties": {
                "tool_id": {
                    "type": "string",
                    "description": "Runtime tool ID or method-level helper ID, for example agr_curation_query, search_genes, record_evidence, or chebi_api_call.",
                },
                "agent_id": {
                    "type": "string",
                    "description": "Optional agent or validator-agent ID used to include agent-specific method context.",
                },
            },
            "required": ["tool_id"],
        },
        handler=_create_get_tool_details_handler(),
        category="tooling",
        tags=["tools", "details", "agent", "debugging"],
    )
    logger.debug("Registered: get_tool_details")

    # -------------------------------------------------------------------------
    # 7. Codebase Search Tool
    # -------------------------------------------------------------------------
    registry.register(
        name="search_codebase",
        description="""Search the AGR AI Curation runtime repository in read-only mode.

Use this when a curator asks whether the current code supports a feature,
contains a limitation, or implements a specific Agent Studio behavior.

Two search modes:
- content: search file contents and return matching lines with file paths
- files: search repository-relative file paths only

Typical workflow:
1. search_codebase(query="agent_studio", search_mode="files")
2. search_codebase(query="tool policy", search_mode="content", path_glob="backend/src/**/*.py")
3. read_source_file(path="backend/src/api/agent_studio.py", start_line=1400, end_line=1505)

The tool only reads files from the current repository checkout and never executes code.""",
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Substring or ripgrep search text to find in file paths or contents.",
                },
                "search_mode": {
                    "type": "string",
                    "description": "Choose 'content' to search file contents or 'files' to search file paths.",
                    "enum": ["content", "files"],
                    "default": "content",
                },
                "path_glob": {
                    "type": "string",
                    "description": "Optional rg-style glob to narrow the search, for example 'backend/src/**/*.py'.",
                },
                "per_file_matches": {
                    "type": "integer",
                    "description": "Maximum content matches to return per file (content mode only).",
                    "minimum": 1,
                    "maximum": 20,
                    "default": 1,
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of matches to return.",
                    "minimum": 1,
                    "maximum": 200,
                    "default": 20,
                },
            },
            "required": ["query"],
        },
        handler=_create_search_codebase_handler(),
        category="codebase",
        tags=["repo", "code", "files", "read-only"],
    )
    logger.debug("Registered: search_codebase")

    # -------------------------------------------------------------------------
    # 8. Source File Reader Tool
    # -------------------------------------------------------------------------
    registry.register(
        name="read_source_file",
        description="""Read a text file from the AGR AI Curation runtime repository.

Use this after search_codebase identifies the relevant file. The response is
line-numbered so you can cite the implementation precisely when explaining a
feature, behavior, or limitation to a curator.

This tool is read-only and restricted to files inside the current repository checkout.""",
        input_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Repository-relative file path, for example 'backend/src/api/agent_studio.py'.",
                },
                "start_line": {
                    "type": "integer",
                    "description": "First line number to read (1-based).",
                    "minimum": 1,
                    "default": 1,
                },
                "end_line": {
                    "type": "integer",
                    "description": "Optional inclusive ending line number. Reads up to 400 lines per call.",
                    "minimum": 1,
                },
            },
            "required": ["path"],
        },
        handler=_create_read_source_file_handler(),
        category="codebase",
        tags=["repo", "code", "file", "read-only"],
    )
    logger.debug("Registered: read_source_file")

    logger.info('Registered %s diagnostic tools', registry.get_tool_count())

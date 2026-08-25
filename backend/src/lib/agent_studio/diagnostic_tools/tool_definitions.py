"""
Tool Definitions for Prompt Explorer Diagnostic Tools.

This module registers all diagnostic tools available to Opus.
Package-owned tools opt in through their installed binding metadata, giving the
diagnostic assistant the same capabilities for trace troubleshooting.

Tool Categories:
- database: package-registered data inspection tools
- api: REST API and package-registered lookup tools
- prompt: Prompt inspection tools (get_prompt)
- codebase: Read-only runtime repository inspection tools
"""

import logging
import inspect
from typing import Any, Callable, Dict, List, Optional

from agents import FunctionTool

from src.lib.openai_agents.bounded_list import (
    normalize_page_limit,
    offset_page,
    parse_offset_cursor,
    substring_match,
)

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
        _build_tool_execution_context,
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
        execution_context = _build_tool_execution_context({})
        unknown_context = [
            key
            for key in binding.required_context
            if not hasattr(execution_context, key)
        ]
        if unknown_context:
            raise ValueError(
                f"Package diagnostic tool '{binding.tool_id}' declares unknown "
                f"execution context: {', '.join(unknown_context)}"
            )
        missing_context = [
            key
            for key in binding.required_context
            if getattr(execution_context, key, None) in (None, "")
        ]
        if missing_context:
            logger.warning(
                "Skipping package diagnostic tool %s; missing context: %s",
                binding.tool_id,
                ", ".join(missing_context),
            )
            continue

        tool = _instantiate_package_tool(
            binding,
            execution_context=execution_context,
        )
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


def _create_get_prompt_handler():
    """
    Create handler for prompt inspection tool.

    Uses the PromptCatalogService to fetch agent prompts.
    """
    from src.lib.agent_studio.catalog_service import get_prompt_catalog

    def handler(
        agent_id: str,
        group_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get an agent's prompt from the catalog.

        Args:
            agent_id: Installed agent identifier
            group_id: Optional installed group-rule identifier

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

        bundle = catalog.get_effective_prompt_bundle(agent_id, group_id=group_id)
        if bundle is None:
            return {
                "status": "error",
                "message": f"Agent '{agent_id}' not found",
            }
        has_group_rules = bool(
            group_id and any(layer.kind == "group_rules" for layer in bundle.layers)
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
            "group_id_applied": group_id if has_group_rules else None,
            "available_groups": list(agent.group_rules.keys()) if agent.group_rules else [],
            "tools": agent.tools
        }

    return handler


def _get_prompt_diagnostic_contract() -> tuple[str, Dict[str, Any]]:
    """Describe prompt inspection using only targets installed in the live catalog."""
    from src.lib.agent_studio.catalog_service import get_prompt_catalog
    from src.lib.prompts.cache import is_initialized

    description = """Get an installed agent's effective prompt from the shared prompt assembler.

Use this to inspect the flat prompt, structured layers, layer manifest, and
effective prompt hash for an installed specialist or validator. Use the live
catalog values below instead of assuming a package-specific agent or group."""
    input_schema = {
        "type": "object",
        "properties": {
            "agent_id": {
                "type": "string",
                "description": "Agent identifier from the installed prompt targets.",
            },
            "group_id": {
                "type": "string",
                "description": (
                    "Optional group-rule identifier from the installed prompt catalog."
                ),
            },
        },
        "required": ["agent_id"],
    }

    if not is_initialized():
        logger.warning(
            "Prompt cache is not initialized; registering get_prompt without "
            "installed target examples"
        )
        return description, input_schema

    catalog = get_prompt_catalog().catalog
    agent_ids = sorted(
        agent.agent_id
        for category in catalog.categories
        for agent in category.agents
        if agent.agent_id != "task_input"
    )
    group_ids = sorted(catalog.available_groups)

    if not agent_ids:
        raise RuntimeError(
            "Cannot register get_prompt before the installed prompt catalog is initialized"
        )

    installed_targets = ", ".join(agent_ids)
    description += f"\n\nInstalled prompt targets: {installed_targets}."
    if group_ids:
        description += (
            "\nInstalled group-rule identifiers: " + ", ".join(group_ids) + "."
        )
    return description, input_schema


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


def _filter_tool_items_by_query(
    tool_items: List[Dict[str, Any]],
    query: Optional[str],
) -> List[Dict[str, Any]]:
    """Keep only the tools whose id, name, or description match the search words."""

    if not query:
        return tool_items
    return [
        item
        for item in tool_items
        if substring_match(
            query,
            item.get("tool_id"),
            item.get("name"),
            item.get("description"),
        )
    ]


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
        query: Optional[str] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        normalized_agent_id = str(agent_id).strip() if agent_id else None
        normalized_category = str(category).strip() if category else None
        normalized_query = str(query).strip() if query else None
        bounded_limit = normalize_page_limit(limit, default=100, maximum=250)
        offset = parse_offset_cursor(cursor)

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

            tool_items = _filter_tool_items_by_query(tool_items, normalized_query)
            page, truncated, next_cursor = offset_page(
                tool_items,
                limit=bounded_limit,
                cursor=offset,
            )

            return {
                "success": True,
                "agent_id": normalized_agent_id,
                "agent_name": agent_entry.get("name"),
                "raw_tool_ids": raw_tool_ids,
                "expanded_tool_ids": expanded_tool_ids,
                "total_tools": len(tool_items),
                "total_count": len(tool_items),
                "returned_count": len(page),
                "tools": page,
                "truncated": truncated,
                "next_cursor": next_cursor,
                "filters": {
                    "category": normalized_category,
                    "include_method_tools": include_method_tools,
                    "query": normalized_query,
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
        tool_items = _filter_tool_items_by_query(tool_items, normalized_query)
        page, truncated, next_cursor = offset_page(
            tool_items,
            limit=bounded_limit,
            cursor=offset,
        )
        return {
            "success": True,
            "agent_id": None,
            "total_tools": len(tool_items),
            "total_count": len(tool_items),
            "returned_count": len(page),
            "categories": _category_counts(filtered_tools),
            "tools": page,
            "truncated": truncated,
            "next_cursor": next_cursor,
            "filters": {
                "category": normalized_category,
                "include_method_tools": include_method_tools,
                "query": normalized_query,
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

    get_prompt_description, get_prompt_input_schema = _get_prompt_diagnostic_contract()
    registry.register(
        name="get_prompt",
        description=get_prompt_description,
        input_schema=get_prompt_input_schema,
        handler=_create_get_prompt_handler(),
        category="prompt",
        tags=["prompt", "agent", "debugging", "group"]
    )
    logger.debug("Registered: get_prompt")

    # -------------------------------------------------------------------------
    # 6.5. Tool Inventory / Details
    # -------------------------------------------------------------------------
    registry.register(
        name="get_tool_inventory",
        description="""Inspect the runtime tool catalog in read-only mode.

Use this when a curator asks what an agent can do, what tools are attached to an
installed agent, or which package tools/method-level helpers exist.
Pass agent_id to see one agent's raw and expanded tool IDs; omit it to list the
global catalog. Pass query to search tools by id, name, or description, and use
limit/cursor to page through large catalogs. Use get_tool_details for full
schemas and method metadata.""",
        input_schema={
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Optional agent ID from the installed runtime catalog.",
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
                "query": {
                    "type": "string",
                    "description": "Optional words to match against a tool's id, name, or description (case-insensitive). Leave blank to list every tool.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum tools to return in this page (default: 100, max: 250).",
                    "default": 100,
                    "minimum": 1,
                    "maximum": 250,
                },
                "cursor": {
                    "type": "string",
                    "description": "Page marker returned as next_cursor by a previous call. Omit to start from the first page.",
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
                    "description": "Runtime tool ID or method-level helper ID from the installed catalog.",
                },
                "agent_id": {
                    "type": "string",
                    "description": "Optional installed agent ID used to include agent-specific method context.",
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

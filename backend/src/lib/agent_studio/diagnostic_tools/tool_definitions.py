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
import hashlib
import json
from typing import Any, Callable, Dict, List, Optional

from agents import FunctionTool

from src.lib.openai_agents.bounded_list import (
    normalize_page_limit,
    offset_page,
    parse_offset_cursor,
    substring_match,
)
from src.lib.openai_agents.config import (
    get_agent_studio_prompt_inspection_chunk_max_chars,
    get_agent_studio_provider_tool_result_inline_max_chars,
    get_agent_studio_tool_details_chunk_max_chars,
    get_agent_studio_tool_details_result_max_chars,
    get_agent_studio_tool_inventory_default_items,
    get_agent_studio_tool_inventory_max_items,
    get_agent_studio_tool_inventory_result_max_chars,
    get_agent_studio_tool_inventory_summary_max_chars,
)

from .registry import DiagnosticToolRegistry
from .result_contracts import (
    create_bounded_result_handler,
    create_sql_query_handler,
    diagnostic_input_schema,
    validate_result_contract,
)

logger = logging.getLogger(__name__)

_TOOL_INVENTORY_DEFAULT_ITEMS = get_agent_studio_tool_inventory_default_items()
_TOOL_INVENTORY_MAX_ITEMS = get_agent_studio_tool_inventory_max_items()
_PROVIDER_RESULT_INLINE_MAX_CHARS = get_agent_studio_provider_tool_result_inline_max_chars()
_TOOL_INVENTORY_RESULT_MAX_CHARS = min(
    get_agent_studio_tool_inventory_result_max_chars(),
    _PROVIDER_RESULT_INLINE_MAX_CHARS,
)
_TOOL_INVENTORY_SUMMARY_MAX_CHARS = get_agent_studio_tool_inventory_summary_max_chars()
_TOOL_DETAILS_RESULT_MAX_CHARS = min(
    get_agent_studio_tool_details_result_max_chars(),
    _PROVIDER_RESULT_INLINE_MAX_CHARS,
)
_TOOL_DETAILS_CHUNK_MAX_CHARS = get_agent_studio_tool_details_chunk_max_chars()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _serialized_chars(value: Dict[str, Any]) -> int:
    """Measure the exact JSON representation used for provider continuation."""
    return len(json.dumps(value, default=str))


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

    # Name overrides (for example chebi_api_call around a function named
    # restricted_rest_api_call) cannot use the signal above. Prefer a real
    # Python function whose parameters match the published tool schema; SDK
    # wrappers also retain a callable Pydantic input model with the same fields.
    expected_params = set(
        (getattr(tool, "params_json_schema", {}) or {}).get("properties", {})
    )
    for fn in candidates:
        if not (inspect.isfunction(fn) or inspect.ismethod(fn)):
            continue
        try:
            params = set(inspect.signature(fn).parameters)
        except Exception:
            continue
        if expected_params and params == expected_params:
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


def _callable_handler_from_tool(
    tool: Any,
    result_contract: Dict[str, Any],
) -> Callable[..., Dict[str, Any]]:
    """Create a diagnostic handler from a package-bound callable tool."""
    base_callable = _unwrap_function_tool(tool) if isinstance(tool, FunctionTool) else tool
    return create_bounded_result_handler(base_callable, result_contract)


def _create_sql_query_handler(
    database_url: str,
    result_contract: Dict[str, Any],
) -> Callable[..., Dict[str, Any]]:
    """Create a bounded SQL diagnostic without materializing the full SELECT."""
    return create_sql_query_handler(database_url, result_contract)


def _create_rest_api_handler(
    tool: Any,
    result_contract: Dict[str, Any],
) -> Callable[..., Dict[str, Any]]:
    """Keep the package REST allowlist while bounding its diagnostic result."""
    return _callable_handler_from_tool(tool, result_contract)


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
    execution_context = _build_tool_execution_context({})
    for binding in package_registry.bindings:
        tool_info = tool_catalog.get(binding.tool_id, {})
        agent_studio_metadata = tool_info.get("agent_studio")
        if not isinstance(agent_studio_metadata, dict):
            continue
        diagnostic = agent_studio_metadata.get("diagnostic")
        if not isinstance(diagnostic, dict) or not bool(diagnostic.get("enabled")):
            continue
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

        result_contract = validate_result_contract(
            diagnostic.get("result_contract"),
            tool_id=binding.tool_id,
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
        page_paths = result_contract.get("page_paths") or []
        continuation_description = (
            "Start with result_view='summary'. "
            + (
                f"Use result_view='page' with result_path one of {', '.join(page_paths)} for structured continuation. "
                if page_paths
                else ""
            )
            + "Use result_view='detail' with detail_path/detail_cursor for exact hash-addressed chunks."
        )
        description = f"{description}\n\nResult contract: {continuation_description}"
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

        bounded_input_schema = diagnostic_input_schema(input_schema, result_contract)
        if result_contract["kind"] == "sql_rows":
            database_url = execution_context.database_url
            if not isinstance(database_url, str) or not database_url:
                raise ValueError(
                    f"Package diagnostic tool '{binding.tool_id}' requires a database URL."
                )
            handler = _create_sql_query_handler(
                database_url,
                result_contract,
            )
        else:
            tool = _instantiate_package_tool(
                binding,
                execution_context=execution_context,
            )
            if result_contract["kind"] == "raw":
                handler = _create_rest_api_handler(tool, result_contract)
            else:
                handler = _callable_handler_from_tool(tool, result_contract)

        registry.register(
            name=binding.tool_id,
            description=description,
            input_schema=bounded_input_schema,
            handler=handler,
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
        view: str = "summary",
        layer_id: Optional[str] = None,
        layer_index: Optional[int] = None,
        cursor: int = 0,
        max_chars: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Inspect an agent's prompt summary or one bounded exact-text chunk.

        Args:
            agent_id: Installed agent identifier
            group_id: Optional installed group-rule identifier
            view: Summary, effective prompt, or selected layer view
            layer_id: Stable layer identifier for the layer view
            layer_index: Ordered zero-based layer index for the layer view
            cursor: Zero-based character offset for exact-text views
            max_chars: Requested chunk size, capped by environment configuration

        Returns:
            Compact prompt manifest or bounded exact-text chunk
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
        effective_prompt = bundle.render()

        layer_summaries = [
            {
                "index": index,
                "id": layer.id,
                "kind": layer.kind,
                "title": layer.title,
                "provenance": layer.provenance,
                "editable": layer.editable,
                "locked": layer.locked,
                "source_ref": layer.source_ref,
                "hash": layer.hash,
                "total_length": len(layer.content),
            }
            for index, layer in enumerate(bundle.layers)
        ]
        summary = {
            "status": "ok",
            "view": "summary",
            "agent_id": agent_id,
            "agent_name": agent.agent_name,
            "description": agent.description,
            "source_file": agent.source_file,
            "effective_prompt_hash": bundle.hash,
            "effective_prompt_total_length": len(effective_prompt),
            "has_group_rules": agent.has_group_rules,
            "group_id_requested": group_id,
            "group_id_applied": group_id if has_group_rules else None,
            "available_groups": sorted(agent.group_rules) if agent.group_rules else [],
            "layers": layer_summaries,
        }

        if view == "summary":
            if any(value is not None for value in (layer_id, layer_index, max_chars)) or cursor:
                return {
                    "status": "error",
                    "message": (
                        "Summary view does not accept layer_id, layer_index, cursor, "
                        "or max_chars"
                    ),
                }
            return summary

        if view not in {"effective_prompt", "layer"}:
            return {
                "status": "error",
                "message": (
                    f"Unsupported view '{view}'. Must be 'summary', "
                    "'effective_prompt', or 'layer'."
                ),
            }
        if not isinstance(cursor, int) or isinstance(cursor, bool) or cursor < 0:
            return {
                "status": "error",
                "message": "cursor must be a non-negative integer character offset",
            }
        if max_chars is not None and (
            not isinstance(max_chars, int) or isinstance(max_chars, bool) or max_chars < 1
        ):
            return {
                "status": "error",
                "message": "max_chars must be a positive integer",
            }

        target_text: str
        target_hash: str
        selected_layer: Dict[str, Any] | None = None
        if view == "effective_prompt":
            if layer_id is not None or layer_index is not None:
                return {
                    "status": "error",
                    "message": "effective_prompt view does not accept layer_id or layer_index",
                }
            target_text = effective_prompt
            target_hash = bundle.hash
        else:
            if (layer_id is None) == (layer_index is None):
                return {
                    "status": "error",
                    "message": (
                        "layer view requires exactly one of layer_id or layer_index"
                    ),
                }
            selected_index: int | None = None
            if layer_id is not None:
                selected_index = next(
                    (
                        index
                        for index, layer in enumerate(bundle.layers)
                        if layer.id == layer_id
                    ),
                    None,
                )
                if selected_index is None:
                    return {
                        "status": "error",
                        "message": f"Layer '{layer_id}' not found",
                        "available_layers": layer_summaries,
                    }
            else:
                if (
                    not isinstance(layer_index, int)
                    or isinstance(layer_index, bool)
                    or layer_index < 0
                    or layer_index >= len(bundle.layers)
                ):
                    return {
                        "status": "error",
                        "message": f"Layer index '{layer_index}' is out of range",
                        "available_layers": layer_summaries,
                    }
                selected_index = layer_index
            selected_prompt_layer = bundle.layers[selected_index]
            selected_layer = layer_summaries[selected_index]
            target_text = selected_prompt_layer.content
            target_hash = selected_prompt_layer.hash

        total_length = len(target_text)
        if cursor > total_length:
            return {
                "status": "error",
                "message": (
                    f"cursor {cursor} exceeds target length {total_length}"
                ),
            }
        chunk_cap = get_agent_studio_prompt_inspection_chunk_max_chars()
        chunk_size = min(max_chars or chunk_cap, chunk_cap)
        requested_end = min(cursor + chunk_size, total_length)

        def render(end: int) -> Dict[str, Any]:
            complete = end == total_length
            next_arguments: Dict[str, Any] = {
                "agent_id": agent_id,
                "view": view,
                "cursor": end,
                "max_chars": chunk_size,
            }
            if group_id is not None:
                next_arguments["group_id"] = group_id
            if layer_id is not None:
                next_arguments["layer_id"] = layer_id
            if layer_index is not None:
                next_arguments["layer_index"] = layer_index
            result = {
                "status": "ok",
                "view": view,
                "agent_id": agent_id,
                "group_id_applied": group_id if has_group_rules else None,
                "encoding": "text",
                "hash": target_hash,
                "total_length": total_length,
                "returned_range": {"start": cursor, "end": end},
                "content": target_text[cursor:end],
                "complete": complete,
                "next_cursor": None if complete else end,
                "next_call": (
                    {"tool": "get_prompt", "arguments": next_arguments}
                    if not complete
                    else None
                ),
            }
            if selected_layer is not None:
                result["layer"] = selected_layer
            return result

        provider_limit = get_agent_studio_provider_tool_result_inline_max_chars()
        fitting_end: int | None = None
        if _serialized_chars(render(requested_end)) <= provider_limit:
            fitting_end = requested_end
        low = cursor + 1
        high = requested_end - 1
        if fitting_end is None:
            while low <= high:
                candidate_end = (low + high) // 2
                if _serialized_chars(render(candidate_end)) <= provider_limit:
                    fitting_end = candidate_end
                    low = candidate_end + 1
                else:
                    high = candidate_end - 1
        if fitting_end is None and requested_end == cursor:
            if _serialized_chars(render(cursor)) <= provider_limit:
                fitting_end = cursor
        if fitting_end is None:
            return {
                "status": "error",
                "error": "provider_limit_too_small",
                "message": (
                    "The configured provider result envelope cannot hold prompt "
                    "detail metadata plus one exact character."
                ),
            }
        return render(fitting_end)

    return handler


def _get_prompt_diagnostic_contract() -> tuple[str, Dict[str, Any]]:
    """Describe prompt inspection using only targets installed in the live catalog."""
    from src.lib.agent_studio.catalog_service import get_prompt_catalog
    from src.lib.prompts.cache import is_initialized

    chunk_cap = get_agent_studio_prompt_inspection_chunk_max_chars()
    description = """Inspect an installed specialist or validator prompt from the shared prompt assembler.

Omit view for a compact content-free summary with the effective prompt hash,
total length, selected group context, and ordered layer identities. Use
view="effective_prompt" with cursor/max_chars to retrieve exact prompt chunks.
Use view="layer" with exactly one stable layer_id or zero-based layer_index to
retrieve exact layer chunks. Follow next_cursor until complete is true."""
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
            "view": {
                "type": "string",
                "enum": ["summary", "effective_prompt", "layer"],
                "default": "summary",
                "description": "Content-free summary or bounded exact-text target.",
            },
            "layer_id": {
                "type": "string",
                "description": "Stable layer id from the summary; valid only for layer view.",
            },
            "layer_index": {
                "type": "integer",
                "minimum": 0,
                "description": "Zero-based layer index from the summary; valid only for layer view.",
            },
            "cursor": {
                "type": "integer",
                "minimum": 0,
                "default": 0,
                "description": "Character offset for effective_prompt or layer retrieval.",
            },
            "max_chars": {
                "type": "integer",
                "minimum": 1,
                "maximum": chunk_cap,
                "default": chunk_cap,
                "description": (
                    "Maximum exact characters to return; capped at the configured "
                    f"limit of {chunk_cap}."
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
    description += (
        "\n\nUse these live catalog values instead of assuming a package-specific "
        f"agent or group.\nInstalled prompt targets: {installed_targets}."
    )
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
        cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        return search_codebase(
            query=query,
            search_mode=search_mode,
            path_glob=path_glob,
            per_file_matches=per_file_matches,
            limit=limit,
            cursor=cursor,
        )

    return handler


def _create_read_source_file_handler():
    """Create handler for reading a repository file."""
    from .codebase_tools import read_source_file

    def handler(
        path: str,
        start_line: int = 1,
        end_line: Optional[int] = None,
        line_char_start: int = 0,
    ) -> Dict[str, Any]:
        return read_source_file(
            path=path,
            start_line=start_line,
            end_line=end_line,
            line_char_start=line_char_start,
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
            item.get("search_description", item.get("description")),
        )
    ]


def _tool_inventory_item(tool_id: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
    methods = metadata.get("methods")
    agent_methods = metadata.get("agent_methods")
    description = metadata.get("description")
    description_text = str(description) if description is not None else None
    return {
        "tool_id": tool_id,
        "name": metadata.get("name") or tool_id,
        "description": description_text[:_TOOL_INVENTORY_SUMMARY_MAX_CHARS] if description_text is not None else None,
        "search_description": description_text,
        "description_truncated": bool(description_text and len(description_text) > _TOOL_INVENTORY_SUMMARY_MAX_CHARS),
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
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        normalized_agent_id = str(agent_id).strip() if agent_id else None
        normalized_category = str(category).strip() if category else None
        normalized_query = str(query).strip() if query else None
        bounded_limit = normalize_page_limit(limit, default=_TOOL_INVENTORY_DEFAULT_ITEMS, maximum=_TOOL_INVENTORY_MAX_ITEMS)
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
            for item in tool_items:
                item.pop("search_description", None)
            page, _, _ = offset_page(
                tool_items,
                limit=bounded_limit,
                cursor=offset,
            )

            def build_agent_response() -> Dict[str, Any]:
                next_offset = offset + len(page)
                next_cursor = str(next_offset) if next_offset < len(tool_items) else None
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
                "truncated": next_cursor is not None,
                "next_cursor": next_cursor,
                "next_call": ({"tool": "get_tool_inventory", "arguments": {
                    "agent_id": normalized_agent_id,
                    **({"category": normalized_category} if normalized_category else {}),
                    "include_method_tools": include_method_tools,
                    **({"query": normalized_query} if normalized_query else {}),
                    "limit": bounded_limit, "cursor": next_cursor,
                }} if next_cursor is not None else None),
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

            response = build_agent_response()
            while page and _serialized_chars(response) > _TOOL_INVENTORY_RESULT_MAX_CHARS:
                page.pop()
                response = build_agent_response()
            if not page and offset < len(tool_items):
                return {"success": False, "error": "metadata_too_large",
                        "message": "One focused tool summary plus catalog continuation metadata exceeds AGENT_STUDIO_TOOL_INVENTORY_RESULT_MAX_CHARS."}
            return response

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
        for item in tool_items:
            item.pop("search_description", None)
        page, _, _ = offset_page(
            tool_items,
            limit=bounded_limit,
            cursor=offset,
        )
        def build_global_response() -> Dict[str, Any]:
            next_offset = offset + len(page)
            next_cursor = str(next_offset) if next_offset < len(tool_items) else None
            return {
            "success": True,
            "agent_id": None,
            "total_tools": len(tool_items),
            "total_count": len(tool_items),
            "returned_count": len(page),
            "categories": _category_counts(filtered_tools),
            "tools": page,
            "truncated": next_cursor is not None,
            "next_cursor": next_cursor,
            "next_call": ({"tool": "get_tool_inventory", "arguments": {
                **({"category": normalized_category} if normalized_category else {}),
                "include_method_tools": include_method_tools,
                **({"query": normalized_query} if normalized_query else {}),
                "limit": bounded_limit, "cursor": next_cursor,
            }} if next_cursor is not None else None),
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

        response = build_global_response()
        while page and _serialized_chars(response) > _TOOL_INVENTORY_RESULT_MAX_CHARS:
            page.pop()
            response = build_global_response()
        if not page and offset < len(tool_items):
            return {"success": False, "error": "metadata_too_large",
                    "message": "One tool summary plus catalog continuation metadata exceeds AGENT_STUDIO_TOOL_INVENTORY_RESULT_MAX_CHARS."}
        return response

    return handler


def _create_get_tool_details_handler():
    """Create handler for read-only tool detail inspection."""
    from src.lib.agent_studio import catalog_service

    def handler(
        tool_id: str,
        agent_id: Optional[str] = None,
        section: Optional[str] = None,
        cursor: Optional[int] = None,
        max_chars: Optional[int] = None,
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

        inline_result = {
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
        if section is None and _serialized_chars(inline_result) <= _TOOL_DETAILS_RESULT_MAX_CHARS:
            return inline_result

        metadata_json = _canonical_json(metadata)
        metadata_hash = hashlib.sha256(metadata_json.encode("utf-8")).hexdigest()
        sections = [{"section": str(key),
                     "sha256": hashlib.sha256(_canonical_json(metadata[key]).encode("utf-8")).hexdigest(),
                     "total_chars": len(_canonical_json(metadata[key]))} for key in sorted(metadata)]
        if section is None:
            result = {"success": True, "tool_id": normalized_tool_id,
                      "agent_id": normalized_agent_id, "detail_mode": "sections",
                      "tool_sha256": metadata_hash, "tool_total_chars": len(metadata_json),
                      "sections": sections,
                      "instruction": "Call get_tool_details again with one returned section. Follow next_call until complete; concatenate content in range order and verify sha256.",
                      "next_call": ({"tool": "get_tool_details", "arguments": {
                          "tool_id": normalized_tool_id,
                          **({"agent_id": normalized_agent_id} if normalized_agent_id else {}),
                          "section": sections[0]["section"]}} if sections else None)}
            if _serialized_chars(result) > _TOOL_DETAILS_RESULT_MAX_CHARS:
                return {"success": False, "error": "metadata_too_large",
                        "message": "Tool metadata section index exceeds AGENT_STUDIO_TOOL_DETAILS_RESULT_MAX_CHARS.",
                        "tool_id": normalized_tool_id, "tool_sha256": metadata_hash}
            return result
        if section not in metadata:
            return {"success": False, "error": f"Unknown section {section}.",
                    "available_sections": [item["section"] for item in sections]}
        section_text = _canonical_json(metadata[section])
        section_hash = hashlib.sha256(section_text.encode("utf-8")).hexdigest()
        start = parse_offset_cursor(cursor)
        if start > len(section_text):
            return {"success": False, "error": "cursor is beyond the selected metadata section."}
        requested_chars = normalize_page_limit(max_chars, default=_TOOL_DETAILS_CHUNK_MAX_CHARS,
                                               maximum=_TOOL_DETAILS_CHUNK_MAX_CHARS)

        def build_chunk(end: int) -> Dict[str, Any]:
            complete = end >= len(section_text)
            return {"success": True, "tool_id": normalized_tool_id, "agent_id": normalized_agent_id,
                    "detail_mode": "section", "section": section, "tool_sha256": metadata_hash,
                    "sha256": section_hash, "total_chars": len(section_text),
                    "range": {"start": start, "end": end}, "content": section_text[start:end],
                    "complete": complete, "next_cursor": None if complete else end,
                    "next_call": (None if complete else {"tool": "get_tool_details", "arguments": {
                        "tool_id": normalized_tool_id,
                        **({"agent_id": normalized_agent_id} if normalized_agent_id else {}),
                        "section": section, "cursor": end, "max_chars": requested_chars}})}
        low, high = start + 1, min(len(section_text), start + requested_chars)
        fitting_end: Optional[int] = start if start == len(section_text) else None
        while low <= high:
            end = (low + high) // 2
            if _serialized_chars(build_chunk(end)) <= _TOOL_DETAILS_RESULT_MAX_CHARS:
                fitting_end, low = end, end + 1
            else:
                high = end - 1
        if fitting_end is None:
            return {"success": False, "error": "metadata_too_large",
                    "message": "Metadata section identity exceeds AGENT_STUDIO_TOOL_DETAILS_RESULT_MAX_CHARS before one exact character can be returned.",
                    "tool_id": normalized_tool_id, "section": section, "sha256": section_hash}
        return build_chunk(fitting_end)

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
limit/cursor to page through large catalogs; truncated results include next_call.
Use get_tool_details for full schemas and method metadata.""",
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
                    "description": f"Maximum tools in this page (default: {_TOOL_INVENTORY_DEFAULT_ITEMS}, max: {_TOOL_INVENTORY_MAX_ITEMS}); the character budget may return fewer.",
                    "default": _TOOL_INVENTORY_DEFAULT_ITEMS,
                    "minimum": 1,
                    "maximum": _TOOL_INVENTORY_MAX_ITEMS,
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
tools. Oversized parent metadata returns hash-addressed sections and exact
continuation chunks; small method/PDF details remain a single call.""",
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
                "section": {"type": "string", "description": "Optional top-level metadata section returned by an oversized first call."},
                "cursor": {"type": "integer", "minimum": 0, "description": "Exact character offset returned by a section next_call."},
                "max_chars": {"type": "integer", "minimum": 1, "maximum": _TOOL_DETAILS_CHUNK_MAX_CHARS,
                              "description": "Requested exact section characters; JSON escaping may reduce the range."},
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
        description="""Search the runtime repository in read-only mode.

Use this when a curator asks whether the current code supports a feature,
contains a limitation, or implements a specific Agent Studio behavior.

Two search modes:
- content: search file contents and return matching lines with file paths
- files: search repository-relative file paths only

Typical workflow:
1. search_codebase(query="agent_studio", search_mode="files")
2. search_codebase(query="tool policy", search_mode="content", path_glob="backend/src/**/*.py")
3. read_source_file(path="backend/src/api/agent_studio.py", start_line=1400, end_line=1505)

Every page bounds the complete serialized result. Follow next_call for more
matches or exact chunks of one oversized/minified matching line. When
result_set_truncated is true, narrow query or path_glob because the bounded
search catalog contains additional matches. The tool only reads files from the
current repository checkout and never executes code.""",
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
                "cursor": {"type": "string", "description": "Opaque match/line marker returned by next_cursor."},
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
        description="""Read a text file from the runtime repository.

Use this after search_codebase identifies the relevant file. The response is
line-numbered so you can cite the implementation precisely when explaining a
feature, behavior, or limitation to a curator.

The complete serialized result is character-bounded. Oversized lines use exact
hash-addressed chunks and executable next_call continuation. This tool is
read-only and restricted to files inside the current repository checkout.""",
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
                    "description": "Optional inclusive ending line number. Configured line and character bounds still apply.",
                    "minimum": 1,
                },
                "line_char_start": {"type": "integer", "minimum": 0, "default": 0,
                                    "description": "Exact character offset from an oversized-line next_call."},
            },
            "required": ["path"],
        },
        handler=_create_read_source_file_handler(),
        category="codebase",
        tags=["repo", "code", "file", "read-only"],
    )
    logger.debug("Registered: read_source_file")

    logger.info('Registered %s diagnostic tools', registry.get_tool_count())

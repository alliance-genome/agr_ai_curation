"""Shared Agent Studio tool-surface golden cases (ALL-1280).

The golden payloads in fixtures/agent_studio_tool_surface_golden.json were
captured from the pre-compiler ``build_agent_studio_tools`` implementation;
the compiler-backed implementation must reproduce them byte for byte.
"""

from __future__ import annotations

import json

from agents.models.openai_responses import Converter

STUDIO_NAMESPACES = {
    "search_studio_capabilities": ("studio_capabilities", "Live authenticated Agent Studio resource discovery"),
    "get_studio_capability_detail": ("studio_capabilities", "Live authenticated Agent Studio resource discovery"),
    "search_traces": ("trace_overview", "Trace discovery, summaries, conversation, and cost"),
    "get_trace_summary": ("trace_overview", "Trace discovery, summaries, conversation, and cost"),
    "get_trace_payload": ("trace_payload", "Exact trace payload and reconstruction inspection"),
    "refresh_workshop_prompt": ("workshop_authoring", "Workshop inspection and curator-reviewed complete agent proposals"),
    "propose_workshop_draft_update": ("workshop_authoring", "Workshop inspection and curator-reviewed complete agent proposals"),
    "validate_flow": ("flow_authoring", "Curator-reviewed flow proposals, templates, and validation"),
    "get_chat_history": ("studio_history", "Conversation recall, feedback, and failure reporting"),
}


def studio_definitions() -> list[dict]:
    # compile_authorized_tool_universe returns name-sorted definitions.
    return [
        {
            "name": name,
            "description": f"Run {name} for the curator.",
            "input_schema": {
                "type": "object",
                "properties": {"value": {"type": "string"}, "limit": {"type": "integer"}},
                "required": ["value"],
                "additionalProperties": False,
            },
        }
        for name in sorted(STUDIO_NAMESPACES)
    ]


def namespace_for_tool(name: str) -> tuple[str, str]:
    return STUDIO_NAMESPACES[name]


CASES = {
    "authoring_turn": {"forced_tool_name": None},
    "forced_refresh_turn": {"forced_tool_name": "refresh_workshop_prompt"},
}


def provider_payload(tools: list) -> str:
    converted = Converter.convert_tools(tools, [])
    return json.dumps(
        {"tools": converted.tools, "includes": list(converted.includes)},
        sort_keys=True,
        default=str,
    )

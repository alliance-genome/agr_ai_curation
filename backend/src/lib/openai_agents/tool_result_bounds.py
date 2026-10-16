"""Backend import path for shared exact tool-result selection helpers.

The page/detail contract lives in ``agr_ai_curation_runtime`` so isolated
package tools share it. Complete selected content is passed to the shared
model context policy; this module defines no per-tool size quota.
"""

from __future__ import annotations

from typing import Any

from agr_ai_curation_runtime.tool_result_bounds import INVALID_RESULT_CURSOR, RESULT_IDENTITY_KEYS, STALE_RESULT_CURSOR, bounded_json_result, canonical_json, clamp_page_limit, content_sha256, detail_chunk, fit_page, fit_text_window, full_tool_results_requested, full_tool_results_are_requested, invalid_cursor, json_pointer_for_row, parse_offset, resolve_path, serialized_size, value_descriptor


# Arguments a package lookup accepts in addition to its own inputs.
RESULT_VIEW_ARGUMENTS = ("result_offset", "result_sha256", "detail_path", "detail_cursor")


def result_view_schema_properties() -> dict[str, dict[str, Any]]:
    """JSON-schema properties for explicit page/detail selection."""

    return {
        "result_offset": {
            "type": ["integer", "null"],
            "minimum": 0,
            "description": (
                "Optional starting row offset; returns the complete remaining collection."
            ),
        },
        "result_sha256": {
            "type": ["string", "null"],
            "description": (
                "Optional source hash from "
                "result_page, so a changed result is reported instead of mixed."
            ),
        },
        "detail_path": {
            "type": ["string", "null"],
            "description": (
                "Optional JSON pointer selecting one exact value from the result."
            ),
        },
        "detail_cursor": {
            "type": ["integer", "null"],
            "minimum": 0,
            "description": "Optional character offset within the selected detail value.",
        },
    }


__all__ = [
    "INVALID_RESULT_CURSOR",
    "RESULT_IDENTITY_KEYS",
    "RESULT_VIEW_ARGUMENTS",
    "STALE_RESULT_CURSOR",
    "bounded_json_result",
    "canonical_json",
    "clamp_page_limit",
    "content_sha256",
    "detail_chunk",
    "fit_page",
    "fit_text_window",
    "full_tool_results_are_requested",
    "full_tool_results_requested",
    "invalid_cursor",
    "json_pointer_for_row",
    "parse_offset",
    "resolve_path",
    "result_view_schema_properties",
    "serialized_size",
    "value_descriptor",
]

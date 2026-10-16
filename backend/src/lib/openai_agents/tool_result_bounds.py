"""Backend import path and reporting for size-bounded tool results.

The pure page/detail contract lives in ``agr_ai_curation_runtime`` so isolated
package tools share it. This module adds what only the backend may do: read
the budget through config getters, and report unexpected contract escapes to
Sentry through the shared payload-contract helper.
"""

from __future__ import annotations

from typing import Any

from agr_ai_curation_runtime.tool_result_bounds import INVALID_RESULT_CURSOR, RESULT_IDENTITY_KEYS, STALE_RESULT_CURSOR, bounded_json_result, canonical_json, clamp_page_limit, content_sha256, detail_chunk, fit_page, fit_text_window, full_tool_results_requested, full_tool_results_are_requested, invalid_cursor, json_pointer_for_row, parse_offset, resolve_path, serialized_size, value_descriptor


# Arguments a bounded package lookup accepts in addition to its own inputs.
RESULT_VIEW_ARGUMENTS = ("result_offset", "result_sha256", "detail_path", "detail_cursor")


def result_view_schema_properties() -> dict[str, dict[str, Any]]:
    """JSON-schema properties for the bounded page/detail arguments."""

    return {
        "result_offset": {
            "type": ["integer", "null"],
            "minimum": 0,
            "description": (
                "Only when continuing a paged result: the next_offset from its result_page."
            ),
        },
        "result_sha256": {
            "type": ["string", "null"],
            "description": (
                "Only when continuing a paged result: the result_sha256 from its "
                "result_page, so a changed result is reported instead of mixed."
            ),
        },
        "detail_path": {
            "type": ["string", "null"],
            "description": (
                "Only to read a withheld value exactly: its detail_path from result_page."
            ),
        },
        "detail_cursor": {
            "type": ["integer", "null"],
            "minimum": 0,
            "description": "Character cursor for detail_path; use the previous chunk's next_cursor.",
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

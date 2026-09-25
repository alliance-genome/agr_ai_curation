"""Backend import path and reporting for size-bounded tool results.

The pure page/detail contract lives in ``agr_ai_curation_runtime`` so isolated
package tools share it. This module adds what only the backend may do: read
the budget through config getters, and report unexpected contract escapes to
Sentry through the shared payload-contract helper.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from agr_ai_curation_runtime.tool_result_bounds import (
    INVALID_RESULT_CURSOR,
    RESULT_IDENTITY_KEYS,
    STALE_RESULT_CURSOR,
    TOOL_RESULT_BUDGET_UNMET,
    TOOL_RESULT_MAX_BYTES_ENV,
    ToolResultBudgetError,
    bounded_json_result,
    budget_failure,
    canonical_json,
    clamp_page_limit,
    content_sha256,
    detail_chunk,
    fit_page,
    fit_text_window,
    invalid_cursor,
    is_budget_failure,
    json_pointer_for_row,
    parse_offset,
    resolve_path,
    serialized_size,
    value_descriptor,
)
from src.lib.observability.payload_contracts import (
    PayloadContractViolation,
    report_payload_contract_violation,
)

from .config import get_tool_result_max_bytes

# Arguments a bounded package lookup accepts in addition to its own inputs.
RESULT_VIEW_ARGUMENTS = ("result_offset", "result_sha256", "detail_path", "detail_cursor")

_FULL_TOOL_RESULTS_REQUESTED: ContextVar[bool] = ContextVar(
    "full_tool_results_requested",
    default=False,
)


@contextmanager
def full_tool_results_requested() -> Iterator[None]:
    """Let an application-side capture receive a lookup's complete result.

    Validator lookup capture stores the complete provider response and then
    serves the model its own bounded view; the package adapter must not page
    the result underneath it.
    """

    token = _FULL_TOOL_RESULTS_REQUESTED.set(True)
    try:
        yield
    finally:
        _FULL_TOOL_RESULTS_REQUESTED.reset(token)


def full_tool_results_are_requested() -> bool:
    return _FULL_TOOL_RESULTS_REQUESTED.get()


def tool_result_budget() -> int:
    """Configured byte budget for one bounded model-facing tool result."""

    return get_tool_result_max_bytes()


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


def report_tool_result_budget_escape(
    *,
    tool_name: str,
    measured: int | None,
    limit: int,
    component: str,
    field: str | None = None,
    enforced: bool = True,
    correlation: Mapping[str, Any] | None = None,
) -> bool:
    """Report one tool result that could not meet its size contract.

    ``enforced`` is False when the result was observed over budget for a tool
    whose inventory entry records it as not yet bounded; the result itself is
    left unchanged in that case.
    """

    violation = PayloadContractViolation(
        category="tool_result_budget_escape",
        component=component,
        message=(
            f"Tool '{tool_name}' result exceeded the model-facing budget"
            if enforced
            else f"Unbounded tool '{tool_name}' returned a result over the model-facing budget"
        ),
        measured=measured,
        unit="bytes",
        limit=limit,
        setting=TOOL_RESULT_MAX_BYTES_ENV,
        field=field,
    )
    return report_payload_contract_violation(
        violation,
        phase="tool_result",
        tool_name=tool_name,
        correlation={**dict(correlation or {}), "enforced": enforced},
        level="error" if enforced else "warning",
    )


def report_budget_failure_result(
    result: Any,
    *,
    tool_name: str,
    component: str,
    correlation: Mapping[str, Any] | None = None,
) -> bool:
    """Report a compact ``tool_result_budget_unmet`` failure returned by a tool.

    A failure dict is marked once reported, so a tool that reports its own
    failure and the adapter that forwards it do not capture it twice.
    """

    payload = result.model_dump(mode="json") if hasattr(result, "model_dump") else result
    if isinstance(payload, str):
        payload = json.loads(payload)
    bounds = payload.get("result_bounds") if isinstance(payload, Mapping) else None
    if isinstance(bounds, dict) and bounds.get("reported"):
        return False
    details = bounds if isinstance(bounds, Mapping) else {}
    captured = report_tool_result_budget_escape(
        tool_name=tool_name,
        measured=details.get("measured_bytes"),
        limit=int(details.get("limit_bytes") or tool_result_budget()),
        component=component,
        field=details.get("field"),
        correlation=correlation,
    )
    if isinstance(result, dict) and isinstance(result.get("result_bounds"), dict):
        result["result_bounds"]["reported"] = True
    return captured


__all__ = [
    "INVALID_RESULT_CURSOR",
    "RESULT_IDENTITY_KEYS",
    "RESULT_VIEW_ARGUMENTS",
    "STALE_RESULT_CURSOR",
    "TOOL_RESULT_BUDGET_UNMET",
    "TOOL_RESULT_MAX_BYTES_ENV",
    "ToolResultBudgetError",
    "bounded_json_result",
    "budget_failure",
    "canonical_json",
    "clamp_page_limit",
    "content_sha256",
    "detail_chunk",
    "fit_page",
    "fit_text_window",
    "full_tool_results_are_requested",
    "full_tool_results_requested",
    "invalid_cursor",
    "is_budget_failure",
    "json_pointer_for_row",
    "parse_offset",
    "report_budget_failure_result",
    "report_tool_result_budget_escape",
    "resolve_path",
    "result_view_schema_properties",
    "serialized_size",
    "tool_result_budget",
    "value_descriptor",
]

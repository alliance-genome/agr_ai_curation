"""
Provider Budget Utilities for Claude-Specific Endpoints

Measures serialized response characters against Agent Studio's provider boundary
and retains token estimates as advisory metadata.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional

from ..config import get_agent_studio_provider_tool_result_inline_max_chars


# Configuration
CHARS_PER_TOKEN = 4  # Heuristic: 4 characters ≈ 1 token


@dataclass
class TokenBudgetResult:
    """Result of a serialized provider-result budget check."""
    estimated_tokens: int
    serialized_chars: int
    max_serialized_chars: int
    within_budget: bool
    warning: Optional[str] = None


def estimate_tokens(text: str) -> int:
    """
    Estimate token count using character heuristic.

    Uses 4 chars ≈ 1 token, which is accurate enough for budget management.
    This avoids API calls to tokenizers while providing reasonable estimates.

    Args:
        text: The text to estimate tokens for

    Returns:
        Estimated token count
    """
    if not text:
        return 0
    return len(text) // CHARS_PER_TOKEN


def check_budget(
    data: Any,
    max_chars: int | None = None,
) -> TokenBudgetResult:
    """
    Check whether data fits within the serialized-character boundary.

    Args:
        data: Any JSON-serializable data to check
        max_chars: Maximum serialized response characters. Defaults to the
            shared Agent Studio provider inline-result boundary.

    Returns:
        TokenBudgetResult with estimated tokens, budget status, and warning if exceeded
    """
    serialized = json.dumps(data, default=str)
    serialized_chars = len(serialized)
    resolved_max_chars = max_chars or get_agent_studio_provider_tool_result_inline_max_chars()
    estimated = estimate_tokens(serialized)
    within_budget = serialized_chars <= resolved_max_chars

    warning = None
    if not within_budget:
        warning = (
            "Response exceeds the Agent Studio serialized-character boundary "
            f"({serialized_chars:,} chars > {resolved_max_chars:,}). "
            "Use the response collection inventory and continuation arguments."
        )

    return TokenBudgetResult(
        estimated_tokens=estimated,
        serialized_chars=serialized_chars,
        max_serialized_chars=resolved_max_chars,
        within_budget=within_budget,
        warning=warning
    )


def create_token_info_dict(data: Any, max_chars: int | None = None) -> Dict[str, Any]:
    """
    Create a token info dictionary suitable for API responses.

    Args:
        data: The response data to check
        max_chars: Maximum serialized response characters

    Returns:
        Dictionary with token info fields
    """
    result = check_budget(data, max_chars)
    return {
        "estimated_tokens": result.estimated_tokens,
        "serialized_chars": result.serialized_chars,
        "max_serialized_chars": result.max_serialized_chars,
        "within_budget": result.within_budget,
        "warning": result.warning
    }


def create_lightweight_tool_call_summary(tool_call: Dict) -> Dict:
    """
    Create a lightweight summary of a tool call for listing.

    Removes full results, keeping only essential info for overview.

    Args:
        tool_call: Full tool call dictionary

    Returns:
        Lightweight summary dictionary
    """
    # Convert datetime to ISO string if needed
    time_value = tool_call.get("time")
    if isinstance(time_value, datetime):
        time_value = time_value.isoformat()

    def display_value(value, default: str = "N/A") -> str:
        if value is None:
            return default
        text = str(value)
        return text if text else default

    summary: Dict[str, Any] = {
        "call_id": display_value(tool_call.get("call_id"), "N/A"),
        "name": display_value(tool_call.get("name"), "unknown"),
        "time": display_value(time_value, "N/A"),
        "duration": display_value(tool_call.get("duration"), "N/A"),
        "status": display_value(tool_call.get("status"), "N/A"),
    }
    domain_envelope = tool_call.get("domain_envelope")
    if isinstance(domain_envelope, dict) and domain_envelope.get("found"):
        summary["domain_envelope"] = domain_envelope

    # Add input summary (truncate if too long)
    input_data = tool_call.get("input", {})
    if isinstance(input_data, dict):
        # Create a summary of input parameters
        input_summary_parts = []
        for key, value in input_data.items():
            if key in ["calling", "tool_string"]:  # Skip verbose fields
                continue
            value_str = str(value)
            if len(value_str) > 50:
                value_str = value_str[:50] + "..."
            input_summary_parts.append(f"{key}={value_str}")
        summary["input_summary"] = ", ".join(input_summary_parts[:3])  # Max 3 params
    else:
        summary["input_summary"] = str(input_data)[:100] if input_data else ""

    # Add result summary if available
    tool_result = tool_call.get("tool_result")
    if isinstance(tool_result, dict):
        summary["result_summary"] = display_value(tool_result.get("summary"), "N/A")
    else:
        summary["result_summary"] = "N/A"

    if isinstance(domain_envelope, dict) and domain_envelope.get("found"):
        envelope_ids = ", ".join(domain_envelope.get("envelope_ids", [])[:3])
        counts = domain_envelope.get("summary", {})
        blocker_count = counts.get("blocker_count", 0)
        domain_bits = []
        if envelope_ids:
            domain_bits.append(f"envelopes={envelope_ids}")
        if blocker_count:
            domain_bits.append(f"blockers={blocker_count}")
        if domain_bits:
            summary["result_summary"] = f"{summary['result_summary']} | Domain envelope: {', '.join(domain_bits)}"

    return summary

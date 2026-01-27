"""
Token Budget Utilities for Claude-Specific Endpoints

Provides token estimation and budget checking for responses sent to Claude/Opus.
Uses a simple character-based heuristic (4 chars ≈ 1 token) which is accurate
enough for budget management without requiring external API calls.
"""

import copy
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional, List, Dict


# Configuration
MAX_TOKENS_DEFAULT = 50000  # Default budget per response (leaves headroom in 200K window)
CHARS_PER_TOKEN = 4  # Heuristic: 4 characters ≈ 1 token


@dataclass
class TokenBudgetResult:
    """Result of a token budget check."""
    estimated_tokens: int
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


def estimate_tokens_for_data(data: Any) -> int:
    """
    Estimate token count for arbitrary data by serializing to JSON.

    Args:
        data: Any JSON-serializable data

    Returns:
        Estimated token count
    """
    if data is None:
        return 0
    if isinstance(data, str):
        return estimate_tokens(data)
    try:
        serialized = json.dumps(data, default=str)
        return estimate_tokens(serialized)
    except (TypeError, ValueError):
        # Fallback for non-serializable data
        return estimate_tokens(str(data))


def check_budget(data: Any, max_tokens: int = MAX_TOKENS_DEFAULT) -> TokenBudgetResult:
    """
    Check if data fits within token budget.

    Args:
        data: Any JSON-serializable data to check
        max_tokens: Maximum allowed tokens (default: 50,000)

    Returns:
        TokenBudgetResult with estimated tokens, budget status, and warning if exceeded
    """
    estimated = estimate_tokens_for_data(data)
    within_budget = estimated <= max_tokens

    warning = None
    if not within_budget:
        warning = (
            f"Response exceeds token budget ({estimated:,} tokens > {max_tokens:,}). "
            "Consider using pagination or filtering for smaller responses."
        )

    return TokenBudgetResult(
        estimated_tokens=estimated,
        within_budget=within_budget,
        warning=warning
    )


def create_token_info_dict(data: Any, max_tokens: int = MAX_TOKENS_DEFAULT) -> Dict[str, Any]:
    """
    Create a token info dictionary suitable for API responses.

    Args:
        data: The response data to check
        max_tokens: Maximum allowed tokens

    Returns:
        Dictionary with token info fields
    """
    result = check_budget(data, max_tokens)
    return {
        "estimated_tokens": result.estimated_tokens,
        "within_budget": result.within_budget,
        "warning": result.warning
    }


def truncate_tool_call_results(
    tool_calls: List[Dict],
    max_tokens: int = MAX_TOKENS_DEFAULT
) -> List[Dict]:
    """
    Truncate tool call results to fit within budget.

    Truncation priority (preserves most important data):
    1. Remove tool_result.raw (often 50-100KB)
    2. Truncate tool_result.parsed.hits to first 3 items
    3. Truncate tool_result.parsed.data to first 3 items
    4. Truncate content fields to 500 chars
    5. NEVER remove: name, input, call_id, status, tool_result.summary

    Args:
        tool_calls: List of tool call dictionaries
        max_tokens: Maximum token budget

    Returns:
        Truncated tool calls list
    """
    if not tool_calls:
        return tool_calls

    # Check if already within budget
    if check_budget(tool_calls, max_tokens).within_budget:
        return tool_calls

    # Deep copy to avoid modifying original
    truncated = copy.deepcopy(tool_calls)

    # Step 1: Remove raw results (biggest savings)
    for tc in truncated:
        if isinstance(tc.get("tool_result"), dict):
            tc["tool_result"].pop("raw", None)

    if check_budget(truncated, max_tokens).within_budget:
        return truncated

    # Step 2: Truncate parsed hits/data arrays
    for tc in truncated:
        tool_result = tc.get("tool_result")
        if isinstance(tool_result, dict):
            parsed = tool_result.get("parsed")
            if isinstance(parsed, dict):
                # Truncate hits to first 3
                if "hits" in parsed and isinstance(parsed["hits"], list):
                    if len(parsed["hits"]) > 3:
                        parsed["hits"] = parsed["hits"][:3]
                        parsed["hits_truncated"] = True

                # Truncate data to first 3
                if "data" in parsed and isinstance(parsed["data"], list):
                    if len(parsed["data"]) > 3:
                        parsed["data"] = parsed["data"][:3]
                        parsed["data_truncated"] = True

                # Truncate section content
                if "section" in parsed and isinstance(parsed["section"], dict):
                    section = parsed["section"]
                    if "full_content" in section and len(str(section["full_content"])) > 500:
                        section["full_content"] = str(section["full_content"])[:500] + "...[truncated]"
                    if "content_preview" in section and len(str(section["content_preview"])) > 300:
                        section["content_preview"] = str(section["content_preview"])[:300] + "...[truncated]"

                # Truncate subsection content
                if "subsection" in parsed and isinstance(parsed["subsection"], dict):
                    subsection = parsed["subsection"]
                    if "full_content" in subsection and len(str(subsection["full_content"])) > 500:
                        subsection["full_content"] = str(subsection["full_content"])[:500] + "...[truncated]"

    if check_budget(truncated, max_tokens).within_budget:
        return truncated

    # Step 3: Truncate individual hit/data item content
    for tc in truncated:
        tool_result = tc.get("tool_result")
        if isinstance(tool_result, dict):
            parsed = tool_result.get("parsed")
            if isinstance(parsed, dict):
                # Truncate content in hits
                if "hits" in parsed and isinstance(parsed["hits"], list):
                    for hit in parsed["hits"]:
                        if isinstance(hit, dict) and "content" in hit:
                            if len(str(hit["content"])) > 200:
                                hit["content"] = str(hit["content"])[:200] + "...[truncated]"

    return truncated


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

    summary = {
        "call_id": tool_call.get("call_id", "N/A"),
        "name": tool_call.get("name", "unknown"),
        "time": time_value,
        "duration": tool_call.get("duration", "N/A"),
        "status": tool_call.get("status", "N/A"),
    }

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
        summary["result_summary"] = tool_result.get("summary", "N/A")
    else:
        summary["result_summary"] = "N/A"

    return summary

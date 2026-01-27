"""
Response models for Claude-specific API endpoints.

These models standardize responses with token metadata for budget-aware
interactions with Claude/Opus in the workflow analysis feature.
"""

from pydantic import BaseModel, Field
from typing import Any, Optional, List, Dict


class TokenInfo(BaseModel):
    """Token budget information included in all Claude API responses."""
    estimated_tokens: int = Field(
        ...,
        description="Estimated token count for this response (4 chars ≈ 1 token)"
    )
    within_budget: bool = Field(
        ...,
        description="Whether response is within 50K token budget"
    )
    warning: Optional[str] = Field(
        default=None,
        description="Warning message if budget exceeded or data truncated"
    )


class PaginationInfo(BaseModel):
    """Pagination metadata for paginated responses."""
    page: int = Field(..., description="Current page number (1-indexed)")
    page_size: int = Field(..., description="Items per page")
    total_items: int = Field(..., description="Total number of items")
    total_pages: int = Field(..., description="Total number of pages")
    has_next: bool = Field(..., description="Whether there is a next page")
    has_prev: bool = Field(..., description="Whether there is a previous page")


class ClaudeTraceResponse(BaseModel):
    """Standard response wrapper for Claude trace endpoints."""
    status: str = Field(default="success", description="Response status")
    data: Any = Field(..., description="Response data")
    token_info: TokenInfo = Field(..., description="Token budget information")


class TraceSummaryData(BaseModel):
    """Lightweight trace summary data."""
    trace_id: str
    trace_id_short: str
    trace_name: Optional[str] = None
    duration_seconds: Optional[float] = None
    total_cost: Optional[float] = None
    total_tokens: Optional[int] = None
    tool_call_count: int = 0
    unique_tools: List[str] = []
    has_errors: bool = False
    context_overflow_detected: bool = False
    timestamp: Optional[str] = None


class ToolCallSummaryItem(BaseModel):
    """Lightweight summary of a single tool call."""
    index: int = Field(..., description="Index in the tool calls list")
    call_id: str = Field(..., description="Unique call identifier")
    name: str = Field(..., description="Tool name")
    time: Optional[str] = Field(default=None, description="Timestamp")
    duration: str = Field(default="N/A", description="Duration string")
    status: str = Field(default="N/A", description="Call status")
    input_summary: str = Field(default="", description="Brief summary of input parameters")
    result_summary: str = Field(default="N/A", description="Brief summary of result")


class ToolCallsSummaryData(BaseModel):
    """Summary of all tool calls without full results."""
    total_count: int = Field(..., description="Total number of tool calls")
    unique_tools: List[str] = Field(..., description="List of unique tool names used")
    tool_calls: List[ToolCallSummaryItem] = Field(..., description="Lightweight call summaries")
    has_duplicates: bool = Field(default=False, description="Whether duplicate calls were detected")
    duplicate_count: int = Field(default=0, description="Number of duplicate call groups")


class ToolCallsSummaryResponse(BaseModel):
    """Response for tool calls summary endpoint."""
    status: str = Field(default="success")
    data: ToolCallsSummaryData
    token_info: TokenInfo


class PaginatedToolCallsResponse(BaseModel):
    """Response for paginated tool calls endpoint."""
    status: str = Field(default="success")
    tool_calls: List[Dict] = Field(..., description="Full tool call details")
    pagination: PaginationInfo
    token_info: TokenInfo
    filter_applied: Optional[str] = Field(
        default=None,
        description="Tool name filter if applied"
    )


class SingleToolCallResponse(BaseModel):
    """Response for single tool call detail endpoint."""
    status: str = Field(default="success")
    tool_call: Dict = Field(..., description="Full tool call details")
    token_info: TokenInfo


class ConversationData(BaseModel):
    """User query and assistant response."""
    user_query: Optional[str] = None
    assistant_response: Optional[str] = None
    response_length: int = 0


class ConversationResponse(BaseModel):
    """Response for conversation endpoint."""
    status: str = Field(default="success")
    data: ConversationData
    token_info: TokenInfo

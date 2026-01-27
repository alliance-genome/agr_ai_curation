"""
Pydantic models for Prompt Explorer feature.

Defines data structures for:
- Agent prompt metadata (base prompts, MOD rules)
- Agent documentation (capabilities, data sources, limitations)
- Chat messages for Opus conversations
- Trace context for execution history
"""

from typing import List, Optional, Dict
from pydantic import BaseModel, Field
from datetime import datetime


# ============================================================================
# Agent Documentation Models
# ============================================================================

class AgentCapability(BaseModel):
    """A single capability of an agent with optional example."""
    name: str = Field(..., description="Short name of the capability (e.g., 'Exact symbol lookup')")
    description: str = Field(..., description="Plain English description of what it does")
    example_query: Optional[str] = Field(None, description="Example query a curator might ask")
    example_result: Optional[str] = Field(None, description="Example result the agent would return")


class DataSourceInfo(BaseModel):
    """Information about a data source an agent can access."""
    name: str = Field(..., description="Name of the data source (e.g., 'Alliance Curation Database')")
    description: str = Field(..., description="What data is available from this source")
    species_supported: Optional[List[str]] = Field(
        None, description="List of species/MOD codes supported (e.g., ['WB', 'FB', 'MGI'])"
    )
    data_types: Optional[List[str]] = Field(
        None, description="Types of data available (e.g., ['genes', 'alleles', 'strains'])"
    )


class AgentDocumentation(BaseModel):
    """Curator-friendly documentation for an agent."""
    summary: str = Field(..., description="One-line summary of what the agent does")
    capabilities: List[AgentCapability] = Field(
        default_factory=list, description="List of agent capabilities with examples"
    )
    data_sources: List[DataSourceInfo] = Field(
        default_factory=list, description="Data sources the agent can access"
    )
    limitations: List[str] = Field(
        default_factory=list, description="Known limitations as simple strings"
    )


# ============================================================================
# Prompt Catalog Models
# ============================================================================

class MODRuleInfo(BaseModel):
    """MOD-specific rule information."""
    mod_id: str = Field(..., description="MOD identifier (e.g., 'WB', 'FB', 'MGI')")
    content: str = Field(..., description="MOD rule content (YAML or processed text)")
    source_file: str = Field(..., description="Path to source YAML file (legacy) or 'database'")
    description: Optional[str] = Field(None, description="Brief description of what the MOD rule adds")

    # Version metadata (from prompt_templates table)
    prompt_id: Optional[str] = Field(None, description="UUID of the prompt_templates row")
    prompt_version: Optional[int] = Field(None, description="Version number of this prompt")
    created_at: Optional[datetime] = Field(None, description="When this version was created")
    created_by: Optional[str] = Field(None, description="Who created this version")


class PromptInfo(BaseModel):
    """Information about a single agent's prompt."""
    agent_id: str = Field(..., description="Unique agent identifier (e.g., 'supervisor', 'gene_expression')")
    agent_name: str = Field(..., description="Human-readable agent name")
    description: str = Field(..., description="Brief description of what the agent does")
    base_prompt: str = Field(..., description="Base prompt instructions (before MOD injection)")
    source_file: str = Field(..., description="Path to the agent source file (legacy) or 'database'")
    has_mod_rules: bool = Field(False, description="Whether this agent supports MOD-specific rules")
    mod_rules: Dict[str, MODRuleInfo] = Field(
        default_factory=dict,
        description="MOD-specific rules keyed by MOD ID"
    )
    tools: List[str] = Field(
        default_factory=list,
        description="List of tools available to this agent"
    )
    model: Optional[str] = Field(None, description="Model used by this agent (if known)")
    subcategory: Optional[str] = Field(None, description="Subcategory for palette grouping (e.g., 'PDF Extraction', 'Data Validation', 'Output')")

    # Curator-friendly documentation
    documentation: Optional[AgentDocumentation] = Field(
        None, description="Curator-friendly documentation with capabilities, data sources, and limitations"
    )

    # Version metadata (from prompt_templates table)
    prompt_id: Optional[str] = Field(None, description="UUID of the prompt_templates row")
    prompt_version: Optional[int] = Field(None, description="Version number of this prompt")
    created_at: Optional[datetime] = Field(None, description="When this version was created")
    created_by: Optional[str] = Field(None, description="Who created this version")


class AgentPrompts(BaseModel):
    """Collection of prompts for a category of agents."""
    category: str = Field(..., description="Category name (e.g., 'Routing', 'Extraction', 'Validation')")
    agents: List[PromptInfo] = Field(default_factory=list, description="Agents in this category")


class PromptCatalog(BaseModel):
    """Complete catalog of all agent prompts."""
    categories: List[AgentPrompts] = Field(
        default_factory=list,
        description="Prompts organized by category"
    )
    total_agents: int = Field(0, description="Total number of agents")
    available_mods: List[str] = Field(
        default_factory=list,
        description="List of MODs with available rules"
    )
    last_updated: datetime = Field(
        default_factory=datetime.utcnow,
        description="When the catalog was last refreshed"
    )


# ============================================================================
# Chat Models (for Opus conversations)
# ============================================================================

class ChatMessage(BaseModel):
    """A single message in the Opus conversation."""
    role: str = Field(..., description="Message role: 'user' or 'assistant'")
    content: str = Field(..., description="Message content")
    timestamp: Optional[datetime] = Field(None, description="When the message was sent")


class FlowNodeContext(BaseModel):
    """Simplified flow node for chat context."""
    id: str
    agent_id: str
    agent_display_name: str
    task_instructions: Optional[str] = None  # For task_input nodes
    custom_instructions: Optional[str] = None
    input_source: str = "previous_output"  # 'previous_output' or 'custom'
    custom_input: Optional[str] = None
    output_key: str


class FlowEdgeContext(BaseModel):
    """Simplified flow edge for chat context."""
    source: str
    target: str


class FlowContextDefinition(BaseModel):
    """Flow definition passed to chat for validation/discussion."""
    nodes: List[FlowNodeContext] = Field(default_factory=list)
    edges: List[FlowEdgeContext] = Field(default_factory=list)


class ChatContext(BaseModel):
    """Context for the Opus chat session."""
    selected_agent_id: Optional[str] = Field(
        None,
        description="ID of currently selected agent in the prompt browser"
    )
    selected_mod_id: Optional[str] = Field(
        None,
        description="ID of currently selected MOD (if viewing MOD-specific rules)"
    )
    trace_id: Optional[str] = Field(
        None,
        description="Trace ID if opened from a chat message"
    )
    view_mode: str = Field(
        "base",
        description="Current view mode: 'base', 'mod', or 'combined'"
    )
    # Flow context (when on Flows tab)
    active_tab: Optional[str] = Field(
        None,
        description="Which tab is active: 'agents' or 'flows'"
    )
    flow_name: Optional[str] = Field(
        None,
        description="Name of the flow being edited"
    )
    flow_definition: Optional[FlowContextDefinition] = Field(
        None,
        description="Current flow definition being edited"
    )


class ChatRequest(BaseModel):
    """Request to send a message to Opus."""
    messages: List[ChatMessage] = Field(..., description="Conversation history")
    context: Optional[ChatContext] = Field(None, description="Current UI context")


class ChatResponse(BaseModel):
    """Non-streaming response from Opus (for error cases)."""
    content: str = Field(..., description="Response content")
    error: Optional[str] = Field(None, description="Error message if any")


# ============================================================================
# Trace Context Models
# ============================================================================

class ToolCallInfo(BaseModel):
    """Information about a single tool call in a trace."""
    name: str = Field(..., description="Tool name")
    input: Dict = Field(default_factory=dict, description="Tool input parameters")
    output_preview: Optional[str] = Field(None, description="Truncated output preview")
    duration_ms: Optional[int] = Field(None, description="Duration in milliseconds")
    status: str = Field("completed", description="Status: 'completed', 'error'")


class RoutingDecision(BaseModel):
    """A routing decision made by the supervisor."""
    from_agent: str = Field(..., description="Source agent (usually 'supervisor')")
    to_agent: str = Field(..., description="Target agent")
    reason: Optional[str] = Field(None, description="Why this routing was chosen")
    timestamp: Optional[datetime] = Field(None, description="When the decision was made")


class PromptExecution(BaseModel):
    """Information about a prompt that was executed in a trace."""
    agent_id: str = Field(..., description="Agent that executed")
    agent_name: str = Field(..., description="Human-readable agent name")
    prompt_preview: str = Field(..., description="First ~500 chars of the prompt used")
    mod_applied: Optional[str] = Field(None, description="MOD rules that were applied (if any)")
    model: Optional[str] = Field(None, description="Model used")
    tokens_used: Optional[int] = Field(None, description="Tokens consumed")


class TraceContext(BaseModel):
    """
    Enriched trace context for display in Prompt Explorer.

    Provides a summary of what happened during a chat interaction,
    including which prompts fired, tool calls, and routing decisions.
    """
    trace_id: str = Field(..., description="Langfuse trace ID")
    session_id: Optional[str] = Field(None, description="Chat session ID")
    timestamp: datetime = Field(..., description="When the trace started")

    # User interaction
    user_query: str = Field(..., description="Original user query")
    final_response_preview: str = Field(
        ...,
        description="First ~500 chars of the final response"
    )

    # Execution details
    prompts_executed: List[PromptExecution] = Field(
        default_factory=list,
        description="Prompts that were executed (in order)"
    )
    routing_decisions: List[RoutingDecision] = Field(
        default_factory=list,
        description="Routing decisions made by supervisor"
    )
    tool_calls: List[ToolCallInfo] = Field(
        default_factory=list,
        description="Tool calls made during execution"
    )

    # Metrics
    total_duration_ms: Optional[int] = Field(None, description="Total execution time")
    total_tokens: Optional[int] = Field(None, description="Total tokens used")
    agent_count: int = Field(0, description="Number of agents involved")


# ============================================================================
# API Response Models
# ============================================================================

class PromptCatalogResponse(BaseModel):
    """API response for GET /api/prompt-explorer/catalog"""
    catalog: PromptCatalog


class TraceContextResponse(BaseModel):
    """API response for GET /api/prompt-explorer/trace/{trace_id}/context"""
    context: TraceContext


class ErrorResponse(BaseModel):
    """Standard error response."""
    error: str = Field(..., description="Error message")
    detail: Optional[str] = Field(None, description="Additional details")

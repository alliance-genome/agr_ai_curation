"""
Pydantic models for Prompt Explorer feature.

Defines data structures for:
- Agent prompt metadata (base prompts, group rules)
- Agent documentation (capabilities, data sources, limitations, usage guidance)
- Chat messages for Agent Studio AI Chat conversations
- Trace context for execution history
"""

from typing import Annotated, Dict, List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, model_validator
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
        None, description="List of species/group codes supported (e.g., ['WB', 'FB', 'MGI'])"
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
    use_when: List[str] = Field(
        default_factory=list,
        description="Curator-voice situations where this agent is the right choice",
    )
    avoid_when: List[str] = Field(
        default_factory=list,
        description="Curator-voice situations where another agent is the right choice",
    )
    # Content rule: notes appear on validation agents only, and each note states
    # whether that check runs automatically. Whether it runs is decided by the
    # domain packs' ACTIVE validator bindings (under_development bindings do not
    # run); the UI renders the text verbatim and applies no category logic.
    note: str = Field(
        default="",
        description="Curator-voice note shown above the guidance, verbatim from docs.yaml",
    )


# ============================================================================
# Prompt Catalog Models
# ============================================================================

class GroupRuleInfo(BaseModel):
    """Organization-group-specific rule information."""

    model_config = ConfigDict(extra="forbid")

    group_id: str = Field(..., description="Group identifier (e.g., 'WB', 'FB', 'MGI')")
    content: str = Field(..., description="Group rule content (YAML or processed text)")
    source_file: str = Field(..., description="Path to source YAML file (legacy) or 'database'")
    description: Optional[str] = Field(None, description="Brief description of what the group rule adds")

    # Version metadata (from prompt_templates table)
    prompt_id: Optional[str] = Field(None, description="UUID of the prompt_templates row")
    prompt_version: Optional[int] = Field(None, description="Version number of this prompt")
    created_at: Optional[datetime] = Field(None, description="When this version was created")
    created_by: Optional[str] = Field(None, description="Who created this version")


class PromptLayerInfo(BaseModel):
    """Structured prompt layer metadata for Agent Studio display."""

    id: str = Field(..., description="Stable layer identifier")
    kind: Literal[
        "core_static",
        "core_generated",
        "base_prompt",
        "group_rules",
        "curator_overlay",
        "runtime_context",
    ] = Field(..., description="Prompt layer kind")
    title: str = Field(..., description="Curator-readable layer title")
    content: str = Field(..., description="Layer prompt text")
    provenance: str = Field(..., description="Where this layer came from")
    editable: bool = Field(..., description="Whether curators may edit this layer")
    locked: bool = Field(..., description="Whether backend APIs protect this layer from editing")
    source_ref: str = Field(..., description="Stable source reference for audit/display")
    hash: str = Field(..., description="Stable layer content hash")


class PromptInfo(BaseModel):
    """Information about a single agent's prompt."""

    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(..., description="Unique agent identifier (e.g., 'supervisor', 'gene_expression')")
    agent_name: str = Field(..., description="Human-readable agent name")
    description: str = Field(..., description="Brief description of what the agent does")
    base_prompt: str = Field(..., description="Base prompt instructions (before group-rule injection)")
    source_file: str = Field(..., description="Path to the agent source file (legacy) or 'database'")
    has_group_rules: bool = Field(False, description="Whether this agent supports group-specific rules")
    group_rules: Dict[str, GroupRuleInfo] = Field(
        default_factory=dict,
        description="Group-specific rules keyed by group ID",
    )
    prompt_layers: List[PromptLayerInfo] = Field(
        default_factory=list,
        description="Structured effective prompt layers with lock/editability metadata",
    )
    effective_prompt_hash: Optional[str] = Field(
        None,
        description="Hash of the effective prompt represented by prompt_layers",
    )
    layer_manifest: Dict[str, object] = Field(
        default_factory=dict,
        description="Raw structured layer manifest for Agent Studio and tools",
    )
    prompt_layer_error: Optional[str] = Field(
        None,
        description="Prompt-layer assembly error surfaced to Agent Studio instead of hidden as empty layers",
    )
    custom_prompt_overlay_status: Optional[Literal["clean", "deduplicated", "needs_review"]] = Field(
        None,
        description="Legacy copied-layer normalization status for custom-agent main prompt text",
    )
    custom_prompt_removed_layer_kinds: List[str] = Field(
        default_factory=list,
        description="Locked/generated parent layer kinds removed from legacy custom-agent prompt text",
    )
    custom_prompt_warning: Optional[str] = Field(
        None,
        description="Coordinator-review warning for ambiguous legacy custom-agent prompt text",
    )
    tools: List[str] = Field(
        default_factory=list,
        description="List of tools available to this agent"
    )
    model: Optional[str] = Field(None, description="Model used by this agent (if known)")
    subcategory: Optional[str] = Field(None, description="Subcategory for palette grouping (e.g., 'PDF Extraction', 'Data Validation', 'Output')")
    show_in_palette: bool = Field(True, description="Whether this agent should appear in the Flow Builder palette")

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
    model_config = ConfigDict(extra="forbid")

    categories: List[AgentPrompts] = Field(
        default_factory=list,
        description="Prompts organized by category"
    )
    total_agents: int = Field(0, description="Total number of agents")
    available_groups: List[str] = Field(
        default_factory=list,
        description="List of groups with available rules",
    )
    last_updated: datetime = Field(
        default_factory=datetime.utcnow,
        description="When the catalog was last refreshed"
    )


# ============================================================================
# Chat models for Agent Studio AI Chat conversations
# ============================================================================

class ChatMessage(BaseModel):
    """A single message in an AI Chat conversation."""
    role: str = Field(..., description="Message role: 'user' or 'assistant'")
    content: str = Field(..., description="Message content")
    timestamp: Optional[datetime] = Field(None, description="When the message was sent")


class FlowNodeContext(BaseModel):
    """Lossless save-equivalent flow node for chat context."""
    id: str
    node_type: str = "agent"
    position: Dict[str, float]
    agent_id: str
    agent_display_name: str
    agent_description: Optional[str] = None
    task_instructions: Optional[str] = None  # For task_input nodes
    step_goal: Optional[str] = None
    custom_instructions: Optional[str] = None
    prompt_version: Optional[int] = None
    include_evidence: Optional[bool] = None
    output_filename_template: Optional[str] = None
    projection_plan: Optional[Dict[str, object]] = None
    output_key: str
    validation_attachments: List[Dict[str, object]] = Field(default_factory=list)
    validation_groups: List[Dict[str, object]] = Field(default_factory=list)


class FlowEdgeContext(BaseModel):
    """Lossless save-equivalent flow edge for chat context."""
    id: str
    source: str
    target: str
    role: str = "control_flow"
    satisfies_binding_id: Optional[str] = None
    replaces_attachment_id: Optional[str] = None
    condition: Optional[Dict[str, object]] = None


class FlowContextDefinition(BaseModel):
    """Complete editable flow definition passed to chat at send time."""
    version: Literal["1.1"] = "1.1"
    task_instructions_default_only: Optional[bool] = None
    entry_node_id: Optional[str] = None
    nodes: List[FlowNodeContext] = Field(default_factory=list)
    edges: List[FlowEdgeContext] = Field(default_factory=list)


class AgentWorkshopContext(BaseModel):
    """Agent Workshop context passed to AI Chat."""

    model_config = ConfigDict(extra="forbid")

    getting_started_mode: Optional[Literal["template", "scratch", "clone"]] = None
    template_source: Optional[str] = None
    template_name: Optional[str] = None
    custom_agent_id: Optional[str] = None
    custom_agent_name: Optional[str] = None
    draft_name: Optional[str] = None
    draft_description: Optional[str] = None
    draft_icon: Optional[str] = None
    draft_visibility: Optional[Literal["private", "project"]] = None
    draft_allowed_group_ids: Optional[List[str]] = None
    inherited_allowed_group_ids: Optional[List[str]] = None
    include_group_rules: Optional[bool] = None
    selected_group_id: Optional[str] = None
    prompt_draft: Optional[str] = None
    selected_group_prompt_draft: Optional[str] = None
    group_prompt_overrides: Optional[Dict[str, str]] = None
    draft_is_dirty: Optional[bool] = None
    draft_fingerprint: Optional[
        Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    ] = None
    custom_agent_updated_at: Optional[str] = None
    group_prompt_override_count: Optional[int] = None
    has_group_prompt_overrides: Optional[bool] = None
    draft_tool_ids: Optional[List[str]] = None
    draft_model_id: Optional[str] = None
    draft_model_reasoning: Optional[str] = None
    draft_output_schema_key: Optional[str] = None


class ChatContext(BaseModel):
    """Context for the AI Chat session."""

    model_config = ConfigDict(extra="forbid")

    selected_agent_id: Optional[str] = Field(
        None,
        description="ID of currently selected agent in the prompt browser"
    )
    selected_group_id: Optional[str] = Field(
        None,
        description="ID of currently selected group (if viewing group-specific rules)",
    )
    trace_id: Optional[str] = Field(
        None,
        description="Trace ID if opened from a chat message"
    )
    session_id: Optional[str] = Field(
        None,
        description="Durable or seeded session ID carried with the Agent Studio chat context",
    )
    view_mode: Literal["base", "group", "combined"] = Field(
        "base",
        description="Current view mode: 'base', 'group', or 'combined'"
    )
    # Flow context (when on Flows tab)
    active_tab: Optional[str] = Field(
        None,
        description="Which tab is active: 'agents', 'flows', or 'agent_workshop'"
    )
    flow_id: Optional[str] = Field(None, description="Stable saved-flow identity")
    flow_name: Optional[str] = Field(
        None,
        description="Name of the flow being edited"
    )
    flow_description: Optional[str] = Field(
        None, description="Current editable flow description"
    )
    flow_updated_at: Optional[str] = Field(
        None, description="Saved baseline identity used for stale-edit protection"
    )
    flow_is_dirty: Optional[bool] = Field(
        None, description="Whether the editable flow differs from its saved baseline"
    )
    flow_draft_fingerprint: Optional[
        Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    ] = Field(None, description="Transient fingerprint of the exact flow draft")
    flow_definition: Optional[FlowContextDefinition] = Field(
        None,
        description="Current flow definition being edited"
    )
    agent_workshop: Optional[AgentWorkshopContext] = Field(
        None,
        description="Current Agent Workshop state when active tab is agent_workshop",
    )


class ChatRequest(BaseModel):
    """Request to send a message to AI Chat."""
    messages: List[ChatMessage] = Field(..., description="Conversation history")
    context: Optional[ChatContext] = Field(None, description="Current UI context")

    @model_validator(mode="after")
    def validate_authoring_context_fingerprints(self) -> "ChatRequest":
        """Reject incomplete or altered editor snapshots at the API boundary."""

        from src.lib.agent_studio.authoring_context import (
            flow_draft_fingerprint,
            workshop_draft_fingerprint,
        )

        context = self.context
        if context is None:
            return self

        if context.flow_definition is not None:
            if context.flow_draft_fingerprint is None:
                raise ValueError("flow_draft_fingerprint is required with flow_definition")
            if bool(context.flow_id) != bool(context.flow_updated_at):
                raise ValueError(
                    "saved flow authoring context requires both flow_id and flow_updated_at"
                )
            if context.flow_draft_fingerprint != flow_draft_fingerprint(context):
                raise ValueError("flow_draft_fingerprint does not match the flow draft")

        if context.agent_workshop is not None:
            workshop = context.agent_workshop
            if workshop.draft_fingerprint is None:
                raise ValueError("draft_fingerprint is required with agent_workshop")
            if bool(workshop.custom_agent_id) != bool(workshop.custom_agent_updated_at):
                raise ValueError(
                    "saved Workshop context requires both custom_agent_id and custom_agent_updated_at"
                )
            if workshop.draft_fingerprint != workshop_draft_fingerprint(workshop):
                raise ValueError("draft_fingerprint does not match the Workshop draft")
        return self


class ChatResponse(BaseModel):
    """Non-streaming AI Chat response for error cases."""
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
    status: str = Field(
        "completed",
        description="Status: 'completed', 'error', or neutral 'N/A'",
    )


class RoutingDecision(BaseModel):
    """A routing decision made by the supervisor."""
    from_agent: str = Field(..., description="Source agent (usually 'supervisor')")
    to_agent: str = Field(..., description="Target agent")
    reason: Optional[str] = Field(None, description="Why this routing was chosen")
    timestamp: Optional[datetime] = Field(None, description="When the decision was made")


class PromptExecution(BaseModel):
    """Information about a prompt that was executed in a trace."""
    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(..., description="Agent that executed")
    agent_name: str = Field(..., description="Human-readable agent name")
    prompt_preview: str = Field(..., description="First ~500 chars of the prompt used")
    group_applied: Optional[str] = Field(
        None,
        description="Group rules that were applied (if any)",
    )
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


# Removed legacy Agent Studio mod_* aliases — superseded by canonical group_*
# contracts in PR #580.

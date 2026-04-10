"""
Pydantic models for Prompt Explorer feature.

Defines data structures for:
- Agent prompt metadata (base prompts, group rules)
- Agent documentation (capabilities, data sources, limitations)
- Chat messages for Opus conversations
- Trace context for execution history
"""

from typing import Dict, List, Optional
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator
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


# ============================================================================
# Prompt Catalog Models
# ============================================================================

class GroupRuleInfo(BaseModel):
    """Organization-group-specific rule information."""

    model_config = ConfigDict(populate_by_name=True)

    group_id: str = Field(
        ...,
        description="Group identifier (e.g., 'WB', 'FB', 'MGI')",
        validation_alias=AliasChoices("group_id", "mod_id"),
    )
    content: str = Field(..., description="Group rule content (YAML or processed text)")
    source_file: str = Field(..., description="Path to source YAML file (legacy) or 'database'")
    description: Optional[str] = Field(None, description="Brief description of what the group rule adds")

    # Version metadata (from prompt_templates table)
    prompt_id: Optional[str] = Field(None, description="UUID of the prompt_templates row")
    prompt_version: Optional[int] = Field(None, description="Version number of this prompt")
    created_at: Optional[datetime] = Field(None, description="When this version was created")
    created_by: Optional[str] = Field(None, description="Who created this version")


class PromptInfo(BaseModel):
    """Information about a single agent's prompt."""

    model_config = ConfigDict(populate_by_name=True)

    agent_id: str = Field(..., description="Unique agent identifier (e.g., 'supervisor', 'gene_expression')")
    agent_name: str = Field(..., description="Human-readable agent name")
    description: str = Field(..., description="Brief description of what the agent does")
    base_prompt: str = Field(..., description="Base prompt instructions (before group-rule injection)")
    source_file: str = Field(..., description="Path to the agent source file (legacy) or 'database'")
    has_group_rules: bool = Field(
        False,
        description="Whether this agent supports group-specific rules",
        validation_alias=AliasChoices("has_group_rules", "has_mod_rules"),
    )
    group_rules: Dict[str, GroupRuleInfo] = Field(
        default_factory=dict,
        description="Group-specific rules keyed by group ID",
        validation_alias=AliasChoices("group_rules", "mod_rules"),
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
    model_config = ConfigDict(populate_by_name=True)

    categories: List[AgentPrompts] = Field(
        default_factory=list,
        description="Prompts organized by category"
    )
    total_agents: int = Field(0, description="Total number of agents")
    available_groups: List[str] = Field(
        default_factory=list,
        description="List of groups with available rules",
        validation_alias=AliasChoices("available_groups", "available_mods"),
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
    output_filename_template: Optional[str] = None
    output_key: str


class FlowEdgeContext(BaseModel):
    """Simplified flow edge for chat context."""
    source: str
    target: str


class FlowContextDefinition(BaseModel):
    """Flow definition passed to chat for validation/discussion."""
    nodes: List[FlowNodeContext] = Field(default_factory=list)
    edges: List[FlowEdgeContext] = Field(default_factory=list)


class AgentWorkshopContext(BaseModel):
    """Agent Workshop context passed to Opus chat."""

    model_config = ConfigDict(populate_by_name=True)

    template_source: Optional[str] = None
    template_name: Optional[str] = None
    custom_agent_id: Optional[str] = None
    custom_agent_name: Optional[str] = None
    include_group_rules: Optional[bool] = Field(
        None,
        validation_alias=AliasChoices("include_group_rules", "include_mod_rules"),
    )
    selected_group_id: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("selected_group_id", "selected_mod_id"),
    )
    prompt_draft: Optional[str] = None
    selected_group_prompt_draft: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("selected_group_prompt_draft", "selected_mod_prompt_draft"),
    )
    group_prompt_override_count: Optional[int] = Field(
        None,
        validation_alias=AliasChoices("group_prompt_override_count", "mod_prompt_override_count"),
    )
    has_group_prompt_overrides: Optional[bool] = Field(
        None,
        validation_alias=AliasChoices("has_group_prompt_overrides", "has_mod_prompt_overrides"),
    )
    template_prompt_stale: Optional[bool] = None
    template_exists: Optional[bool] = None
    draft_tool_ids: Optional[List[str]] = None
    draft_model_id: Optional[str] = None
    draft_model_reasoning: Optional[str] = None


class ChatContext(BaseModel):
    """Context for the Opus chat session."""

    model_config = ConfigDict(populate_by_name=True)

    selected_agent_id: Optional[str] = Field(
        None,
        description="ID of currently selected agent in the prompt browser"
    )
    selected_group_id: Optional[str] = Field(
        None,
        description="ID of currently selected group (if viewing group-specific rules)",
        validation_alias=AliasChoices("selected_group_id", "selected_mod_id"),
    )
    trace_id: Optional[str] = Field(
        None,
        description="Trace ID if opened from a chat message"
    )
    view_mode: str = Field(
        "base",
        description="Current view mode: 'base', 'group', or 'combined'"
    )
    # Flow context (when on Flows tab)
    active_tab: Optional[str] = Field(
        None,
        description="Which tab is active: 'agents', 'flows', or 'agent_workshop'"
    )
    flow_name: Optional[str] = Field(
        None,
        description="Name of the flow being edited"
    )
    flow_definition: Optional[FlowContextDefinition] = Field(
        None,
        description="Current flow definition being edited"
    )
    agent_workshop: Optional[AgentWorkshopContext] = Field(
        None,
        description="Current Agent Workshop state when active tab is agent_workshop",
    )

    @field_validator("view_mode", mode="before")
    @classmethod
    def normalize_view_mode(cls, value: object) -> object:
        if isinstance(value, str) and value.strip().lower() == "mod":
            return "group"
        return value


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
    model_config = ConfigDict(populate_by_name=True)

    agent_id: str = Field(..., description="Agent that executed")
    agent_name: str = Field(..., description="Human-readable agent name")
    prompt_preview: str = Field(..., description="First ~500 chars of the prompt used")
    group_applied: Optional[str] = Field(
        None,
        description="Group rules that were applied (if any)",
        validation_alias=AliasChoices("group_applied", "mod_applied"),
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


def _prompt_info_has_mod_rules(self) -> bool:
    return self.has_group_rules


def _prompt_info_set_mod_rules(self, value: bool) -> None:
    self.has_group_rules = value


def _prompt_info_get_mod_rules(self) -> Dict[str, GroupRuleInfo]:
    return self.group_rules


def _prompt_info_set_mod_rule_map(self, value: Dict[str, GroupRuleInfo]) -> None:
    self.group_rules = value


def _group_rule_info_get_mod_id(self) -> str:
    return self.group_id


def _group_rule_info_set_mod_id(self, value: str) -> None:
    self.group_id = value


def _agent_workshop_get_include_mod_rules(self) -> Optional[bool]:
    return self.include_group_rules


def _agent_workshop_set_include_mod_rules(self, value: Optional[bool]) -> None:
    self.include_group_rules = value


def _agent_workshop_get_selected_mod_id(self) -> Optional[str]:
    return self.selected_group_id


def _agent_workshop_set_selected_mod_id(self, value: Optional[str]) -> None:
    self.selected_group_id = value


def _agent_workshop_get_selected_mod_prompt_draft(self) -> Optional[str]:
    return self.selected_group_prompt_draft


def _agent_workshop_set_selected_mod_prompt_draft(self, value: Optional[str]) -> None:
    self.selected_group_prompt_draft = value


def _agent_workshop_get_mod_prompt_override_count(self) -> Optional[int]:
    return self.group_prompt_override_count


def _agent_workshop_set_mod_prompt_override_count(self, value: Optional[int]) -> None:
    self.group_prompt_override_count = value


def _agent_workshop_get_has_mod_prompt_overrides(self) -> Optional[bool]:
    return self.has_group_prompt_overrides


def _agent_workshop_set_has_mod_prompt_overrides(self, value: Optional[bool]) -> None:
    self.has_group_prompt_overrides = value


def _chat_context_get_selected_mod_id(self) -> Optional[str]:
    return self.selected_group_id


def _chat_context_set_selected_mod_id(self, value: Optional[str]) -> None:
    self.selected_group_id = value


def _prompt_execution_get_mod_applied(self) -> Optional[str]:
    return self.group_applied


def _prompt_execution_set_mod_applied(self, value: Optional[str]) -> None:
    self.group_applied = value


GroupRuleInfo.mod_id = property(_group_rule_info_get_mod_id, _group_rule_info_set_mod_id)
PromptInfo.has_mod_rules = property(_prompt_info_has_mod_rules, _prompt_info_set_mod_rules)
PromptInfo.mod_rules = property(_prompt_info_get_mod_rules, _prompt_info_set_mod_rule_map)
PromptCatalog.available_mods = property(lambda self: self.available_groups, lambda self, value: setattr(self, "available_groups", value))
AgentWorkshopContext.include_mod_rules = property(_agent_workshop_get_include_mod_rules, _agent_workshop_set_include_mod_rules)
AgentWorkshopContext.selected_mod_id = property(_agent_workshop_get_selected_mod_id, _agent_workshop_set_selected_mod_id)
AgentWorkshopContext.selected_mod_prompt_draft = property(_agent_workshop_get_selected_mod_prompt_draft, _agent_workshop_set_selected_mod_prompt_draft)
AgentWorkshopContext.mod_prompt_override_count = property(_agent_workshop_get_mod_prompt_override_count, _agent_workshop_set_mod_prompt_override_count)
AgentWorkshopContext.has_mod_prompt_overrides = property(_agent_workshop_get_has_mod_prompt_overrides, _agent_workshop_set_has_mod_prompt_overrides)
ChatContext.selected_mod_id = property(_chat_context_get_selected_mod_id, _chat_context_set_selected_mod_id)
PromptExecution.mod_applied = property(_prompt_execution_get_mod_applied, _prompt_execution_set_mod_applied)
MODRuleInfo = GroupRuleInfo

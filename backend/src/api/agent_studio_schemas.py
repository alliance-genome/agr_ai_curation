"""Request and response schemas for Agent Studio API endpoints."""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from src.lib.agent_studio import ChatContext, ChatMessage, PromptCatalog


class ChatRequest(BaseModel):
    """Request to send a message to Opus."""

    messages: List[ChatMessage]
    context: Optional[ChatContext] = None


class CatalogResponse(BaseModel):
    """Response for prompt catalog."""

    catalog: PromptCatalog


class CombinedPromptRequest(BaseModel):
    """Request for a combined prompt (base + group rules)."""

    model_config = ConfigDict(populate_by_name=True)

    agent_id: str
    group_id: str = Field(
        ...,
        validation_alias=AliasChoices("group_id", "mod_id"),
    )


class CombinedPromptResponse(BaseModel):
    """Response with combined prompt."""

    agent_id: str
    group_id: str
    combined_prompt: str


class PromptPreviewResponse(BaseModel):
    """Response with resolved prompt text for preview/testing."""

    model_config = ConfigDict(populate_by_name=True)

    agent_id: str
    prompt: str
    group_id: Optional[str] = None
    source: str
    parent_agent_key: Optional[str] = None
    include_group_rules: Optional[bool] = None


class AgentTestRequest(BaseModel):
    """Request for isolated agent test streaming."""

    model_config = ConfigDict(populate_by_name=True)

    input: str
    group_id: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("group_id", "mod_id"),
    )
    document_id: Optional[str] = None
    session_id: Optional[str] = None


class ManualSuggestionRequest(BaseModel):
    """Request to manually submit a prompt suggestion."""

    model_config = ConfigDict(populate_by_name=True)

    agent_id: Optional[str] = None
    suggestion_type: str
    summary: str
    detailed_reasoning: str
    proposed_change: Optional[str] = None
    group_id: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("group_id", "mod_id"),
    )
    trace_id: Optional[str] = None


class SuggestionResponse(BaseModel):
    """Response after submitting a suggestion."""

    status: str
    suggestion_id: Optional[str] = None
    message: str


class AgentMetadata(BaseModel):
    """Metadata for a single agent."""

    name: str
    icon: str
    category: str
    subcategory: Optional[str] = None
    supervisor_tool: Optional[str] = None


class RegistryMetadataResponse(BaseModel):
    """Response for registry metadata endpoint."""

    agents: Dict[str, AgentMetadata]


class ModelOption(BaseModel):
    """Curator-selectable model option."""

    model_id: str
    name: str
    provider: str
    description: str = ""
    guidance: str = ""
    default: bool = False
    supports_reasoning: bool = True
    supports_temperature: bool = True
    reasoning_options: List[str] = Field(default_factory=list)
    default_reasoning: Optional[str] = None
    reasoning_descriptions: Dict[str, str] = Field(default_factory=dict)
    recommended_for: List[str] = Field(default_factory=list)
    avoid_for: List[str] = Field(default_factory=list)


class ModelsResponse(BaseModel):
    """Response for available model options."""

    models: List[ModelOption]


class ToolLibraryItem(BaseModel):
    """Single tool entry from tool library policy table."""

    tool_key: str
    display_name: str
    description: str
    category: str
    curator_visible: bool
    allow_attach: bool
    allow_execute: bool
    config: Dict[str, Any] = Field(default_factory=dict)


class ToolLibraryResponse(BaseModel):
    """Response for tool library."""

    tools: List[ToolLibraryItem]


class AgentTemplateItem(BaseModel):
    """System agent template option for Agent Workshop."""

    agent_id: str
    name: str
    description: Optional[str] = None
    icon: str
    category: Optional[str] = None
    model_id: str
    tool_ids: List[str]
    output_schema_key: Optional[str] = None


class AgentTemplatesResponse(BaseModel):
    """Response for available system templates."""

    templates: List[AgentTemplateItem]


class CloneAgentRequest(BaseModel):
    """Optional clone parameters."""

    name: Optional[str] = Field(None, min_length=1, max_length=100)


class ShareAgentRequest(BaseModel):
    """Visibility update payload for sharing toggle."""

    visibility: Literal["private", "project"]


class ToolIdeaConversationEntry(BaseModel):
    """Single Opus ideation conversation turn."""

    role: Literal["user", "assistant", "system"]
    content: str = Field(..., min_length=1)
    timestamp: Optional[str] = None


class ToolIdeaCreateRequest(BaseModel):
    """Payload for submitting a new tool idea request."""

    title: str = Field(..., min_length=1, max_length=255)
    description: str = Field(..., min_length=1)
    opus_conversation: Optional[List[ToolIdeaConversationEntry]] = None

    model_config = ConfigDict(extra="forbid")


class ToolIdeaResponseItem(BaseModel):
    """Tool idea request row returned to curators."""

    id: str
    user_id: int
    project_id: Optional[str] = None
    title: str
    description: str
    opus_conversation: List[Dict[str, Any]]
    status: Literal["submitted", "reviewed", "in_progress", "completed", "declined"]
    developer_notes: Optional[str] = None
    resulting_tool_key: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class ToolIdeaListResponse(BaseModel):
    """Response payload for current user's tool idea requests."""

    tool_ideas: List[ToolIdeaResponseItem]
    total: int


class DirectSubmissionRequest(BaseModel):
    """Request to directly trigger suggestion submission via Opus (bypassing chat UI)."""

    context: Optional[ChatContext] = None
    messages: Optional[List[ChatMessage]] = None


class DirectSubmissionResponse(BaseModel):
    """Response from direct suggestion submission."""

    success: bool
    suggestion_id: Optional[str] = None
    message: str
    error: Optional[str] = None

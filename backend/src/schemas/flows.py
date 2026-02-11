"""Pydantic schemas for curation flows API.

Defines request/response schemas for Flow CRUD operations
and validation for FlowDefinition JSONB structure.
"""
from datetime import datetime
from typing import List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# =============================================================================
# FlowDefinition Schema (JSONB Validation)
# =============================================================================

class FlowNodePosition(BaseModel):
    """Position of a node on the canvas."""
    x: float = Field(..., description="X coordinate")
    y: float = Field(..., description="Y coordinate")


class FlowNodeData(BaseModel):
    """Configuration data for a flow node."""

    agent_id: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description="Agent ID from catalog (e.g., 'pdf', 'gene') or 'task_input' for initial instructions"
    )
    agent_display_name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Human-readable agent name"
    )
    agent_description: Optional[str] = Field(
        None,
        max_length=500,
        description="Agent description for display"
    )

    # Task input configuration (for task_input nodes only)
    task_instructions: Optional[str] = Field(
        None,
        max_length=5000,
        description="Curator's task/request that initiates the flow (required for task_input nodes)"
    )

    # Step configuration (for agent nodes)
    step_goal: Optional[str] = Field(
        None,
        max_length=500,
        description="Goal description for this step"
    )
    custom_instructions: Optional[str] = Field(
        None,
        max_length=2000,
        description="Custom instructions prepended to agent prompt with highest priority"
    )
    prompt_version: Optional[int] = Field(
        None,
        ge=1,
        description="Pinned prompt version (None = use active)"
    )

    # Input/Output configuration
    input_source: Literal["user_query", "previous_output", "custom"] = Field(
        "previous_output",
        description="Where this step gets its input"
    )
    custom_input: Optional[str] = Field(
        None,
        max_length=2000,
        description="Template with {{variable}} placeholders"
    )
    # IMPORTANT: output_key pattern ensures valid Python identifier
    output_key: str = Field(
        ...,
        min_length=1,
        max_length=50,
        pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$",
        description="Variable name for downstream templates"
    )


class FlowNode(BaseModel):
    """A node in the flow graph."""

    id: str = Field(..., min_length=1, max_length=50)
    type: Literal["agent", "decision", "output", "task_input"] = Field(
        "agent",
        description="Node type: 'agent' for processing, 'task_input' for initial instructions"
    )
    position: FlowNodePosition
    data: FlowNodeData

    @model_validator(mode="after")
    def validate_task_input_requirements(self) -> "FlowNode":
        """Ensure task_input nodes have required task_instructions."""
        if self.type == "task_input":
            if not self.data.task_instructions or not self.data.task_instructions.strip():
                raise ValueError("task_input nodes must have non-empty task_instructions")
            if self.data.agent_id != "task_input":
                raise ValueError("task_input nodes must have agent_id='task_input'")
        return self


class FlowEdgeCondition(BaseModel):
    """Conditional edge (V2 forward-compatibility)."""

    type: Literal["contains", "not_empty", "matches_pattern"]
    value: Optional[str] = None


class FlowEdge(BaseModel):
    """An edge connecting two nodes."""

    id: str = Field(..., min_length=1, max_length=50)
    source: str = Field(..., description="Source node ID")
    target: str = Field(..., description="Target node ID")
    condition: Optional[FlowEdgeCondition] = Field(
        None,
        description="V2: Conditional execution (ignored in V1)"
    )


class FlowDefinition(BaseModel):
    """Complete flow definition stored in JSONB."""

    version: Literal["1.0"] = "1.0"
    nodes: List[FlowNode] = Field(..., min_length=1, max_length=30)
    edges: List[FlowEdge] = Field(default_factory=list)
    entry_node_id: str = Field(..., description="Starting node ID")

    # VALIDATOR 1: Unique node IDs
    @field_validator("nodes")
    @classmethod
    def validate_unique_node_ids(cls, nodes: List[FlowNode]) -> List[FlowNode]:
        """Ensure all node IDs are unique."""
        ids = [n.id for n in nodes]
        if len(ids) != len(set(ids)):
            raise ValueError("Node IDs must be unique")
        return nodes

    # VALIDATOR 2: Unique output keys
    @field_validator("nodes")
    @classmethod
    def validate_unique_output_keys(cls, nodes: List[FlowNode]) -> List[FlowNode]:
        """Ensure all output_key values are unique."""
        keys = [n.data.output_key for n in nodes]
        if len(keys) != len(set(keys)):
            raise ValueError("Output keys must be unique across all nodes")
        return nodes

    # VALIDATOR 3: Entry node exists (model_validator runs after field validators)
    @model_validator(mode="after")
    def validate_entry_node_exists(self) -> "FlowDefinition":
        """Ensure entry_node_id references a valid node."""
        node_ids = {n.id for n in self.nodes}
        if self.entry_node_id not in node_ids:
            raise ValueError(f"entry_node_id '{self.entry_node_id}' not found in nodes")
        return self

    # VALIDATOR 4: Edge references valid nodes
    @model_validator(mode="after")
    def validate_edges_reference_valid_nodes(self) -> "FlowDefinition":
        """Ensure all edge source/target reference valid nodes."""
        node_ids = {n.id for n in self.nodes}
        for edge in self.edges:
            if edge.source not in node_ids:
                raise ValueError(f"Edge source '{edge.source}' not found in nodes")
            if edge.target not in node_ids:
                raise ValueError(f"Edge target '{edge.target}' not found in nodes")
        return self

    # VALIDATOR 5: Exactly one task_input node required
    @model_validator(mode="after")
    def validate_single_task_input(self) -> "FlowDefinition":
        """Ensure flow has exactly one task_input node with instructions."""
        task_input_nodes = [n for n in self.nodes if n.type == "task_input"]
        if len(task_input_nodes) == 0:
            raise ValueError(
                "Flow must have a 'Task Input' node with instructions. "
                "Add one from the agent catalog to define what the flow should do."
            )
        if len(task_input_nodes) > 1:
            raise ValueError("Flow can only have one task_input node")
        return self

    # VALIDATOR 6: task_input must be entry node
    @model_validator(mode="after")
    def validate_task_input_is_entry(self) -> "FlowDefinition":
        """If flow has task_input node, it must be the entry_node_id."""
        task_input_nodes = [n for n in self.nodes if n.type == "task_input"]
        if task_input_nodes:
            task_input_id = task_input_nodes[0].id
            if self.entry_node_id != task_input_id:
                raise ValueError("task_input node must be the entry_node_id")
        return self

    # VALIDATOR 7: task_input cannot have incoming edges
    @model_validator(mode="after")
    def validate_task_input_no_incoming_edges(self) -> "FlowDefinition":
        """Ensure task_input nodes have no incoming edges."""
        task_input_nodes = [n for n in self.nodes if n.type == "task_input"]
        if task_input_nodes:
            task_input_id = task_input_nodes[0].id
            incoming_edges = [e for e in self.edges if e.target == task_input_id]
            if incoming_edges:
                raise ValueError("task_input node cannot have incoming edges")
        return self

    # VALIDATOR 8: Reject non-executable agents (e.g., supervisor)
    _BLOCKED_AGENT_IDS = frozenset({"supervisor"})

    @model_validator(mode="after")
    def validate_no_blocked_agents(self) -> "FlowDefinition":
        """Ensure flows don't contain system-only agents like supervisor."""
        for node in self.nodes:
            if node.data.agent_id in self._BLOCKED_AGENT_IDS:
                raise ValueError(
                    f"Agent '{node.data.agent_id}' cannot be used in flows. "
                    "It is an internal system agent."
                )
        return self


# =============================================================================
# Request Schemas
# =============================================================================

class ExecuteFlowRequest(BaseModel):
    """Request to execute a curation flow."""

    flow_id: UUID = Field(..., description="ID of the flow to execute")
    session_id: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Session ID for tracing (Langfuse)"
    )
    document_id: Optional[UUID] = Field(
        None,
        description="Document ID for PDF-aware agents (required if flow uses pdf/gene_expression)"
    )
    user_query: Optional[str] = Field(
        None,
        max_length=2000,
        description="Optional user-provided context or query"
    )


class CreateFlowRequest(BaseModel):
    """Request to create a new flow."""

    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Flow name (must be unique per user)"
    )
    description: Optional[str] = Field(
        None,
        max_length=2000,
        description="Optional flow description"
    )
    flow_definition: FlowDefinition

    @field_validator("name")
    @classmethod
    def validate_name_not_whitespace(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Name cannot be empty or whitespace only")
        return v.strip()


class UpdateFlowRequest(BaseModel):
    """Request to update an existing flow (partial update)."""

    name: Optional[str] = Field(
        None,
        min_length=1,
        max_length=255,
        description="New flow name"
    )
    description: Optional[str] = Field(
        None,
        max_length=2000,
        description="New description (use empty string to clear)"
    )
    flow_definition: Optional[FlowDefinition] = None

    @field_validator("name")
    @classmethod
    def validate_name_not_whitespace(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.strip():
            raise ValueError("Name cannot be empty or whitespace only")
        return v.strip() if v else v


# =============================================================================
# Response Schemas
# =============================================================================

class FlowSummaryResponse(BaseModel):
    """Summary of a flow (for list view - excludes full flow_definition)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: int = Field(..., description="Owner user ID")
    name: str
    description: Optional[str]
    step_count: int = Field(..., description="Number of nodes in the flow")
    execution_count: int
    last_executed_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime


class FlowResponse(BaseModel):
    """Full flow response with definition."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: int = Field(..., description="Owner user ID")
    name: str
    description: Optional[str]
    flow_definition: FlowDefinition
    execution_count: int
    last_executed_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime


class FlowListResponse(BaseModel):
    """Paginated list of flows."""

    flows: List[FlowSummaryResponse]
    total: int = Field(..., description="Total number of flows matching filters")
    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1, le=100)


# NOTE: For delete/simple responses, use existing OperationResult from src.models.api_schemas

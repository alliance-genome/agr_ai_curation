"""SQLAlchemy model for curation flows.

Curation flows enable curators to create, save, and execute pre-defined workflows
for common curation tasks. Each flow consists of ordered agent steps with optional
customizations.

Key features:
- Soft reference for user_id (no FK constraint) to support future sharing
- JSONB storage for flow_definition (validated at API layer via Pydantic)
- Soft delete via is_active flag (matches User, PromptTemplate pattern)
- Execution statistics for analytics
"""
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class CurationFlow(Base):
    """User-defined curation workflow with ordered agent steps.

    Flows define sequences of agents to execute for specific curation tasks.
    Each flow belongs to a single user (soft reference, no FK constraint).

    The flow_definition JSONB stores:
    - version: Schema version (currently "1.0")
    - nodes: List of FlowNode objects with agent config
    - edges: List of FlowEdge objects connecting nodes
    - entry_node_id: ID of the starting node

    Validation of flow_definition structure happens at the Pydantic layer,
    not in the database.
    """

    __tablename__ = "curation_flows"

    id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True), primary_key=True, default=uuid4
    )

    # Owner/creator - soft reference (no FK constraint) to support future sharing
    # Flows persist even if owner is deleted; ownership can transfer
    user_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Owner user ID - references users(user_id)"
    )

    name: Mapped[str] = mapped_column(
        String(255), nullable=False, comment="User-defined flow name"
    )
    description: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="Optional flow description"
    )
    flow_definition: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        comment="Flow structure: nodes, edges, step configs. Validated by Pydantic at API layer."
    )

    # Execution statistics
    execution_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        comment="Number of times this flow has been executed"
    )
    last_executed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Timestamp of most recent execution"
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False
    )

    # Soft delete: uses is_active=True (matches User, PromptTemplate pattern)
    # NOT is_deleted - codebase convention is positive flag with inverted semantics
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
        comment="Soft delete flag (false = archived/deleted)"
    )

    # Table constraints and indexes
    # Note: The partial unique index for (user_id, name) WHERE is_active = TRUE
    # is created in the migration, not here, for cleaner PostgreSQL syntax
    __table_args__ = (
        CheckConstraint("name <> ''", name="ck_flows_name_not_empty"),
        Index("idx_curation_flows_user_id", "user_id"),
        # Partial index - only indexes active flows (PostgreSQL-specific)
        Index(
            "idx_curation_flows_user_active",
            "user_id",
            postgresql_where=(is_active == True)  # noqa: E712 - SQLAlchemy requires == for SQL generation
        ),
    )

    def __repr__(self) -> str:
        """String representation for debugging."""
        return (
            f"<CurationFlow(id={self.id}, "
            f"name='{self.name}', "
            f"user_id={self.user_id}, "
            f"is_active={self.is_active})>"
        )

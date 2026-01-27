"""Prompt template and execution log models for versioned prompt management.

This module defines the database models for:
- PromptTemplate: Versioned prompt storage with agent/type/mod_id keys
- PromptExecutionLog: Audit trail of which prompts were used per execution

Key structure for PromptTemplate: agent_name + prompt_type + mod_id
- Base prompts: gene:system:NULL
- MOD rules: gene:mod_rules:FB

Agent names use catalog_service.py AGENT_REGISTRY IDs (e.g., 'pdf', 'gene').
"""

import uuid
from sqlalchemy import Column, String, Integer, Boolean, Text, DateTime, ForeignKey, Index, func
from sqlalchemy.dialects.postgresql import UUID

from .database import Base


class PromptTemplate(Base):
    """Versioned prompt template storage.

    Key structure: agent_name + prompt_type + mod_id
    - Base prompts: gene:system:NULL
    - MOD rules: gene:mod_rules:FB

    Agent names use catalog_service.py AGENT_REGISTRY IDs (e.g., 'pdf', 'gene').
    """

    __tablename__ = "prompt_templates"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Identity (uses catalog_service.py AGENT_REGISTRY IDs)
    agent_name = Column(String(100), nullable=False)  # e.g., 'pdf', 'gene', 'supervisor'
    prompt_type = Column(String(50), nullable=False)  # e.g., 'system', 'format_gene_expression', 'mod_rules'
    mod_id = Column(String(20), nullable=True)  # NULL for base prompts, e.g., 'FB', 'WB', 'MGI' for MOD rules

    # Content
    content = Column(Text, nullable=False)  # The actual prompt text

    # Versioning
    version = Column(Integer, nullable=False)  # Auto-incremented per agent_name+prompt_type+mod_id
    is_active = Column(Boolean, nullable=False, default=False)  # Only one active per agent_name+prompt_type+mod_id

    # Metadata
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_by = Column(String(255), nullable=True)  # Who created this version
    change_notes = Column(Text, nullable=True)  # Why this version was created
    source_file = Column(Text, nullable=True)  # Preserve UI contract + provenance
    description = Column(Text, nullable=True)  # Optional prompt/MOD description

    __table_args__ = (
        # Unique constraint for non-NULL mod_id
        Index(
            "uq_prompt_templates_with_mod",
            "agent_name",
            "prompt_type",
            "mod_id",
            "version",
            unique=True,
            postgresql_where=(mod_id.isnot(None)),
        ),
        # Partial unique index for base prompts (mod_id IS NULL)
        # Required because PostgreSQL doesn't consider NULLs equal in unique constraints
        Index(
            "idx_prompt_templates_base_unique",
            "agent_name",
            "prompt_type",
            "version",
            unique=True,
            postgresql_where=(mod_id.is_(None)),
        ),
        # Index for fast lookups of active prompts (includes mod_id)
        Index(
            "idx_prompt_templates_active",
            "agent_name",
            "prompt_type",
            "mod_id",
            postgresql_where=(is_active == True),
        ),
        # Index for version lookups
        Index(
            "idx_prompt_templates_version",
            "agent_name",
            "prompt_type",
            "mod_id",
            "version",
        ),
    )

    def __repr__(self) -> str:
        mod_str = f"/{self.mod_id}" if self.mod_id else ""
        active_str = " [ACTIVE]" if self.is_active else ""
        return f"<PromptTemplate {self.agent_name}:{self.prompt_type}{mod_str} v{self.version}{active_str}>"


class PromptExecutionLog(Base):
    """Audit trail of prompt usage per execution.

    One row per prompt used (agent run may log multiple: base + MOD rule).
    Strict audit trail: logs every invocation (no de-dupe).
    """

    __tablename__ = "prompt_execution_log"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Execution context
    trace_id = Column(String(64), nullable=True)  # Langfuse trace ID
    session_id = Column(String(255), nullable=True)  # Chat session
    flow_execution_id = Column(
        UUID(as_uuid=True), nullable=True
    )  # If part of a flow (FK added when curation_flows exists)

    # Prompt reference
    prompt_template_id = Column(
        UUID(as_uuid=True), ForeignKey("prompt_templates.id"), nullable=False
    )
    agent_name = Column(String(100), nullable=False)  # Denormalized for easy querying
    prompt_type = Column(String(50), nullable=False)  # 'system' or 'mod_rules'
    mod_id = Column(String(20), nullable=True)  # NULL for base prompts
    prompt_version = Column(Integer, nullable=False)  # Denormalized for easy querying

    # Timing
    executed_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index("idx_prompt_exec_trace", "trace_id"),
        Index("idx_prompt_exec_session", "session_id"),
    )

    def __repr__(self) -> str:
        mod_str = f"/{self.mod_id}" if self.mod_id else ""
        return f"<PromptExecutionLog {self.agent_name}:{self.prompt_type}{mod_str} v{self.prompt_version}>"

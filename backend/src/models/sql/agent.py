"""Agent workshop SQL models."""

import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from .database import Base


class Project(Base):
    """Sharing boundary for agent visibility."""

    __tablename__ = "projects"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    is_active = Column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )

    def __repr__(self) -> str:
        return f"<Project id={self.id} name='{self.name}'>"


class ProjectMember(Base):
    """Membership mapping between users and projects."""

    __tablename__ = "project_members"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id"),
        nullable=False,
        index=True,
    )
    user_id = Column(Integer, ForeignKey("users.user_id"), nullable=False, index=True)
    role = Column(
        String(50),
        nullable=False,
        default="member",
        server_default="member",
    )
    joined_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "user_id",
            name="uq_project_members_project_user",
        ),
        CheckConstraint("role IN ('admin', 'member')", name="ck_project_members_role"),
    )

    def __repr__(self) -> str:
        return (
            f"<ProjectMember project_id={self.project_id} "
            f"user_id={self.user_id} role={self.role}>"
        )


class Agent(Base):
    """Unified agent record for system and custom agents."""

    __tablename__ = "agents"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    agent_key = Column(String(100), nullable=False, unique=True, index=True)
    user_id = Column(Integer, ForeignKey("users.user_id"), nullable=True, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    # Unified Agent records are first-class after ALL-796; legacy custom-agent
    # prompt aliases were removed in favor of canonical instructions.
    instructions = Column(Text, nullable=False)
    model_id = Column(String(100), nullable=False)
    model_temperature = Column(
        Float,
        nullable=False,
        default=0.1,
        server_default="0.1",
    )
    model_reasoning = Column(String(20), nullable=True, default=None)
    tool_ids = Column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    allowed_group_ids = Column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    inherited_allowed_group_ids = Column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    output_schema_key = Column(String(100), nullable=True)

    group_rules_enabled = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    group_rules_component = Column(String(100), nullable=True)
    group_prompt_overrides = Column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )

    icon = Column(
        String(10),
        nullable=False,
        default="\U0001F916",
        server_default="\U0001F916",
    )
    category = Column(String(100), nullable=True)

    visibility = Column(
        String(20),
        nullable=False,
        default="private",
        server_default="private",
    )
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id"),
        nullable=True,
        index=True,
    )
    shared_at = Column(DateTime(timezone=True), nullable=True)
    # Legacy custom-agent template freshness compatibility was removed after
    # ALL-796; template_source is the canonical template provenance field.
    template_source = Column(String(100), nullable=True)

    supervisor_enabled = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    supervisor_description = Column(Text, nullable=True)
    supervisor_batchable = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    supervisor_batching_entity = Column(String(100), nullable=True)
    show_in_palette = Column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )

    version = Column(Integer, nullable=False, default=1, server_default="1")
    is_active = Column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "visibility IN ('private', 'project', 'system')",
            name="ck_agents_visibility",
        ),
        Index(
            "uq_agents_active_custom_name_per_user",
            "user_id",
            func.lower(name),
            unique=True,
            postgresql_where=text(
                "is_active = true AND user_id IS NOT NULL AND visibility IN ('private', 'project')"
            ),
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<Agent id={self.id} key='{self.agent_key}' "
            f"visibility='{self.visibility}' active={self.is_active}>"
        )

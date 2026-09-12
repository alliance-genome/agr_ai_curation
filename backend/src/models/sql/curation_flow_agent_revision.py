"""Database-maintained references for custom nodes in mutable curation flows."""

from sqlalchemy import Column, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID

from .database import Base


class CurationFlowAgentRevision(Base):
    __tablename__ = "curation_flow_agent_revisions"

    flow_id = Column(UUID(as_uuid=True), ForeignKey("curation_flows.id", ondelete="CASCADE"), primary_key=True)
    node_id = Column(String(50), primary_key=True)
    agent_revision_id = Column(UUID(as_uuid=True), ForeignKey(
        "agent_execution_revisions.id", ondelete="RESTRICT"), nullable=False, index=True)

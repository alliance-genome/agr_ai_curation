"""Curator acknowledgment of an exact extraction-only configuration."""
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from .database import Base


class ValidationAcknowledgment(Base):
    __tablename__ = "validation_acknowledgments"
    user_id = Column(Integer, ForeignKey("users.user_id", ondelete="CASCADE"), primary_key=True)
    fingerprint = Column(String(71), primary_key=True)
    scope = Column(JSONB, nullable=False)
    acknowledged_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

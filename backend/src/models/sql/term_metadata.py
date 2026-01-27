"""SQLAlchemy model for ontology term metadata."""

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship


from src.models.sql.database import Base


class TermMetadata(Base):
    """
    Flexible metadata storage for ontology terms.

    Supports storing additional term properties and annotations in both
    structured JSONB format and as individual property-value pairs.

    Attributes:
        id: Primary key (UUID).
        term_id: Foreign key reference to ontology_terms.id.
        metadata_json: JSONB field for structured metadata storage (db column: metadata).
        property_name: Optional name of a specific property.
        property_value: Optional text value for the property.
        ontology_term: Relationship back to the parent OntologyTerm.

    Examples:
        - Store custom annotations like comment, xref, subset
        - Store additional OBO fields not in the main term table
        - Store tool-specific metadata or curation information
    """

    __tablename__ = "term_metadata"

    id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    term_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey("ontology_terms.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata",  # Database column name
        JSONB,
        nullable=True,
    )

    property_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    property_value: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Relationship to OntologyTerm (will be defined in ontology_term.py)
    term: Mapped["OntologyTerm"] = relationship(  # type: ignore[name-defined]
        "OntologyTerm",
        back_populates="metadata_entries",
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        """String representation for debugging."""
        if self.property_name:
            return f"<TermMetadata(id={self.id}, property='{self.property_name}')>"
        return f"<TermMetadata(id={self.id}, jsonb_fields={list(self.metadata_json.keys()) if self.metadata_json else []})>"
"""SQLAlchemy model for ontology terms."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.sql.database import Base


class OntologyTerm(Base):
    """
    Represents an individual term within an ontology.

    Each term belongs to an ontology and contains:
    - A unique term_id (e.g., "GO:0008150")
    - Name and definition
    - Namespace (for organizing terms within an ontology)
    - Obsolescence status and replacement information
    - Relationships to synonyms, related terms, and additional metadata
    """

    __tablename__ = "ontology_terms"

    # Primary key
    id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    # Foreign key to ontology
    ontology_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey("ontologies.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Core term identification
    term_id: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="Term identifier (e.g., GO:0008150, DOID:0001816)",
    )

    # Term content
    name: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        comment="Human-readable term name",
    )
    definition: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Detailed definition of the term",
    )
    namespace: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="Namespace within the ontology",
    )

    # Obsolescence tracking
    is_obsolete: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="false",
        comment="Whether this term is obsolete",
    )
    replaced_by: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        comment="Term ID that replaces this obsolete term",
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Relationships
    ontology: Mapped["Ontology"] = relationship(
        "Ontology",
        back_populates="terms",
        lazy="joined",
    )

    synonyms: Mapped[list["TermSynonym"]] = relationship(
        "TermSynonym",
        back_populates="term",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    # Relationships where this term is the subject
    subject_relationships: Mapped[list["TermRelationship"]] = relationship(
        "TermRelationship",
        foreign_keys="[TermRelationship.subject_term_id]",
        back_populates="subject_term",
        cascade="all, delete-orphan",
        lazy="select",
    )

    # Relationships where this term is the object
    object_relationships: Mapped[list["TermRelationship"]] = relationship(
        "TermRelationship",
        foreign_keys="[TermRelationship.object_term_id]",
        back_populates="object_term",
        cascade="all, delete-orphan",
        lazy="select",
    )

    metadata_entries: Mapped[list["TermMetadata"]] = relationship(
        "TermMetadata",
        back_populates="term",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    # Table constraints and indexes
    __table_args__ = (
        # Composite unique constraint: each ontology can only have one instance of a term_id
        UniqueConstraint(
            "ontology_id",
            "term_id",
            name="uq_ontology_terms_ontology_term",
        ),
        # Individual indexes for common queries
        Index("ix_ontology_terms_term_id", "term_id"),
        Index("ix_ontology_terms_name", "name"),
        Index("ix_ontology_terms_is_obsolete", "is_obsolete"),
        Index("ix_ontology_terms_ontology_id", "ontology_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return (
            f"<OntologyTerm(id={self.id}, term_id='{self.term_id}', "
            f"name='{self.name}', obsolete={self.is_obsolete})>"
        )
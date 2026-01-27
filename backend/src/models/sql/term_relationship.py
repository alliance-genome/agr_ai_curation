"""SQLAlchemy model for term relationships in ontologies."""

from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.sql.database import Base


class TermRelationship(Base):
    """
    Represents a relationship between two ontology terms.

    Models semantic relationships like 'is_a', 'part_of', 'regulates', etc.
    between terms within an ontology. Subject and object both reference
    ontology_terms, and the predicate defines the relationship type.

    Attributes:
        id: Primary key UUID
        subject_term_id: UUID of the subject term (source of relationship)
        predicate: Type of relationship (e.g., 'is_a', 'part_of')
        object_term_id: UUID of the object term (target of relationship)

    Constraints:
        - subject_term_id and object_term_id must reference valid ontology_terms
        - subject_term_id cannot equal object_term_id (no self-references)
        - Both FKs cascade on delete to maintain referential integrity

    Indexes:
        - subject_term_id (for finding all relationships from a term)
        - object_term_id (for finding all relationships to a term)
        - predicate (for filtering by relationship type)
    """

    __tablename__ = "term_relationships"

    id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    subject_term_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey("ontology_terms.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    predicate: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
    )

    object_term_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey("ontology_terms.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Relationships back to OntologyTerm
    subject_term: Mapped["OntologyTerm"] = relationship(
        "OntologyTerm",
        foreign_keys=[subject_term_id],
        back_populates="subject_relationships",
    )

    object_term: Mapped["OntologyTerm"] = relationship(
        "OntologyTerm",
        foreign_keys=[object_term_id],
        back_populates="object_relationships",
    )

    __table_args__ = (
        CheckConstraint(
            "subject_term_id != object_term_id",
            name="ck_term_relationships_no_self_reference",
        ),
        Index("ix_term_relationships_subject_term_id", "subject_term_id"),
        Index("ix_term_relationships_object_term_id", "object_term_id"),
        Index("ix_term_relationships_predicate", "predicate"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return (
            f"<TermRelationship(id={self.id}, "
            f"subject={self.subject_term_id}, "
            f"predicate='{self.predicate}', "
            f"object={self.object_term_id})>"
        )
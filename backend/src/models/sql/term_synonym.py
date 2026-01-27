"""SQLAlchemy model for term synonyms."""

from uuid import UUID, uuid4

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.sql.database import Base


class TermSynonym(Base):
    """
    Stores synonyms for ontology terms.

    Each term can have multiple synonyms with different scopes (EXACT, BROAD, NARROW, RELATED).
    Supports text search through indexed synonym column.

    Attributes:
        id: Primary key UUID
        term_id: Foreign key to ontology_terms table
        synonym: The synonym text (indexed for search)
        scope: OBO synonym scope (EXACT, BROAD, NARROW, RELATED)
        synonym_type: Optional synonym type from OBO format
        term: Relationship back to the OntologyTerm
    """

    __tablename__ = "term_synonyms"

    id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    term_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey("ontology_terms.id", ondelete="CASCADE"),
        nullable=False,
    )
    synonym: Mapped[str] = mapped_column(String(500), nullable=False)
    scope: Mapped[str] = mapped_column(String(20), nullable=False)
    synonym_type: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Relationship back to OntologyTerm
    # Note: back_populates will be set to "synonyms" when OntologyTerm is created
    term: Mapped["OntologyTerm"] = relationship(  # type: ignore[name-defined]
        "OntologyTerm",
        back_populates="synonyms",
    )

    __table_args__ = (
        # Index for text search on synonym
        Index("ix_term_synonyms_synonym", "synonym"),
        # Index on term_id for efficient lookups
        Index("ix_term_synonyms_term_id", "term_id"),
        # Composite index for term_id + scope queries
        Index("ix_term_synonyms_scope", "term_id", "scope"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<TermSynonym(id={self.id}, synonym='{self.synonym}', scope='{self.scope}')>"
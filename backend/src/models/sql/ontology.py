"""SQLAlchemy ORM model for ontologies metadata."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.sql.database import Base


class Ontology(Base):
    """
    Ontology metadata model.

    Stores high-level information about loaded ontologies including version,
    source URL, term count, and refresh timestamps. Each ontology can have
    multiple OntologyTerm records associated with it.

    Attributes:
        id: Primary key UUID for the ontology
        name: Unique name identifier for the ontology (e.g., 'GO', 'DOID')
        version: Version string of the ontology (e.g., '2024-01-01')
        source_url: URL where the ontology was downloaded from
        date_loaded: Timestamp when ontology was first loaded
        term_count: Number of terms in this ontology (must be >= 0)
        namespace: Default namespace for the ontology
        format_version: Format version of the source file (e.g., 'obo-1.4')
        last_refreshed: Timestamp of last ontology refresh/update
    """

    __tablename__ = "ontologies"

    id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        unique=True,
        index=True,
    )
    version: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )
    source_url: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )
    date_loaded: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )
    term_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="0",
    )
    namespace: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    format_version: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )
    last_refreshed: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationship to OntologyTerm with cascade delete
    terms: Mapped[list["OntologyTerm"]] = relationship(
        "OntologyTerm",
        back_populates="ontology",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint(
            "term_count >= 0",
            name="ck_ontologies_term_count",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<Ontology(id={self.id}, name='{self.name}', version='{self.version}')>"
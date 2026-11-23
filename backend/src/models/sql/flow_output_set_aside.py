"""The set-aside records present in a delivered file or chat output."""

from uuid import UUID

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Mapped, Session, mapped_column

from .database import Base


class FlowOutputSetAside(Base):
    __tablename__ = "flow_output_set_aside"

    output_id: Mapped[UUID] = mapped_column(primary_key=True)
    owner_id: Mapped[str] = mapped_column(String, nullable=False)
    flow_run_id: Mapped[str] = mapped_column(String, nullable=False)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("pdf_documents.id", ondelete="CASCADE"), nullable=False)
    included_keys: Mapped[list[str]] = mapped_column(JSONB, nullable=False)

    def persist(self, db: Session) -> None:
        """Flush within the output transaction. The formatter reports save failures."""
        try:
            db.add(self)
            db.flush([self])
            return
        except SQLAlchemyError as exc:
            # SQL parameters contain private record keys. Only the exception type
            # belongs in the formatter's delivery error and its diagnostic.
            error_type = type(exc).__name__
        raise RuntimeError(error_type)

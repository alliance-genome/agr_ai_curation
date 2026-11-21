"""Stored structural check results for provider files (``document_file_assessments``)."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models.sql.database import Base

ASSESSMENT_RESULT_OK = "ok"
ASSESSMENT_RESULT_REFUSED = "refused"


class DocumentFileAssessment(Base):
    """One check result for one provider file at one MD5 and one check version.

    AI Curation's own table, keyed by the provider's real file id and MD5. A
    stored result is never an access decision: every listing and import
    authorizes the file for the curator first.
    """

    __tablename__ = "document_file_assessments"

    id: Mapped[UUID] = mapped_column(PostgresUUID(as_uuid=True), primary_key=True, default=uuid4)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_file_id: Mapped[str] = mapped_column(String(64), nullable=False)
    md5: Mapped[str] = mapped_column(String(64), nullable=False)
    # Code version plus the threshold values in force, so a changed setting re-checks.
    check_version: Mapped[str] = mapped_column(String(128), nullable=False)
    result: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    measured_value: Mapped[int | None] = mapped_column(Integer, nullable=True)
    limit_value: Mapped[int | None] = mapped_column(Integer, nullable=True)
    few_characters_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    assessed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "provider", "provider_file_id", "md5", "check_version",
            name="uq_document_file_assessments_identity",
        ),
        CheckConstraint(
            f"result IN ('{ASSESSMENT_RESULT_OK}', '{ASSESSMENT_RESULT_REFUSED}')",
            name="ck_document_file_assessments_result",
        ),
        CheckConstraint(
            f"(result = '{ASSESSMENT_RESULT_REFUSED}') = (reason_code IS NOT NULL)",
            name="ck_document_file_assessments_reason",
        ),
        CheckConstraint(
            "few_characters_count IS NULL OR few_characters_count >= 0",
            name="ck_document_file_assessments_few_characters",
        ),
    )

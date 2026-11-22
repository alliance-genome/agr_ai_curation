"""Read and write stored file check results (``document_file_assessments``).

A stored result is reused for the same provider file, MD5 and check version.
It is never an access decision: callers authorize the file for the curator
before reading or showing any stored result.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from src.lib.document_sources.file_assessment import FileAssessment
from src.lib.document_sources.file_reasons import CHECK_REASONS, reason_in_group
from src.models.sql.document_file_assessment import (
    ASSESSMENT_RESULT_OK,
    ASSESSMENT_RESULT_REFUSED,
    DocumentFileAssessment,
)


def stored_assessment(
    session: Session, *, provider: str, provider_file_id: str, md5: str, check_version: str,
) -> FileAssessment | None:
    row = session.execute(
        select(DocumentFileAssessment).where(
            DocumentFileAssessment.provider == provider,
            DocumentFileAssessment.provider_file_id == provider_file_id,
            DocumentFileAssessment.md5 == md5,
            DocumentFileAssessment.check_version == check_version,
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    return FileAssessment(
        refused=row.result == ASSESSMENT_RESULT_REFUSED,
        reason=(
            reason_in_group(row.reason_code, CHECK_REASONS, group_name="check reasons")
            if row.reason_code is not None else None
        ),
        measured_value=row.measured_value,
        limit_value=row.limit_value,
        few_characters_count=row.few_characters_count,
    )


def store_assessment(
    session: Session,
    *,
    provider: str,
    provider_file_id: str,
    md5: str,
    check_version: str,
    assessment: FileAssessment,
) -> None:
    """Record a result; a concurrent writer of the same key wins and nothing is overwritten.

    The checks are deterministic for one file, MD5 and version, so two writers
    always hold the same result.
    """

    session.execute(
        insert(DocumentFileAssessment)
        .values(
            provider=provider,
            provider_file_id=provider_file_id,
            md5=md5,
            check_version=check_version,
            result=ASSESSMENT_RESULT_REFUSED if assessment.refused else ASSESSMENT_RESULT_OK,
            reason_code=assessment.reason.value if assessment.reason is not None else None,
            measured_value=assessment.measured_value,
            limit_value=assessment.limit_value,
            few_characters_count=assessment.few_characters_count,
        )
        .on_conflict_do_nothing(constraint="uq_document_file_assessments_identity")
    )

"""Capture owned document identity before invoking an extraction specialist."""

from uuid import UUID

from sqlalchemy import select

from src.schemas.execution_provenance import SourceDocumentProvenance


def capture_source_document(
    document_id: str | None, user_id: str | None
) -> SourceDocumentProvenance | None:
    """Read only the initiating user's document; never import credentials/context.

    Called off the event loop. Missing documents/context do not fabricate a digest.
    Database errors propagate rather than quietly producing false missingness.
    """
    if not document_id or not user_id:
        return None

    from src.models.sql.database import SessionLocal
    from src.models.sql.pdf_document import PDFDocument
    from src.models.sql.user import User

    with SessionLocal() as db:
        document = db.scalar(
            select(PDFDocument)
            .join(User, PDFDocument.user_id == User.id)
            .where(PDFDocument.id == UUID(document_id), User.auth_sub == user_id)
        )
        if document is None:
            return None
        # The main file's converted text; a paper of supplements only has none.
        main_file = document.main_file_or_none
        artifact_id = main_file.source_provider_converted_artifact_id if main_file is not None else None
        return SourceDocumentProvenance(
            document_id=document.id,
            provider=document.source_provider,
            reference_curie=document.source_provider_reference_curie,
            converted_artifact_id=str(artifact_id) if artifact_id is not None else None,
            converted_artifact_sha256=(
                main_file.source_converted_artifact_sha256 if main_file is not None else None
            ),
        )

"""Request-level file listing: by identifier before import, or for an imported paper (P6)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from src.lib.document_sources.access import DocumentSourceRequestContext
from src.lib.document_sources.file_assessment import FileAssessment
from src.lib.document_sources.file_assessment_store import store_assessment, stored_assessment
from src.lib.document_sources.file_listing import (
    FileListing,
    current_file_check_limits,
    list_reference_files,
)
from src.lib.document_sources.models import DocumentSourceProvider
from src.models.sql.database import SessionLocal
from src.models.sql.pdf_document import PDFDocument


class FileListingUnavailable(RuntimeError):
    """The listing cannot be given for this request; ``code`` says why (codes only)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _stores(provider_id: str) -> tuple[Callable[..., FileAssessment | None], Callable[..., None]]:
    def load(file_id: str, md5: str, check_version: str) -> FileAssessment | None:
        with SessionLocal() as session:
            return stored_assessment(
                session, provider=provider_id, provider_file_id=file_id, md5=md5,
                check_version=check_version,
            )

    def save(file_id: str, md5: str, check_version: str, assessment: FileAssessment) -> None:
        with SessionLocal.begin() as session:
            store_assessment(
                session, provider=provider_id, provider_file_id=file_id, md5=md5,
                check_version=check_version, assessment=assessment,
            )

    return load, save


async def _list(
    provider: DocumentSourceProvider,
    lookup: str,
    context: DocumentSourceRequestContext,
    files_in_paper: tuple[str, ...],
    *,
    expected_reference_id: str | None = None,
) -> FileListing:
    from src.lib.openai_agents.config import get_document_file_listing_check_limit

    token = (context.curator_token or "").strip()
    if not token:
        raise FileListingUnavailable("document_source_curator_token_unavailable")
    # Always the provider's resolved reference: licence rules apply only to one.
    reference = await provider.resolve_reference(lookup, request_bearer_token=token)
    if expected_reference_id is not None and reference.reference_id != expected_reference_id:
        # The stored reference no longer resolves to the paper's own reference: never
        # list (or let an import take) another reference's files.
        raise FileListingUnavailable("paper_reference_mismatch")
    load, save = _stores(provider.provider_id)
    return await list_reference_files(
        provider=provider,
        reference=reference,
        authorized_group_ids=tuple(context.authorized_group_ids),
        bearer_kind=context.bearer_kind,
        request_bearer_token=token,
        files_in_paper=files_in_paper,
        limits=current_file_check_limits(),
        check_limit=get_document_file_listing_check_limit(),
        load_stored=load,
        save_assessment=save,
    )


async def list_files_for_identifier(
    identifier: str,
    *,
    provider: DocumentSourceProvider,
    context: DocumentSourceRequestContext,
) -> FileListing:
    normalized = provider.normalize_identifier(identifier)
    if not normalized.is_valid or normalized.normalized is None:
        raise FileListingUnavailable("invalid_identifier")
    return await _list(provider, normalized.normalized, context, ())


@dataclass(frozen=True, slots=True)
class _PaperSource:
    provider: str | None
    reference_id: str | None
    reference_curie: str | None
    file_ids: tuple[str, ...]


def _paper_lookup(document_id: UUID, owner_user_id: int) -> _PaperSource:
    from src.services.document_access import require_owned_document

    with SessionLocal() as session:
        paper: PDFDocument = require_owned_document(session, document_id, owner_user_id)
        file_ids = tuple(
            identifier
            for file_row in paper.files
            for identifier in (file_row.source_provider_source_file_id, file_row.source_provider_pdf_artifact_id)
            if identifier
        )
        return _PaperSource(
            provider=paper.source_provider,
            reference_id=paper.source_provider_reference_id,
            reference_curie=paper.source_provider_reference_curie,
            file_ids=file_ids,
        )


async def list_files_for_paper(
    document_id: UUID,
    *,
    owner_user_id: int,
    provider: DocumentSourceProvider,
    context: DocumentSourceRequestContext,
) -> FileListing:
    """List an imported paper's provider files; only papers imported from the provider have them.

    An uploaded paper, including one linked to its reference
    (``pdf_document_reference_links``), has no provider file list in this release.
    The paper is resolved by its stored reference CURIE, the provider's own id, and
    must come back as the stored reference id. A stored reference id is never looked
    up as an identifier: a provider may read a bare number as another kind of id
    (ABC reads it as a PMID).
    """

    import asyncio

    paper = await asyncio.to_thread(_paper_lookup, document_id, owner_user_id)
    if paper.provider != provider.provider_id:
        raise FileListingUnavailable("not_imported_from_provider")
    if not paper.reference_id or not paper.reference_curie:
        raise FileListingUnavailable("paper_reference_incomplete")
    return await _list(
        provider, paper.reference_curie, context, paper.file_ids,
        expected_reference_id=paper.reference_id,
    )

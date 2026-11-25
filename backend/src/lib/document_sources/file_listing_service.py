"""Request-level file listing: by identifier before import, or for an imported paper (P6)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select

from src.lib.document_sources.access import DocumentSourceRequestContext
from src.lib.document_sources.file_assessment import FileAssessment
from src.lib.document_sources.file_assessment_store import store_assessment, stored_assessment
from src.lib.document_sources.file_listing import (
    FileListing,
    FileListingError,
    ListingRoute,
    ProviderObservation,
    current_file_check_limits,
    list_reference_files,
)
from src.lib.document_sources.file_reasons import (
    PROVIDER_OBSERVATION_REASONS,
    FileReason,
    FileReasonGroupError,
    SupplementState,
)
from src.lib.document_sources.models import (
    DocumentSourceProvider,
    SourceArtifact,
    SourceReference,
)
from src.models.sql.database import SessionLocal
from src.models.sql.pdf_document import FILE_STATUS_FAILED, FILE_STATUS_REMOVED, PDFDocument
from src.models.sql.pdf_processing_job import PdfProcessingJob


class FileListingUnavailable(RuntimeError):
    """The listing cannot be given for this request; ``code`` says why (codes only)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class SupplementProgressMetadataError(FileListingError):
    """A job's ``metadata_json.supplements`` (interface I1) is not in the written shape."""


def provider_observations_from_jobs(
    job_metadata_newest_first: Iterable[Mapping[str, Any] | None],
) -> dict[str, ProviderObservation]:
    """The last thing the provider told this paper's imports, per provider file id.

    Reads each job's ``metadata_json.supplements`` (I1), newest job first; the first
    entry for a file decides. A ``left_out`` entry whose ``reason_code`` is a provider
    observation gives the file's observation; any other first entry (added, still
    waiting, left out by a check) gives none, so a file added later shows nothing.
    Jobs without ``supplements`` (every job before P7b) are skipped. An entry in
    another shape is a named error: our own imports write it.
    """

    decided: set[str] = set()
    observations: dict[str, ProviderObservation] = {}
    for metadata in job_metadata_newest_first:
        if not metadata or "supplements" not in metadata:
            continue
        entries = metadata["supplements"]
        if not isinstance(entries, list):
            raise SupplementProgressMetadataError("metadata.supplements is not a list")
        for entry in entries:
            file_id, observation = _observation_from_entry(entry)
            if file_id in decided:
                continue
            decided.add(file_id)
            if observation is not None:
                observations[file_id] = observation
    return observations


def _observation_from_entry(entry: object) -> tuple[str, ProviderObservation | None]:
    if not isinstance(entry, Mapping):
        raise SupplementProgressMetadataError("a supplements entry is not an object")
    file_id = entry.get("provider_file_id")
    state = entry.get("state")
    code = entry.get("reason_code")
    observed_at = entry.get("observed_at")
    if not isinstance(file_id, str) or not file_id:
        raise SupplementProgressMetadataError("a supplements entry has no provider_file_id")
    if state not in {member.value for member in SupplementState}:
        raise SupplementProgressMetadataError(f"a supplements entry has an unknown state {state!r}")
    if code is None:
        return file_id, None
    if not isinstance(code, str):
        raise SupplementProgressMetadataError("a supplements entry's reason_code is not a string")
    try:
        reason = FileReason(code)
    except ValueError:
        raise SupplementProgressMetadataError(f"a supplements entry has an unknown reason_code {code!r}") from None
    if state != SupplementState.LEFT_OUT.value or reason not in PROVIDER_OBSERVATION_REASONS:
        return file_id, None
    if not isinstance(observed_at, str):
        raise SupplementProgressMetadataError("a provider observation has no observed_at")
    try:
        when = datetime.fromisoformat(observed_at)
    except ValueError:
        raise SupplementProgressMetadataError("a provider observation's observed_at is not ISO-8601") from None
    if when.tzinfo is None:
        raise SupplementProgressMetadataError("a provider observation's observed_at has no time zone")
    try:
        return file_id, ProviderObservation(code=reason, observed_at=when)
    except FileReasonGroupError as exc:
        raise SupplementProgressMetadataError(str(exc)) from None


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


async def _list_resolved(
    provider: DocumentSourceProvider,
    reference: SourceReference,
    context: DocumentSourceRequestContext,
    files_in_paper: tuple[str, ...],
    *,
    route: ListingRoute,
    provider_observations: Mapping[str, ProviderObservation],
    owner_user_id: int,
    expected_reference_id: str | None = None,
    artifacts: Sequence[SourceArtifact] | None = None,
    check_unchecked: bool = True,
) -> FileListing:
    from src.lib.openai_agents.config import get_document_file_listing_check_limit

    token = (context.curator_token or "").strip()
    if not token:
        raise FileListingUnavailable("document_source_curator_token_unavailable")
    if not isinstance(reference, SourceReference):
        raise FileListingError("Files are listed only for a resolved provider reference")
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
        route=route,
        provider_observations=provider_observations,
        find_paper_in_library=(
            _library_lookup(provider.provider_id, owner_user_id) if route is ListingRoute.IDENTIFIER else None
        ),
        artifacts=artifacts,
        check_unchecked=check_unchecked,
    )


async def _list(
    provider: DocumentSourceProvider,
    lookup: str,
    context: DocumentSourceRequestContext,
    files_in_paper: tuple[str, ...],
    *,
    route: ListingRoute,
    provider_observations: Mapping[str, ProviderObservation],
    owner_user_id: int,
    expected_reference_id: str | None = None,
) -> FileListing:
    token = (context.curator_token or "").strip()
    if not token:
        raise FileListingUnavailable("document_source_curator_token_unavailable")
    # Always the provider's resolved reference: licence rules apply only to one.
    reference = await provider.resolve_reference(lookup, request_bearer_token=token)
    return await _list_resolved(
        provider,
        reference,
        context,
        files_in_paper,
        route=route,
        provider_observations=provider_observations,
        owner_user_id=owner_user_id,
        expected_reference_id=expected_reference_id,
    )


def _library_lookup(provider_id: str, owner_user_id: int):
    """The import's own duplicate check for this decision, read only (no phantom clean-up)."""

    async def find(decision) -> str | None:
        from src.lib.document_sources.identifier_import import _build_reference_source_provenance
        from src.lib.document_sources.provenance import find_existing_document_by_source

        provenance = _build_reference_source_provenance(decision)

        def lookup() -> str | None:
            with SessionLocal() as session:
                existing = find_existing_document_by_source(
                    session,
                    user_id=owner_user_id,
                    source_provider=provider_id,
                    reference_id=provenance.get("reference_id"),
                    reference_curie=provenance.get("reference_curie"),
                    converted_artifact_id=provenance.get("converted_artifact_id"),
                    source_md5=provenance.get("source_md5"),
                )
                return str(existing.id) if existing is not None else None

        return await asyncio.to_thread(lookup)

    return find


async def list_files_for_identifier(
    identifier: str,
    *,
    owner_user_id: int,
    provider: DocumentSourceProvider,
    context: DocumentSourceRequestContext,
) -> FileListing:
    normalized = provider.normalize_identifier(identifier)
    if not normalized.is_valid or normalized.normalized is None:
        raise FileListingUnavailable("invalid_identifier")
    return await _list(
        provider, normalized.normalized, context, (), route=ListingRoute.IDENTIFIER, provider_observations={},
        owner_user_id=owner_user_id,
    )


@dataclass(frozen=True, slots=True)
class _PaperSource:
    provider: str | None
    reference_id: str | None
    reference_curie: str | None
    file_ids: tuple[str, ...]
    provider_observations: dict[str, ProviderObservation]


def _paper_lookup(document_id: UUID, owner_user_id: int) -> _PaperSource:
    from src.services.document_access import require_owned_document

    with SessionLocal() as session:
        paper: PDFDocument = require_owned_document(session, document_id, owner_user_id)
        # Files being removed and failed leftovers from a dead add are not live in the
        # paper: a removed file answers ``file_removal_unfinished`` until its row is gone
        # (``start_supplement_add``), and a failed leftover is cleared before the next add.
        file_ids = tuple(
            identifier
            for file_row in paper.files
            if file_row.status not in (FILE_STATUS_REMOVED, FILE_STATUS_FAILED)
            for identifier in (file_row.source_provider_source_file_id, file_row.source_provider_pdf_artifact_id)
            if identifier
        )
        # This paper's jobs, newest first; jobs belong to the paper's owner.
        job_metadata = session.execute(
            select(PdfProcessingJob.metadata_json)
            .where(PdfProcessingJob.document_id == document_id, PdfProcessingJob.user_id == owner_user_id)
            .order_by(PdfProcessingJob.created_at.desc(), PdfProcessingJob.id.desc())
        ).scalars()
        return _PaperSource(
            provider=paper.source_provider,
            reference_id=paper.source_provider_reference_id,
            reference_curie=paper.source_provider_reference_curie,
            file_ids=file_ids,
            provider_observations=provider_observations_from_jobs(job_metadata),
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

    paper = await asyncio.to_thread(_paper_lookup, document_id, owner_user_id)
    if paper.provider != provider.provider_id:
        raise FileListingUnavailable("not_imported_from_provider")
    if not paper.reference_id or not paper.reference_curie:
        raise FileListingUnavailable("paper_reference_incomplete")
    return await _list(
        provider, paper.reference_curie, context, paper.file_ids,
        route=ListingRoute.PAPER, provider_observations=paper.provider_observations,
        owner_user_id=owner_user_id, expected_reference_id=paper.reference_id,
    )


async def list_files_for_paper_from_artifacts(
    document_id: UUID,
    *,
    owner_user_id: int,
    provider: DocumentSourceProvider,
    context: DocumentSourceRequestContext,
    reference: SourceReference,
    artifacts: Sequence[SourceArtifact],
    check_unchecked: bool = True,
) -> FileListing:
    """List an imported paper's files from a resolved reference and already-fetched artifacts.

    ``artifacts`` must have been fetched with ``provider.list_artifacts(reference, ...)``
    using the resolved ``SourceReference`` (never a bare identifier string) so the
    provider's copyright-licence access rules are applied identically to
    ``list_files_for_paper``. Sends no reference lookup and no artifact-list request to
    the provider; only check downloads of unchecked converted texts remain (stored checks
    reused by MD5, or skipped when ``check_unchecked=False``). Enforces the same paper
    ownership, provider, and reference-id checks as ``list_files_for_paper`` and never
    falls back to a fresh provider listing.
    """

    paper = await asyncio.to_thread(_paper_lookup, document_id, owner_user_id)
    if paper.provider != provider.provider_id:
        raise FileListingUnavailable("not_imported_from_provider")
    if not paper.reference_id or not paper.reference_curie:
        raise FileListingUnavailable("paper_reference_incomplete")
    if not isinstance(reference, SourceReference):
        raise FileListingError("Files are listed only for a resolved provider reference")
    if reference.provider != provider.provider_id:
        raise FileListingError("Resolved reference belongs to another provider")
    if artifacts is None:
        raise FileListingError("Pre-fetched artifacts are required")
    return await _list_resolved(
        provider,
        reference,
        context,
        paper.file_ids,
        route=ListingRoute.PAPER,
        provider_observations=paper.provider_observations,
        owner_user_id=owner_user_id,
        expected_reference_id=paper.reference_id,
        artifacts=artifacts,
        check_unchecked=check_unchecked,
    )

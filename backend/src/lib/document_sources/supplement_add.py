"""Add converted text supplements to an existing paper (KANBAN-1976 / ALL-1460, P7c1).

The write path every text supplement goes through: a curator's "add files" (the
``POST /documents/{id}/files`` route) or an import's supplement stage (P7b, in its
own job) calls ``add_files_to_paper``. The paper stays usable throughout.

- No conversion request is ever sent: an add takes only files the provider has
  already converted.
- **Check phase, nothing written.** The paper's files are listed again with the
  curator's login. Each chosen file, in listing order, is checked; a file that
  fails a step is left out with a ``FileReason`` code and the next file goes on.
- **Write phase.** Every accepted file gets its row (``pending``), its stored
  Markdown and processed JSON, its piece range and its pieces; then all of them
  become ``ready`` together. A failure while writing removes this add's pieces
  (by range), stored files and rows, and leaves the paper and its earlier files
  unchanged. The paper's status and completion time never change.
- Rows left ``failed`` by an add that died (the stale sweep marks them) are removed
  with their pieces at the start of the next add on the paper.

Codes only: no wording is built here.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select

from src.lib.document_sources.access import DocumentSourceRequestContext
from src.lib.document_sources.file_assessment import FileProfile, assess_converted_file
from src.lib.document_sources.file_listing import (
    ConvertedTextDownloadFailure,
    ListedFile,
    ListedFileStatus,
    current_file_check_limits,
    download_converted_text,
)
from src.lib.document_sources.file_reasons import ADD_REFUSAL_REASONS, FileReason
from src.lib.document_sources.models import DocumentSourceError, DocumentSourceProvider
from src.lib.observability.cost_context import costed_document_processing
from src.models.sql.pdf_document import (
    DISPLAY_MODE_TEXT,
    FILE_ROLE_SUPPLEMENT,
    FILE_STATUS_FAILED,
    FILE_STATUS_PENDING,
    FILE_STATUS_READY,
    PDFDocument,
    PDFDocumentFile,
)

logger = logging.getLogger(__name__)

PAPER_STATUS_COMPLETED = "completed"


class SupplementAddError(RuntimeError):
    """The add cannot go on at all; ``code`` says why (codes only)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class ChosenFile:
    """One file the curator chose, as the listing showed it (interface I2)."""

    provider_file_id: str
    md5: str | None
    converted_md5: str


@dataclass(frozen=True, slots=True)
class FileOutcome:
    """What happened to one chosen file (interface I2).

    ``measured_value`` and ``limit_value`` are integers whose unit the reason code
    names: bytes for size codes, files for ``paper_file_limit``.
    """

    provider_file_id: str
    outcome: str  # "added" or "left_out"
    reason_code: FileReason | None = None
    measured_value: int | None = None
    limit_value: int | None = None
    file_id: UUID | None = None
    display_name: str | None = None

    def __post_init__(self) -> None:
        if self.outcome not in ("added", "left_out"):
            raise ValueError(f"Unknown add outcome {self.outcome!r}")
        if (self.outcome == "left_out") != (self.reason_code is not None):
            raise ValueError("A left-out file needs a reason code; an added file has none")
        if self.reason_code is not None and self.reason_code not in ADD_REFUSAL_REASONS:
            raise ValueError(f"{self.reason_code.value!r} is not an add refusal reason")


@dataclass(frozen=True, slots=True)
class AddResult:
    files: tuple[FileOutcome, ...]

    @property
    def added(self) -> tuple[FileOutcome, ...]:
        return tuple(item for item in self.files if item.outcome == "added")


@dataclass(frozen=True, slots=True)
class _PaperState:
    owner_subject: str
    status: str
    file_count: int
    ready_text_bytes: int
    next_tab_order: int
    leftovers: tuple["_Leftover", ...]


@dataclass(frozen=True, slots=True)
class _Leftover:
    """A file row an earlier add left ``failed``: its pieces and stored files are removed."""

    file_id: UUID
    chunk_index_start: int | None
    chunk_index_end: int | None
    paths: tuple[str, ...]


@dataclass(slots=True)
class _Accepted:
    listed: ListedFile
    display_name: str
    markdown: str
    sha256: str
    prepared: Any  # ingestion.PreparedFile
    file_id: UUID


Checkpoint = Callable[[str], Awaitable[None]]


def _display_name(listed: ListedFile) -> str:
    """The provider's display name with the original's extension (``Table_S1.xlsx``)."""

    name = (listed.display_name or listed.provider_file_id).strip()
    extension = str(listed.source_artifact.metadata.get("file_extension") or "").strip().lower()
    if extension and not name.lower().endswith(f".{extension}"):
        name = f"{name}.{extension}"
    return name[:255]


def _scoped_file_hash(owner_user_id: int, sha256: str) -> str:
    # The same user-scoped form as a main file's hash (identifier_import).
    return hashlib.sha256(f"{owner_user_id}:{sha256}".encode()).hexdigest()


async def add_files_to_paper(
    document_id: UUID,
    owner_user_id: int,
    request_context: DocumentSourceRequestContext,
    files: Sequence[ChosenFile],
    *,
    job_id: UUID,
    provider: DocumentSourceProvider,
    weaviate_client: Any,
    stage_checkpoint: Checkpoint,
) -> AddResult:
    """Add the chosen converted text supplements to one of the curator's papers.

    Runs inside the job ``job_id`` (the paper's one active job). ``stage_checkpoint``
    raises when the job is cancelled; it is called between files and stages.
    """

    from src.lib.document_sources.file_listing_service import list_files_for_paper
    from src.lib.openai_agents.config import (
        get_document_file_max_converted_text_bytes,
        get_document_max_files_per_paper,
        get_document_paper_max_text_bytes,
    )

    if not files:
        raise SupplementAddError("no_files_chosen")
    chosen_ids = [chosen.provider_file_id for chosen in files]
    if len(set(chosen_ids)) != len(chosen_ids):
        raise SupplementAddError("file_chosen_twice")
    paper = await asyncio.to_thread(_paper_state, document_id, owner_user_id)
    if paper.status != PAPER_STATUS_COMPLETED:
        raise SupplementAddError("paper_not_completed")
    if paper.leftovers:
        await _remove_leftovers(document_id, paper.owner_subject, paper.leftovers)

    listing = await list_files_for_paper(
        document_id, owner_user_id=owner_user_id, provider=provider, context=request_context,
    )
    listed_by_id = {item.provider_file_id: item for item in listing.files}
    listing_order = {item.provider_file_id: index for index, item in enumerate(listing.files)}
    # In listing order (the tab order the new files take); files no longer listed go last.
    ordered = sorted(files, key=lambda chosen: listing_order.get(chosen.provider_file_id, len(listing_order)))

    max_files = get_document_max_files_per_paper()
    max_text_bytes = get_document_paper_max_text_bytes()
    download_cap = get_document_file_max_converted_text_bytes()
    limits = current_file_check_limits()
    token = (request_context.curator_token or "").strip()

    outcomes: dict[str, FileOutcome] = {}
    accepted: list[_Accepted] = []
    file_count = paper.file_count
    text_total = paper.ready_text_bytes
    for chosen in ordered:
        await stage_checkpoint("checking_files")
        file_id = chosen.provider_file_id
        listed = listed_by_id.get(file_id)
        if listed is None:
            outcomes[file_id] = FileOutcome(file_id, "left_out", FileReason.FILE_NOT_IN_REFERENCE)
            continue
        display_name = _display_name(listed)

        def left_out(reason: FileReason, measured: int | None = None, limit: int | None = None) -> FileOutcome:
            return FileOutcome(
                file_id, "left_out", reason, measured_value=measured, limit_value=limit,
                display_name=display_name,
            )

        if listed.in_this_paper:
            outcomes[file_id] = left_out(FileReason.ALREADY_IN_PAPER)
            continue
        if listed.status is not ListedFileStatus.READY or listed.converted_artifact is None or listed.file_role is None:
            outcomes[file_id] = left_out(FileReason.FILE_NOT_TICKABLE)
            continue
        if listed.file_role != FILE_ROLE_SUPPLEMENT:
            # A paper's main file is never added to an existing paper.
            outcomes[file_id] = left_out(FileReason.FILE_NOT_TICKABLE)
            continue
        if listed.converted_md5 != chosen.converted_md5 or listed.md5 != chosen.md5:
            outcomes[file_id] = left_out(FileReason.FILE_CHANGED_SINCE_LISTED)
            continue
        if file_count + 1 > max_files:
            outcomes[file_id] = left_out(FileReason.PAPER_FILE_LIMIT, file_count + 1, max_files)
            continue

        checked = await _download_and_check(
            provider, listed, token=token, download_cap=download_cap, limits=limits,
        )
        if isinstance(checked, FileOutcome):
            outcomes[file_id] = left_out(checked.reason_code, checked.measured_value, checked.limit_value)
            continue
        markdown, sha256 = checked

        prepared = await _prepare(listed, markdown, document_id=document_id, display_name=display_name)
        if prepared is None:
            outcomes[file_id] = left_out(FileReason.MARKDOWN_INVALID)
            continue
        if text_total + prepared.text_bytes > max_text_bytes:
            outcomes[file_id] = left_out(FileReason.PAPER_TEXT_LIMIT, text_total + prepared.text_bytes, max_text_bytes)
            continue
        text_total += prepared.text_bytes
        file_count += 1
        accepted.append(_Accepted(
            listed=listed, display_name=display_name, markdown=markdown, sha256=sha256, prepared=prepared,
            file_id=uuid.uuid4(),
        ))

    if accepted:
        await _write(
            accepted, document_id=document_id, owner_user_id=owner_user_id, paper=paper,
            weaviate_client=weaviate_client, stage_checkpoint=stage_checkpoint,
        )
        for item in accepted:
            outcomes[item.listed.provider_file_id] = FileOutcome(
                item.listed.provider_file_id, "added", file_id=item.file_id, display_name=item.display_name,
            )
    return AddResult(files=tuple(outcomes[chosen.provider_file_id] for chosen in ordered))


async def _download_and_check(
    provider: DocumentSourceProvider,
    listed: ListedFile,
    *,
    token: str,
    download_cap: int,
    limits: Any,
) -> tuple[str, str] | FileOutcome:
    """Download the converted text with the curator's login and run the stored or new checks."""

    from src.lib.document_sources.file_listing_service import _stores

    converted = listed.converted_artifact
    assert converted is not None
    profile = FileProfile(single_title_required=listed.single_title_required)
    load, save = _stores(provider.provider_id)
    md5 = converted.md5sum
    stored = await asyncio.to_thread(load, converted.artifact_id, md5, limits.check_version) if md5 else None
    if stored is not None and stored.refused:
        # Refused before for this exact text and check version: not downloaded again.
        return FileOutcome(
            listed.provider_file_id, "left_out", stored.reason,
            measured_value=stored.measured_value, limit_value=stored.limit_value,
        )

    def use(content: bytes):
        assessment = stored if stored is not None else assess_converted_file(content, profile, limits)
        return content, hashlib.sha256(content).hexdigest(), assessment

    try:
        outcome = await download_converted_text(
            provider, converted.artifact_id, request_bearer_token=token, max_bytes=download_cap, use=use,
        )
    except DocumentSourceError:
        return FileOutcome(listed.provider_file_id, "left_out", FileReason.SOURCE_DOWNLOAD_FAILED)
    failure_codes = {
        ConvertedTextDownloadFailure.TOO_LARGE: FileReason.TOO_LARGE,
        ConvertedTextDownloadFailure.MISSING: FileReason.SOURCE_FILE_MISSING,
        ConvertedTextDownloadFailure.UNAVAILABLE: FileReason.SOURCE_UNAVAILABLE,
        ConvertedTextDownloadFailure.ACCESS_DENIED: FileReason.ACCESS_DENIED,
    }
    if outcome.failure is not None:
        return FileOutcome(
            listed.provider_file_id, "left_out", failure_codes[outcome.failure], limit_value=outcome.limit_value,
        )
    assert outcome.result is not None
    content, sha256, assessment = outcome.result
    if stored is None and md5:
        await asyncio.to_thread(save, converted.artifact_id, md5, limits.check_version, assessment)
    if assessment.refused:
        return FileOutcome(
            listed.provider_file_id, "left_out", assessment.reason,
            measured_value=assessment.measured_value, limit_value=assessment.limit_value,
        )
    # R2 passed, so the bytes are valid UTF-8.
    return content.decode("utf-8"), sha256


async def _prepare(listed: ListedFile, markdown: str, *, document_id: UUID, display_name: str):
    """Clean up, section by structure and chunk one file. ``None`` when its text cannot be read."""

    from src.lib.document_sources.ingestion import (
        DocumentSourceMarkdownValidationError,
        prepare_file_pieces,
        provider_markdown_to_pipeline_elements,
        structural_sections,
    )
    from src.lib.pipeline.text_cleanup import run_text_cleanup
    from src.models.strategy import ChunkingStrategy

    try:
        elements, _warnings = await run_text_cleanup(
            provider_markdown_to_pipeline_elements, markdown, (), log_id=listed.provider_file_id,
        )
    except DocumentSourceMarkdownValidationError:
        return None
    return await prepare_file_pieces(
        elements,
        document_id=str(document_id),
        sectioning=structural_sections(
            file_title_is_first_heading=listed.single_title_required, fallback_section=display_name,
        ),
        strategy=ChunkingStrategy.get_research_strategy(),
    )


async def _write(
    accepted: list[_Accepted],
    *,
    document_id: UUID,
    owner_user_id: int,
    paper: _PaperState,
    weaviate_client: Any,
    stage_checkpoint: Checkpoint,
) -> None:
    from src.lib.document_files.service import mark_files_ready
    from src.lib.document_sources.ingestion import _save_processed_json, _save_source_markdown, store_file_pieces

    subject = paper.owner_subject
    written_paths: list[str] = []
    try:
        await stage_checkpoint("saving_files")
        rows = []
        for tab_order, item in enumerate(accepted, start=paper.next_tab_order):
            markdown_path = await _save_source_markdown(markdown=item.markdown, file_id=item.file_id, user_id=subject)
            written_paths.append(markdown_path)
            processed_path = await _save_processed_json(
                elements=item.prepared.elements, file_id=item.file_id, user_id=subject,
            )
            written_paths.append(processed_path)
            rows.append(_new_row(item, document_id=document_id, owner_user_id=owner_user_id, tab_order=tab_order,
                                 markdown_path=markdown_path, processed_path=processed_path))
        await asyncio.to_thread(_insert_rows, document_id, rows)
        for item in accepted:
            await stage_checkpoint("vector_storage")
            await store_file_pieces(
                item.prepared, document_id=str(document_id), file_id=item.file_id, user_id=subject,
                weaviate_client=weaviate_client, resolve_figures=False,
            )
        await stage_checkpoint("finishing")
        await asyncio.to_thread(mark_files_ready, {item.file_id: item.prepared.text_bytes for item in accepted})
    except BaseException:
        await _undo(document_id, subject, [item.file_id for item in accepted], written_paths)
        raise
    await _after_add(document_id, subject, weaviate_client)


def _new_row(
    item: _Accepted,
    *,
    document_id: UUID,
    owner_user_id: int,
    tab_order: int,
    markdown_path: str,
    processed_path: str,
) -> PDFDocumentFile:
    from datetime import datetime, timezone

    original = item.listed.source_artifact
    converted = item.listed.converted_artifact
    assert converted is not None
    return PDFDocumentFile(
        id=item.file_id,
        document_id=document_id,
        role=FILE_ROLE_SUPPLEMENT,
        display_name=item.display_name,
        status=FILE_STATUS_PENDING,
        tab_order=tab_order,
        display_mode=DISPLAY_MODE_TEXT,
        file_path=markdown_path,
        file_hash=_scoped_file_hash(owner_user_id, item.sha256),
        file_size=len(item.markdown.encode("utf-8")),
        page_count=None,
        processed_json_path=processed_path,
        source_markdown_path=markdown_path,
        source_provider_source_file_id=original.artifact_id,
        source_provider_converted_artifact_id=converted.artifact_id,
        source_md5=original.md5sum,
        source_converted_artifact_sha256=item.sha256,
        source_file_class=str(converted.metadata.get("file_class") or converted.role.value),
        source_file_extension=str(converted.metadata.get("file_extension") or converted.artifact_format.value),
        source_artifact_status=converted.status.value,
        source_import_status="processing",
        source_imported_at=datetime.now(timezone.utc),
        source_access_scope=original.access_policy.scope.value,
        source_access_group_ids=list(original.access_policy.group_ids),
    )


def _paper_state(document_id: UUID, owner_user_id: int) -> _PaperState:
    from src.lib.document_files.service import FileRangeMissingError
    from src.models.sql.database import SessionLocal
    from src.models.sql.user import User
    from src.services.document_access import require_owned_document

    with SessionLocal() as session:
        paper: PDFDocument = require_owned_document(session, document_id, owner_user_id)
        subject = session.scalar(select(User.auth_sub).where(User.id == owner_user_id))
        if not subject:
            raise SupplementAddError("owner_without_subject")
        live = [row for row in paper.files if row.status != FILE_STATUS_FAILED]
        ready_bytes = 0
        for row in live:
            if row.status == FILE_STATUS_READY:
                if row.text_bytes is None:
                    raise FileRangeMissingError(f"Ready file {row.id} has no measured text size")
                ready_bytes += row.text_bytes
        leftovers = tuple(
            _Leftover(
                file_id=row.id, chunk_index_start=row.chunk_index_start, chunk_index_end=row.chunk_index_end,
                paths=tuple(path for path in (row.source_markdown_path, row.processed_json_path, row.file_path) if path),
            )
            for row in paper.files
            if row.status == FILE_STATUS_FAILED and row.role == FILE_ROLE_SUPPLEMENT
        )
        return _PaperState(
            owner_subject=subject,
            status=paper.status,
            file_count=len(live),
            ready_text_bytes=ready_bytes,
            next_tab_order=max((row.tab_order for row in paper.files), default=-1) + 1,
            leftovers=leftovers,
        )


def _insert_rows(document_id: UUID, rows: list[PDFDocumentFile]) -> None:
    from src.models.sql.database import SessionLocal

    with SessionLocal() as session, session.begin():
        # Serialize with other writers of the paper's files (range reservation locks it too).
        session.execute(select(PDFDocument.id).where(PDFDocument.id == document_id).with_for_update())
        session.add_all(rows)


def _delete_rows(document_id: UUID, file_ids: Sequence[UUID]) -> None:
    from sqlalchemy import delete

    from src.models.sql.database import SessionLocal

    with SessionLocal() as session, session.begin():
        session.execute(
            delete(PDFDocumentFile).where(
                PDFDocumentFile.document_id == document_id,
                PDFDocumentFile.id.in_(list(file_ids)),
                PDFDocumentFile.role == FILE_ROLE_SUPPLEMENT,
                PDFDocumentFile.status.in_((FILE_STATUS_PENDING, FILE_STATUS_FAILED)),
            )
        )


def _ranges(document_id: UUID, file_ids: Sequence[UUID]) -> list[tuple[int, int]]:
    from src.models.sql.database import SessionLocal

    with SessionLocal() as session:
        rows = session.execute(
            select(PDFDocumentFile.chunk_index_start, PDFDocumentFile.chunk_index_end).where(
                PDFDocumentFile.document_id == document_id, PDFDocumentFile.id.in_(list(file_ids)),
            )
        ).all()
    return [(start, end) for start, end in rows if start is not None and end is not None]


def _remove_stored_files(paths: Sequence[str]) -> None:
    from src.config import get_pdf_storage_path

    root = Path(get_pdf_storage_path())
    for relative in paths:
        (root / relative).unlink(missing_ok=True)


async def _delete_pieces(document_id: UUID, subject: str, ranges: Sequence[tuple[int, int]]) -> None:
    from src.lib.weaviate_client.chunks import delete_chunk_range

    for start, end in ranges:
        await asyncio.to_thread(delete_chunk_range, str(document_id), subject, start=start, end=end)


async def _undo(document_id: UUID, subject: str, file_ids: list[UUID], paths: list[str]) -> None:
    """Remove what this add wrote: pieces in its ranges, its stored files, its rows."""

    ranges = await asyncio.to_thread(_ranges, document_id, file_ids)
    await _delete_pieces(document_id, subject, ranges)
    await asyncio.to_thread(_remove_stored_files, paths)
    await asyncio.to_thread(_delete_rows, document_id, file_ids)


async def _remove_leftovers(document_id: UUID, subject: str, leftovers: Sequence[_Leftover]) -> None:
    ranges = [
        (item.chunk_index_start, item.chunk_index_end)
        for item in leftovers
        if item.chunk_index_start is not None and item.chunk_index_end is not None
    ]
    await _delete_pieces(document_id, subject, ranges)
    await asyncio.to_thread(_remove_stored_files, [path for item in leftovers for path in item.paths])
    await asyncio.to_thread(_delete_rows, document_id, [item.file_id for item in leftovers])


async def _after_add(document_id: UUID, subject: str, weaviate_client: Any) -> None:
    """The paper's search-index piece count and the document-context cache; never its status."""

    from src.lib.document_cache import invalidate_cache
    from src.lib.document_files.service import ready_piece_total
    from src.lib.pipeline.store import update_document_metadata
    from src.models.sql.database import SessionLocal

    def total() -> int:
        with SessionLocal() as session:
            return ready_piece_total(session, document_id)

    stored = await asyncio.to_thread(total)
    await update_document_metadata(str(document_id), {"stored_chunks": stored, "failed_chunks": 0}, weaviate_client, subject)
    invalidate_cache(subject, str(document_id))


# ---------------------------------------------------------------------------
# The curator's "add files" request: one job, run in the background.
# ---------------------------------------------------------------------------

# Job messages the jobs panel shows. Proposals awaiting Chris's sign-off (P7c1 PR).
_MESSAGE_ADDED = "Added {added} of {chosen} files."
_MESSAGE_NONE_ADDED = "None of the chosen files could be added."
_MESSAGE_FAILED = "Adding files failed."
_MESSAGE_TIMED_OUT = "Adding files took too long and was stopped."


@dataclass(frozen=True, slots=True)
class SupplementAddJobRequest:
    """One add, as the background task runs it. ``user_id`` is the owner's subject."""

    job_id: UUID
    document_id: UUID
    user_id: str
    owner_user_id: int
    context: DocumentSourceRequestContext
    files: tuple[ChosenFile, ...]


class SupplementAddRequestError(ValueError):
    """The add request is refused before any job exists; ``code`` says why (422)."""

    def __init__(self, code: str, *, limit_value: int | None = None):
        super().__init__(code)
        self.code = code
        self.limit_value = limit_value


def start_supplement_add(document_id: UUID, owner_user_id: int, files: Sequence[ChosenFile]):
    """Check the request's shape, lock the paper row and create the add's job, in one transaction.

    Raises ``SupplementAddRequestError`` (422 codes), the ownership ``HTTPException``
    (404 for a missing or frozen benchmark paper, 403 for another curator's) and
    ``ActiveJobExistsError`` (409) when the paper already has an active job.
    """

    from src.lib.openai_agents.config import get_document_max_files_per_paper
    from src.lib.pdf_jobs.service import create_job_in_session
    from src.models.sql.database import SessionLocal
    from src.services.document_access import require_owned_document

    if not files:
        raise SupplementAddRequestError("no_files_chosen")
    ids = [chosen.provider_file_id for chosen in files]
    if len(set(ids)) != len(ids):
        raise SupplementAddRequestError("file_chosen_twice")
    max_files = get_document_max_files_per_paper()
    with SessionLocal() as session:
        paper = require_owned_document(session, document_id, owner_user_id, for_update=True)
        live = sum(1 for row in paper.files if row.status != FILE_STATUS_FAILED)
        if live + len(files) > max_files:
            session.rollback()
            raise SupplementAddRequestError("paper_file_limit", limit_value=max_files)
        return create_job_in_session(session, document_id=document_id, user_id=owner_user_id)


def _supplement_entries(result: AddResult) -> list[dict[str, Any]]:
    """The job's ``metadata.supplements`` entries (interface I1)."""

    return [
        {
            "provider_file_id": item.provider_file_id,
            "display_name": item.display_name,
            "state": "added" if item.outcome == "added" else "left_out",
            "reason_code": item.reason_code.value if item.reason_code else None,
            "observed_at": None,
            "file_id": str(item.file_id) if item.file_id else None,
            "measured_value": item.measured_value,
            "limit_value": item.limit_value,
        }
        for item in result.files
    ]


def _report_nothing_added(result: AddResult, document_id: UUID) -> None:
    """One warning when every chosen file was left out: codes and numbers, never names or text."""

    from src.lib.observability.runtime import report_runtime_exception
    from src.lib.observability.sentry import hash_sentry_identifier

    class SupplementAddNothingAdded(RuntimeError):
        pass

    report_runtime_exception(
        SupplementAddNothingAdded("Every chosen file was left out of the add"),
        component="document_files",
        operation="supplement_add_nothing_added",
        level="warning",
        context={
            "document_id": str(document_id),
            "refusals": [
                {
                    "reason_code": item.reason_code.value if item.reason_code else None,
                    "measured_value": item.measured_value,
                    "limit_value": item.limit_value,
                    "artifact_id_hash": hash_sentry_identifier(item.provider_file_id),
                }
                for item in result.files
            ],
        },
        fingerprint=["supplement_add_nothing_added"],
    )


def _cancel_checkpoint(job_id: UUID) -> Checkpoint:
    from src.lib.exceptions import PDFCancellationError
    from src.lib.pdf_jobs import service as pdf_job_service

    async def checkpoint(stage: str) -> None:
        if await asyncio.to_thread(pdf_job_service.is_cancel_requested, job_id=job_id):
            raise PDFCancellationError("Processing cancelled by user request")
        await asyncio.to_thread(
            pdf_job_service.update_progress, job_id=job_id, stage=stage, status="running",
        )

    return checkpoint


@costed_document_processing
async def run_supplement_add_job(request: SupplementAddJobRequest) -> None:
    """Run one add as its job: completed, failed or cancelled; the paper is never touched."""

    from src.lib.document_sources.file_listing_service import FileListingUnavailable
    from src.lib.document_sources.registry import get_configured_document_source_provider
    from src.lib.exceptions import PDFCancellationError
    from src.lib.openai_agents.config import get_document_source_import_timeout_seconds
    from src.lib.pdf_jobs import service as pdf_job_service
    from src.lib.weaviate_client.connection import get_connection

    job_id = request.job_id
    provider = get_configured_document_source_provider()
    try:
        result = await asyncio.wait_for(
            add_files_to_paper(
                request.document_id, request.owner_user_id, request.context, request.files,
                job_id=job_id, provider=provider, weaviate_client=get_connection(),
                stage_checkpoint=_cancel_checkpoint(job_id),
            ),
            timeout=get_document_source_import_timeout_seconds(),
        )
    except PDFCancellationError:
        pdf_job_service.mark_cancelled(job_id=job_id)
        return
    except TimeoutError:
        pdf_job_service.mark_failed(
            job_id=job_id, message=_MESSAGE_TIMED_OUT, stage="timed_out", metadata={"error_code": "add_timed_out"},
        )
        return
    except (SupplementAddError, FileListingUnavailable) as stopped:
        pdf_job_service.mark_failed(
            job_id=job_id, message=_MESSAGE_FAILED, stage="failed", metadata={"error_code": stopped.code},
        )
        return
    except Exception:
        # Reported once, by the background task's own failure reporting.
        pdf_job_service.mark_failed(
            job_id=job_id, message=_MESSAGE_FAILED, stage="failed", metadata={"error_code": "add_failed"},
        )
        raise
    finally:
        await provider.aclose()

    metadata = {"supplements": _supplement_entries(result)}
    if not result.added:
        _report_nothing_added(result, request.document_id)
        pdf_job_service.mark_failed(job_id=job_id, message=_MESSAGE_NONE_ADDED, stage="failed", metadata=metadata)
        return
    pdf_job_service.mark_completed(
        job_id=job_id,
        message=_MESSAGE_ADDED.format(added=len(result.added), chosen=len(result.files)),
        metadata=metadata,
    )

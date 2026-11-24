"""Add converted supplements to an existing paper (KANBAN-1976 / ALL-1460, P7c1, P7c2).

The write path every supplement goes through: a curator's "add files" (the
``POST /documents/{id}/files`` route) or an import's supplement stage (P7b, in its
own job) calls ``add_files_to_paper``. The paper stays usable throughout.

- No conversion request is ever sent: an add takes only files the provider has
  already converted.
- **Check phase, nothing written.** The paper's files are listed again with the
  curator's login. Each chosen file, in listing order, is checked; a file that
  fails a step is left out with a ``FileReason`` code and the next file goes on.
- **Write phase.** Every accepted file gets its row (``pending``), its stored
  Markdown and processed JSON, its piece range and its pieces; then all of them
  become ``ready`` together. Cancellation waits for an in-flight write to finish.
  Before the ready commit, rollback removes this add's uncommitted rows, files
  and pieces. After the commit the files stay readable. The paper's status and
  completion time never change.
- Rows left ``failed`` by an add that died (the stale sweep marks them) are removed
  with their pieces at the start of the next add on the paper.
- **A PDF supplement** (P7c2) is added as its PDF: its original PDF is downloaded
  with the curator's login (a read of a file the provider lists), checked against
  the listing's MD5 and counted for pages, and stored next to the paper
  (``{owner}/{paper}/supplement_pdf/{file}.pdf``); its pieces still come from the
  provider's converted text, never from the extraction service. Between the checks
  and the write an accepted PDF waits in an unnamed temporary file on the document
  storage volume, never in memory.

Each file's outcome is a code. The four job messages near the end of this module
are proposals awaiting sign-off.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import tempfile
import uuid
from collections.abc import Awaitable, Callable, Coroutine, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, TypeVar
from uuid import UUID

from sqlalchemy import and_, select

from src.lib.document_sources.access import DocumentSourceRequestContext
from src.lib.document_sources.file_assessment import FileProfile, assess_converted_file
from src.lib.document_sources.file_listing import (
    ProviderFileDownloadFailure,
    ListedFile,
    ListedFileStatus,
    current_file_check_limits,
    download_provider_file,
)
from src.lib.document_sources.file_reasons import ADD_REFUSAL_REASONS, FileReason, SupplementState
from src.lib.document_sources.models import DocumentSourceError, DocumentSourceProvider, SourceArtifactFormat
from src.lib.observability.cost_context import costed_document_processing
from src.models.sql.pdf_document import (
    DISPLAY_MODE_LOCAL_PDF,
    DISPLAY_MODE_TEXT,
    FILE_ROLE_SUPPLEMENT,
    FILE_STATUS_FAILED,
    FILE_STATUS_PENDING,
    FILE_STATUS_READY,
    FILE_STATUS_REMOVED,
    PDFDocument,
    PDFDocumentFile,
)

PAPER_STATUS_COMPLETED = "completed"
_T = TypeVar("_T")
logger = logging.getLogger(__name__)


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


@dataclass(slots=True)
class SupplementAddCommit:
    """One add's ready transaction outcome, observable by its owner after cancellation."""

    committed: bool = False


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
    """A supplement row whose range and artifacts belong to cleanup."""

    file_id: UUID
    chunk_index_start: int | None
    chunk_index_end: int | None
    paths: tuple[str, ...]

    @classmethod
    def from_row(cls, row: PDFDocumentFile) -> _Leftover:
        paths = (row.source_markdown_path, row.processed_json_path, row.file_path)
        unique_paths = tuple(dict.fromkeys(path for path in paths if path))
        return cls(row.id, row.chunk_index_start, row.chunk_index_end, unique_paths)


@dataclass(frozen=True, slots=True)
class _SpooledPdf:
    """An accepted supplement PDF waiting on the storage volume; its bytes are not in memory."""

    file: BinaryIO  # an unnamed temporary file: gone when closed or when the process dies
    sha256: str
    size: int
    page_count: int


@dataclass(slots=True)
class _Accepted:
    listed: ListedFile
    display_name: str
    markdown: str
    sha256: str
    prepared: Any  # ingestion.PreparedFile
    file_id: UUID
    pdf: _SpooledPdf | None = None


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
    commit_state: SupplementAddCommit | None = None,
) -> AddResult:
    """Add the chosen converted text supplements to one of the curator's papers.

    Runs inside the job ``job_id`` (the paper's one active job). ``stage_checkpoint``
    raises when the job is cancelled; it is called between files and stages.
    Pass a fresh ``commit_state`` to observe whether this add became ready.
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
    try:
        await _check_and_write(
            ordered, listed_by_id, outcomes, accepted,
            document_id=document_id, owner_user_id=owner_user_id, paper=paper, provider=provider,
            token=token, limits=limits, max_files=max_files, max_text_bytes=max_text_bytes,
            download_cap=download_cap, weaviate_client=weaviate_client, stage_checkpoint=stage_checkpoint,
            commit_state=commit_state if commit_state is not None else SupplementAddCommit(),
        )
    finally:
        for item in accepted:
            if item.pdf is not None:
                item.pdf.file.close()
    return AddResult(files=tuple(outcomes[chosen.provider_file_id] for chosen in ordered))


async def _check_and_write(
    ordered: Sequence[ChosenFile],
    listed_by_id: dict[str, ListedFile],
    outcomes: dict[str, FileOutcome],
    accepted: list[_Accepted],
    *,
    document_id: UUID,
    owner_user_id: int,
    paper: _PaperState,
    provider: DocumentSourceProvider,
    token: str,
    limits: Any,
    max_files: int,
    max_text_bytes: int,
    download_cap: int,
    weaviate_client: Any,
    stage_checkpoint: Checkpoint,
    commit_state: SupplementAddCommit,
) -> None:
    from src.lib.openai_agents.config import get_document_file_max_pdf_bytes

    pdf_cap = get_document_file_max_pdf_bytes()
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
        pdf = None
        if listed.source_artifact.artifact_format is SourceArtifactFormat.PDF:
            # A PDF supplement is added with its PDF, never as text alone (N3).
            spooled = await _download_pdf(provider, listed, token=token, cap=pdf_cap)
            if isinstance(spooled, FileOutcome):
                outcomes[file_id] = left_out(spooled.reason_code, spooled.measured_value, spooled.limit_value)
                continue
            pdf = spooled
        text_total += prepared.text_bytes
        file_count += 1
        accepted.append(_Accepted(
            listed=listed, display_name=display_name, markdown=markdown, sha256=sha256, prepared=prepared,
            file_id=uuid.uuid4(), pdf=pdf,
        ))

    if accepted:
        await _write(
            accepted, document_id=document_id, owner_user_id=owner_user_id, paper=paper,
            weaviate_client=weaviate_client, stage_checkpoint=stage_checkpoint,
            commit_state=commit_state,
        )
        for item in accepted:
            outcomes[item.listed.provider_file_id] = FileOutcome(
                item.listed.provider_file_id, "added", file_id=item.file_id, display_name=item.display_name,
            )


def _spool_directory() -> Path:
    """Where accepted supplement PDFs wait: on the document storage volume, not system temp."""

    from src.config import get_pdf_storage_path
    from src.lib.storage_permissions import ensure_writable_directory

    return ensure_writable_directory(Path(get_pdf_storage_path()) / ".add_spool")


def _spool_pdf(content: bytes, *, listed_md5: str | None, spool: Path) -> _SpooledPdf | FileReason:
    """Check one downloaded supplement PDF and move it out of memory (runs in a worker thread).

    Structural checks only: the listing's MD5, then a page count of at least one.
    """

    from src.lib.pipeline.upload import pdf_page_count

    if hashlib.md5(content).hexdigest() != listed_md5:
        return FileReason.FILE_CHANGED_SINCE_LISTED
    try:
        page_count = pdf_page_count(io.BytesIO(content))
    except Exception:  # noqa: BLE001 - untrusted bytes: any parser error means "not a readable PDF"
        return FileReason.PDF_INVALID
    if page_count < 1:
        return FileReason.PDF_INVALID
    spooled = tempfile.TemporaryFile(dir=spool)
    spooled.write(content)
    spooled.flush()
    return _SpooledPdf(
        file=spooled, sha256=hashlib.sha256(content).hexdigest(), size=len(content), page_count=page_count,
    )


async def _download_pdf(
    provider: DocumentSourceProvider, listed: ListedFile, *, token: str, cap: int,
) -> _SpooledPdf | FileOutcome:
    """Download a supplement's original PDF with the curator's login, within the shared limit.

    A read of a file the provider lists; it never asks the provider to convert anything.
    """

    spool = await asyncio.to_thread(_spool_directory)
    left_out = lambda reason, limit=None: FileOutcome(  # noqa: E731
        listed.provider_file_id, "left_out", reason, limit_value=limit,
    )
    if not listed.md5:
        # Without the provider's MD5 the bytes cannot be matched to what was listed.
        return left_out(FileReason.FILE_NOT_TICKABLE)
    try:
        outcome = await download_provider_file(
            provider, listed.provider_file_id, request_bearer_token=token, max_bytes=cap,
            use=lambda content: _spool_pdf(content, listed_md5=listed.md5, spool=spool),
        )
    except DocumentSourceError:
        return left_out(FileReason.SOURCE_DOWNLOAD_FAILED)
    failure_codes = {
        ProviderFileDownloadFailure.TOO_LARGE: FileReason.PDF_TOO_LARGE,
        ProviderFileDownloadFailure.MISSING: FileReason.SOURCE_FILE_MISSING,
        ProviderFileDownloadFailure.UNAVAILABLE: FileReason.SOURCE_UNAVAILABLE,
        ProviderFileDownloadFailure.ACCESS_DENIED: FileReason.PDF_ACCESS_DENIED,
    }
    if outcome.failure is not None:
        return left_out(failure_codes[outcome.failure], outcome.limit_value)
    assert outcome.result is not None
    if isinstance(outcome.result, FileReason):
        return left_out(outcome.result)
    return outcome.result


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
        outcome = await download_provider_file(
            provider, converted.artifact_id, request_bearer_token=token, max_bytes=download_cap, use=use,
        )
    except DocumentSourceError:
        return FileOutcome(listed.provider_file_id, "left_out", FileReason.SOURCE_DOWNLOAD_FAILED)
    failure_codes = {
        ProviderFileDownloadFailure.TOO_LARGE: FileReason.TOO_LARGE,
        ProviderFileDownloadFailure.MISSING: FileReason.SOURCE_FILE_MISSING,
        ProviderFileDownloadFailure.UNAVAILABLE: FileReason.SOURCE_UNAVAILABLE,
        ProviderFileDownloadFailure.ACCESS_DENIED: FileReason.ACCESS_DENIED,
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


async def _drain_after_cancel(
    work: Awaitable[Any], *, operation: str = "supplement_add_cancelled_mutation_failed",
) -> None:
    """Finish outstanding work and report a discarded failure before the caller re-raises cancellation."""
    async def drain() -> None:
        try:
            await work
        except asyncio.CancelledError:
            # A chunk writer can finish its own cancellation and failure reporting.
            pass
        except Exception as exc:
            from src.lib.observability.runtime import report_runtime_exception, sanitized_runtime_error

            try:
                report_runtime_exception(
                    sanitized_runtime_error("Supplement mutation failed during cancellation"),
                    component="document_files", operation=operation,
                    context={"exception_type": type(exc).__name__}, fingerprint=[operation], include_request=False,
                )
            except Exception:
                logger.warning("Supplement failure reporting unavailable", extra={"sentry_skip_event": True})

    finalizer = asyncio.create_task(drain())
    while not finalizer.done():
        try:
            await asyncio.shield(finalizer)
        except asyncio.CancelledError:
            continue
    finalizer.result()


async def _finish_mutation(mutation: Coroutine[Any, Any, _T]) -> _T:
    """Keep ownership of an executor-backed mutation until rollback can safely run."""
    task = asyncio.create_task(mutation)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await _drain_after_cancel(task)
        raise


async def _store_supplement_pieces(item: _Accepted, document_id: UUID, subject: str, weaviate_client: Any) -> None:
    from src.lib.document_sources.ingestion import store_file_pieces

    # The reservation thread cannot be interrupted. At the vector storage checkpoint
    # the pipeline takes ownership and must receive cancellation to drain its chunk writer.
    storing = False
    cancelled = False

    async def range_reserved(_stage: str) -> None:
        nonlocal storing
        if cancelled:
            raise asyncio.CancelledError
        storing = True

    task = asyncio.create_task(store_file_pieces(
        item.prepared, document_id=str(document_id), file_id=item.file_id, user_id=subject,
        weaviate_client=weaviate_client, added_to_finished_paper=True, stage_checkpoint=range_reserved,
    ))
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        cancelled = True
        if storing:
            task.cancel()
        await _drain_after_cancel(task)
        raise


async def _write(
    accepted: list[_Accepted],
    *,
    document_id: UUID,
    owner_user_id: int,
    paper: _PaperState,
    weaviate_client: Any,
    stage_checkpoint: Checkpoint,
    commit_state: SupplementAddCommit,
) -> None:
    from src.lib.document_files.service import mark_files_ready
    from src.lib.document_sources.ingestion import _save_processed_json, _save_source_markdown

    subject = paper.owner_subject

    async def commit() -> None:
        sizes = {item.file_id: item.prepared.text_bytes for item in accepted}
        await asyncio.to_thread(mark_files_ready, sizes)
        commit_state.committed = True
        try:
            await _after_add(document_id, subject, weaviate_client)
        except Exception as exc:
            # The files are readable. Only the paper's index counts or cache may be stale.
            _report_after_add_failure(exc, document_id)

    # The rows are written first, naming their files, so a process that dies before the
    # files are written still leaves rows the stale sweep and the next add can clean up.
    planned = [
        (item, f"{subject}/source_markdown/{item.file_id}.md", f"{subject}/processed_json/{item.file_id}.json")
        for item in accepted
    ]
    pdf_paths = {
        item.file_id: f"{subject}/{document_id}/supplement_pdf/{item.file_id}.pdf"
        for item in accepted if item.pdf is not None
    }
    planned_paths = [
        path for _, markdown_path, processed_path in planned for path in (markdown_path, processed_path)
    ] + list(pdf_paths.values())
    try:
        await stage_checkpoint("saving_files")
        rows = [
            _new_row(item, document_id=document_id, owner_user_id=owner_user_id, tab_order=tab_order,
                     markdown_path=markdown_path, processed_path=processed_path,
                     pdf_path=pdf_paths.get(item.file_id))
            for tab_order, (item, markdown_path, processed_path) in enumerate(planned, start=paper.next_tab_order)
        ]
        await _finish_mutation(asyncio.to_thread(_insert_rows, document_id, rows))
        for item, markdown_path, processed_path in planned:
            saved_markdown = await _finish_mutation(
                _save_source_markdown(markdown=item.markdown, file_id=item.file_id, user_id=subject)
            )
            saved_json = await _finish_mutation(
                _save_processed_json(elements=item.prepared.elements, file_id=item.file_id, user_id=subject)
            )
            if (saved_markdown, saved_json) != (markdown_path, processed_path):
                raise SupplementAddError("stored_file_path_mismatch")
            if item.pdf is not None:
                await _finish_mutation(asyncio.to_thread(_save_supplement_pdf, item.pdf.file, pdf_paths[item.file_id]))
        for item in accepted:
            await stage_checkpoint("vector_storage")
            await _store_supplement_pieces(item, document_id, subject, weaviate_client)
        await stage_checkpoint("finishing")
        await _finish_mutation(commit())
    except BaseException as exc:
        if not commit_state.committed:
            rollback = _undo(document_id, subject, [item.file_id for item in accepted], planned_paths)
            if isinstance(exc, asyncio.CancelledError):
                await _drain_after_cancel(rollback, operation="supplement_add_cancelled_rollback_failed")
            else:
                await _finish_mutation(rollback)
        raise


def _save_supplement_pdf(spooled: BinaryIO, relative_path: str) -> None:
    """Copy a spooled supplement PDF to its stored path under the owner's folder."""

    import shutil

    from src.config import get_pdf_storage_path
    from src.lib.storage_permissions import ensure_writable_directory

    target = Path(get_pdf_storage_path()) / relative_path
    ensure_writable_directory(target.parent)
    spooled.seek(0)
    with open(target, "wb") as stored:
        shutil.copyfileobj(spooled, stored)


def _report_after_add_failure(exc: Exception, document_id: UUID) -> None:
    from src.lib.observability.runtime import report_runtime_exception

    class SupplementAddFinishFailed(RuntimeError):
        pass

    report_runtime_exception(
        SupplementAddFinishFailed(f"Updating the paper after an add failed ({type(exc).__name__})"),
        component="document_files",
        operation="supplement_add_finish_failed",
        level="warning",
        context={"document_id": str(document_id)},
        fingerprint=["supplement_add_finish_failed"],
    )


def _new_row(
    item: _Accepted,
    *,
    document_id: UUID,
    owner_user_id: int,
    tab_order: int,
    markdown_path: str,
    processed_path: str,
    pdf_path: str | None = None,
) -> PDFDocumentFile:
    from datetime import datetime, timezone

    original = item.listed.source_artifact
    converted = item.listed.converted_artifact
    assert converted is not None
    if item.pdf is not None:
        assert pdf_path is not None
        # A PDF supplement: the viewer shows its stored PDF; its pieces come from the text.
        stored = {
            "display_mode": DISPLAY_MODE_LOCAL_PDF, "file_path": pdf_path,
            "file_hash": _scoped_file_hash(owner_user_id, item.pdf.sha256), "file_size": item.pdf.size,
            "page_count": item.pdf.page_count, "source_provider_pdf_artifact_id": original.artifact_id,
        }
    else:
        stored = {
            "display_mode": DISPLAY_MODE_TEXT, "file_path": markdown_path,
            "file_hash": _scoped_file_hash(owner_user_id, item.sha256),
            "file_size": len(item.markdown.encode("utf-8")), "page_count": None,
        }
    return PDFDocumentFile(
        id=item.file_id,
        document_id=document_id,
        role=FILE_ROLE_SUPPLEMENT,
        display_name=item.display_name,
        status=FILE_STATUS_PENDING,
        tab_order=tab_order,
        **stored,
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
        # Failed leftovers and files being removed never count against the paper's limits.
        live = [row for row in paper.files if row.status not in _NOT_LIVE]
        ready_bytes = 0
        for row in live:
            if row.status == FILE_STATUS_READY:
                if row.text_bytes is None:
                    raise FileRangeMissingError(f"Ready file {row.id} has no measured text size")
                ready_bytes += row.text_bytes
        leftovers = tuple(
            _Leftover.from_row(row)
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


def _rollback_predicate(file_ids: Sequence[UUID]):
    return and_(
        PDFDocumentFile.id.in_(file_ids),
        PDFDocumentFile.role == FILE_ROLE_SUPPLEMENT,
        PDFDocumentFile.status.in_((FILE_STATUS_PENDING, FILE_STATUS_FAILED)),
    )


def _delete_rows(document_id: UUID, file_ids: Sequence[UUID]) -> None:
    from sqlalchemy import delete

    from src.models.sql.database import SessionLocal

    with SessionLocal() as session, session.begin():
        session.execute(delete(PDFDocumentFile).where(
            PDFDocumentFile.document_id == document_id, _rollback_predicate(file_ids),
        ))


def _rollback_files(document_id: UUID, file_ids: Sequence[UUID]) -> list[_Leftover] | None:
    """Read both sides of a possible paper deletion in one snapshot. None means the parent is gone."""
    from src.models.sql.database import SessionLocal

    with SessionLocal() as session:
        rows = session.execute(
            select(PDFDocument.id, PDFDocumentFile)
            .outerjoin(PDFDocument.files.and_(_rollback_predicate(file_ids)))
            .where(PDFDocument.id == document_id)
        ).all()
        if not rows:
            return None
        return [_Leftover.from_row(row) for _, row in rows if row is not None]


def _remove_stored_files(paths: Sequence[str], subject: str) -> None:
    from src.lib.document_files.service import remove_stored_files

    remove_stored_files(paths, owner_subject=subject)


async def _delete_pieces(document_id: UUID, subject: str, ranges: Sequence[tuple[int, int]]) -> None:
    from src.lib.weaviate_client.chunks import delete_chunk_range

    for start, end in ranges:
        await asyncio.to_thread(delete_chunk_range, str(document_id), subject, start=start, end=end)


async def _undo(document_id: UUID, subject: str, file_ids: list[UUID], planned_paths: list[str]) -> None:
    """Remove what this add wrote: pieces in its ranges, its stored files, its rows.

    When the paper itself was deleted while the add ran, its rows (and so the ranges)
    are gone with it: every piece still filed under the paper's id is deleted instead,
    since no other file can own them.
    """

    leftovers = await asyncio.to_thread(_rollback_files, document_id, file_ids)
    if leftovers is not None:
        await _remove_leftovers(document_id, subject, leftovers)
        return
    await _delete_pieces_of_deleted_paper(document_id, subject)
    await asyncio.to_thread(_remove_stored_files, planned_paths, subject)


async def _delete_pieces_of_deleted_paper(document_id: UUID, subject: str) -> None:
    from src.lib.weaviate_client.chunks import delete_pieces_of_deleted_paper

    await asyncio.to_thread(delete_pieces_of_deleted_paper, str(document_id), subject)


async def _remove_leftovers(document_id: UUID, subject: str, leftovers: Sequence[_Leftover]) -> None:
    ranges = [
        (item.chunk_index_start, item.chunk_index_end)
        for item in leftovers
        if item.chunk_index_start is not None and item.chunk_index_end is not None
    ]
    await _delete_pieces(document_id, subject, ranges)
    await asyncio.to_thread(
        _remove_stored_files, [path for item in leftovers for path in item.paths], subject,
    )
    await asyncio.to_thread(_delete_rows, document_id, [item.file_id for item in leftovers])


async def _after_add(document_id: UUID, subject: str, weaviate_client: Any) -> None:
    """The paper's piece count on its search-index object, and the document cache.

    Only the two counts are written (the Library's chunk column and vector-count filter
    read them); the object's processing and embedding status are never touched by an add.
    """

    from src.lib.document_cache import invalidate_cache
    from src.lib.document_files.service import ready_piece_total
    from src.lib.pipeline.store import update_document_piece_count
    from src.models.sql.database import SessionLocal

    def total() -> int:
        with SessionLocal() as session:
            return ready_piece_total(session, document_id)

    stored = await asyncio.to_thread(total)
    await update_document_piece_count(str(document_id), stored, weaviate_client, subject)
    invalidate_cache(subject, str(document_id))


# ---------------------------------------------------------------------------
# The curator's "add files" request: one job, run in the background.
# ---------------------------------------------------------------------------

# Job messages the jobs panel shows. Proposals awaiting Chris's sign-off (P7c1 PR).
_NOT_LIVE = (FILE_STATUS_FAILED, FILE_STATUS_REMOVED)
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


class FileRemovalUnfinished(RuntimeError):
    """A chosen file's earlier removal has not finished; the add is refused (409)."""

    code = "file_removal_unfinished"


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

    from src.lib.document_files.paper_lock import try_lock_paper_for_add
    from src.lib.openai_agents.config import get_document_max_files_per_paper
    from src.lib.pdf_jobs.service import (
        JOB_KIND_KEY,
        SUPPLEMENT_ADD_JOB_KIND,
        ActiveJobExistsError,
        create_job_in_session,
    )
    from src.models.sql.database import SessionLocal
    from src.services.document_access import require_owned_document

    if not files:
        raise SupplementAddRequestError("no_files_chosen")
    ids = [chosen.provider_file_id for chosen in files]
    if len(set(ids)) != len(ids):
        raise SupplementAddRequestError("file_chosen_twice")
    max_files = get_document_max_files_per_paper()
    with SessionLocal() as session:
        # Ordered against a delete of the same paper (paper_lock.py), without waiting: while
        # a delete holds the paper's lock the add is refused as another operation in progress.
        if not try_lock_paper_for_add(session, document_id):
            session.rollback()
            raise ActiveJobExistsError(document_id)
        paper = require_owned_document(session, document_id, owner_user_id, for_update=True)
        live = sum(1 for row in paper.files if row.status not in _NOT_LIVE)
        if live + len(files) > max_files:
            session.rollback()
            raise SupplementAddRequestError("paper_file_limit", limit_value=max_files)
        # A removal of the same provider file has not finished clearing its row; adding the
        # file again succeeds once that row is gone (removing it again finishes the removal).
        if any(
            row.status == FILE_STATUS_REMOVED and row.source_provider_source_file_id in ids
            for row in paper.files
        ):
            session.rollback()
            raise FileRemovalUnfinished(document_id)
        # The paper's name, never its id, names the job; its kind keeps it out of the paper's status.
        return create_job_in_session(
            session, document_id=document_id, user_id=owner_user_id, filename=paper.filename,
            metadata={JOB_KIND_KEY: SUPPLEMENT_ADD_JOB_KIND},
        )


def _supplement_entries(result: AddResult) -> list[dict[str, Any]]:
    """The job's ``metadata.supplements`` entries (interface I1)."""

    return [
        {
            "provider_file_id": item.provider_file_id,
            "display_name": item.display_name,
            "state": (SupplementState.ADDED if item.outcome == "added" else SupplementState.LEFT_OUT).value,
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
        # Choosing files that are all in the paper already (a repeated click) is not worth a warning.
        if any(item.reason_code is not FileReason.ALREADY_IN_PAPER for item in result.files):
            _report_nothing_added(result, request.document_id)
        pdf_job_service.mark_failed(job_id=job_id, message=_MESSAGE_NONE_ADDED, stage="failed", metadata=metadata)
        return
    pdf_job_service.mark_completed(
        job_id=job_id,
        message=_MESSAGE_ADDED.format(added=len(result.added), chosen=len(result.files)),
        metadata=metadata,
    )

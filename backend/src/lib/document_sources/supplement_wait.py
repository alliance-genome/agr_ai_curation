"""Following a provider's conversion of the supplements a curator chose.

``classify_conversion_answer`` says, from one conversion answer and a fresh file
listing, where each chosen supplement stands. It reads only structured fields:
the listing's converted texts, the provider's per-file statuses (by provider
file id) and the answer's overall status. The provider's free-text errors are
never read, so a cause the provider states only in words (its group rule, its
size limits) is not known here.

``run_supplement_stage`` is the supplement stage of an import (KANBAN-1976 /
ALL-1460, P7b): after the main paper is ready, it waits for the provider to
convert the chosen supplements, then adds the converted ones in one write. The
wait only reads the provider's file listing: it never sends the conversion
request (the import's main phase sent it once), so it can never start or
restart a provider job. ``start_supplement_stage`` runs it as its own in-process
task, so the next paper of the request starts meanwhile.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import UUID

from src.lib.document_sources.file_listing import FileListing
from src.lib.document_sources.file_reasons import FileReason, SupplementState
from src.lib.document_sources.supplement_plan import SupplementPlan
from src.lib.document_sources.models import (
    DocumentSourceError,
    DocumentSourceLoginExpired,
    DocumentSourceProvider,
    SourceConversionFileStatus,
    SourceConversionResult,
    SourceConversionStatus,
)
from src.lib.document_sources.registry import get_document_source_provider_metadata
from src.lib.exceptions import PDFCancellationError
from src.lib.openai_agents.config import (
    get_document_source_listing_max_failed_reads,
    get_document_source_provider,
    get_document_supplement_add_reserve_seconds,
    get_document_supplement_listing_poll_seconds,
    get_document_supplement_max_concurrent_waits,
    get_document_supplement_wait_timeout_seconds,
)
from src.lib.observability.runtime import report_runtime_exception, sanitized_runtime_error
from src.lib.pdf_jobs import service as pdf_job_service
from src.services.processing_status_policy import TERMINAL_PDF_JOB_STATUSES

logger = logging.getLogger(__name__)

# The listing has converted text for the file: it can be added.
CONVERTED = "converted"
# The provider is still working and has not reported on the file.
WAITING = "waiting"

_FINAL_ANSWERS = frozenset({
    SourceConversionStatus.CONVERTED,
    SourceConversionStatus.FAILED,
    SourceConversionStatus.NO_SOURCES,
})


class UnclassifiableConversionAnswer(ValueError):
    """The answer's overall status is neither running nor final."""


def classify_conversion_answer(
    answer: SourceConversionResult,
    file_statuses: Mapping[str, SourceConversionFileStatus],
    chosen_provider_file_ids: Iterable[str],
    listing: FileListing,
) -> dict[str, str]:
    """Per chosen supplement: ``converted``, ``waiting`` or the reason it is not converted.

    ``file_statuses`` is the provider's ``conversion_file_statuses(answer)``;
    ``listing`` is listed after ``answer`` was received.

    - ``converted``: the listing has converted text for the file.
    - ``provider_conversion_failed``: the provider reported a failure for the file.
    - ``waiting``: the provider is still running.
    - ``provider_not_converted``: the answer is final and the file has neither.
    """

    if answer.status is SourceConversionStatus.RUNNING:
        final = False
    elif answer.status in _FINAL_ANSWERS:
        final = True
    else:
        raise UnclassifiableConversionAnswer(
            f"Conversion answer status {answer.status.value!r} is neither running nor final"
        )
    converted = {
        listed.provider_file_id
        for listed in listing.files
        if listed.converted_text_id is not None
    }
    classified: dict[str, str] = {}
    for provider_file_id in chosen_provider_file_ids:
        if provider_file_id in converted:
            classified[provider_file_id] = CONVERTED
        elif file_statuses.get(provider_file_id) is SourceConversionFileStatus.FAILED:
            classified[provider_file_id] = FileReason.PROVIDER_CONVERSION_FAILED.value
        elif not final:
            classified[provider_file_id] = WAITING
        else:
            classified[provider_file_id] = FileReason.PROVIDER_NOT_CONVERTED.value
    return classified


# --- The supplement stage -----------------------------------------------------

# Curator-facing job message when the stage itself fails unexpectedly; the main
# paper stays usable. Approved wording (Chris, 23 Nov 2026, card 39).
SUPPLEMENT_STAGE_FAILED_MESSAGE = "The main paper is ready, but its supplements could not be added."


class StageOutcomes(Protocol):
    """How the stage ends the import's job (the execution service implements it)."""

    async def completed(self) -> None: ...

    async def cancelled(self) -> None: ...

    async def failed(self, message: str) -> None: ...


class _JobNoLongerActive(Exception):
    """The job was made terminal outside this stage; the stage writes nothing more."""


_FINAL_STATES = frozenset({SupplementState.ADDED, SupplementState.LEFT_OUT})


@dataclass(slots=True)
class _Entry:
    provider_file_id: str
    display_name: str | None
    state: SupplementState
    reason_code: str | None = None
    observed_at: str | None = None
    file_id: str | None = None
    converted_md5: str | None = None
    md5: str | None = None
    measured_value: int | None = None
    limit_value: int | None = None

    def as_metadata(self) -> dict[str, Any]:
        return {
            "provider_file_id": self.provider_file_id,
            "display_name": self.display_name,
            "state": self.state.value,
            "reason_code": self.reason_code,
            "observed_at": self.observed_at,
            "file_id": self.file_id,
            "measured_value": self.measured_value,
            "limit_value": self.limit_value,
        }


def _iso(epoch_seconds: float) -> str:
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).isoformat()


def _clean_display_name(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def queued_entries(plan: SupplementPlan) -> list[dict[str, Any]]:
    """Initial per-file entries recorded when an import with supplements is accepted."""

    return [
        _Entry(
            provider_file_id=chosen.provider_file_id,
            display_name=_clean_display_name(chosen.display_name),
            state=SupplementState.QUEUED,
            reason_code=None,
            observed_at=None,
        ).as_metadata()
        for chosen in plan.chosen
    ]


def not_started_entries(plan: SupplementPlan, *, now: float | None = None) -> list[dict[str, Any]]:
    """Per-file outcomes when no supplement stage could start (the paper was busy or gone)."""

    observed = _iso(time.time() if now is None else now)
    return [
        _Entry(
            provider_file_id=chosen.provider_file_id,
            display_name=_clean_display_name(chosen.display_name),
            state=SupplementState.LEFT_OUT,
            reason_code=FileReason.PAPER_BUSY_WHEN_READY.value,
            observed_at=observed,
        ).as_metadata()
        for chosen in plan.chosen
    ]


class _Stage:
    """One import's supplement stage: its entries, its job writes and its limits."""

    def __init__(self, *, job_id: str, plan: SupplementPlan, main_ready_at: float,
                 clock: Callable[[], float]):
        self.job_id = job_id
        self.plan = plan
        self.clock = clock
        self.entries: dict[str, _Entry] = {}
        self.login_cutoff = (
            plan.login_expires_at - get_document_supplement_add_reserve_seconds()
            if plan.login_expires_at is not None
            else None
        )
        self.wait_ends_at = main_ready_at + get_document_supplement_wait_timeout_seconds()
        if self.login_cutoff is not None:
            self.wait_ends_at = min(self.wait_ends_at, self.login_cutoff)

    def login_ended(self) -> bool:
        return self.login_cutoff is not None and self.clock() >= self.login_cutoff

    def login_gone(self) -> bool:
        """The login itself has expired: nothing may be asked of the provider with it."""
        return self.plan.login_expires_at is not None and self.clock() >= self.plan.login_expires_at

    async def checkpoint(self, stage: str | None = None) -> None:
        job = await asyncio.to_thread(
            pdf_job_service.get_job_by_id, job_id=self.job_id, reconcile_stale=False
        )
        if job is None or job.status in TERMINAL_PDF_JOB_STATUSES:
            raise _JobNoLongerActive()
        if job.cancel_requested:
            raise PDFCancellationError("Processing cancelled by user request")

    async def ended_elsewhere(self) -> bool:
        """Whether the job was made terminal outside this stage (a delete, a dead-wait cancel)."""
        job = await asyncio.to_thread(
            pdf_job_service.get_job_by_id, job_id=self.job_id, reconcile_stale=False
        )
        return job is None or job.status in TERMINAL_PDF_JOB_STATUSES

    async def record(self, stage: str, message: str) -> None:
        await asyncio.to_thread(
            pdf_job_service.update_progress,
            job_id=self.job_id,
            stage=stage,
            message=message,
            status="running",
            metadata={
                "supplements": [entry.as_metadata() for entry in self.entries.values()],
                "supplement_wait_ends_at": _iso(self.wait_ends_at),
            },
        )

    def leave_out(self, provider_file_id: str, reason: FileReason | str, *,
                  measured_value: int | None = None, limit_value: int | None = None) -> None:
        entry = self.entries[provider_file_id]
        entry.state = SupplementState.LEFT_OUT
        entry.reason_code = reason.value if isinstance(reason, FileReason) else reason
        entry.observed_at = _iso(self.clock())
        entry.measured_value = measured_value
        entry.limit_value = limit_value

    def ids_in(self, *states: SupplementState) -> list[str]:
        return [key for key, entry in self.entries.items() if entry.state in states]

    def end_unfinished(self, reason: FileReason) -> None:
        for provider_file_id in [
            key for key, entry in self.entries.items() if entry.state not in _FINAL_STATES
        ]:
            self.leave_out(provider_file_id, reason)

    def apply(self, classified: Mapping[str, str], listing: FileListing) -> None:
        listed = {item.provider_file_id: item for item in listing.files}
        for provider_file_id, outcome in classified.items():
            if outcome == CONVERTED:
                entry = self.entries[provider_file_id]
                entry.state = SupplementState.QUEUED
                entry.converted_md5 = listed[provider_file_id].converted_md5
            elif outcome != WAITING:
                self.leave_out(provider_file_id, outcome)


async def run_supplement_stage(
    *,
    job_id: str,
    document_id: str,
    owner_user_id: int,
    reference: str,
    plan: SupplementPlan,
    conversion_requested: bool,
    last_answer: SourceConversionResult | None,
    main_ready_at: float,
    provider: DocumentSourceProvider,
    weaviate_client: Any,
    outcomes: StageOutcomes,
    list_files: Callable[..., Awaitable[FileListing]],
    add_files: Callable[..., Awaitable[Any]],
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Wait for the provider to convert the chosen supplements, then add the converted ones.

    Runs under its own job (the paper is already ``completed``; its status never
    depends on this). It never sends the provider's conversion request (Chris,
    23 Nov 2026): if the main phase's last answer was already final, it classifies
    the chosen files once from it; otherwise it reads the provider's read-only
    file listing every ``DOCUMENT_SUPPLEMENT_LISTING_POLL_SECONDS`` and ends at the
    first of: every chosen file converted; the wait limit (from
    ``main_ready_at``); the login's expiry minus the add reserve; the first
    provider error; a cancel; or the job made terminal elsewhere (then nothing
    more is written). A file the provider fails during the wait ends
    ``not_converted_in_time``. Every entry ends ``added`` or ``left_out`` with a
    reason.
    """

    stage = _Stage(job_id=job_id, plan=plan, main_ready_at=main_ready_at, clock=clock)
    for chosen in plan.chosen:
        stage.entries[chosen.provider_file_id] = _Entry(
            provider_file_id=chosen.provider_file_id,
            display_name=_clean_display_name(chosen.display_name),
            state=(
                SupplementState.QUEUED
                if chosen.converted_md5 is not None
                else SupplementState.WAITING
            ),
            converted_md5=chosen.converted_md5,
            md5=chosen.md5,
        )

    try:
        await stage.checkpoint()
        time_used_up = clock() >= stage.wait_ends_at
        if time_used_up and stage.login_ended():
            # Inside the login's reserve, or past it: ask the provider nothing.
            stage.end_unfinished(FileReason.LOGIN_EXPIRED)
            await stage.record(pdf_job_service.WAITING_FOR_SUPPLEMENTS_STAGE, "Supplements finished")
            await outcomes.completed()
            return

        add_allowed = True
        try:
            listing = await list_files(
                UUID(document_id), owner_user_id=owner_user_id, provider=provider,
                context=plan.request_context,
            )
        except DocumentSourceLoginExpired:
            listing = None
            stage.end_unfinished(FileReason.LOGIN_EXPIRED)
            add_allowed = False
        except DocumentSourceError:
            listing = None
        if listing is not None:
            names = {
                item.provider_file_id: _clean_display_name(item.display_name)
                for item in listing.files
            }
            for entry in stage.entries.values():
                entry.display_name = names.get(entry.provider_file_id) or entry.display_name
        if stage.ids_in(SupplementState.WAITING):
            await stage.record(
                pdf_job_service.WAITING_FOR_SUPPLEMENTS_STAGE,
                _waiting_message(stage, provider_id=getattr(provider, "provider_id", None)),
            )

        if time_used_up:
            # The wait time was used up (for example while queued) but the login is
            # valid: no conversion request and no polling; files the provider has
            # already converted (by the one read-only listing above) are added, the
            # rest end ``wait_limit_reached``.
            if listing is not None:
                stage.apply(_converted_in(listing, stage.ids_in(SupplementState.WAITING)), listing)
            elif add_allowed:
                for provider_file_id in stage.ids_in(SupplementState.QUEUED):
                    stage.leave_out(provider_file_id, FileReason.SOURCE_UNAVAILABLE)
            for provider_file_id in stage.ids_in(SupplementState.WAITING):
                stage.leave_out(provider_file_id, FileReason.WAIT_LIMIT_REACHED)
        elif stage.ids_in(SupplementState.WAITING):
            if not conversion_requested or last_answer is None or listing is None:
                # Nothing asked the provider to convert them in this import, or the
                # paper's files cannot be listed: they are not converted.
                for provider_file_id in stage.ids_in(SupplementState.WAITING):
                    stage.leave_out(
                        provider_file_id,
                        FileReason.NOT_CONVERTED_IN_TIME
                        if listing is None
                        else FileReason.PROVIDER_NOT_CONVERTED,
                    )
            elif last_answer.status is not SourceConversionStatus.RUNNING:
                # The main phase already had the provider's final answer:
                # classify once and never ask again.
                try:
                    classified = classify_conversion_answer(
                        last_answer, provider.conversion_file_statuses(last_answer),
                        stage.ids_in(SupplementState.WAITING), listing,
                    )
                except (DocumentSourceError, UnclassifiableConversionAnswer):
                    for provider_file_id in stage.ids_in(SupplementState.WAITING):
                        stage.leave_out(provider_file_id, FileReason.PROVIDER_ANSWER_UNKNOWN)
                else:
                    stage.apply(classified, listing)
            else:
                add_allowed = await _poll_until_done(
                    stage=stage, provider=provider, reference=reference,
                    document_id=document_id, owner_user_id=owner_user_id,
                    listing=listing, sleep=sleep,
                    list_files=list_files,
                )

        to_add = [stage.entries[key] for key in stage.ids_in(SupplementState.QUEUED)]
        if to_add and (not add_allowed or stage.login_gone()):
            stage.end_unfinished(FileReason.LOGIN_EXPIRED)
            to_add = []
        if to_add:
            await stage.checkpoint()
            for entry in to_add:
                entry.state = SupplementState.ADDING
            await stage.record(pdf_job_service.ADDING_SUPPLEMENTS_STAGE, "Adding supplements")
            from src.lib.document_sources.supplement_add import ChosenFile

            try:
                result = await add_files(
                    UUID(document_id),
                    owner_user_id,
                    plan.request_context,
                    [
                        ChosenFile(
                            provider_file_id=entry.provider_file_id,
                            md5=entry.md5,
                            converted_md5=entry.converted_md5,
                        )
                        for entry in to_add
                    ],
                    job_id=UUID(job_id),
                    provider=provider,
                    weaviate_client=weaviate_client,
                    stage_checkpoint=stage.checkpoint,
                )
            except DocumentSourceLoginExpired:
                stage.end_unfinished(FileReason.LOGIN_EXPIRED)
            except DocumentSourceError:
                # The provider failed during the add (its listing or downloads):
                # the add wrote nothing; the files are left out.
                stage.end_unfinished(FileReason.SOURCE_UNAVAILABLE)
            else:
                for outcome in result.files:
                    entry = stage.entries[outcome.provider_file_id]
                    cleaned_name = _clean_display_name(outcome.display_name)
                    if cleaned_name is not None:
                        entry.display_name = cleaned_name
                    if outcome.outcome == "added":
                        entry.state = SupplementState.ADDED
                        entry.file_id = str(outcome.file_id)
                    else:
                        stage.leave_out(
                            outcome.provider_file_id, outcome.reason_code,
                            measured_value=outcome.measured_value,
                            limit_value=outcome.limit_value,
                        )
        await stage.checkpoint()
        await stage.record(pdf_job_service.ADDING_SUPPLEMENTS_STAGE, "Supplements finished")
        await outcomes.completed()
    except _JobNoLongerActive:
        logger.info("Supplement stage of job %s ended: the job was finalized elsewhere", job_id)
    except PDFCancellationError:
        stage.end_unfinished(FileReason.IMPORT_CANCELLED)
        await stage.record(pdf_job_service.WAITING_FOR_SUPPLEMENTS_STAGE, "Cancelled")
        await outcomes.cancelled()
    except Exception:
        if await stage.ended_elsewhere():
            # The curator ended it meanwhile (deleting the paper, which can make a
            # listing fail): not a failure of the stage, and no report.
            logger.info("Supplement stage of job %s ended: the job was finalized elsewhere", job_id)
            return
        # The stage's own failure: every file still open is left out by name, then
        # the task's guard ends the job and reports it.
        stage.end_unfinished(FileReason.SUPPLEMENT_STAGE_FAILED)
        await stage.record(pdf_job_service.WAITING_FOR_SUPPLEMENTS_STAGE, "Supplements failed")
        raise


async def _poll_until_done(
    *,
    stage: _Stage,
    provider: DocumentSourceProvider,
    reference: str,
    document_id: str,
    owner_user_id: int,
    listing: FileListing,
    sleep: Callable[[float], Awaitable[None]],
    list_files: Callable[..., Awaitable[FileListing]],
) -> bool:
    """Watch the provider's read-only file listing until every chosen file is converted.

    Never sends the conversion request. Each poll is one listing read; a chosen
    file counts as converted once a listed converted supplement text names it
    (structural, as the file listing decides it). Only when a new such text
    appears does it run the full per-paper listing, for that text's id and
    checks. A file the provider failed stays waiting and ends
    ``not_converted_in_time`` when the wait ends. A failed read is tried again at
    the next interval, up to ``DOCUMENT_SOURCE_LISTING_MAX_FAILED_READS`` in a row
    (a good read resets the count); a refused login ends the wait at once.

    Returns whether the converted files may still be added (False once the
    curator's login was refused).
    """

    poll_seconds = get_document_supplement_listing_poll_seconds()
    max_failed_reads = get_document_source_listing_max_failed_reads()
    failed_reads = 0
    token = stage.plan.request_context.curator_token
    stage.apply(_converted_in(listing, stage.ids_in(SupplementState.WAITING)), listing)
    seen_texts: set[str] = set()
    while stage.ids_in(SupplementState.WAITING):
        await stage.record(
            pdf_job_service.WAITING_FOR_SUPPLEMENTS_STAGE,
            _waiting_message(stage, provider_id=getattr(provider, "provider_id", None)),
        )
        remaining = stage.wait_ends_at - stage.clock()
        if remaining <= 0:
            break
        await sleep(min(poll_seconds, remaining))
        await stage.checkpoint()
        if stage.clock() >= stage.wait_ends_at:
            break
        try:
            artifacts = await provider.list_artifacts(reference, request_bearer_token=token)
        except DocumentSourceLoginExpired:
            for provider_file_id in stage.ids_in(SupplementState.WAITING):
                stage.leave_out(provider_file_id, FileReason.LOGIN_EXPIRED)
            return False
        except DocumentSourceError:
            # A pure read: tried again at the next interval, up to the tolerated
            # number in a row; then the wait ends and converted files still join.
            failed_reads += 1
            if failed_reads >= max_failed_reads:
                break
            continue
        failed_reads = 0
        waiting = set(stage.ids_in(SupplementState.WAITING))
        new_texts = {
            artifact.artifact_id
            for artifact in artifacts
            if provider.is_listed_converted_supplement_text(artifact)
            and artifact.parent_artifact_id in waiting
            and artifact.artifact_id not in seen_texts
        }
        if not new_texts:
            continue
        try:
            listing = await list_files(
                UUID(document_id), owner_user_id=owner_user_id, provider=provider,
                context=stage.plan.request_context,
            )
        except DocumentSourceLoginExpired:
            for provider_file_id in stage.ids_in(SupplementState.WAITING):
                stage.leave_out(provider_file_id, FileReason.LOGIN_EXPIRED)
            return False
        except DocumentSourceError:
            # Counted as a failed read; the new texts are looked at again next time.
            failed_reads += 1
            if failed_reads >= max_failed_reads:
                break
            continue
        seen_texts |= new_texts
        stage.apply(_converted_in(listing, stage.ids_in(SupplementState.WAITING)), listing)
    for provider_file_id in stage.ids_in(SupplementState.WAITING):
        stage.leave_out(provider_file_id, FileReason.NOT_CONVERTED_IN_TIME)
    return True


def _converted_in(listing: FileListing, provider_file_ids: Iterable[str]) -> dict[str, str]:
    """The given files the listing shows with readable converted text."""

    converted = {item.provider_file_id for item in listing.files if item.converted_text_id is not None}
    return {key: CONVERTED for key in provider_file_ids if key in converted}


def _provider_short_label(provider_id: str | None = None) -> str:
    """Resolve the provider's short label from canonical registration metadata."""
    resolved_id = (provider_id or get_document_source_provider() or "").strip()
    if resolved_id:
        metadata = get_document_source_provider_metadata(resolved_id)
        if isinstance(metadata, Mapping):
            for key in ("short_label", "display_label"):
                raw = metadata.get(key)
                if isinstance(raw, str) and raw.strip():
                    return raw.strip()
    return "the document source"


def _waiting_message(stage: _Stage, *, provider_id: str | None = None) -> str:
    # Approved wording (Chris, 23 Nov 2026, card 39; the singular for one file too).
    count = len(stage.ids_in(SupplementState.WAITING))
    noun = "supplement" if count == 1 else "supplements"
    provider_label = _provider_short_label(provider_id)
    return (
        f"Main paper ready. Waiting for {provider_label} to convert {count} {noun}; "
        "you can use the paper and leave this page."
    )


# --- Running the stage as its own task ------------------------------------------

_stage_tasks: set[asyncio.Task] = set()
# One semaphore per event loop (asyncio primitives belong to one loop).
_stage_slots: dict[asyncio.AbstractEventLoop, tuple[int, asyncio.Semaphore]] = {}


def _slots() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    size = get_document_supplement_max_concurrent_waits()
    current = _stage_slots.get(loop)
    if current is None or current[0] != size:
        current = (size, asyncio.Semaphore(size))
        _stage_slots.clear()
        _stage_slots[loop] = current
    return current[1]


def start_supplement_stage(
    *,
    job_id: str,
    run: Callable[[], Awaitable[None]],
    outcomes: StageOutcomes,
) -> asyncio.Task:
    """Run one import's supplement stage as its own task, kept referenced until it ends.

    At most ``DOCUMENT_SUPPLEMENT_MAX_CONCURRENT_WAITS`` stages run at once in
    this process; the rest queue, recording activity at the poll interval so
    the dead-wait rule and the stale sweep do not take them for dead. A queued
    stage's wait limit still counts from its own main paper's ready time. Created
    inside the import's task, it inherits that task's cost and trace context.
    """

    task = asyncio.create_task(
        _run_guarded(job_id=job_id, run=run, outcomes=outcomes),
        name=f"supplement-stage-{job_id}",
    )
    _stage_tasks.add(task)
    task.add_done_callback(_stage_tasks.discard)
    return task


async def _run_guarded(
    *, job_id: str, run: Callable[[], Awaitable[None]], outcomes: StageOutcomes,
) -> None:
    slots = _slots()
    try:
        await _wait_for_slot(slots, job_id=job_id, outcomes=outcomes)
    except _JobNoLongerActive:
        return
    except PDFCancellationError:
        await outcomes.cancelled()
        return
    try:
        await run()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        # A failure of the stage itself ends the job by name; never a silent task death.
        logger.exception(
            "Supplement stage of job %s failed", job_id, extra={"sentry_skip_event": True},
        )
        report_runtime_exception(
            sanitized_runtime_error("Supplement stage failed"),
            component="document_sources",
            operation="supplement_stage",
            tags={"failure_type": type(exc).__name__},
        )
        await outcomes.failed(SUPPLEMENT_STAGE_FAILED_MESSAGE)
    finally:
        slots.release()


async def _wait_for_slot(
    slots: asyncio.Semaphore, *, job_id: str, outcomes: StageOutcomes,
) -> None:
    """Wait for a running slot, in order, recording activity at the poll interval meanwhile.

    One ``acquire`` per waiting stage stays in the semaphore's queue for the
    whole wait, so stages are served in the order they arrived.
    """
    poll_seconds = get_document_supplement_listing_poll_seconds()
    acquiring = asyncio.ensure_future(slots.acquire())
    try:
        while True:
            done, _pending = await asyncio.wait({acquiring}, timeout=poll_seconds)
            if done:
                acquiring.result()
                return
            job = await asyncio.to_thread(
                pdf_job_service.get_job_by_id, job_id=job_id, reconcile_stale=False
            )
            if job is None or job.status in TERMINAL_PDF_JOB_STATUSES:
                raise _JobNoLongerActive()
            if job.cancel_requested:
                raise PDFCancellationError("Processing cancelled by user request")
            await asyncio.to_thread(
                pdf_job_service.update_progress,
                job_id=job_id,
                stage=pdf_job_service.WAITING_FOR_SUPPLEMENTS_STAGE,
                message=f"Waiting for {_provider_short_label()}",
                status="running",
            )
    except BaseException:
        if acquiring.done() and not acquiring.cancelled() and acquiring.exception() is None:
            slots.release()  # granted at the same moment the wait ended
        else:
            acquiring.cancel()
        raise


async def stop_supplement_stages() -> None:
    """Shutdown: cancel every running stage and wait for them to end.

    Their jobs stay active; the stale sweep ends them later, leaving the papers
    (already completed) usable, as after a restart.
    """

    tasks = list(_stage_tasks)
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

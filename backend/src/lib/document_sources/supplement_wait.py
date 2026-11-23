"""Following a provider's conversion of the supplements a curator chose.

``classify_conversion_answer`` says, from one conversion answer and a fresh file
listing, where each chosen supplement stands. It reads only structured fields:
the listing's converted texts, the provider's per-file statuses (by provider
file id) and the answer's overall status. The provider's free-text errors are
never read, so a cause the provider states only in words (its group rule, its
size limits) is not known here.

``run_supplement_stage`` is the supplement stage of an import (KANBAN-1976 /
ALL-1460, P7b; reused by adds and P7d): after the main paper is ready, it waits
for the provider to convert the chosen supplements, then adds the converted
ones in one write. ``start_supplement_stage`` runs it as its own in-process
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
    DocumentSourceProvider,
    SourceConversionFileStatus,
    SourceConversionResult,
    SourceConversionStatus,
)
from src.lib.exceptions import PDFCancellationError
from src.lib.openai_agents.config import (
    get_document_source_poll_interval_seconds,
    get_document_supplement_add_reserve_seconds,
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
# paper stays usable. Proposed wording awaiting the owner's sign-off.
SUPPLEMENT_STAGE_FAILED_MESSAGE = "The main paper is ready, but its supplements could not be added."


class StageOutcomes(Protocol):
    """How the stage ends the import's job (the execution service implements it)."""

    async def completed(self) -> None: ...

    async def cancelled(self) -> None: ...

    async def failed(self, message: str) -> None: ...


class _JobNoLongerActive(Exception):
    """The job was made terminal outside this stage; the stage writes nothing more."""


@dataclass(slots=True)
class _Entry:
    provider_file_id: str
    display_name: str
    state: SupplementState
    reason_code: str | None = None
    observed_at: str | None = None
    file_id: str | None = None
    converted_md5: str | None = None
    md5: str | None = None

    def as_metadata(self) -> dict[str, Any]:
        return {
            "provider_file_id": self.provider_file_id,
            "display_name": self.display_name,
            "state": self.state.value,
            "reason_code": self.reason_code,
            "observed_at": self.observed_at,
            "file_id": self.file_id,
        }


def _iso(epoch_seconds: float) -> str:
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).isoformat()


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

    The paper is already ``completed``; its status never changes here. The wait
    polls the request the main phase sent, only while that request's answer is
    ``running``, and ends at the first of: every file classified, a final answer,
    the supplement wait limit (from ``main_ready_at``), the login's expiry minus
    the add reserve, the first provider error, a cancel, or the job made terminal
    elsewhere (then nothing more is written). The request is never sent after a
    final answer.
    """

    wait_ends_at = main_ready_at + get_document_supplement_wait_timeout_seconds()
    if plan.login_expires_at is not None:
        wait_ends_at = min(
            wait_ends_at,
            plan.login_expires_at - get_document_supplement_add_reserve_seconds(),
        )
    token = plan.request_context.curator_token
    entries: dict[str, _Entry] = {}

    async def checkpoint(stage: str | None = None) -> None:
        job = await asyncio.to_thread(
            pdf_job_service.get_job_by_id, job_id=job_id, reconcile_stale=False
        )
        if job is None or job.status in TERMINAL_PDF_JOB_STATUSES:
            raise _JobNoLongerActive()
        if job.cancel_requested:
            raise PDFCancellationError("Processing cancelled by user request")

    def record(stage: str, message: str) -> None:
        pdf_job_service.update_progress(
            job_id=job_id,
            stage=stage,
            message=message,
            status="running",
            metadata={
                "supplements": [entry.as_metadata() for entry in entries.values()],
                "supplement_wait_ends_at": _iso(wait_ends_at),
            },
        )

    def leave_out(provider_file_id: str, reason: str) -> None:
        entry = entries[provider_file_id]
        entry.state = SupplementState.LEFT_OUT
        entry.reason_code = reason
        entry.observed_at = _iso(clock())

    def waiting_ids() -> list[str]:
        return [key for key, entry in entries.items() if entry.state is SupplementState.WAITING]

    def apply(classified: Mapping[str, str], listing: FileListing) -> None:
        listed = {item.provider_file_id: item for item in listing.files}
        for provider_file_id, outcome in classified.items():
            if outcome == CONVERTED:
                entry = entries[provider_file_id]
                entry.state = SupplementState.QUEUED
                entry.converted_md5 = listed[provider_file_id].converted_md5
            elif outcome != WAITING:
                leave_out(provider_file_id, outcome)

    try:
        await checkpoint()
        try:
            listing = await list_files(
                UUID(document_id), owner_user_id=owner_user_id, provider=provider,
                context=plan.request_context,
            )
        except DocumentSourceError:
            listing = None
        names = {item.provider_file_id: item.display_name for item in listing.files} if listing else {}
        for chosen in plan.chosen:
            entries[chosen.provider_file_id] = _Entry(
                provider_file_id=chosen.provider_file_id,
                display_name=names.get(chosen.provider_file_id) or chosen.provider_file_id,
                state=(
                    SupplementState.QUEUED
                    if chosen.converted_md5 is not None
                    else SupplementState.WAITING
                ),
                converted_md5=chosen.converted_md5,
                md5=chosen.md5,
            )
        record(pdf_job_service.WAITING_FOR_SUPPLEMENTS_STAGE, "Waiting for ABC")

        if waiting_ids():
            if not conversion_requested or last_answer is None or listing is None:
                # Nothing asked the provider to convert them in this import, or
                # the paper's files cannot be listed: they are not converted.
                for provider_file_id in waiting_ids():
                    leave_out(
                        provider_file_id,
                        FileReason.NOT_CONVERTED_IN_TIME.value
                        if listing is None
                        else FileReason.PROVIDER_NOT_CONVERTED.value,
                    )
            elif last_answer.status is not SourceConversionStatus.RUNNING:
                # The main phase already had the provider's final answer:
                # classify once and never ask again.
                apply(
                    classify_conversion_answer(
                        last_answer, provider.conversion_file_statuses(last_answer),
                        waiting_ids(), listing,
                    ),
                    listing,
                )
            else:
                await _poll_until_done(
                    provider=provider, reference=reference, token=token, plan=plan,
                    document_id=document_id, owner_user_id=owner_user_id,
                    listing=listing, wait_ends_at=wait_ends_at, clock=clock, sleep=sleep,
                    list_files=list_files, checkpoint=checkpoint, record=record,
                    leave_out=leave_out, waiting_ids=waiting_ids, apply=apply,
                )

        to_add = [
            entry for entry in entries.values() if entry.state is SupplementState.QUEUED
        ]
        if to_add:
            await checkpoint()
            for entry in to_add:
                entry.state = SupplementState.ADDING
            record(pdf_job_service.ADDING_SUPPLEMENTS_STAGE, "Adding supplements")
            from src.lib.document_sources.supplement_add import ChosenFile

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
                stage_checkpoint=checkpoint,
            )
            for outcome in result.files:
                entry = entries[outcome.provider_file_id]
                if outcome.outcome == "added":
                    entry.state = SupplementState.ADDED
                    entry.file_id = str(outcome.file_id)
                else:
                    leave_out(outcome.provider_file_id, outcome.reason_code.value)
        await checkpoint()
        record(pdf_job_service.ADDING_SUPPLEMENTS_STAGE, "Supplements finished")
        await outcomes.completed()
    except _JobNoLongerActive:
        logger.info("Supplement stage of job %s ended: the job was finalized elsewhere", job_id)
    except PDFCancellationError:
        for provider_file_id in waiting_ids():
            leave_out(provider_file_id, FileReason.NOT_CONVERTED_IN_TIME.value)
        if entries:
            record(pdf_job_service.WAITING_FOR_SUPPLEMENTS_STAGE, "Cancelled")
        await outcomes.cancelled()


async def _poll_until_done(
    *,
    provider: DocumentSourceProvider,
    reference: str,
    token: str | None,
    plan: SupplementPlan,
    document_id: str,
    owner_user_id: int,
    listing: FileListing,
    wait_ends_at: float,
    clock: Callable[[], float],
    sleep: Callable[[float], Awaitable[None]],
    list_files: Callable[..., Awaitable[FileListing]],
    checkpoint: Callable[..., Awaitable[None]],
    record: Callable[[str, str], None],
    leave_out: Callable[[str, str], None],
    waiting_ids: Callable[[], list[str]],
    apply: Callable[[Mapping[str, str], FileListing], None],
) -> None:
    """Re-send the main phase's request while it answers ``running``; stop at the first end."""

    poll_seconds = get_document_source_poll_interval_seconds()
    while waiting_ids():
        remaining = wait_ends_at - clock()
        if remaining <= 0:
            break
        await sleep(min(poll_seconds, remaining))
        await checkpoint()
        if clock() >= wait_ends_at:
            break
        try:
            answer = await provider.request_conversion(
                reference, wait=False, request_bearer_token=token,
            )
            statuses = provider.conversion_file_statuses(answer)
            final = answer.status is not SourceConversionStatus.RUNNING
            newly_converted = any(
                statuses.get(provider_file_id) is SourceConversionFileStatus.CONVERTED
                for provider_file_id in waiting_ids()
            )
            if final or newly_converted:
                listing = await list_files(
                    UUID(document_id), owner_user_id=owner_user_id, provider=provider,
                    context=plan.request_context,
                )
            classified = classify_conversion_answer(answer, statuses, waiting_ids(), listing)
        except (DocumentSourceError, UnclassifiableConversionAnswer):
            # The first provider error ends the wait (no retries).
            break
        apply(classified, listing)
        record(pdf_job_service.WAITING_FOR_SUPPLEMENTS_STAGE, "Waiting for ABC")
        if final:
            break
    for provider_file_id in waiting_ids():
        leave_out(provider_file_id, FileReason.NOT_CONVERTED_IN_TIME.value)


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
    poll_seconds = get_document_source_poll_interval_seconds()
    while True:
        try:
            await asyncio.wait_for(slots.acquire(), timeout=poll_seconds)
            return
        except asyncio.TimeoutError:
            job = await asyncio.to_thread(
                pdf_job_service.get_job_by_id, job_id=job_id, reconcile_stale=False
            )
            if job is None or job.status in TERMINAL_PDF_JOB_STATUSES:
                raise _JobNoLongerActive() from None
            if job.cancel_requested:
                raise PDFCancellationError("Processing cancelled by user request") from None
            pdf_job_service.update_progress(
                job_id=job_id,
                stage=pdf_job_service.WAITING_FOR_SUPPLEMENTS_STAGE,
                message="Waiting for ABC",
                status="running",
            )


async def stop_supplement_stages() -> None:
    """Shutdown: cancel every running stage and wait for them to end.

    Their jobs stay active; the stale sweep ends them later, leaving the papers
    (already completed) usable, as after a restart.
    """

    tasks = list(_stage_tasks)
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

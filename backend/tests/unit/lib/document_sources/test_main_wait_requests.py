"""How often the main paper's wait asks ABC to convert (KANBAN-2191 / ALL-1725).

Chris's decision of 23 Nov 2026: the import sends ABC's on-demand conversion
request once, the wait then watches ABC's read-only ``show_all`` listing for the
main paper's converted text, and asks ABC again exactly once at the end, only to
learn why nothing appeared (inside ABC's one-hour failure hold, the request
reports a failure rather than starting a new job).

Counted at the HTTP boundary: the real Alliance ABC adapter and its HTTP client
on an ``httpx.MockTransport`` serving ABC's recorded shapes. The job store is an
in-memory stand-in that keeps the job's metadata (the persistence lane covers
the real row). All values are made up.
"""

from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import unquote

import httpx
import pytest

from agr_ai_curation_alliance.document_sources.abc_literature import (
    ABCLiteratureDocumentSourceProvider,
    _map_conversion_result,
)
from agr_ai_curation_alliance.literature.client import ABCLiteratureClient, ABCLiteratureClientConfig
from src.lib.document_sources.models import ProviderBearerKind
from src.lib.pdf_jobs import upload_execution_service as service_module
from src.lib.pdf_jobs.upload_execution_service import (
    ProviderConversionExecutionRequest,
    UploadExecutionService,
)
from tests.unit.lib.document_sources.test_abc_literature_provider import (
    _abc_show_all_main_pdf,
    _abc_show_all_row,
    _abc_source,
)
from tests.unit.lib.document_sources.test_supplement_wait import _answer_payload, _job_entry

REFERENCE = "AGRKB:101"
CONVERSION_PATH = f"/reference/referencefile/conversion_request/{REFERENCE}"
SHOW_ALL_PATH = f"/reference/referencefile/show_all/{REFERENCE}"
RUNNING = _answer_payload("running", [_job_entry(10, "main", "pending")])
FAILED = _answer_payload(
    "failed", [_job_entry(10, "main", "failed", error="Made-up PDF could not be parsed")],
    error_message="Made-up PDF could not be parsed",
)
NO_SOURCES = {**_answer_payload("no_sources", []), "job_id": None}


class _Abc:
    """ABC over HTTP. ``answers`` are its conversion answers in order; ``main_after``
    is the ``show_all`` read from which the converted main text is listed."""

    def __init__(self, *, answers=(), main_after: int | None = None,
                 failing_reads: set[int] | None = None, read_status: int = 502, read_seconds: float = 0.0):
        self.read_seconds = read_seconds
        self.answers = list(answers)
        self.main_after = main_after
        self.failing_reads = failing_reads or set()
        self.read_status = read_status
        self.requests: list[str] = []
        self.main = _abc_show_all_main_pdf(10, "WB")
        self.main.update({"reference_id": 101, "source": None})

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = unquote(request.url.path)
        self.requests.append(path)
        if path == CONVERSION_PATH:
            return httpx.Response(202, json=self.answers.pop(0) if len(self.answers) > 1 else self.answers[0])
        if path == SHOW_ALL_PATH:
            if self.read_seconds:
                import time

                time.sleep(self.read_seconds)  # ABC's listing takes this long to answer
            if self.show_all_reads in self.failing_reads:
                return httpx.Response(self.read_status, json={"detail": "made-up failure"})
            rows = [self.main]
            if self.main_after is not None and self.show_all_reads >= self.main_after:
                rows.append(_abc_show_all_row(
                    11, "WB", file_class="converted_merged_main", file_extension="md",
                    display_name="WBPaper00067001_merged", source=_abc_source(self.main),
                ))
            return httpx.Response(200, json=rows)
        if path.startswith("/reference/referencefile/download_file/"):
            body = b"# A made-up paper\n\n## Results\n\nMade-up results.\n"
            return httpx.Response(200, stream=httpx.ByteStream(body), headers={"content-length": str(len(body))})
        return httpx.Response(404, json={"detail": "Not Found"})

    @property
    def conversion_requests(self) -> int:
        return self.requests.count(CONVERSION_PATH)

    @property
    def show_all_reads(self) -> int:
        return self.requests.count(SHOW_ALL_PATH)

    def provider(self):
        client = ABCLiteratureClient(
            ABCLiteratureClientConfig(base_url="https://abc.example.org"),
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(self.handle)),
        )
        return ABCLiteratureDocumentSourceProvider(client)


class _Job:
    """The durable job: its status, cancel flag and metadata, as the job service keeps them."""

    def __init__(self, *, metadata=None, record_delay: float = 0.0):
        self.record_delay = record_delay
        self.status = "pending"
        self.cancel_requested = False
        self.metadata: dict = dict(metadata or {})
        self.ended: list[tuple[str, str]] = []

    def install(self, monkeypatch):
        jobs = service_module.pdf_job_service

        def _view(**_kwargs):
            return SimpleNamespace(status=self.status, cancel_requested=self.cancel_requested,
                                   metadata=dict(self.metadata))

        def _progress(**kwargs):
            if kwargs.get("status"):
                self.status = kwargs["status"]
            if kwargs.get("metadata"):
                self.metadata.update(kwargs["metadata"])

        def _record(*, job_id, which):
            import time

            time.sleep(self.record_delay)  # a slow database write
            requests = dict(self.metadata.get(jobs.CONVERSION_REQUESTS_KEY) or {})
            requests[which] = "2026-11-23T12:00:00+00:00"
            self.metadata[jobs.CONVERSION_REQUESTS_KEY] = requests

        def _end(status):
            def _mark(**kwargs):
                self.status = status
                self.ended.append((status, kwargs.get("message") or kwargs.get("reason") or ""))
            return _mark

        monkeypatch.setattr(jobs, "get_job_by_id", _view)
        monkeypatch.setattr(jobs, "is_cancel_requested", lambda **_k: self.cancel_requested)
        monkeypatch.setattr(jobs, "update_progress", _progress)
        monkeypatch.setattr(jobs, "record_provider_conversion_request", _record)
        monkeypatch.setattr(jobs, "mark_completed", _end("completed"))
        monkeypatch.setattr(jobs, "mark_failed", _end("failed"))
        monkeypatch.setattr(jobs, "mark_cancelled", _end("cancelled"))
        return self


@pytest.fixture
def quiet(monkeypatch):
    async def _nothing(*_args, **_kwargs):
        return None

    monkeypatch.setattr(service_module, "update_document_status_detailed", AsyncMock(return_value={"success": True}))
    monkeypatch.setattr(service_module, "get_connection", lambda: None)
    monkeypatch.setattr(UploadExecutionService, "_sync_provider_conversion_sql_failure", _nothing)
    monkeypatch.setattr(UploadExecutionService, "_sync_provider_conversion_sql_selection", _nothing)
    monkeypatch.setattr(UploadExecutionService, "_report_execution_failure", lambda *_a, **_k: None)
    monkeypatch.setattr(UploadExecutionService, "_sync_tracker_from_terminal_job", _nothing)
    # A 3-second wait read every 0.2 s; the last read that fits ends at 3 - (0.2 + 0.5) s.
    monkeypatch.setattr(service_module, "get_document_source_import_timeout_seconds", lambda: 3.0)
    monkeypatch.setattr(service_module, "get_document_source_poll_interval_seconds", lambda: 0.2)
    monkeypatch.setattr(service_module, "get_document_source_request_timeout_seconds", lambda: 0.5)


def _service(abc: _Abc, ingested: list):
    async def _ingest(request, *, weaviate_client):
        ingested.append(request.document_id)
        return SimpleNamespace(validation_warnings=[])

    async def _complete(self, request, **kwargs):
        service_module.pdf_job_service.mark_completed(job_id=request.job_id, message=kwargs["message"])

    service = UploadExecutionService(
        pipeline_tracker=AsyncMock(), document_source_provider_factory=abc.provider,
        provider_markdown_ingestion_fn=_ingest,
    )
    service._complete_main_paper = _complete.__get__(service)  # the supplement hand-off is P7b's
    return service


def _request(*, import_answer=RUNNING):
    return ProviderConversionExecutionRequest(
        document_id="doc-1", job_id="job-1", user_id="user-1", owner_user_id=7, filename="made-up.pdf",
        reference=REFERENCE, source_artifact_id="10", curator_token="made-up-token",
        bearer_kind=ProviderBearerKind.CURATOR, source_provenance={"provider": "abc_literature"},
        file_path=Path("/tmp/made-up.pdf"),
        conversion_answer=(
            _map_conversion_result(import_answer, provider="abc_literature") if import_answer else None
        ),
    )


def _sent_at_import(seconds_ago: float = 0.0):
    """What the import stores when it created the job after sending ABC's request."""

    from datetime import datetime, timedelta, timezone

    from src.lib.pdf_jobs import service as jobs

    sent_at = datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
    return {jobs.CONVERSION_REQUESTS_KEY: {"on_demand_sent_at": sent_at.isoformat()}}


@pytest.mark.asyncio
async def test_a_paper_abc_converts_is_imported_with_no_further_conversion_request(monkeypatch, quiet):
    abc = _Abc(answers=[RUNNING], main_after=3)
    job = _Job(metadata=_sent_at_import()).install(monkeypatch)
    ingested: list = []

    await _service(abc, ingested).execute_provider_conversion(_request())

    assert abc.conversion_requests == 0  # the import's own request was the one
    assert abc.show_all_reads >= 3
    assert ingested == ["doc-1"]
    assert job.ended == [("completed", "Processing completed")]


@pytest.mark.asyncio
async def test_a_paper_abc_fails_is_reported_at_the_end_with_abcs_reason_after_one_request(monkeypatch, quiet):
    abc = _Abc(answers=[FAILED])
    job = _Job(metadata=_sent_at_import()).install(monkeypatch)
    ingested: list = []

    await _service(abc, ingested).execute_provider_conversion(_request())

    assert abc.conversion_requests == 1  # the one request at the end
    assert ingested == []
    assert job.ended == [("failed", "Made-up PDF could not be parsed")]
    assert job.metadata[service_module.pdf_job_service.CONVERSION_REQUESTS_KEY]["end_sent_at"]


@pytest.mark.asyncio
async def test_a_paper_abc_never_converts_times_out_after_one_request_at_the_end(monkeypatch, quiet):
    abc = _Abc(answers=[RUNNING])
    job = _Job(metadata=_sent_at_import()).install(monkeypatch)

    await _service(abc, []).execute_provider_conversion(_request())

    assert abc.conversion_requests == 1
    assert job.ended == [
        (
            "failed",
            "ABC had not converted this paper 3 seconds after the import started, "
            "so the import stopped. The time includes any wait behind other imports.",
        )
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(("first_answer", "reason"), [
    (NO_SOURCES, "Provider conversion found no convertible source files"),
    (FAILED, "Made-up PDF could not be parsed"),
], ids=["nothing_to_convert", "immediate_failure"])
async def test_a_first_answer_that_ends_the_wait_ends_it_at_once_as_today(monkeypatch, quiet, first_answer, reason):
    """No request was sent at import (the listing already showed ABC converting the main
    text): the wait sends the one request itself, and a final first answer ends it."""

    abc = _Abc(answers=[first_answer])
    job = _Job().install(monkeypatch)

    await _service(abc, []).execute_provider_conversion(_request(import_answer=None))

    assert abc.conversion_requests == 1
    assert abc.show_all_reads <= 1
    assert job.ended == [("failed", reason)]


@pytest.mark.asyncio
async def test_a_wait_started_without_an_import_request_sends_one_then_watches_the_listing(monkeypatch, quiet):
    abc = _Abc(answers=[RUNNING], main_after=2)
    job = _Job().install(monkeypatch)
    ingested: list = []

    await _service(abc, ingested).execute_provider_conversion(_request(import_answer=None))

    assert abc.conversion_requests == 1
    assert job.metadata[service_module.pdf_job_service.CONVERSION_REQUESTS_KEY]["on_demand_sent_at"]
    assert ingested == ["doc-1"]


@pytest.mark.asyncio
async def test_a_cancel_during_the_wait_sends_no_request(monkeypatch, quiet):
    abc = _Abc(answers=[RUNNING])
    job = _Job(metadata=_sent_at_import()).install(monkeypatch)
    original_sleep = asyncio.sleep

    async def _sleep_then_cancel(seconds):
        job.cancel_requested = True
        await original_sleep(0)

    monkeypatch.setattr(service_module.asyncio, "sleep", _sleep_then_cancel)

    await _service(abc, []).execute_provider_conversion(_request())

    assert abc.conversion_requests == 0
    assert job.ended == [("cancelled", "Processing cancelled by user request")]


@pytest.mark.asyncio
async def test_a_wait_run_again_after_a_restart_never_repeats_a_stored_request(monkeypatch, quiet):
    """A backend restart ends the wait's task. Nothing resumes a started job (the replay
    guard skips it); and even a run of the same job while it is still pending reads the
    stored record of what was sent, so neither request is sent twice."""

    abc = _Abc(answers=[RUNNING])
    job = _Job(metadata=_sent_at_import()).install(monkeypatch)
    service = _service(abc, [])

    # First run: killed (the process ends) after the end request was sent.
    first = asyncio.create_task(service.execute_provider_conversion(_request()))
    while abc.conversion_requests < 1:
        await asyncio.sleep(0.05)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert abc.conversion_requests == 1
    stored = job.metadata[service_module.pdf_job_service.CONVERSION_REQUESTS_KEY]
    assert stored["on_demand_sent_at"] and stored["end_sent_at"]

    # A run of the same job while it reads running is skipped (replay guard).
    job.status = "running"
    await service.execute_provider_conversion(_request())
    assert abc.conversion_requests == 1

    # Even a run while it still reads pending sends nothing: both requests are stored as sent.
    job.status = "pending"
    await service.execute_provider_conversion(_request(import_answer=None))
    assert abc.conversion_requests == 1



@pytest.mark.asyncio
async def test_failed_listing_reads_are_tried_again_and_the_paper_still_imports(monkeypatch, quiet):
    """Owner, 23 Nov 2026: up to two failed reads in a row are tolerated
    (DOCUMENT_SOURCE_LISTING_MAX_FAILED_READS, default 3), with no request to ABC."""

    abc = _Abc(answers=[RUNNING], main_after=3, failing_reads={1, 2})
    job = _Job(metadata=_sent_at_import()).install(monkeypatch)
    ingested: list = []

    await _service(abc, ingested).execute_provider_conversion(_request())

    assert abc.conversion_requests == 0
    assert ingested == ["doc-1"]
    assert job.ended == [("completed", "Processing completed")]


@pytest.mark.asyncio
async def test_three_failed_listing_reads_in_a_row_end_the_import(monkeypatch, quiet):
    abc = _Abc(answers=[RUNNING], main_after=10, failing_reads={1, 2, 3})
    job = _Job(metadata=_sent_at_import()).install(monkeypatch)

    await _service(abc, []).execute_provider_conversion(_request())

    assert abc.conversion_requests == 0
    assert abc.show_all_reads == 3
    assert job.ended == [("failed", "ABC Literature artifact listing failed")]


@pytest.mark.asyncio
async def test_a_refused_login_on_a_listing_read_ends_the_import_at_once(monkeypatch, quiet):
    abc = _Abc(answers=[RUNNING], main_after=10, failing_reads={1}, read_status=401)
    job = _Job(metadata=_sent_at_import()).install(monkeypatch)

    await _service(abc, []).execute_provider_conversion(_request())

    assert (abc.conversion_requests, abc.show_all_reads) == (0, 1)
    assert [status for status, _ in job.ended] == ["failed"]



@pytest.mark.asyncio
async def test_a_good_read_resets_the_main_waits_count_of_failed_reads(monkeypatch, quiet):
    """Reads fail, fail, succeed, fail, fail, then the text is listed: never three in a row."""

    abc = _Abc(answers=[RUNNING], main_after=6, failing_reads={1, 2, 4, 5})
    job = _Job(metadata=_sent_at_import()).install(monkeypatch)
    ingested: list = []

    await _service(abc, ingested).execute_provider_conversion(_request())

    assert ingested == ["doc-1"]
    assert job.ended == [("completed", "Processing completed")]


@pytest.mark.asyncio
async def test_abcs_reason_from_the_end_request_is_kept_when_the_next_read_fails(monkeypatch, quiet):
    class _ListingDownAfterTheEndRequest(_Abc):
        def handle(self, request):
            if unquote(request.url.path) == SHOW_ALL_PATH and self.conversion_requests:
                self.requests.append(SHOW_ALL_PATH)
                return httpx.Response(502, json={"detail": "made-up failure"})
            return super().handle(request)

    abc = _ListingDownAfterTheEndRequest(answers=[FAILED])
    job = _Job(metadata=_sent_at_import()).install(monkeypatch)

    await _service(abc, []).execute_provider_conversion(_request())

    assert abc.conversion_requests == 1
    assert job.ended == [("failed", "Made-up PDF could not be parsed")]


@pytest.mark.asyncio
async def test_a_wait_that_starts_after_its_deadline_reads_once_and_never_asks_abc(monkeypatch, quiet):
    """Queued behind other papers past its deadline (timed from its own request): one
    listing read, no request outside ABC's failure hold, then the existing timeout."""

    abc = _Abc(answers=[FAILED])
    job = _Job(metadata=_sent_at_import(seconds_ago=4.0)).install(monkeypatch)

    await _service(abc, []).execute_provider_conversion(_request())

    assert (abc.conversion_requests, abc.show_all_reads) == (0, 1)
    # The time actually waited since the paper's own request.
    assert job.ended == [
        (
            "failed",
            "ABC had not converted this paper 4 seconds after the import started, "
            "so the import stopped. The time includes any wait behind other imports.",
        )
    ]


@pytest.mark.asyncio
async def test_a_paper_abc_converted_while_it_was_queued_is_still_imported(monkeypatch, quiet):
    abc = _Abc(answers=[FAILED], main_after=1)
    job = _Job(metadata=_sent_at_import(seconds_ago=4.0)).install(monkeypatch)
    ingested: list = []

    await _service(abc, ingested).execute_provider_conversion(_request())

    assert abc.conversion_requests == 0
    assert ingested == ["doc-1"]
    assert job.ended == [("completed", "Processing completed")]


class _Jobs:
    """Several durable jobs, for a batch."""

    def __init__(self, metadata_by_job):
        self.jobs = {job_id: _Job(metadata=metadata) for job_id, metadata in metadata_by_job.items()}

    def install(self, monkeypatch):
        jobs = service_module.pdf_job_service

        def _job(kwargs):
            return self.jobs[str(kwargs["job_id"])]

        def _view(**kwargs):
            job = _job(kwargs)
            return SimpleNamespace(status=job.status, cancel_requested=job.cancel_requested,
                                   metadata=dict(job.metadata))

        def _progress(**kwargs):
            job = _job(kwargs)
            if kwargs.get("status"):
                job.status = kwargs["status"]
            if kwargs.get("metadata"):
                job.metadata.update(kwargs["metadata"])

        def _record(*, job_id, which):
            job = self.jobs[str(job_id)]
            requests = dict(job.metadata.get(jobs.CONVERSION_REQUESTS_KEY) or {})
            requests[which] = "recorded"
            job.metadata[jobs.CONVERSION_REQUESTS_KEY] = requests

        def _end(status):
            def _mark(**kwargs):
                job = _job(kwargs)
                job.status = status
                job.ended.append((status, kwargs.get("message") or kwargs.get("reason") or ""))
            return _mark

        monkeypatch.setattr(jobs, "get_job_by_id", _view)
        monkeypatch.setattr(jobs, "is_cancel_requested", lambda **kwargs: _job(kwargs).cancel_requested)
        monkeypatch.setattr(jobs, "update_progress", _progress)
        monkeypatch.setattr(jobs, "record_provider_conversion_request", _record)
        monkeypatch.setattr(jobs, "touch_pending_jobs", lambda *, job_ids: 0)
        monkeypatch.setattr(jobs, "mark_completed", _end("completed"))
        monkeypatch.setattr(jobs, "mark_failed", _end("failed"))
        monkeypatch.setattr(jobs, "mark_cancelled", _end("cancelled"))
        return self


class _TimedAbc(_Abc):
    """Records when each conversion request was sent."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.request_times: list[float] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        import time

        if unquote(request.url.path) == CONVERSION_PATH:
            self.request_times.append(time.time())
        return super().handle(request)


@pytest.mark.asyncio
@pytest.mark.parametrize(("first_paper_answer", "second_paper_requests"), [
    # ABC failed both papers early: paper 1's end answer ends it at about 2.3 s; paper 2
    # starts then and still asks inside its own deadline.
    (FAILED, 1),
    # ABC never answers for paper 1: it uses its whole deadline, so paper 2 starts after
    # its own deadline (both requests were sent at import) and asks nothing.
    (RUNNING, 0),
], ids=["first_fails_early", "first_never_converts"])
async def test_in_a_batch_no_end_request_comes_after_that_papers_deadline(
    monkeypatch, quiet, first_paper_answer, second_paper_requests,
):
    """Finding 1 of #1137's review: a request's papers run one after another, so each
    paper's wait is timed from its own on-demand request, never from when it starts."""

    import time

    from fastapi import BackgroundTasks

    started = time.time()
    abc = _TimedAbc(answers=[first_paper_answer])
    jobs = _Jobs({"job-1": _sent_at_import(), "job-2": _sent_at_import()}).install(monkeypatch)
    service = _service(abc, [])
    background_tasks = BackgroundTasks()
    for job_id in ("job-1", "job-2"):
        request = dataclasses.replace(_request(), job_id=job_id, document_id=f"doc-{job_id}")
        await service.dispatch_provider_conversion_execution(background_tasks=background_tasks, request=request)

    await background_tasks()

    deadline = 3.0
    assert all(sent_at - started <= deadline for sent_at in abc.request_times), [
        round(sent_at - started, 2) for sent_at in abc.request_times
    ]
    assert len(abc.request_times) == 1 + second_paper_requests
    assert [status for status, _ in jobs.jobs["job-2"].ended] == ["failed"]



@pytest.mark.asyncio
@pytest.mark.parametrize(("seconds_ago", "record_delay"), [
    (2.7, 0.0),  # starts 0.3 s before its deadline: no room for a request (0.5 s timeout)
    (2.0, 0.8),  # room at first, but the record write ends too close to the deadline
], ids=["starts_0.3s_before_the_deadline", "slow_record_write"])
async def test_the_end_request_is_sent_only_while_it_can_be_answered_before_the_deadline(
    monkeypatch, quiet, seconds_ago, record_delay,
):
    """Re-review of #1137: the end request is sent only while now plus the request
    timeout is still before the wait's end, so it is sent and answered inside it."""

    abc = _Abc(answers=[FAILED])
    job = _Job(metadata=_sent_at_import(seconds_ago=seconds_ago), record_delay=record_delay).install(monkeypatch)

    await _service(abc, []).execute_provider_conversion(_request())

    assert abc.conversion_requests == 0
    # The whole seconds actually waited since the request (2.7 s and up, depending on the case).
    assert [status for status, _ in job.ended] == ["failed"]
    assert job.ended[0][1] in {
        "ABC had not converted this paper 2 seconds after the import started, "
        "so the import stopped. The time includes any wait behind other imports.",
        "ABC had not converted this paper 3 seconds after the import started, "
        "so the import stopped. The time includes any wait behind other imports.",
    }



@pytest.mark.asyncio
@pytest.mark.parametrize("phase", [0.0, 0.04, 0.08, 0.12, 0.16, 0.2])
async def test_every_start_phase_sends_exactly_one_end_request_when_abc_never_converts(monkeypatch, quiet, phase):
    """The end request's window is one poll long while reads come one poll plus the
    read's own time apart: a read is made to land at its start, whatever the phase
    (a slow listing, 0.15 s against a 0.2 s poll, makes a skipped window likely)."""

    abc = _Abc(answers=[RUNNING], read_seconds=0.15)
    job = _Job(metadata=_sent_at_import(seconds_ago=phase)).install(monkeypatch)

    await _service(abc, []).execute_provider_conversion(_request())

    assert abc.conversion_requests == 1
    assert [status for status, _ in job.ended] == ["failed"]


# --- ALL-1678: the polling cap per main wait (owner's option b, 24 Nov 2026), proven at the HTTP
# boundary. The cap counts our polls: the listing reads the wait makes while it waits and its two
# conversion requests. Each test also reports every request the wait really sends.


class _ReadFailures(_Abc):
    """ABC whose listing fails on the reads ``fails(n)`` says, as often as the tolerance allows."""

    def __init__(self, fails, **kwargs):
        super().__init__(**kwargs)
        self.fails = fails

    def handle(self, request: httpx.Request) -> httpx.Response:
        if unquote(request.url.path) == SHOW_ALL_PATH and self.fails(self.show_all_reads + 1):
            self.requests.append(SHOW_ALL_PATH)
            return httpx.Response(502, json={"detail": "made-up failure"})
        return super().handle(request)

    @property
    def polls(self) -> int:
        """Conversion requests, plus listing reads until the main text was found."""
        found = next(
            (index for index, path in enumerate(self.requests) if "/download_file/" in path),
            len(self.requests),
        )
        return sum(1 for path in self.requests[:found] if path in (SHOW_ALL_PATH, CONVERSION_PATH))


async def _one_main_wait(monkeypatch, *, deadline, poll, fails=lambda n: False, main_after=None):
    """One main wait on a virtual clock; every HTTP request counted. Returns (polls, total)."""

    from tests.unit.lib.pdf_jobs.test_upload_execution_service import _VirtualClock

    clock = _VirtualClock()
    monkeypatch.setattr(service_module, "asyncio", clock)
    monkeypatch.setattr(service_module, "time", SimpleNamespace(time=lambda: clock.now))
    monkeypatch.setattr(service_module, "get_document_source_import_timeout_seconds", lambda: float(deadline))
    monkeypatch.setattr(service_module, "get_document_source_poll_interval_seconds", lambda: float(poll))
    monkeypatch.setattr(service_module, "get_document_source_request_timeout_seconds", lambda: 10.0)
    abc = _ReadFailures(fails, answers=[RUNNING], main_after=main_after)
    _Job().install(monkeypatch)  # no record: the wait sends its own on-demand request
    await _service(abc, []).execute_provider_conversion(_request(import_answer=None))
    return abc.polls, len(abc.requests)


@pytest.mark.asyncio
async def test_at_the_edge_of_the_cap_a_main_wait_makes_exactly_the_bound(monkeypatch, quiet):
    """3,600 s at 9.12 s, the smallest poll the default cap allows. ABC never converts: every
    read, the read the capped sleep adds, the end request and its re-read."""
    from src.lib.openai_agents.config import main_wait_max_polls

    polls, total = await _one_main_wait(monkeypatch, deadline=3600, poll=9.12)

    assert polls == main_wait_max_polls(deadline_seconds=3600, poll_seconds=9.12) == 400
    assert total == 400  # nothing else is sent when ABC never converts


@pytest.mark.asyncio
async def test_a_main_text_found_on_the_last_read_adds_requests_the_cap_does_not_count(monkeypatch, quiet):
    """The reviewer's case: the text appears on the last read. The polls stay within the cap;
    the main text's download comes on top (and in the product the ingestion's figure-metadata
    read and figure downloads, which this harness does not reach)."""
    from src.lib.openai_agents.config import main_wait_max_polls

    polls, total = await _one_main_wait(monkeypatch, deadline=3600, poll=9.12, main_after=398)

    assert polls == main_wait_max_polls(deadline_seconds=3600, poll_seconds=9.12) == 400
    assert total == 401  # the polls, plus the main text's download


@pytest.mark.asyncio
async def test_at_the_edge_with_every_allowed_failed_read_a_main_wait_stays_within_the_cap(monkeypatch, quiet):
    """Two failed reads in a row, then a good one, all the way (three in a row would end it)."""
    from src.lib.openai_agents.config import main_wait_max_polls

    polls, total = await _one_main_wait(monkeypatch, deadline=3600, poll=9.12, fails=lambda n: n % 3 != 0)

    assert polls == total <= main_wait_max_polls(deadline_seconds=3600, poll_seconds=9.12)


@pytest.mark.asyncio
async def test_a_sweep_of_legal_settings_never_exceeds_the_main_waits_cap(monkeypatch, quiet):
    """A few hundred deadline and poll pairs, with and without failed reads and a late text."""
    import random

    from src.lib.openai_agents.config import main_wait_max_polls

    generator = random.Random(1678)
    equal = 0
    for _ in range(240):
        deadline = generator.choice([60, 125, 600, 1800, 3600])
        poll = round(generator.uniform(max(5.0, deadline / 395), deadline / 4), 2)
        pattern = generator.choice(["none", "two_of_three", "random"])
        if pattern == "two_of_three":
            fails = lambda n: n % 3 != 0  # noqa: E731
        elif pattern == "random":
            fails = lambda n, seed=generator.random(): random.Random(seed * 1000 + n).random() < 0.4  # noqa: E731
        else:
            fails = lambda n: False  # noqa: E731
        main_after = generator.choice([None, None, generator.randrange(1, 400)])
        with pytest.MonkeyPatch.context() as each:
            polls, total = await _one_main_wait(each, deadline=deadline, poll=poll, fails=fails, main_after=main_after)
        bound = main_wait_max_polls(deadline_seconds=deadline, poll_seconds=poll)
        assert polls <= bound, (deadline, poll, pattern, main_after, polls, bound)
        assert total <= polls + 1  # at most the main text's download on top here
        equal += polls == bound
    assert equal > 0  # the bound is reached, not only respected

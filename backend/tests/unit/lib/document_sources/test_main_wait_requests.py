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
from pathlib import Path
from types import SimpleNamespace
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
                 failing_reads: set[int] | None = None, read_status: int = 502):
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

    def __init__(self, *, metadata=None):
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

    monkeypatch.setattr(service_module, "update_document_status", _nothing)
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
        pipeline_tracker=SimpleNamespace(), document_source_provider_factory=abc.provider,
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


def _sent_at_import():
    """What the import stores when it created the job after sending ABC's request."""

    from src.lib.pdf_jobs import service as jobs

    return {jobs.CONVERSION_REQUESTS_KEY: {"on_demand_sent_at": "2026-11-23T11:59:00+00:00"}}


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
    assert job.ended == [("failed", "Provider conversion exceeded 3 seconds")]


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

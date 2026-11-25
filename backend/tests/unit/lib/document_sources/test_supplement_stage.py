"""The supplement stage of an import (KANBAN-1976 / ALL-1460, P7b).

ABC's answers are its own response shapes at ``3d3ae96``, sent through the real
ABC adapter; the paper's files come from the real listing over ``show_all``
rows. The add (P7c1), the job service and the clock are fakes. All values are
made up.
"""

from __future__ import annotations

import asyncio
import hashlib
from types import SimpleNamespace
from collections.abc import Callable
from typing import Any
from uuid import uuid4

import pytest

from src.lib.document_sources import supplement_wait as stage
from src.lib.document_sources.access import DocumentSourceRequestContext
from src.lib.document_sources.file_reasons import FileReason
from src.lib.document_sources.models import (
    DocumentSourceLoginExpired,
    DocumentSourceUnavailable,
    ProviderBearerKind,
)
from src.lib.document_sources.supplement_add import AddResult, FileOutcome
from src.lib.document_sources.supplement_plan import ChosenSupplement, SupplementPlan
from tests.unit.lib.document_sources.test_abc_literature_provider import (
    FakeABCLiteratureClient,
    _abc_reference_record,
    _abc_show_all_main_pdf,
    provider_from_fake,
)
from tests.unit.lib.document_sources.test_file_listing import GOOD_TABLE, _listing, _pair
from tests.unit.lib.document_sources.test_supplement_wait import (
    _answer_payload,
    _job_entry,
    _unconverted,
)

NOT_CONVERTED = FileReason.PROVIDER_NOT_CONVERTED.value
FAILED = FileReason.PROVIDER_CONVERSION_FAILED.value
IN_TIME = FileReason.NOT_CONVERTED_IN_TIME.value
DOCUMENT_ID = str(uuid4())
JOB_ID = str(uuid4())


class _Clock:
    def __init__(self, now: float = 1_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds
        await asyncio.sleep(0)


class _AbcClient(FakeABCLiteratureClient):
    """ABC as the stage sees it: ``show_all`` serves the paper's rows as they are now.

    Counts the conversion requests (the stage must send none) and the
    ``show_all`` reads. ``on_read`` runs before read N (1-based) to change the
    paper, as ABC storing a converted file would; ``read_error`` makes every
    read fail as the client would.
    """

    def __init__(self, answers: list[Any] | None = None, *, paper: "_Paper | None" = None,
                 on_read: dict[int, Callable[[], None]] | None = None,
                 read_error: Exception | None = None, failing_reads: set[int] | None = None):
        super().__init__()
        self.answers = answers or [_answer_payload("converted", [])]
        self.requests = 0
        self.reads = 0
        self.reference_reads = 0
        self.paper = paper
        self.on_read = on_read or {}
        self.read_error = read_error
        self.failing_reads = failing_reads  # None: every read fails when read_error is set

    async def show_reference(self, reference, *, request_bearer_token=None):
        self.reference_reads += 1
        if self.paper is not None:
            return _abc_reference_record(
                copyright_license_open_access=self.paper.licence_open,
                copyright_license_name=self.paper.licence_name,
            )
        return _abc_reference_record()

    async def request_referencefile_conversion(self, reference, *, wait=False, request_bearer_token=None):
        self.requests += 1
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def show_referencefiles(self, reference, *, request_bearer_token=None):
        self.reads += 1
        if self.read_error is not None and (self.failing_reads is None or self.reads in self.failing_reads):
            raise self.read_error
        if self.reads in self.on_read:
            self.on_read[self.reads]()
        return self.paper.rows if self.paper is not None else []

    async def download_referencefile(self, artifact_id, *, request_bearer_token=None, max_bytes=None):
        if self.paper is not None and artifact_id in self.paper.payloads:
            payload = self.paper.payloads[artifact_id]
            if isinstance(payload, Exception):
                raise payload
            return payload
        return await super().download_referencefile(
            artifact_id, request_bearer_token=request_bearer_token, max_bytes=max_bytes,
        )


class _Paper:
    """The paper's ABC rows as the listing would show them now."""

    def __init__(self, rows, payloads=None, *, groups=("WB",), licence_open=None, licence_name=None):
        self.rows = rows
        self.payloads = payloads or {}
        self.groups = groups
        self.licence_open = licence_open
        self.licence_name = licence_name
        self.listings = 0
        self.reused_listings = 0
        self.store = None

    async def list_files(self, document_id, *, owner_user_id, provider, context):
        from tests.unit.lib.document_sources.test_file_listing import _Store

        self.listings += 1
        if self.store is None:
            self.store = _Store()
        listing, _ = await _listing(
            self.rows,
            groups=self.groups,
            payloads=self.payloads,
            store=self.store,
            licence_open=self.licence_open,
            licence_name=self.licence_name,
            in_paper=("10",),
        )
        return listing

    async def list_files_from_artifacts(
        self, document_id, *, owner_user_id, provider, context, reference, artifacts, check_unchecked=True,
    ):
        from src.lib.document_sources.file_listing import ListingRoute, list_reference_files
        from tests.unit.lib.document_sources.test_file_listing import _Store, _limits

        self.reused_listings += 1
        if self.store is None:
            self.store = _Store()
        return await list_reference_files(
            provider=provider,
            reference=reference,
            authorized_group_ids=tuple(context.authorized_group_ids),
            bearer_kind=context.bearer_kind,
            request_bearer_token=context.curator_token or "",
            files_in_paper=("10",),
            limits=_limits(),
            check_limit=20,
            load_stored=self.store.load,
            save_assessment=self.store.save,
            route=ListingRoute.PAPER,
            provider_observations={},
            find_paper_in_library=None,
            artifacts=artifacts,
            check_unchecked=check_unchecked,
        )


class _Add:
    def __init__(self):
        self.calls: list[list[Any]] = []
        self.listings: list[Any] = []

    async def __call__(
        self, document_id, owner_user_id, request_context, files, *, listing, job_id,
        provider, weaviate_client, stage_checkpoint, settled_outcomes=None, display_names=None,
    ):
        await stage_checkpoint("checking_files")
        self.calls.append(list(files))
        self.listings.append(listing)
        return AddResult(files=tuple(
            FileOutcome(provider_file_id=chosen.provider_file_id, outcome="added", file_id=uuid4())
            for chosen in files
        ))


class _Outcomes:
    def __init__(self):
        self.ended: list[str] = []

    async def completed(self):
        self.ended.append("completed")

    async def cancelled(self):
        self.ended.append("cancelled")

    async def failed(self, message):
        self.ended.append(f"failed: {message}")


class _Job:
    def __init__(self):
        self.status = "running"
        self.cancel_requested = False
        self.progress: list[dict[str, Any]] = []


@pytest.fixture
def job(monkeypatch):
    state = _Job()
    monkeypatch.setattr(
        stage.pdf_job_service, "get_job_by_id",
        lambda **_kwargs: SimpleNamespace(status=state.status, cancel_requested=state.cancel_requested),
    )
    monkeypatch.setattr(
        stage.pdf_job_service, "update_progress",
        lambda **kwargs: state.progress.append(kwargs),
    )
    for name in (
        "DOCUMENT_SOURCE_POLL_INTERVAL_SECONDS",
        "DOCUMENT_SUPPLEMENT_WAIT_TIMEOUT_SECONDS",
        "DOCUMENT_SUPPLEMENT_ADD_RESERVE_SECONDS",
        "DOCUMENT_SUPPLEMENT_MAX_CONCURRENT_WAITS",
        "DOCUMENT_SUPPLEMENT_LISTING_POLL_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)
    return state


def _context():
    return DocumentSourceRequestContext(
        provider_groups=("WBStaff",), authorized_group_ids=("WB",),
        bearer_kind=ProviderBearerKind.CURATOR, curator_token="curator-token",
    )


GOOD_TABLE_MD5 = hashlib.md5(GOOD_TABLE).hexdigest()


def _chosen(provider_file_id, *, converted_md5=None, display_name=None):
    return ChosenSupplement(
        provider_file_id=provider_file_id,
        md5=f"md5-{provider_file_id}",
        converted_md5=converted_md5,
        display_name=display_name,
    )


async def _run(*, chosen, paper, last_answer_payload, clock, add=None, abc_answers=None,
               conversion_requested=True, login_expires_in=24 * 3600, on_read=None,
               read_error=None, failing_reads=None, sleep=None, context=None):
    from agr_ai_curation_alliance.document_sources.abc_literature import _map_conversion_result

    client = _AbcClient(abc_answers, paper=paper, on_read=on_read, read_error=read_error,
                        failing_reads=failing_reads)
    provider = provider_from_fake(client)
    # The main phase's last answer, mapped as the adapter maps it; the stage's own
    # polls get the scripted answers.
    last_answer = (
        _map_conversion_result(last_answer_payload, provider="abc_literature")
        if last_answer_payload is not None
        else None
    )
    outcomes = _Outcomes()
    add = add or _Add()
    await stage.run_supplement_stage(
        job_id=JOB_ID, document_id=DOCUMENT_ID, owner_user_id=7,
        plan=SupplementPlan(
            chosen=tuple(chosen), request_context=context or _context(),
            login_expires_at=clock() + login_expires_in, owner_user_id=7,
        ),
        conversion_requested=conversion_requested, last_answer=last_answer,
        main_ready_at=clock(), provider=provider, weaviate_client=object(),
        outcomes=outcomes, list_files=paper.list_files,
        list_files_from_artifacts=paper.list_files_from_artifacts, add_files=add,
        clock=clock, sleep=sleep or clock.sleep,
    )
    return client, add, outcomes


def _final_entries(job):
    return {
        entry["provider_file_id"]: (entry["state"], entry["reason_code"])
        for entry in job.progress[-1]["metadata"]["supplements"]
    }


RUNNING = _answer_payload("running", [_job_entry(30, "supplement", "pending")])


@pytest.mark.asyncio
async def test_converted_supplements_are_added_without_asking_abc(job):
    clock = _Clock()
    paper = _Paper([_abc_show_all_main_pdf(10, "WB"), *_pair(20, "WB")], payloads={"21": GOOD_TABLE})

    client, add, outcomes = await _run(
        chosen=[_chosen("20", converted_md5=GOOD_TABLE_MD5)], paper=paper,
        last_answer_payload=None, conversion_requested=False, clock=clock,
    )

    assert (client.requests, client.reads) == (0, 0)
    assert [file.provider_file_id for file in add.calls[0]] == ["20"]
    assert outcomes.ended == ["completed"]
    assert _final_entries(job) == {"20": ("added", None)}


@pytest.mark.asyncio
async def test_a_file_abc_converts_during_the_wait_joins_without_any_conversion_request(job):
    """Chris, 23 Nov 2026 (card 31): the wait only reads ABC's listing."""

    clock = _Clock()
    paper = _Paper([_abc_show_all_main_pdf(10, "WB"), _unconverted(30, "xlsx")])

    def _abc_stores_the_converted_file():
        paper.rows = [_abc_show_all_main_pdf(10, "WB"), *_pair(30, "WB")]
        paper.payloads = {"31": GOOD_TABLE}

    client, add, outcomes = await _run(
        chosen=[_chosen("30")], paper=paper, last_answer_payload=RUNNING, clock=clock,
        on_read={3: _abc_stores_the_converted_file},
    )

    assert client.requests == 0  # the stage never sends the conversion request
    assert client.reads == 3  # one show_all read per poll until the converted row appears
    assert paper.listings == 1  # only the stage's first full listing
    assert paper.reused_listings == 1  # built from the poll's rows and resolved reference
    assert clock.now == 1_000_000.0 + 3 * 60  # DOCUMENT_SUPPLEMENT_LISTING_POLL_SECONDS default
    assert [(file.provider_file_id, file.converted_md5) for file in add.calls[0]] == [("30", GOOD_TABLE_MD5)]
    assert add.listings[0].reference is not None
    assert outcomes.ended == ["completed"]
    assert _final_entries(job) == {"30": ("added", None)}


@pytest.mark.asyncio
async def test_the_full_listing_runs_only_when_a_chosen_file_gets_a_new_converted_text(job):
    clock = _Clock()
    # 40 is converted but not chosen; 30 (chosen) is not converted yet.
    paper = _Paper([_abc_show_all_main_pdf(10, "WB"), *_pair(40, "WB"), _unconverted(30, "pdf")],
                   payloads={"41": GOOD_TABLE})

    def _abc_stores_30():
        paper.rows = [_abc_show_all_main_pdf(10, "WB"), *_pair(40, "WB"), *_pair(30, "WB")]
        paper.payloads = {"41": GOOD_TABLE, "31": GOOD_TABLE}

    client, add, _outcomes = await _run(
        chosen=[_chosen("30")], paper=paper, last_answer_payload=RUNNING, clock=clock,
        on_read={5: _abc_stores_30},
    )

    assert (client.requests, client.reads, paper.listings, paper.reused_listings) == (0, 5, 1, 1)
    assert [file.provider_file_id for file in add.calls[0]] == ["30"]


@pytest.mark.asyncio
async def test_a_paper_whose_supplements_abc_does_not_convert_reports_them_at_once(job):
    """The main phase already had ABC's final answer: classified once, nothing read again."""

    clock = _Clock()
    paper = _Paper([_abc_show_all_main_pdf(10, "WB"), _unconverted(60, "docx")])

    client, add, outcomes = await _run(
        chosen=[_chosen("60")], paper=paper,
        last_answer_payload=_answer_payload("converted", [_job_entry(10, "main", "success", converted_id=11)]),
        clock=clock,
    )

    assert (client.requests, client.reads) == (0, 0)
    assert add.calls == []
    assert outcomes.ended == ["completed"]
    assert _final_entries(job) == {"60": ("left_out", NOT_CONVERTED)}


@pytest.mark.asyncio
async def test_a_failure_in_the_main_phases_final_answer_is_still_reported_at_once(job):
    """The one exception to card 31's "failed files show as not converted in time":
    the main phase already holds ABC's final answer, so no request is needed."""

    clock = _Clock()
    paper = _Paper([_abc_show_all_main_pdf(10, "WB"), _unconverted(30, "pdf"), *_pair(50, "WB")],
                   payloads={"51": GOOD_TABLE})
    failed = _answer_payload("failed", [
        _job_entry(30, "supplement", "failed", error="Supplemental PDF exceeds 5 MB limit"),
        _job_entry(50, "supplement", "success", converted_id=51),
    ], error_message="1 file failed")

    client, add, _outcomes = await _run(
        chosen=[_chosen("30"), _chosen("50")], paper=paper, last_answer_payload=failed, clock=clock,
    )

    assert (client.requests, client.reads) == (0, 0)
    assert [file.provider_file_id for file in add.calls[0]] == ["50"]
    assert _final_entries(job) == {"30": ("left_out", FAILED), "50": ("added", None)}


@pytest.mark.asyncio
async def test_a_file_abc_fails_during_the_wait_ends_not_converted_in_time_at_the_limit(job):
    """Card 31: ABC failing a file mid-wait is not visible in its listing, so the file
    waits until the limit; the converted file still joins."""

    clock = _Clock()
    paper = _Paper([_abc_show_all_main_pdf(10, "WB"), *_pair(20, "WB"), _unconverted(30, "pdf")],
                   payloads={"21": GOOD_TABLE})

    client, add, outcomes = await _run(
        chosen=[_chosen("20", converted_md5=GOOD_TABLE_MD5), _chosen("30")],
        paper=paper, last_answer_payload=RUNNING, clock=clock,
    )

    assert client.requests == 0
    # 10,800 s at the 60 s listing poll; the last sleep ends at the limit, with no read.
    assert client.reads == 179
    assert clock.now == 1_000_000.0 + 10_800
    assert [file.provider_file_id for file in add.calls[0]] == ["20"]
    assert _final_entries(job) == {"20": ("added", None), "30": ("left_out", IN_TIME)}
    assert outcomes.ended == ["completed"]


@pytest.mark.asyncio
async def test_the_login_reserve_ends_the_wait_before_the_limit(job):
    clock = _Clock()
    paper = _Paper([_abc_show_all_main_pdf(10, "WB"), _unconverted(30, "pdf")])

    client, add, outcomes = await _run(
        chosen=[_chosen("30")], paper=paper, last_answer_payload=RUNNING,
        clock=clock, login_expires_in=900 + 600,  # 10 minutes of wait left before the reserve
    )

    assert (client.requests, client.reads) == (0, 9)  # 600 s at 60 s; none at the limit itself
    assert _final_entries(job) == {"30": ("left_out", IN_TIME)}
    assert job.progress[-1]["metadata"]["supplement_wait_ends_at"] == stage._iso(1_000_000.0 + 600)


def _http(status: int):
    from agr_ai_curation_alliance.literature.client import ABCLiteratureHTTPError

    return ABCLiteratureHTTPError(
        f"ABC Literature returned HTTP {status}", status_code=status,
        endpoint="/reference/referencefile/show_all/AGRKB:101",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "reads", "expected"), [
    # Any other ABC error: three failed reads in a row (DOCUMENT_SOURCE_LISTING_MAX_FAILED_READS)
    # end the wait; the waiting file is not converted in time; the converted one joins.
    (502, 3, {"20": ("added", None), "30": ("left_out", IN_TIME)}),
    # The login was refused: nothing more can be asked of ABC with it, the add included.
    (401, 1, {"20": ("left_out", "login_expired"), "30": ("left_out", "login_expired")}),
], ids=["abc_502", "login_expired"])
async def test_failed_reads_end_the_wait_after_the_tolerated_count_a_refused_login_at_once(
    job, status, reads, expected,
):
    clock = _Clock()
    paper = _Paper([_abc_show_all_main_pdf(10, "WB"), *_pair(20, "WB"), _unconverted(30, "pdf")],
                   payloads={"21": GOOD_TABLE})

    client, _add, outcomes = await _run(
        chosen=[_chosen("20", converted_md5=GOOD_TABLE_MD5), _chosen("30")], paper=paper,
        last_answer_payload=RUNNING, clock=clock, read_error=_http(status),
    )

    assert (client.requests, client.reads) == (0, reads)
    assert _final_entries(job) == expected
    assert outcomes.ended == ["completed"]


@pytest.mark.asyncio
async def test_a_cancel_during_the_wait_ends_the_job_cancelled_and_adds_nothing(job):
    clock = _Clock()
    paper = _Paper([_abc_show_all_main_pdf(10, "WB"), _unconverted(30, "pdf")])

    async def _sleep_then_cancel(seconds):
        await clock.sleep(seconds)
        job.cancel_requested = True

    client, add, outcomes = await _run(
        chosen=[_chosen("30")], paper=paper, last_answer_payload=RUNNING, clock=clock,
        sleep=_sleep_then_cancel,
    )

    assert (client.requests, client.reads) == (0, 0)
    assert add.calls == []
    assert outcomes.ended == ["cancelled"]
    assert _final_entries(job) == {"30": ("left_out", "import_cancelled")}


@pytest.mark.asyncio
async def test_a_wait_finalized_elsewhere_writes_nothing_more(job):
    """A dead-wait cancel finalized the job while this stage was still sleeping."""

    clock = _Clock()
    paper = _Paper([_abc_show_all_main_pdf(10, "WB"), _unconverted(30, "pdf")])

    async def _sleep_then_finalize(seconds):
        await clock.sleep(seconds)
        job.status = "cancelled"

    client, add, outcomes = await _run(
        chosen=[_chosen("30")], paper=paper, last_answer_payload=RUNNING, clock=clock,
        sleep=_sleep_then_finalize,
    )
    progress_after_start = len(job.progress)

    assert (client.requests, client.reads) == (0, 0)
    assert add.calls == []
    assert outcomes.ended == []
    assert progress_after_start == 2  # the two "waiting" records before the first sleep


@pytest.mark.asyncio
async def test_every_poll_records_activity_in_the_supplement_wait_stage(job):
    clock = _Clock()
    paper = _Paper([_abc_show_all_main_pdf(10, "WB"), _unconverted(30, "pdf")])

    client, _add, _outcomes = await _run(
        chosen=[_chosen("30")], paper=paper, last_answer_payload=RUNNING,
        clock=clock, login_expires_in=900 + 300,
    )

    waiting = [p for p in job.progress if p["stage"] == "waiting_for_supplements"]
    assert client.reads == 4  # 300 s at 60 s; none at the limit itself
    assert len(waiting) >= client.reads + 1


@pytest.mark.asyncio
async def test_the_jobs_panel_says_how_many_supplements_abc_is_converting(job):
    """Approved wording (Chris, 23 Nov 2026, card 39)."""

    clock = _Clock()
    paper = _Paper([_abc_show_all_main_pdf(10, "WB"), _unconverted(30, "pdf"), _unconverted(50, "pdf")])

    await _run(
        chosen=[_chosen("30"), _chosen("50")], paper=paper, last_answer_payload=RUNNING,
        clock=clock, login_expires_in=900 + 60,
    )

    messages = [p["message"] for p in job.progress if p["stage"] == "waiting_for_supplements"]
    assert messages[0] == (
        "Main paper ready. Waiting for ABC to convert 2 supplements; "
        "you can use the paper and leave this page."
    )


@pytest.mark.asyncio
async def test_the_jobs_panel_uses_the_singular_for_one_supplement(job):
    """Chris approved the singular form for one file (23 Nov 2026)."""

    clock = _Clock()
    paper = _Paper([_abc_show_all_main_pdf(10, "WB"), _unconverted(30, "pdf")])

    await _run(
        chosen=[_chosen("30")], paper=paper, last_answer_payload=RUNNING,
        clock=clock, login_expires_in=900 + 60,
    )

    messages = [p["message"] for p in job.progress if p["stage"] == "waiting_for_supplements"]
    assert messages[0] == (
        "Main paper ready. Waiting for ABC to convert 1 supplement; "
        "you can use the paper and leave this page."
    )


# --- The stage as its own task


class _StageOutcomes(_Outcomes):
    pass


@pytest.mark.asyncio
async def test_a_failing_stage_ends_the_job_by_name_and_is_reported(job, monkeypatch):
    reported = []
    monkeypatch.setattr(stage, "report_runtime_exception", lambda exc, **kwargs: reported.append(kwargs))
    outcomes = _Outcomes()

    async def _boom():
        raise KeyError("made-up bug")

    task = stage.start_supplement_stage(job_id=JOB_ID, run=_boom, outcomes=outcomes)
    await task

    assert outcomes.ended == [f"failed: {stage.SUPPLEMENT_STAGE_FAILED_MESSAGE}"]
    assert reported and reported[0]["operation"] == "supplement_stage"
    assert task not in stage._stage_tasks


@pytest.mark.asyncio
async def test_waits_beyond_the_limit_queue_and_keep_recording_activity(job, monkeypatch):
    monkeypatch.setenv("DOCUMENT_SUPPLEMENT_MAX_CONCURRENT_WAITS", "1")
    monkeypatch.setattr(stage, "get_document_supplement_listing_poll_seconds", lambda: 0.01)
    release = asyncio.Event()
    order = []

    async def _first():
        order.append("first starts")
        await release.wait()
        order.append("first ends")

    async def _second():
        order.append("second starts")

    first = stage.start_supplement_stage(job_id="job-1", run=_first, outcomes=_Outcomes())
    second = stage.start_supplement_stage(job_id="job-2", run=_second, outcomes=_Outcomes())
    await asyncio.sleep(0.05)
    queued_heartbeats = [p for p in job.progress if p["job_id"] == "job-2"]
    release.set()
    await asyncio.gather(first, second)

    assert order == ["first starts", "first ends", "second starts"]
    assert queued_heartbeats and queued_heartbeats[0]["stage"] == "waiting_for_supplements"


@pytest.mark.asyncio
async def test_shutdown_cancels_running_stages_cleanly(job):
    started = asyncio.Event()
    outcomes = _Outcomes()

    async def _long():
        started.set()
        await asyncio.sleep(3600)

    task = stage.start_supplement_stage(job_id=JOB_ID, run=_long, outcomes=outcomes)
    await started.wait()
    await stage.stop_supplement_stages()

    assert task.cancelled()
    assert outcomes.ended == []  # the job stays active; the stale sweep ends it later
    assert stage._stage_tasks == set()


@pytest.mark.asyncio
async def test_the_stage_task_inherits_the_import_cost_context(job):
    from src.lib.observability.cost_context import cost_scope, current_cost_context, execution_context

    seen = []

    async def _look():
        seen.append(current_cost_context().get("job_id"))

    with cost_scope(execution_context(activity="background", document_id=DOCUMENT_ID, user_id="u",
                                      run_id=JOB_ID, job_id=JOB_ID)):
        task = stage.start_supplement_stage(job_id=JOB_ID, run=_look, outcomes=_Outcomes())
    await task

    assert seen == [JOB_ID]


@pytest.mark.asyncio
async def test_the_stage_failure_report_carries_no_paper_or_curator_content(job, monkeypatch):
    """The Sentry contract: a fixed, chain-free message and a type-name tag only."""

    reported = []
    monkeypatch.setattr(
        stage, "report_runtime_exception",
        lambda exc, **kwargs: reported.append((exc, kwargs)),
    )
    secret_title = "Private paper title about made-up-gene-1"
    secret_token = "curator-token-" + "0123456789"

    async def _boom():
        raise RuntimeError(f"{secret_title} {secret_token}")

    await stage.start_supplement_stage(job_id=JOB_ID, run=_boom, outcomes=_Outcomes())

    (exc, kwargs), = reported
    assert str(exc) == "Supplement stage failed"
    assert exc.__cause__ is None and exc.__context__ is None
    assert kwargs["tags"] == {"failure_type": "RuntimeError"}
    flattened = repr((str(exc), kwargs))
    assert secret_title not in flattened and secret_token not in flattened



# --- Review fixes: endings that ask ABC nothing, truthful entries, queue order


def _plain_provider(answers=None):
    client = _AbcClient(answers or [_answer_payload("converted", [])])
    return client, provider_from_fake(client)


async def _stage(job, *, chosen, paper, main_ready_at, clock, provider, add=None,
                 conversion_requested=False, last_answer=None, login_expires_at=None):
    outcomes, add = _Outcomes(), add or _Add()
    await stage.run_supplement_stage(
        job_id=JOB_ID, document_id=DOCUMENT_ID, owner_user_id=7,
        plan=SupplementPlan(chosen=tuple(chosen), request_context=_context(),
                            login_expires_at=login_expires_at, owner_user_id=7),
        conversion_requested=conversion_requested, last_answer=last_answer,
        main_ready_at=main_ready_at, provider=provider, weaviate_client=object(),
        outcomes=outcomes, list_files=paper.list_files,
        list_files_from_artifacts=paper.list_files_from_artifacts, add_files=add,
        clock=clock, sleep=clock.sleep,
    )
    return add, outcomes


@pytest.mark.asyncio
async def test_a_stage_whose_wait_time_is_used_up_adds_only_what_abc_already_converted(job):
    """Team lead's ruling of 23 Nov: one read-only listing, no conversion request, no
    polling; converted files are added, the rest end ``wait_limit_reached``."""

    clock = _Clock()
    # 20 was converted at import; 40 has been converted by ABC since; 30 is still not converted.
    paper = _Paper(
        [_abc_show_all_main_pdf(10, "WB"), *_pair(20, "WB"), *_pair(40, "WB"), _unconverted(30, "pdf")],
        payloads={"21": GOOD_TABLE, "41": GOOD_TABLE},
    )
    client, provider = _plain_provider()

    add, outcomes = await _stage(
        job, chosen=[_chosen("20", converted_md5=GOOD_TABLE_MD5), _chosen("30"), _chosen("40")], paper=paper,
        main_ready_at=clock() - 10_801, clock=clock, provider=provider,
        conversion_requested=True, last_answer=None, login_expires_at=clock() + 86400,
    )

    assert client.requests == 0  # no conversion request, no polling
    assert paper.listings == 1
    assert sorted(file.provider_file_id for file in add.calls[0]) == ["20", "40"]
    assert _final_entries(job) == {
        "20": ("added", None), "30": ("left_out", "wait_limit_reached"), "40": ("added", None),
    }
    assert outcomes.ended == ["completed"]


@pytest.mark.asyncio
async def test_a_stage_whose_login_is_inside_the_reserve_ends_at_once_asking_abc_nothing(job):
    clock = _Clock()
    paper = _Paper([_abc_show_all_main_pdf(10, "WB"), *_pair(20, "WB")], payloads={"21": GOOD_TABLE})
    client, provider = _plain_provider()

    add, outcomes = await _stage(
        job, chosen=[_chosen("20", converted_md5=GOOD_TABLE_MD5)], paper=paper, main_ready_at=clock(),
        clock=clock, provider=provider, login_expires_at=clock() + 300,  # inside the 900 s reserve
    )

    assert (paper.listings, client.requests, add.calls) == (0, 0, [])
    assert _final_entries(job) == {"20": ("left_out", "login_expired")}
    assert outcomes.ended == ["completed"]


@pytest.mark.asyncio
async def test_an_answer_abc_does_not_send_today_ends_the_wait_with_its_own_reason(job):
    clock = _Clock()
    unknown = _answer_payload("failed", [{**_job_entry(30, "supplement", "pending"), "status": "skipped"}])
    paper = _Paper([_abc_show_all_main_pdf(10, "WB"), _unconverted(30, "pdf")])

    client, _add, _outcomes = await _run(
        chosen=[_chosen("30")], paper=paper, last_answer_payload=unknown, clock=clock,
    )

    assert (client.requests, client.reads) == (0, 0)
    assert _final_entries(job) == {"30": ("left_out", "provider_answer_unknown")}


class _RaisingAdd(_Add):
    def __init__(self, error):
        super().__init__()
        self.error = error

    async def __call__(self, *args, stage_checkpoint, **kwargs):
        await stage_checkpoint("checking_files")
        raise self.error


@pytest.mark.asyncio
@pytest.mark.parametrize(("error", "reason"), [
    (DocumentSourceLoginExpired("ABC Literature artifact listing failed"), "login_expired"),
    (DocumentSourceUnavailable("ABC Literature or its file storage could not be reached"), "source_unavailable"),
], ids=["login_expired", "abc_unavailable"])
async def test_an_abc_failure_inside_the_add_ends_with_a_reason_not_a_stage_failure(job, error, reason):
    clock = _Clock()
    paper = _Paper([_abc_show_all_main_pdf(10, "WB"), *_pair(20, "WB")], payloads={"21": GOOD_TABLE})
    client, provider = _plain_provider()

    _add, outcomes = await _stage(
        job, chosen=[_chosen("20", converted_md5=GOOD_TABLE_MD5)], paper=paper, main_ready_at=clock(),
        clock=clock, provider=provider, add=_RaisingAdd(error),
    )

    assert _final_entries(job) == {"20": ("left_out", reason)}
    assert outcomes.ended == ["completed"]


@pytest.mark.asyncio
async def test_a_cancel_during_the_add_leaves_no_entry_adding(job):
    clock = _Clock()
    paper = _Paper([_abc_show_all_main_pdf(10, "WB"), *_pair(20, "WB")], payloads={"21": GOOD_TABLE})
    client, provider = _plain_provider()

    class _CancelledAdd(_Add):
        async def __call__(self, *args, stage_checkpoint, **kwargs):
            job.cancel_requested = True
            await stage_checkpoint("saving_files")

    _add, outcomes = await _stage(
        job, chosen=[_chosen("20", converted_md5=GOOD_TABLE_MD5)], paper=paper, main_ready_at=clock(),
        clock=clock, provider=provider, add=_CancelledAdd(),
    )

    assert _final_entries(job) == {"20": ("left_out", "import_cancelled")}
    assert outcomes.ended == ["cancelled"]


@pytest.mark.asyncio
async def test_an_unexpected_failure_leaves_every_open_file_out_by_name_then_reaches_the_guard(job):
    clock = _Clock()
    paper = _Paper([_abc_show_all_main_pdf(10, "WB"), *_pair(20, "WB")], payloads={"21": GOOD_TABLE})
    client, provider = _plain_provider()

    with pytest.raises(KeyError):
        await _stage(
            job, chosen=[_chosen("20", converted_md5=GOOD_TABLE_MD5)], paper=paper, main_ready_at=clock(),
            clock=clock, provider=provider, add=_RaisingAdd(KeyError("made-up bug")),
        )

    assert _final_entries(job) == {"20": ("left_out", "supplement_stage_failed")}


@pytest.mark.asyncio
async def test_an_add_refusal_keeps_its_measured_and_limit_values(job):
    clock = _Clock()
    paper = _Paper([_abc_show_all_main_pdf(10, "WB"), *_pair(20, "WB")], payloads={"21": GOOD_TABLE})
    client, provider = _plain_provider()

    class _RefusingAdd(_Add):
        async def __call__(self, *args, files, **kwargs):
            return AddResult(files=(FileOutcome(
                provider_file_id="20", outcome="left_out", reason_code=FileReason.TOO_LARGE,
                measured_value=2_000_000, limit_value=1_000_000,
            ),))

    async def _call(document_id, owner_user_id, request_context, files, **kwargs):
        return await _RefusingAdd()(files=files)

    _add, outcomes = await _stage(
        job, chosen=[_chosen("20", converted_md5=GOOD_TABLE_MD5)], paper=paper, main_ready_at=clock(),
        clock=clock, provider=provider, add=_call,
    )

    (entry,) = job.progress[-1]["metadata"]["supplements"]
    assert (entry["state"], entry["reason_code"], entry["measured_value"], entry["limit_value"]) == (
        "left_out", "too_large", 2_000_000, 1_000_000,
    )


@pytest.mark.asyncio
async def test_queued_stages_are_served_in_the_order_they_arrived(job, monkeypatch):
    monkeypatch.setenv("DOCUMENT_SUPPLEMENT_MAX_CONCURRENT_WAITS", "1")
    monkeypatch.setattr(stage, "get_document_supplement_listing_poll_seconds", lambda: 0.01)
    release = asyncio.Event()
    order = []

    def _run_named(name, wait_for=None):
        async def _run():
            order.append(name)
            if wait_for is not None:
                await wait_for.wait()
        return _run

    tasks = [stage.start_supplement_stage(job_id="job-first", run=_run_named("first", release), outcomes=_Outcomes())]
    await asyncio.sleep(0)
    for name in ("second", "third", "fourth"):
        tasks.append(stage.start_supplement_stage(job_id=f"job-{name}", run=_run_named(name), outcomes=_Outcomes()))
        await asyncio.sleep(0.03)  # several heartbeats each while queued
    release.set()
    await asyncio.wait_for(asyncio.gather(*tasks), timeout=10)

    assert order == ["first", "second", "third", "fourth"]



@pytest.mark.asyncio
async def test_a_stage_ended_by_a_delete_mid_listing_is_not_a_stage_failure(job, monkeypatch):
    """A delete cancels the stage's job; the listing then fails on the missing paper."""

    reported = []
    monkeypatch.setattr(stage, "report_runtime_exception", lambda exc, **kwargs: reported.append(kwargs))
    clock = _Clock()
    client, provider = _plain_provider()

    class _DeletedPaper:
        listings = 0

        async def list_files(self, document_id, *, owner_user_id, provider, context):
            job.status = "cancelled"  # the delete ended the stage's job first
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Document not found")

    outcomes = _Outcomes()
    task = stage.start_supplement_stage(
        job_id=JOB_ID,
        run=lambda: stage.run_supplement_stage(
            job_id=JOB_ID, document_id=DOCUMENT_ID, owner_user_id=7,
            plan=SupplementPlan(chosen=(_chosen("20", converted_md5=GOOD_TABLE_MD5),), request_context=_context(),
                                login_expires_at=None, owner_user_id=7),
            conversion_requested=False, last_answer=None, main_ready_at=clock(), provider=provider,
            weaviate_client=object(), outcomes=outcomes, list_files=_DeletedPaper().list_files,
            list_files_from_artifacts=_DeletedPaper().list_files,
            add_files=_Add(), clock=clock, sleep=clock.sleep,
        ),
        outcomes=outcomes,
    )
    await asyncio.wait_for(task, timeout=10)

    assert reported == []
    assert outcomes.ended == []



@pytest.mark.asyncio
async def test_a_failed_read_is_tried_again_at_the_next_interval(job):
    """Owner, 23 Nov 2026: the listing is a pure read, so up to two failed reads in a
    row are tolerated; the file ABC converts meanwhile still joins."""

    clock = _Clock()
    paper = _Paper([_abc_show_all_main_pdf(10, "WB"), _unconverted(30, "pdf")])

    def _abc_stores_30():
        paper.rows = [_abc_show_all_main_pdf(10, "WB"), *_pair(30, "WB")]
        paper.payloads = {"31": GOOD_TABLE}

    client, add, _outcomes = await _run(
        chosen=[_chosen("30")], paper=paper, last_answer_payload=RUNNING, clock=clock,
        read_error=_http(502), failing_reads={1, 2}, on_read={3: _abc_stores_30},
    )

    assert (client.requests, client.reads) == (0, 3)
    assert [file.provider_file_id for file in add.calls[0]] == ["30"]
    assert _final_entries(job) == {"30": ("added", None)}


@pytest.mark.asyncio
async def test_a_good_read_resets_the_count_of_failed_reads(job):
    clock = _Clock()
    paper = _Paper([_abc_show_all_main_pdf(10, "WB"), _unconverted(30, "pdf")])

    def _abc_stores_30():
        paper.rows = [_abc_show_all_main_pdf(10, "WB"), *_pair(30, "WB")]
        paper.payloads = {"31": GOOD_TABLE}

    client, add, _outcomes = await _run(
        chosen=[_chosen("30")], paper=paper, last_answer_payload=RUNNING, clock=clock,
        read_error=_http(502), failing_reads={1, 2, 4, 5, 7, 8}, on_read={9: _abc_stores_30},
    )

    assert client.reads == 9  # never three failed reads in a row
    assert _final_entries(job) == {"30": ("added", None)}


# --- ALL-1678 + ALL-1751: the polling cap and total HTTP requests per supplement wait, proven
# at the HTTP boundary: the real Alliance ABC adapter and its HTTP client over an
# ``httpx.MockTransport`` counting every request, the real file-listing service (fresh start
# listing + reused-rows poll listings), and the real add (``add_files_to_paper_with_listing``)
# driven for real against fake storage.


def _make_sample_pdf(pages: int = 1, *, title: str = "Made-up supplement") -> bytes:
    import io

    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    writer.add_metadata({"/Title": title})
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


class _HttpAbc:
    """ABC over HTTP (``httpx.MockTransport``), counting every request and its phase."""

    def __init__(self, *, ids, extension, converts_at, fails):
        self.ids = list(ids)
        self.extension = extension
        self.converts_at = converts_at
        self.fails = fails  # fails(kind, n, in_listing) -> bool | int (HTTP status code)
        self.converted: list[int] = []
        self.calls: list[tuple[str, bool]] = []
        self.in_listing = False
        self.show_reference_payload = _abc_reference_record()
        # Distinct PDF bytes per supplement so MD5 deduplication keeps every file distinct.
        self.pdf_bytes = (
            {file_id: _make_sample_pdf(1, title=f"Made-up S{file_id}") for file_id in self.ids}
            if extension == "pdf"
            else {}
        )
        self.pdf_md5 = {file_id: hashlib.md5(data).hexdigest() for file_id, data in self.pdf_bytes.items()}

    def _rows(self):
        rows = [_abc_show_all_main_pdf(10, "WB")]
        for file_id in self.ids:
            md5 = self.pdf_md5.get(file_id)
            if file_id in self.converted:
                rows.extend(_pair(file_id, "WB", extension=self.extension, md5=md5, content=GOOD_TABLE))
            else:
                row = _unconverted(file_id, self.extension)
                if md5 is not None:
                    row["md5sum"] = md5
                rows.append(row)
        return rows

    def _failure_status(self, kind: str) -> int | None:
        self.calls.append((kind, self.in_listing))
        count = sum(1 for call_kind, _ in self.calls if call_kind == kind)
        failed = self.fails(kind, count, self.in_listing)
        if isinstance(failed, bool):
            return 502 if failed else None
        return int(failed) if failed else None

    def handle(self, request):
        from urllib.parse import unquote

        import httpx

        path = unquote(request.url.path)
        if path == "/reference/AGRKB:101":
            status = self._failure_status("reference")
            if status is not None:
                return httpx.Response(status, json={"detail": "made-up failure"})
            return httpx.Response(200, json=self.show_reference_payload)
        if path == "/reference/referencefile/show_all/AGRKB:101":
            if not self.in_listing:
                new = self.converts_at(self.polls + 1)
                if new is not None and 0 <= new < len(self.ids) and self.ids[new] not in self.converted:
                    self.converted.append(self.ids[new])
            status = self._failure_status("show_all")
            if status is not None:
                return httpx.Response(status, json={"detail": "made-up failure"})
            return httpx.Response(200, json=self._rows())
        if path.startswith("/reference/referencefile/download_file/"):
            status = self._failure_status("download")
            if status is not None:
                return httpx.Response(status, json={"detail": "made-up failure"})
            artifact_id = int(path.rsplit("/", 1)[1])
            body = self.pdf_bytes.get(artifact_id, GOOD_TABLE)
            return httpx.Response(200, stream=httpx.ByteStream(body), headers={"content-length": str(len(body))})
        if path == "/reference/referencefile/conversion_request/AGRKB:101":
            self._failure_status("conversion")
            return httpx.Response(202, json=_answer_payload("running", []))
        return httpx.Response(404, json={"detail": "Not Found"})

    def provider(self):
        import httpx

        from agr_ai_curation_alliance.document_sources.abc_literature import (
            ABCLiteratureDocumentSourceProvider,
        )
        from agr_ai_curation_alliance.literature.client import (
            ABCLiteratureClient,
            ABCLiteratureClientConfig,
        )

        client = ABCLiteratureClient(
            ABCLiteratureClientConfig(base_url="https://abc.example.org"),
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(self.handle)),
        )
        return ABCLiteratureDocumentSourceProvider(client)

    @property
    def polls(self) -> int:
        return sum(1 for kind, in_listing in self.calls if kind == "show_all" and not in_listing)


def _install_fake_add_storage(monkeypatch, store, tmp_path):
    """In-memory storage boundary so the real ``add_files_to_paper_with_listing`` runs."""
    from src.lib.document_sources import file_listing_service, ingestion, supplement_add
    from tests.unit.lib.document_sources.test_file_listing import _limits

    inserted = []
    ready = {}
    saved_pdfs = {}
    next_index = [30]
    spool = tmp_path / ".add_spool"
    spool.mkdir(exist_ok=True)

    monkeypatch.setattr(file_listing_service, "_stores", lambda _provider: (store.load, store.save))
    monkeypatch.setattr(file_listing_service, "current_file_check_limits", _limits)
    monkeypatch.setattr(
        file_listing_service, "_paper_lookup",
        lambda *_a, **_k: file_listing_service._PaperSource(
            provider="abc_literature", reference_id="101", reference_curie="AGRKB:101",
            file_ids=("10",), provider_observations={},
        ),
    )
    monkeypatch.setattr(supplement_add, "current_file_check_limits", _limits)
    monkeypatch.setattr(
        supplement_add, "_paper_state",
        lambda *_a, **_k: supplement_add._PaperState(
            owner_subject="owner-subject", status="completed", file_count=1, ready_text_bytes=1_000,
            next_tab_order=1, leftovers=(), source_provider="abc_literature",
            source_provider_reference_id="101", source_provider_reference_curie="AGRKB:101",
            live_provider_file_ids=frozenset({"10"}),
        ),
    )
    monkeypatch.setattr(supplement_add, "_spool_directory", lambda: spool)

    def _save_pdf(spooled, relative):
        spooled.seek(0)
        saved_pdfs[relative] = spooled.read()

    monkeypatch.setattr(supplement_add, "_save_supplement_pdf", _save_pdf)

    async def _save_md(*, markdown, file_id, user_id):
        return f"{user_id}/source_markdown/{file_id}.md"

    async def _save_json(*, elements, file_id, user_id):
        return f"{user_id}/processed_json/{file_id}.json"

    monkeypatch.setattr(ingestion, "_save_source_markdown", _save_md)
    monkeypatch.setattr(ingestion, "_save_processed_json", _save_json)
    def _fake_insert(_doc, rows, _copy_ids=None, _text_bytes=None):
        inserted.extend(rows)
        return {}

    monkeypatch.setattr(supplement_add, "_insert_rows", _fake_insert)

    def _reserve(_document_id, file_id, count):
        start = next_index[0]
        next_index[0] += count
        row = next(row for row in inserted if row.id == file_id)
        row.chunk_index_start, row.chunk_index_end = start, start + count - 1
        return start

    async def _store_pieces(*_args, **_kwargs):
        return None

    async def _after_add(*_args, **_kwargs):
        return None

    monkeypatch.setattr("src.lib.document_files.service.reserve_file_chunk_range", _reserve)
    monkeypatch.setattr("src.lib.pipeline.store.store_added_file_pieces", _store_pieces)
    monkeypatch.setattr("src.lib.document_files.service.mark_files_ready", lambda items: ready.update(items))
    monkeypatch.setattr(supplement_add, "_after_add", _after_add)
    monkeypatch.setattr(
        supplement_add,
        "_rollback_files",
        lambda _doc, ids: [supplement_add._Leftover.from_row(row) for row in inserted if row.id in ids],
    )
    monkeypatch.setattr(supplement_add, "_remove_stored_files", lambda _paths, _subject: None)
    monkeypatch.setattr(supplement_add, "_delete_rows", lambda _doc, _ids: None)

    async def _delete_pieces(*_args, **_kwargs):
        return None

    monkeypatch.setattr(supplement_add, "_delete_pieces", _delete_pieces)
    return inserted, saved_pdfs


async def _one_supplement_wait(
    monkeypatch, job, *, wait, poll, files, converts_at, fails=lambda *_: False,
    extension="xlsx", real_add=False, tmp_path=None,
):
    """One supplement wait on the fake clock over ``httpx.MockTransport`` with the real
    listing service and (when ``real_add``) the real add; returns ``(polls, total, client)``.

    ``converts_at(n)`` is the index of the file ABC has converted by the wait's read ``n``.
    """
    from agr_ai_curation_alliance.document_sources.abc_literature import _map_conversion_result
    from src.lib.document_sources import file_listing_service, supplement_add
    from tests.unit.lib.document_sources.test_file_listing import _Store, _limits

    monkeypatch.setenv("DOCUMENT_SUPPLEMENT_WAIT_TIMEOUT_SECONDS", str(wait))
    monkeypatch.setenv("DOCUMENT_SUPPLEMENT_LISTING_POLL_SECONDS", str(poll))
    clock = _Clock()
    ids = [30 + 2 * index for index in range(files)]
    client = _HttpAbc(ids=ids, extension=extension, converts_at=converts_at, fails=fails)
    provider = client.provider()
    store = _Store()

    if real_add:
        assert tmp_path is not None
        _install_fake_add_storage(monkeypatch, store, tmp_path)
    else:
        monkeypatch.setattr(file_listing_service, "_stores", lambda _provider: (store.load, store.save))
        monkeypatch.setattr(file_listing_service, "current_file_check_limits", _limits)
        monkeypatch.setattr(
            file_listing_service, "_paper_lookup",
            lambda *_a, **_k: file_listing_service._PaperSource(
                provider="abc_literature", reference_id="101", reference_curie="AGRKB:101",
                file_ids=("10",), provider_observations={},
            ),
        )

    async def _list_files(document_id, *, owner_user_id, provider, context):
        client.in_listing = True
        try:
            return await file_listing_service.list_files_for_paper(
                document_id, owner_user_id=owner_user_id, provider=provider, context=context,
            )
        finally:
            client.in_listing = False

    async def _list_files_from_artifacts(
        document_id, *, owner_user_id, provider, context, reference, artifacts, check_unchecked=True,
    ):
        client.in_listing = True
        try:
            return await file_listing_service.list_files_for_paper_from_artifacts(
                document_id, owner_user_id=owner_user_id, provider=provider, context=context,
                reference=reference, artifacts=artifacts, check_unchecked=check_unchecked,
            )
        finally:
            client.in_listing = False

    chosen = tuple(
        ChosenSupplement(provider_file_id=str(i), md5=client.pdf_md5.get(i, f"md5-{i}"), converted_md5=None)
        for i in ids
    )
    add_fn = supplement_add.add_files_to_paper_with_listing if real_add else _Add()
    try:
        await stage.run_supplement_stage(
            job_id=JOB_ID, document_id=DOCUMENT_ID, owner_user_id=7,
            plan=SupplementPlan(chosen=chosen, request_context=_context(),
                                login_expires_at=clock() + 86400, owner_user_id=7),
            conversion_requested=True, last_answer=_map_conversion_result(RUNNING, provider="abc_literature"),
            main_ready_at=clock(), provider=provider, weaviate_client=object(), outcomes=_Outcomes(),
            list_files=_list_files, list_files_from_artifacts=_list_files_from_artifacts,
            add_files=add_fn, clock=clock, sleep=clock.sleep,
        )
    finally:
        await provider.aclose()
    assert not any(kind == "conversion" for kind, _ in client.calls)  # never asks ABC to convert
    return client.polls, len(client.calls), client


@pytest.mark.asyncio
async def test_at_the_defaults_a_supplement_wait_makes_exactly_its_cap_bound(job, monkeypatch):
    """ABC never converts: 179 reads in 10,800 s at 60 s. The stage's first listing (a resolve
    and a show_all) comes on top."""
    from src.lib.openai_agents.config import supplement_wait_max_polls

    polls, total, client = await _one_supplement_wait(
        monkeypatch, job, wait=10800, poll=60, files=1, converts_at=lambda n: None,
    )

    assert polls == supplement_wait_max_polls(wait_seconds=10800, listing_poll_seconds=60) == 179
    assert total == 181
    kinds = [kind for kind, _ in client.calls]
    assert (kinds.count("reference"), kinds.count("show_all"), kinds.count("download")) == (1, 180, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("extension", "expected_downloads", "expected_total"),
    [
        # 19 PDF supplements: 19 check downloads + 19 add text downloads + 19 add PDF downloads = 57.
        # Plus 1 initial GET /reference + 1 initial show_all + 179 poll show_all = 238 total
        # (236 after the stage's initial 2-request listing).
        ("pdf", 57, 238),
        # 19 Office supplements: 19 check downloads + 19 add text downloads = 38. Total = 219.
        ("xlsx", 38, 219),
    ],
    ids=["19_pdf_supplements", "19_office_supplements"],
)
async def test_worst_case_at_defaults_with_real_add_meets_all_1751_request_budget(
    job, monkeypatch, tmp_path, extension, expected_downloads, expected_total,
):
    """ALL-1751 proof at the HTTP boundary with the real add: 10,800 s / 60 s, 19 files
    converting across the full wait (18 on polls 1..18 and the 19th on poll 179)."""
    from src.lib.openai_agents.config import supplement_wait_max_polls

    def _converts_at(n: int) -> int | None:
        if 1 <= n <= 18:
            return n - 1
        if n == 179:
            return 18
        return None

    polls, total, client = await _one_supplement_wait(
        monkeypatch, job, wait=10800, poll=60, files=19, converts_at=_converts_at,
        extension=extension, real_add=True, tmp_path=tmp_path,
    )

    assert polls == supplement_wait_max_polls(wait_seconds=10800, listing_poll_seconds=60) == 179
    kinds = [kind for kind, _ in client.calls]
    assert kinds.count("reference") == 1
    assert kinds.count("show_all") == 180  # 1 initial + 179 polls; zero on reused listings or add
    assert kinds.count("download") == expected_downloads
    assert total == expected_total
    final = _final_entries(job)
    assert len(final) == 19 and all(value == ("added", None) for value in final.values())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("poll", "expected_polls", "expected_total"),
    [
        # Defaults (60 s): 179 intervals x 1 request + 2 initial + 1 check + 1 add = 183 (vs 539 before).
        (60, 179, 183),
        # Cap edge (26.94 s): 400 intervals x 1 request + 2 initial + 1 check + 1 add = 404 (vs 1,202 before).
        (26.94, 400, 404),
    ],
    ids=["defaults_60s", "cap_edge_26_94s"],
)
async def test_alternating_failure_costs_one_request_per_interval_instead_of_three(
    job, monkeypatch, tmp_path, poll, expected_polls, expected_total,
):
    """ALL-1751: because the full listing reuses the cheap read's rows, alternating good and
    failed show_all reads costs 1 request per interval (never 3) and the converted file still joins."""
    from src.lib.openai_agents.config import supplement_wait_max_polls

    polls, total, client = await _one_supplement_wait(
        monkeypatch, job, wait=10800, poll=poll, files=19,
        converts_at=lambda n: 0 if n == 1 else None,
        fails=lambda kind, n, in_listing: kind == "show_all" and not in_listing and n > 2 and n % 2 == 1,
        real_add=True, tmp_path=tmp_path,
    )

    assert polls == supplement_wait_max_polls(wait_seconds=10800, listing_poll_seconds=poll) == expected_polls
    kinds = [kind for kind, _ in client.calls]
    assert kinds.count("reference") == 1
    assert kinds.count("show_all") == expected_polls + 1
    assert kinds.count("download") == 2  # 1 check download + 1 add download for file 0
    assert total == expected_total
    assert _final_entries(job)["30"] == ("added", None)


@pytest.mark.asyncio
async def test_files_converting_one_per_read_reuse_the_listing_and_add_sends_only_downloads(
    job, monkeypatch, tmp_path,
):
    """610 s at 60 s: ten reads, a file converted before each, real add driven for all ten."""
    from src.lib.openai_agents.config import supplement_wait_max_polls

    polls, total, client = await _one_supplement_wait(
        monkeypatch, job, wait=610, poll=60, files=19, converts_at=lambda n: n - 1,
        real_add=True, tmp_path=tmp_path,
    )

    assert polls == supplement_wait_max_polls(wait_seconds=610, listing_poll_seconds=60) == 10
    kinds = [kind for kind, _ in client.calls]
    # 1 initial resolve + 1 initial show_all + 10 polls + 10 check downloads + 10 add downloads = 32
    # (was 42 before even without running the add).
    assert (kinds.count("reference"), kinds.count("show_all"), kinds.count("download"), total) == (1, 11, 20, 32)
    final = _final_entries(job)
    assert sum(1 for state, _ in final.values() if state == "added") == 10


@pytest.mark.asyncio
async def test_failed_read_tolerance_applies_to_reused_listing_errors(job, monkeypatch):
    """HTTP 500 on a check download raises DocumentSourceError inside the reused listing and
    is retried up to DOCUMENT_SOURCE_LISTING_MAX_FAILED_READS (default 3) without extra show_all."""
    polls, total, client = await _one_supplement_wait(
        monkeypatch, job, wait=600, poll=60, files=1,
        converts_at=lambda n: 0 if n == 1 else None,
        fails=lambda kind, n, _in_listing: 500 if kind == "download" and n <= 2 else False,
    )

    assert polls == 3
    kinds = [kind for kind, _ in client.calls]
    assert (kinds.count("reference"), kinds.count("show_all"), kinds.count("download")) == (1, 4, 3)
    assert total == 8
    assert _final_entries(job) == {"30": ("added", None)}


@pytest.mark.asyncio
async def test_three_consecutive_reused_listing_errors_stop_the_wait_at_max_failed_reads(job, monkeypatch):
    """ALL-1751 review: failed_reads resets only after the whole poll succeeds, so 3 consecutive
    HTTP 500s inside list_files_from_artifacts end the wait after 3 polls (not 9)."""
    polls, total, client = await _one_supplement_wait(
        monkeypatch, job, wait=600, poll=60, files=1,
        converts_at=lambda n: 0 if n == 1 else None,
        fails=lambda kind, _n, _in_listing: 500 if kind == "download" else False,
    )

    assert polls == 3
    kinds = [kind for kind, _ in client.calls]
    assert (kinds.count("reference"), kinds.count("show_all"), kinds.count("download")) == (1, 4, 3)
    assert total == 8
    assert _final_entries(job) == {"30": ("left_out", IN_TIME)}


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["disappeared", "lost_access"])
async def test_queued_supplement_revalidated_against_latest_poll_before_add_without_extra_show_all(
    job, monkeypatch, tmp_path, mode,
):
    """ALL-1751 review: a queued Office supplement that disappears or loses original access on
    a later poll is NOT added from a stale listing (0 add downloads, 0 extra show_all)."""
    from agr_ai_curation_alliance.document_sources.abc_literature import _map_conversion_result
    from src.lib.document_sources import file_listing_service, supplement_add
    from tests.unit.lib.document_sources.test_file_listing import _Store

    monkeypatch.setenv("DOCUMENT_SUPPLEMENT_WAIT_TIMEOUT_SECONDS", "180")
    monkeypatch.setenv("DOCUMENT_SUPPLEMENT_LISTING_POLL_SECONDS", "60")
    clock = _Clock()
    store = _Store()
    _install_fake_add_storage(monkeypatch, store, tmp_path)

    class _RevokingHttpAbc(_HttpAbc):
        def _rows(self):
            rows = [_abc_show_all_main_pdf(10, "WB"), _unconverted(30, "xlsx")]
            if self.polls < 2:
                rows.extend(_pair(20, "WB", content=GOOD_TABLE))
            elif mode == "lost_access":
                # Original becomes FB-only while converted text stays WB and downloadable.
                rows.extend(_pair(20, "FB", converted_mods=("WB",), content=GOOD_TABLE))
            return rows

    client = _RevokingHttpAbc(ids=[30], extension="xlsx", converts_at=lambda _n: None, fails=lambda *_: False)
    provider = client.provider()

    async def _list_files(document_id, *, owner_user_id, provider, context):
        client.in_listing = True
        try:
            return await file_listing_service.list_files_for_paper(
                document_id, owner_user_id=owner_user_id, provider=provider, context=context,
            )
        finally:
            client.in_listing = False

    async def _list_files_from_artifacts(
        document_id, *, owner_user_id, provider, context, reference, artifacts, check_unchecked=True,
    ):
        client.in_listing = True
        try:
            return await file_listing_service.list_files_for_paper_from_artifacts(
                document_id, owner_user_id=owner_user_id, provider=provider, context=context,
                reference=reference, artifacts=artifacts, check_unchecked=check_unchecked,
            )
        finally:
            client.in_listing = False

    try:
        await stage.run_supplement_stage(
            job_id=JOB_ID, document_id=DOCUMENT_ID, owner_user_id=7,
            plan=SupplementPlan(
                chosen=(_chosen("20", converted_md5=GOOD_TABLE_MD5), _chosen("30")),
                request_context=_context(), login_expires_at=clock() + 86400,
                owner_user_id=7,
            ),
            conversion_requested=True, last_answer=_map_conversion_result(RUNNING, provider="abc_literature"),
            main_ready_at=clock(), provider=provider, weaviate_client=object(), outcomes=_Outcomes(),
            list_files=_list_files, list_files_from_artifacts=_list_files_from_artifacts,
            add_files=supplement_add.add_files_to_paper_with_listing, clock=clock, sleep=clock.sleep,
        )
    finally:
        await provider.aclose()

    kinds = [kind for kind, _ in client.calls]
    assert kinds.count("reference") == 1
    assert kinds.count("show_all") == 3  # 1 initial + 2 polls; zero extra show_all for revalidation or add
    # Only the 1 check download during initial listing; ZERO add downloads after revocation/removal.
    assert kinds.count("download") == 1
    assert _final_entries(job) == {
        "20": ("left_out", FileReason.FILE_NOT_IN_REFERENCE.value),
        "30": ("left_out", IN_TIME),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("extension", "fail_download_n", "expected_downloads"),
    [
        # 2 files convert on polls 1..2 (2 check downloads). In add: download #3 (file 0 text) returns 401 -> stops immediately.
        ("xlsx", 3, 3),
        # 2 PDFs convert on polls 1..2 (2 check downloads). In add: download #3 (file 0 text) succeeds, #4 (file 0 PDF) returns 401 -> stops before file 1!
        ("pdf", 4, 4),
    ],
    ids=["401_on_converted_text_download", "401_on_original_pdf_download"],
)
async def test_401_during_real_add_ends_all_files_login_expired_without_further_requests(
    job, monkeypatch, tmp_path, extension, fail_download_n, expected_downloads,
):
    """ALL-1751 review: a 401 on either converted-text or original-PDF download during the real
    add propagates DocumentSourceLoginExpired, ends all unadded files login_expired, and sends
    zero subsequent requests."""
    polls, total, client = await _one_supplement_wait(
        monkeypatch, job, wait=300, poll=60, files=2,
        converts_at=lambda n: n - 1 if n <= 2 else None,
        fails=lambda kind, n, _in_listing: 401 if kind == "download" and n == fail_download_n else False,
        extension=extension, real_add=True, tmp_path=tmp_path,
    )

    assert polls == 2
    kinds = [kind for kind, _ in client.calls]
    assert (kinds.count("reference"), kinds.count("show_all"), kinds.count("download")) == (
        1, 3, expected_downloads,
    )
    assert total == 1 + 3 + expected_downloads
    assert _final_entries(job) == {
        "30": ("left_out", "login_expired"),
        "32": ("left_out", "login_expired"),
    }


@pytest.mark.asyncio
async def test_a_sweep_of_legal_settings_never_exceeds_the_supplement_waits_cap(job, monkeypatch):
    """Random settings, file counts, conversions and failures of reads and downloads,
    all sent as real HTTP requests through the adapter and the real listing service."""
    import random

    from src.lib.openai_agents.config import supplement_wait_max_polls

    generator = random.Random(16780)
    equal = 0
    for _ in range(200):
        wait = generator.choice([300, 610, 1800])
        poll = round(generator.uniform(max(5.0, wait / 60), wait / 3), 2)
        seed = generator.random()
        with pytest.MonkeyPatch.context() as each:
            polls, total, client = await _one_supplement_wait(
                each, job, wait=wait, poll=poll, files=generator.choice([1, 5, 19]),
                converts_at=lambda n, s=seed: random.Random(s * 7 + n).randrange(-3, 19),
                fails=lambda kind, n, in_listing, s=seed: random.Random(f"{s}{kind}{n}{in_listing}").random() < 0.3,
            )
        bound = supplement_wait_max_polls(wait_seconds=wait, listing_poll_seconds=poll)
        kinds = [kind for kind, _ in client.calls]
        assert polls <= bound, (wait, poll, polls, bound)
        assert kinds.count("reference") <= 1
        assert kinds.count("show_all") <= polls + 1  # only initial listing + polls; never a second show_all
        assert total >= polls
        equal += polls == bound
    assert equal > 0


def test_queued_entries_use_real_display_name_never_provider_id():
    plan = SupplementPlan(
        chosen=(
            _chosen("30", display_name="Table_S1.xlsx"),
            _chosen("40", converted_md5="md5-41", display_name=None),
            _chosen("50", display_name="   "),
        ),
        request_context=_context(),
        login_expires_at=None,
        owner_user_id=7,
    )

    queued = stage.queued_entries(plan)
    assert [(entry["provider_file_id"], entry["display_name"], entry["state"]) for entry in queued] == [
        ("30", "Table_S1.xlsx", "queued"),
        ("40", None, "queued"),
        ("50", None, "queued"),
    ]
    assert all(entry["display_name"] != entry["provider_file_id"] for entry in queued)


@pytest.mark.asyncio
async def test_login_expired_before_listing_keeps_chosen_display_name_never_provider_id(job):
    clock = _Clock()
    paper = _Paper([_abc_show_all_main_pdf(10, "WB"), *_pair(20, "WB")], payloads={"21": GOOD_TABLE})
    _client, provider = _plain_provider()

    await _stage(
        job,
        chosen=[_chosen("20", converted_md5="md5-21", display_name="Table_S20.xlsx"), _chosen("30")],
        paper=paper,
        main_ready_at=clock(),
        clock=clock,
        provider=provider,
        login_expires_at=clock() + 300,
    )

    entries = job.progress[-1]["metadata"]["supplements"]
    assert [(e["provider_file_id"], e["display_name"], e["reason_code"]) for e in entries] == [
        ("20", "Table_S20.xlsx", "login_expired"),
        ("30", None, "login_expired"),
    ]


@pytest.mark.asyncio
async def test_whitespace_display_name_in_listing_and_unnamed_add_outcome_preserve_clean_names(job):
    clock = _Clock()
    pair_20 = _pair(20, "WB")
    pair_20[0]["display_name"] = "   "
    pair_30 = _pair(30, "WB")
    pair_30[0]["display_name"] = ""
    rows = [_abc_show_all_main_pdf(10, "WB"), *pair_20, *pair_30]
    paper = _Paper(rows, payloads={"21": GOOD_TABLE, "31": GOOD_TABLE})
    _client, provider = _plain_provider()

    class _UnnamedOutcomeAdd:
        async def __call__(self, document_id, owner_user_id, context, chosen, **kwargs):
            return AddResult(
                files=tuple(
                    FileOutcome(
                        item.provider_file_id,
                        "added",
                        file_id=uuid4(),
                        display_name="   " if item.provider_file_id == "20" else None,
                    )
                    for item in chosen
                )
            )

    await _stage(
        job,
        chosen=[
            _chosen("20", converted_md5="md5-21", display_name="Table_S20.xlsx"),
            _chosen("30", converted_md5="md5-31", display_name=None),
        ],
        paper=paper,
        main_ready_at=clock(),
        clock=clock,
        provider=provider,
        add=_UnnamedOutcomeAdd(),
    )

    entries = job.progress[-1]["metadata"]["supplements"]
    assert [(e["provider_file_id"], e["display_name"], e["state"]) for e in entries] == [
        ("20", "Table_S20.xlsx", "added"),
        ("30", None, "added"),
    ]


def test_waiting_message_uses_registered_provider_short_label_without_hardcoding(monkeypatch):
    plan = SupplementPlan(
        chosen=(_chosen("30", display_name="Table_S1.xlsx"),),
        request_context=_context(),
        login_expires_at=None,
        owner_user_id=7,
    )
    st = stage._Stage(job_id=JOB_ID, plan=plan, main_ready_at=1_000_000.0, clock=_Clock())
    st.entries["30"] = stage._Entry(
        provider_file_id="30",
        display_name="Table_S1.xlsx",
        state=stage.SupplementState.WAITING,
    )

    assert stage._waiting_message(st, provider_id="abc_literature") == (
        "Main paper ready. Waiting for ABC to convert 1 supplement; "
        "you can use the paper and leave this page."
    )

    monkeypatch.setattr(
        stage,
        "get_document_source_provider_metadata",
        lambda provider_id: {"short_label": "EXL", "display_label": "Example Literature"}
        if provider_id == "example_lit"
        else None,
    )
    assert stage._waiting_message(st, provider_id="example_lit") == (
        "Main paper ready. Waiting for EXL to convert 1 supplement; "
        "you can use the paper and leave this page."
    )
    assert stage._waiting_message(st, provider_id="unknown_source") == (
        "Main paper ready. Waiting for the document source to convert 1 supplement; "
        "you can use the paper and leave this page."
    )


@pytest.mark.asyncio
async def test_stage_with_initial_listing_failure_leaves_queued_files_out_without_calling_add(job):
    """If the stage's initial listing fails with DocumentSourceError, QUEUED files end
    source_unavailable without triggering a fresh listing inside the add."""
    clock = _Clock()
    client, provider = _plain_provider()

    class _UnavailablePaper(_Paper):
        async def list_files(self, document_id, *, owner_user_id, provider, context):
            self.listings += 1
            raise DocumentSourceUnavailable("provider unavailable")

    paper = _UnavailablePaper([])
    add, outcomes = await _stage(
        job, chosen=[_chosen("20", converted_md5=GOOD_TABLE_MD5)], paper=paper, main_ready_at=clock(),
        clock=clock, provider=provider,
    )

    assert paper.listings == 1
    assert add.calls == []
    assert _final_entries(job) == {"20": ("left_out", "source_unavailable")}
    assert outcomes.ended == ["completed"]


@pytest.mark.asyncio
async def test_poll_reads_use_the_resolved_reference_so_licence_rules_stay_intact(job):
    """ALL-1751: passing the resolved SourceReference to provider.list_artifacts during polling
    preserves copyright-licence access rules (open access allows no-MOD curators; unset licence
    refuses access)."""
    clock = _Clock()
    # Open-access paper: supplement belongs to FB only, curator has no authorized groups,
    # yet open_access=True grants GLOBAL access when the resolved SourceReference is passed.
    paper = _Paper(
        [_abc_show_all_main_pdf(10, "FB"), _unconverted(30, "xlsx")],
        groups=(), licence_open=True, licence_name="CC BY 4.0",
    )

    def _abc_stores_30():
        paper.rows = [_abc_show_all_main_pdf(10, "FB"), *_pair(30, "FB")]
        paper.payloads = {"31": GOOD_TABLE}

    no_group_context = DocumentSourceRequestContext(
        provider_groups=(), authorized_group_ids=(),
        bearer_kind=ProviderBearerKind.CURATOR, curator_token="curator-token",
    )
    client, add, outcomes = await _run(
        chosen=[_chosen("30")], paper=paper, last_answer_payload=RUNNING, clock=clock,
        on_read={1: _abc_stores_30}, context=no_group_context,
    )

    assert (client.reads, client.reference_reads, paper.listings, paper.reused_listings) == (1, 1, 1, 1)
    assert [file.provider_file_id for file in add.calls[0]] == ["30"]
    assert _final_entries(job) == {"30": ("added", None)}
    assert outcomes.ended == ["completed"]

    # Refused licence (licence name set, open_access=None): poll read must NOT grant access
    # even though the supplement lists WB and the curator has WB!
    clock2 = _Clock()
    refused_paper = _Paper(
        [_abc_show_all_main_pdf(10, "WB"), _unconverted(30, "xlsx")],
        groups=("WB",), licence_open=None, licence_name="Restricted License",
    )

    def _abc_stores_30_refused():
        refused_paper.rows = [_abc_show_all_main_pdf(10, "WB"), *_pair(30, "WB")]
        refused_paper.payloads = {"31": GOOD_TABLE}

    client2, add2, outcomes2 = await _run(
        chosen=[_chosen("30")], paper=refused_paper, last_answer_payload=RUNNING, clock=clock2,
        on_read={1: _abc_stores_30_refused}, login_expires_in=900 + 120,
    )
    assert add2.calls == []
    assert _final_entries(job) == {"30": ("left_out", IN_TIME)}
    assert outcomes2.ended == ["completed"]


@pytest.mark.asyncio
async def test_poll_without_resolved_reference_fails_with_named_error_no_fallback(job):
    from dataclasses import replace

    from src.lib.document_sources.file_listing import FileListingError

    clock = _Clock()
    paper = _Paper([_abc_show_all_main_pdf(10, "WB"), _unconverted(30, "xlsx")])

    async def _listing_without_reference(*args, **kwargs):
        built = await paper.list_files(*args, **kwargs)
        return replace(built, reference=None)

    client, provider = _plain_provider()
    from agr_ai_curation_alliance.document_sources.abc_literature import _map_conversion_result

    with pytest.raises(FileListingError):
        await stage.run_supplement_stage(
            job_id=JOB_ID, document_id=DOCUMENT_ID, owner_user_id=7,
            plan=SupplementPlan(chosen=(_chosen("30"),), request_context=_context(),
                                login_expires_at=clock() + 86400, owner_user_id=7),
            conversion_requested=True, last_answer=_map_conversion_result(RUNNING, provider="abc_literature"),
            main_ready_at=clock(), provider=provider, weaviate_client=object(), outcomes=_Outcomes(),
            list_files=_listing_without_reference,
            list_files_from_artifacts=paper.list_files_from_artifacts,
            add_files=_Add(), clock=clock, sleep=clock.sleep,
        )
    assert client.reads == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["valid", "disappeared", "lost_access"])
async def test_max_failed_reads_on_waiting_file_does_not_retry_a_fourth_time_and_revalidates_queued_file(
    job, monkeypatch, tmp_path, mode,
):
    """ALL-1751 review: 1 QUEUED file A + 1 WAITING file B whose check returns HTTP 500 3 times
    stops at strictly 3 check attempts for B (never 4), ends B not_converted_in_time, and
    revalidates A against the latest poll's rows with zero extra show_all or check downloads."""
    from agr_ai_curation_alliance.document_sources.abc_literature import _map_conversion_result
    from src.lib.document_sources import file_listing_service, supplement_add
    from tests.unit.lib.document_sources.test_file_listing import _Store

    monkeypatch.setenv("DOCUMENT_SUPPLEMENT_WAIT_TIMEOUT_SECONDS", "600")
    monkeypatch.setenv("DOCUMENT_SUPPLEMENT_LISTING_POLL_SECONDS", "60")
    clock = _Clock()
    store = _Store()
    _install_fake_add_storage(monkeypatch, store, tmp_path)

    b_check_downloads = 0

    class _FailingWaitingHttpAbc(_HttpAbc):
        def _rows(self):
            rows = [_abc_show_all_main_pdf(10, "WB")]
            if self.polls < 3 or mode == "valid":
                rows.extend(_pair(20, "WB", content=GOOD_TABLE))
            elif mode == "lost_access":
                rows.extend(_pair(20, "FB", converted_mods=("WB",), content=GOOD_TABLE))
            if self.polls >= 1:
                rows.extend(_pair(30, "WB", content=GOOD_TABLE))
            else:
                rows.append(_unconverted(30, "xlsx"))
            return rows

        def handle(self, request):
            nonlocal b_check_downloads
            if request.url.path.endswith("/31"):
                b_check_downloads += 1
            return super().handle(request)

    client = _FailingWaitingHttpAbc(
        ids=[30], extension="xlsx", converts_at=lambda _n: None,
        fails=lambda kind, _n, in_listing: 500 if kind == "download" and in_listing and client.polls >= 1 else False,
    )
    provider = client.provider()

    async def _list_files(document_id, *, owner_user_id, provider, context):
        client.in_listing = True
        try:
            return await file_listing_service.list_files_for_paper(
                document_id, owner_user_id=owner_user_id, provider=provider, context=context,
            )
        finally:
            client.in_listing = False

    async def _list_files_from_artifacts(
        document_id, *, owner_user_id, provider, context, reference, artifacts, check_unchecked=True,
    ):
        client.in_listing = True
        try:
            return await file_listing_service.list_files_for_paper_from_artifacts(
                document_id, owner_user_id=owner_user_id, provider=provider, context=context,
                reference=reference, artifacts=artifacts, check_unchecked=check_unchecked,
            )
        finally:
            client.in_listing = False

    try:
        await stage.run_supplement_stage(
            job_id=JOB_ID, document_id=DOCUMENT_ID, owner_user_id=7,
            plan=SupplementPlan(
                chosen=(_chosen("20", converted_md5=GOOD_TABLE_MD5), _chosen("30")),
                request_context=_context(), login_expires_at=clock() + 86400, owner_user_id=7,
            ),
            conversion_requested=True, last_answer=_map_conversion_result(RUNNING, provider="abc_literature"),
            main_ready_at=clock(), provider=provider, weaviate_client=object(), outcomes=_Outcomes(),
            list_files=_list_files, list_files_from_artifacts=_list_files_from_artifacts,
            add_files=supplement_add.add_files_to_paper_with_listing, clock=clock, sleep=clock.sleep,
        )
    finally:
        await provider.aclose()

    kinds = [kind for kind, _ in client.calls]
    assert client.polls == 3
    assert kinds.count("reference") == 1
    assert kinds.count("show_all") == 4  # 1 initial + 3 polls; zero on final revalidation or add
    assert b_check_downloads == 3  # strictly max_failed_reads (3), never a 4th attempt
    if mode == "valid":
        # 1 initial check for A + 3 failed checks for B + 1 add download for A = 5
        assert kinds.count("download") == 5
        assert _final_entries(job) == {"20": ("added", None), "30": ("left_out", IN_TIME)}
    else:
        # 1 initial check for A + 3 failed checks for B + 0 add downloads = 4
        assert kinds.count("download") == 4
        assert _final_entries(job) == {
            "20": ("left_out", FileReason.FILE_NOT_IN_REFERENCE.value),
            "30": ("left_out", IN_TIME),
        }


@pytest.mark.asyncio
async def test_cancel_at_wait_expiry_stops_before_final_pre_add_revalidation(job, monkeypatch):
    """ALL-1751 review: stage.checkpoint() runs before final pre-add revalidation so a cancel
    requested as the wait expires stops cleanly without rebuilding the listing or adding."""
    monkeypatch.setenv("DOCUMENT_SUPPLEMENT_WAIT_TIMEOUT_SECONDS", "120")
    monkeypatch.setenv("DOCUMENT_SUPPLEMENT_LISTING_POLL_SECONDS", "60")
    clock = _Clock()
    paper = _Paper(
        [_abc_show_all_main_pdf(10, "WB"), *_pair(20, "WB"), _unconverted(30, "xlsx")],
        payloads={"21": GOOD_TABLE},
    )

    def _cancel_after_first_poll():
        # Poll 1 runs at t=60 (setting latest_artifacts), then the next sleep reaches t=120.
        # Request cancellation during poll 1 so expiry triggers checkpoint before revalidation.
        pass

    async def _sleep_and_cancel(seconds: float) -> None:
        clock.now += seconds
        if clock.now >= 1_000_120.0:
            job.cancel_requested = True
        await asyncio.sleep(0)

    revalidation_calls = 0
    original_from_artifacts = paper.list_files_from_artifacts

    async def _counting_from_artifacts(*args, **kwargs):
        nonlocal revalidation_calls
        revalidation_calls += 1
        return await original_from_artifacts(*args, **kwargs)

    paper.list_files_from_artifacts = _counting_from_artifacts  # type: ignore[method-assign]
    _client, add, outcomes = await _run(
        chosen=[_chosen("20", converted_md5=GOOD_TABLE_MD5), _chosen("30")],
        paper=paper, last_answer_payload=RUNNING, clock=clock,
        on_read={1: _cancel_after_first_poll}, sleep=_sleep_and_cancel,
    )

    assert revalidation_calls == 0
    assert add.calls == []
    assert outcomes.ended == ["cancelled"]
    assert _final_entries(job) == {
        "20": ("left_out", "import_cancelled"),
        "30": ("left_out", "import_cancelled"),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("when", ["upfront", "raced_under_lock"])
async def test_stage_catches_file_removal_unfinished_cleanly_without_stage_failure(
    job, monkeypatch, tmp_path, when,
):
    """ALL-1751 review: FileRemovalUnfinished raised upfront or under _insert_rows lock is
    caught in run_supplement_stage, preserving error_code='file_removal_unfinished' without
    failing the stage, re-raising, or reporting to Sentry."""
    from src.lib.document_sources import file_listing_service, supplement_add
    from tests.unit.lib.document_sources.test_file_listing import _Store

    reported = []
    monkeypatch.setattr(stage, "report_runtime_exception", lambda exc, **kwargs: reported.append(kwargs))
    clock = _Clock()
    store = _Store()
    _install_fake_add_storage(monkeypatch, store, tmp_path)

    client = _HttpAbc(ids=[], extension="xlsx", converts_at=lambda _n: None, fails=lambda *_: False)

    def _rows_with_20():
        return [_abc_show_all_main_pdf(10, "WB"), *_pair(20, "WB", content=GOOD_TABLE)]

    client._rows = _rows_with_20  # type: ignore[method-assign]
    provider = client.provider()

    if when == "upfront":
        monkeypatch.setattr(
            supplement_add, "_paper_state",
            lambda *_a, **_k: supplement_add._PaperState(
                owner_subject="owner-subject", status="completed", file_count=1, ready_text_bytes=1_000,
                next_tab_order=1, leftovers=(), source_provider="abc_literature",
                source_provider_reference_id="101", source_provider_reference_curie="AGRKB:101",
                live_provider_file_ids=frozenset({"10"}),
                removed_provider_file_ids=frozenset({"20"}),
            ),
        )
    else:
        def _race_removed(doc_id, _rows, _copy_ids=None, _text_bytes=None):
            raise supplement_add.FileRemovalUnfinished(doc_id)

        monkeypatch.setattr(supplement_add, "_insert_rows", _race_removed)

    outcomes = _Outcomes()
    try:
        await stage.run_supplement_stage(
            job_id=JOB_ID, document_id=DOCUMENT_ID, owner_user_id=7,
            plan=SupplementPlan(
                chosen=(_chosen("20", converted_md5=GOOD_TABLE_MD5),),
                request_context=_context(), login_expires_at=clock() + 86400, owner_user_id=7,
            ),
            conversion_requested=False, last_answer=None, main_ready_at=clock(),
            provider=provider, weaviate_client=object(), outcomes=outcomes,
            list_files=file_listing_service.list_files_for_paper,
            list_files_from_artifacts=file_listing_service.list_files_for_paper_from_artifacts,
            add_files=supplement_add.add_files_to_paper_with_listing,
            clock=clock, sleep=clock.sleep,
        )
    finally:
        await provider.aclose()

    assert reported == []
    assert outcomes.ended == ["completed"]
    final_meta = job.progress[-1]["metadata"]
    assert final_meta["error_code"] == "file_removal_unfinished"
    assert _final_entries(job) == {"20": ("left_out", FileReason.PAPER_BUSY_WHEN_READY.value)}
    assert file_listing_service.provider_observations_from_jobs([final_meta]) == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "licence_outcome",
    [
        "restricted",
        "unchanged",
        "resolve_error",
        "resolve_401",
        "all_polls_fail_then_restricted",
        "deadline_during_first_sleep_then_restricted",
    ],
)
async def test_open_access_reference_revalidated_once_before_add_after_polls(
    job, monkeypatch, tmp_path, licence_outcome,
):
    """ALL-1751 review: an open-access (GLOBAL) reference revalidates GET /reference once
    before adding whenever wait time elapsed (after polls, when all polls fail, or when
    the wait hits its deadline during its first sleep). If the licence became restricted,
    show_all is refreshed once with the new reference so a queued Office supplement that
    lost access is NOT added (0 add downloads). If GET /reference returns 401, it ends
    login_expired. If unchanged, 0 extra show_all calls are made and the file joins."""
    from agr_ai_curation_alliance.document_sources.abc_literature import _map_conversion_result
    from src.lib.document_sources import file_listing_service, supplement_add
    from tests.unit.lib.document_sources.test_file_listing import _Store

    if licence_outcome == "all_polls_fail_then_restricted":
        wait_seconds = "600"
    elif licence_outcome == "deadline_during_first_sleep_then_restricted":
        wait_seconds = "45"
    else:
        wait_seconds = "120"
    monkeypatch.setenv("DOCUMENT_SUPPLEMENT_WAIT_TIMEOUT_SECONDS", wait_seconds)
    monkeypatch.setenv("DOCUMENT_SUPPLEMENT_LISTING_POLL_SECONDS", "60")
    clock = _Clock()
    store = _Store()
    _install_fake_add_storage(monkeypatch, store, tmp_path)

    class _OpenAccessHttpAbc(_HttpAbc):
        def _rows(self):
            # Both main and supplements belong to FB only; the WB curator can read them
            # ONLY while the reference's open copyright licence grants GLOBAL access.
            return [
                _abc_show_all_main_pdf(10, "FB"),
                *_pair(20, "FB", content=GOOD_TABLE),
                _unconverted(30, "xlsx"),
            ]

        def handle(self, request):
            if (
                (self.polls >= 1 and licence_outcome in ("restricted", "all_polls_fail_then_restricted"))
                or (licence_outcome == "deadline_during_first_sleep_then_restricted" and clock() > 1_000_000.0)
            ):
                self.show_reference_payload = _abc_reference_record(
                    copyright_license_open_access=False,
                    copyright_license_name="Restricted License",
                )
            return super().handle(request)

    def _fails(kind: str, n: int, in_listing: bool):
        if licence_outcome == "resolve_error" and kind == "reference" and n == 2:
            return 500
        if licence_outcome == "resolve_401" and kind == "reference" and n == 2:
            return 401
        if licence_outcome == "all_polls_fail_then_restricted" and kind == "show_all" and not in_listing and n <= 4:
            # Show-all #1 is initial listing; #2..#4 are the 3 failed polls hitting max_failed_reads=3;
            # #5 is the post-licence-change refresh which succeeds with the updated reference.
            return 502
        return False

    client = _OpenAccessHttpAbc(
        ids=[30],
        extension="xlsx",
        converts_at=lambda _n: None,
        fails=_fails,
    )
    client.show_reference_payload = _abc_reference_record(
        copyright_license_open_access=True,
        copyright_license_name="CC BY 4.0",
    )
    provider = client.provider()

    async def _list_files(document_id, *, owner_user_id, provider, context):
        client.in_listing = True
        try:
            return await file_listing_service.list_files_for_paper(
                document_id, owner_user_id=owner_user_id, provider=provider, context=context,
            )
        finally:
            client.in_listing = False

    async def _list_files_from_artifacts(
        document_id, *, owner_user_id, provider, context, reference, artifacts, check_unchecked=True,
    ):
        client.in_listing = True
        try:
            return await file_listing_service.list_files_for_paper_from_artifacts(
                document_id, owner_user_id=owner_user_id, provider=provider, context=context,
                reference=reference, artifacts=artifacts, check_unchecked=check_unchecked,
            )
        finally:
            client.in_listing = False

    try:
        await stage.run_supplement_stage(
            job_id=JOB_ID, document_id=DOCUMENT_ID, owner_user_id=7,
            plan=SupplementPlan(
                chosen=(_chosen("20", converted_md5=GOOD_TABLE_MD5), _chosen("30")),
                request_context=_context(), login_expires_at=clock() + 86400, owner_user_id=7,
            ),
            conversion_requested=True, last_answer=_map_conversion_result(RUNNING, provider="abc_literature"),
            main_ready_at=clock(), provider=provider, weaviate_client=object(), outcomes=_Outcomes(),
            list_files=_list_files, list_files_from_artifacts=_list_files_from_artifacts,
            add_files=supplement_add.add_files_to_paper_with_listing, clock=clock, sleep=clock.sleep,
        )
    finally:
        await provider.aclose()

    kinds = [kind for kind, _ in client.calls]
    assert kinds.count("reference") == 2  # 1 initial + 1 pre-add open-licence check
    if licence_outcome == "restricted":
        # 1 initial + 1 poll + 1 refresh after licence changed = 3 show_all; 0 add downloads (only 1 initial check)
        assert kinds.count("show_all") == 3
        assert kinds.count("download") == 1
        assert _final_entries(job) == {
            "20": ("left_out", FileReason.FILE_NOT_IN_REFERENCE.value),
            "30": ("left_out", IN_TIME),
        }
    elif licence_outcome == "all_polls_fail_then_restricted":
        # 1 initial + 3 failed polls + 1 refresh after licence changed = 5 show_all; 0 add downloads
        assert kinds.count("show_all") == 5
        assert kinds.count("download") == 1
        assert _final_entries(job) == {
            "20": ("left_out", FileReason.FILE_NOT_IN_REFERENCE.value),
            "30": ("left_out", IN_TIME),
        }
    elif licence_outcome == "deadline_during_first_sleep_then_restricted":
        # Slept once for 45s and hit deadline with 0 wait polls; 1 initial + 1 post-licence-change refresh = 2 show_all
        assert clock() == 1_000_045.0
        assert kinds.count("show_all") == 2
        assert kinds.count("download") == 1
        assert _final_entries(job) == {
            "20": ("left_out", FileReason.FILE_NOT_IN_REFERENCE.value),
            "30": ("left_out", IN_TIME),
        }
    elif licence_outcome == "unchanged":
        # 1 initial + 1 poll; ZERO extra show_all when reference metadata is unchanged
        assert kinds.count("show_all") == 2
        assert kinds.count("download") == 2  # 1 initial check + 1 add download
        assert _final_entries(job) == {"20": ("added", None), "30": ("left_out", IN_TIME)}
    elif licence_outcome == "resolve_401":
        # 401 on pre-add resolve_reference maps to DocumentSourceLoginExpired -> login_expired (0 add downloads)
        assert kinds.count("show_all") == 2
        assert kinds.count("download") == 1
        assert _final_entries(job) == {
            "20": ("left_out", FileReason.LOGIN_EXPIRED.value),
            "30": ("left_out", IN_TIME),
        }
    else:
        # Fail closed on pre-add resolve_reference 500: 0 extra show_all, 0 add downloads
        assert kinds.count("show_all") == 2
        assert kinds.count("download") == 1
        assert _final_entries(job) == {
            "20": ("left_out", FileReason.SOURCE_UNAVAILABLE.value),
            "30": ("left_out", IN_TIME),
        }


@pytest.mark.asyncio
async def test_stage_prebuilt_add_401_preserves_earlier_settled_refusal_and_display_name(
    job, monkeypatch, tmp_path,
):
    """ALL-1751 review: in run_supplement_stage's prebuilt-listing add, if file 1 settles as
    file_changed_since_listed before file 2's download returns 401, file 1 keeps its settled
    refusal and display name while file 2 ends login_expired with zero further requests."""
    from src.lib.document_sources import file_listing_service, supplement_add
    from tests.unit.lib.document_sources.test_file_listing import _Store

    clock = _Clock()
    store = _Store()
    _install_fake_add_storage(monkeypatch, store, tmp_path)

    client = _HttpAbc(
        ids=[],
        extension="xlsx",
        converts_at=lambda _n: None,
        # Downloads #1 and #2 are initial listing checks for 21 and 41; #3 is add download of 41 -> 401.
        fails=lambda kind, n, _in_listing: 401 if kind == "download" and n == 3 else False,
    )

    def _two_files():
        return [
            _abc_show_all_main_pdf(10, "WB"),
            *_pair(20, "WB", content=GOOD_TABLE),
            *_pair(40, "WB", content=GOOD_TABLE),
        ]

    client._rows = _two_files  # type: ignore[method-assign]
    provider = client.provider()

    async def _list_files(document_id, *, owner_user_id, provider, context):
        client.in_listing = True
        try:
            return await file_listing_service.list_files_for_paper(
                document_id, owner_user_id=owner_user_id, provider=provider, context=context,
            )
        finally:
            client.in_listing = False

    outcomes = _Outcomes()
    try:
        await stage.run_supplement_stage(
            job_id=JOB_ID, document_id=DOCUMENT_ID, owner_user_id=7,
            plan=SupplementPlan(
                chosen=(
                    _chosen("20", converted_md5="0" * 32),  # stale checksum -> file_changed_since_listed
                    _chosen("40", converted_md5=GOOD_TABLE_MD5),
                ),
                request_context=_context(), login_expires_at=clock() + 86400, owner_user_id=7,
            ),
            conversion_requested=False, last_answer=None, main_ready_at=clock(),
            provider=provider, weaviate_client=object(), outcomes=outcomes,
            list_files=_list_files,
            list_files_from_artifacts=file_listing_service.list_files_for_paper_from_artifacts,
            add_files=supplement_add.add_files_to_paper_with_listing,
            clock=clock, sleep=clock.sleep,
        )
    finally:
        await provider.aclose()

    assert outcomes.ended == ["completed"]
    entries = {
        entry["provider_file_id"]: (entry["display_name"], entry["state"], entry["reason_code"])
        for entry in job.progress[-1]["metadata"]["supplements"]
    }
    assert entries == {
        "20": ("Made_up_S20.xlsx", "left_out", FileReason.FILE_CHANGED_SINCE_LISTED.value),
        "40": ("Made_up_S40.xlsx", "left_out", FileReason.LOGIN_EXPIRED.value),
    }
    kinds = [kind for kind, _ in client.calls]
    assert (kinds.count("reference"), kinds.count("show_all"), kinds.count("download")) == (1, 1, 3)

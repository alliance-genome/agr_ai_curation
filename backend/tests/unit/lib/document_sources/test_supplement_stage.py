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
        self.paper = paper
        self.on_read = on_read or {}
        self.read_error = read_error
        self.failing_reads = failing_reads  # None: every read fails when read_error is set

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


class _Paper:
    """The paper's ABC rows as the listing would show them now."""

    def __init__(self, rows, payloads=None):
        self.rows = rows
        self.payloads = payloads or {}
        self.listings = 0

    async def list_files(self, document_id, *, owner_user_id, provider, context):
        self.listings += 1
        listing, _ = await _listing(self.rows, payloads=self.payloads)
        return listing


class _Add:
    def __init__(self):
        self.calls: list[list[Any]] = []

    async def __call__(self, document_id, owner_user_id, request_context, files, *, job_id,
                       provider, weaviate_client, stage_checkpoint):
        await stage_checkpoint("checking_files")
        self.calls.append(list(files))
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


def _chosen(provider_file_id, *, converted_md5=None):
    return ChosenSupplement(
        provider_file_id=provider_file_id, md5=f"md5-{provider_file_id}", converted_md5=converted_md5,
    )


async def _run(*, chosen, paper, last_answer_payload, clock, add=None, abc_answers=None,
               conversion_requested=True, login_expires_in=24 * 3600, on_read=None,
               read_error=None, failing_reads=None, sleep=None):
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
        job_id=JOB_ID, document_id=DOCUMENT_ID, owner_user_id=7, reference="AGRKB:101",
        plan=SupplementPlan(
            chosen=tuple(chosen), request_context=_context(),
            login_expires_at=clock() + login_expires_in, owner_user_id=7, reference="AGRKB:101",
        ),
        conversion_requested=conversion_requested, last_answer=last_answer,
        main_ready_at=clock(), provider=provider, weaviate_client=object(),
        outcomes=outcomes, list_files=paper.list_files, add_files=add,
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
    assert paper.listings == 2  # the stage's first listing, then one when the new text appeared
    assert clock.now == 1_000_000.0 + 3 * 60  # DOCUMENT_SUPPLEMENT_LISTING_POLL_SECONDS default
    assert [(file.provider_file_id, file.converted_md5) for file in add.calls[0]] == [("30", GOOD_TABLE_MD5)]
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

    assert (client.requests, client.reads, paper.listings) == (0, 5, 2)
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
        job_id=JOB_ID, document_id=DOCUMENT_ID, owner_user_id=7, reference="AGRKB:101",
        plan=SupplementPlan(chosen=tuple(chosen), request_context=_context(),
                            login_expires_at=login_expires_at, owner_user_id=7, reference="AGRKB:101"),
        conversion_requested=conversion_requested, last_answer=last_answer,
        main_ready_at=main_ready_at, provider=provider, weaviate_client=object(),
        outcomes=outcomes, list_files=paper.list_files, add_files=add,
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
            job_id=JOB_ID, document_id=DOCUMENT_ID, owner_user_id=7, reference="AGRKB:101",
            plan=SupplementPlan(chosen=(_chosen("20", converted_md5=GOOD_TABLE_MD5),), request_context=_context(),
                                login_expires_at=None, owner_user_id=7, reference="AGRKB:101"),
            conversion_requested=False, last_answer=None, main_ready_at=clock(), provider=provider,
            weaviate_client=object(), outcomes=outcomes, list_files=_DeletedPaper().list_files,
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


# --- ALL-1678: the polling cap per supplement wait (owner's option b, 24 Nov 2026), proven
# against the real wait sending real requests: the real ABC adapter over a client that counts
# every call, and the real file listing (resolve, show_all, check downloads). The cap counts the
# wait's own reads of the listing; each test also reports every request the wait really sends.


class _CountingAbc(FakeABCLiteratureClient):
    """ABC's client, every call counted and marked as the wait's own poll or not."""

    def __init__(self, *, fails):
        super().__init__()
        self.rows: list = []
        self.calls: list[tuple[str, bool]] = []
        self.in_listing = False
        self.fails = fails  # fails(kind, n, in_listing) -> bool
        self.show_reference_payload = _abc_reference_record()

    def _record(self, kind):
        self.calls.append((kind, self.in_listing))
        if self.fails(kind, sum(1 for call in self.calls if call[0] == kind), self.in_listing):
            from agr_ai_curation_alliance.literature.client import ABCLiteratureHTTPError

            raise ABCLiteratureHTTPError("ABC Literature returned HTTP 502", status_code=502, endpoint=kind)

    async def show_reference(self, reference, *, request_bearer_token=None):
        self._record("reference")
        return self.show_reference_payload

    async def show_referencefiles(self, reference, *, request_bearer_token=None):
        self._record("show_all")
        return list(self.rows)

    async def download_referencefile(self, artifact_id, *, request_bearer_token=None, max_bytes=None):
        self._record("download")
        return GOOD_TABLE

    async def request_referencefile_conversion(self, reference, *, wait=False, request_bearer_token=None):
        self._record("conversion")
        return _answer_payload("running", [])

    @property
    def polls(self) -> int:
        return sum(1 for kind, in_listing in self.calls if kind == "show_all" and not in_listing)


async def _one_supplement_wait(monkeypatch, job, *, wait, poll, files, converts_at, fails=lambda *_: False):
    """One supplement wait on the fake clock with the real listing; returns (polls, total, client).

    ``converts_at(n)`` is the index of the file ABC has converted by the wait's read ``n``. The
    add is a stand-in here (the persistence route test drives the real one): its own listing
    and downloads come on top of ``total``.
    """
    from src.lib.document_sources.file_listing import ListingRoute, list_reference_files
    from tests.unit.lib.document_sources.test_file_listing import _limits, _Store

    monkeypatch.setenv("DOCUMENT_SUPPLEMENT_WAIT_TIMEOUT_SECONDS", str(wait))
    monkeypatch.setenv("DOCUMENT_SUPPLEMENT_LISTING_POLL_SECONDS", str(poll))
    clock = _Clock()
    client = _CountingAbc(fails=fails)
    provider = provider_from_fake(client)
    ids = [30 + 2 * index for index in range(files)]
    converted: list[int] = []

    def _rows():
        return [_abc_show_all_main_pdf(10, "WB")] + [
            row for i in ids for row in (_pair(i, "WB") if i in converted else [_unconverted(i, "xlsx")])
        ]

    client.rows = _rows()
    original_show_all = client.show_referencefiles

    async def _show_all(reference, *, request_bearer_token=None):
        if not client.in_listing:
            new = converts_at(client.polls + 1)
            if new is not None and 0 <= new < len(ids) and ids[new] not in converted:
                converted.append(ids[new])
                client.rows = _rows()
        return await original_show_all(reference, request_bearer_token=request_bearer_token)

    client.show_referencefiles = _show_all
    store = _Store()

    async def _list_files(document_id, *, owner_user_id, provider, context):
        client.in_listing = True
        try:
            reference = await provider.resolve_reference("AGRKB:101", request_bearer_token="curator-token")
            return await list_reference_files(
                provider=provider, reference=reference, authorized_group_ids=("WB",),
                bearer_kind=ProviderBearerKind.CURATOR, request_bearer_token="curator-token",
                files_in_paper=("10",), limits=_limits(), check_limit=20,
                load_stored=store.load, save_assessment=store.save, route=ListingRoute.PAPER,
                provider_observations={}, find_paper_in_library=None,
            )
        finally:
            client.in_listing = False

    from agr_ai_curation_alliance.document_sources.abc_literature import _map_conversion_result

    await stage.run_supplement_stage(
        job_id=JOB_ID, document_id=DOCUMENT_ID, owner_user_id=7, reference="AGRKB:101",
        plan=SupplementPlan(chosen=tuple(_chosen(str(i)) for i in ids), request_context=_context(),
                            login_expires_at=clock() + 86400, owner_user_id=7, reference="AGRKB:101"),
        conversion_requested=True, last_answer=_map_conversion_result(RUNNING, provider="abc_literature"),
        main_ready_at=clock(), provider=provider, weaviate_client=object(), outcomes=_Outcomes(),
        list_files=_list_files, add_files=_Add(), clock=clock, sleep=clock.sleep,
    )
    assert not any(kind == "conversion" for kind, _ in client.calls)  # never asks ABC to convert
    return client.polls, len(client.calls), client


@pytest.mark.asyncio
async def test_at_the_defaults_a_supplement_wait_makes_exactly_its_cap_bound(job, monkeypatch):
    """ABC never converts: 179 reads in 10,800 s at 60 s. The stage's first listing (a resolve
    and a show_all) comes on top."""
    from src.lib.openai_agents.config import supplement_wait_max_polls

    polls, total, _ = await _one_supplement_wait(
        monkeypatch, job, wait=10800, poll=60, files=1, converts_at=lambda n: None,
    )

    assert polls == supplement_wait_max_polls(wait_seconds=10800, listing_poll_seconds=60) == 179
    assert total == 181


@pytest.mark.asyncio
async def test_at_the_edge_with_the_alternating_failure_the_polls_stay_capped_and_the_rest_is_counted(
    job, monkeypatch,
):
    """10,800 s at 26.94 s, the smallest listing poll the default cap allows. A converted row is
    listed from the first read while every later full listing's show_all fails (a good read
    resets the count, so they alternate to the limit: the separate finding)."""
    from src.lib.openai_agents.config import supplement_wait_max_polls

    polls, total, client = await _one_supplement_wait(
        monkeypatch, job, wait=10800, poll=26.94, files=19, converts_at=lambda n: 0 if n == 1 else None,
        fails=lambda kind, n, in_listing: kind == "show_all" and in_listing and n > 1,
    )

    assert polls == supplement_wait_max_polls(wait_seconds=10800, listing_poll_seconds=26.94) == 400
    # The cap does not count: the first listing (2), then a resolve and a failing show_all in
    # every interval (2 x 400).
    assert total == 1202
    assert [kind for kind, _ in client.calls].count("download") == 0


@pytest.mark.asyncio
async def test_files_converting_one_per_read_send_listings_and_downloads_the_cap_does_not_count(job, monkeypatch):
    """610 s at 60 s: ten reads, a file converted before each; every full listing answers."""
    from src.lib.openai_agents.config import supplement_wait_max_polls

    polls, total, client = await _one_supplement_wait(
        monkeypatch, job, wait=610, poll=60, files=19, converts_at=lambda n: n - 1,
    )

    assert polls == supplement_wait_max_polls(wait_seconds=610, listing_poll_seconds=60) == 10
    kinds = [kind for kind, _ in client.calls]
    # 10 polls; 11 listings (a resolve and a show_all each); one check download per converted text.
    assert (kinds.count("reference"), kinds.count("download"), total) == (11, 10, 42)


@pytest.mark.asyncio
async def test_a_sweep_of_legal_settings_never_exceeds_the_supplement_waits_cap(job, monkeypatch):
    """Random settings, file counts, conversions and failures of reads, listings and downloads,
    all sent as real requests through the adapter and the real listing."""
    import random

    from src.lib.openai_agents.config import supplement_wait_max_polls

    generator = random.Random(16780)
    equal = 0
    for _ in range(200):
        wait = generator.choice([300, 610, 1800])
        poll = round(generator.uniform(max(5.0, wait / 60), wait / 3), 2)
        seed = generator.random()
        with pytest.MonkeyPatch.context() as each:
            polls, total, _ = await _one_supplement_wait(
                each, job, wait=wait, poll=poll, files=generator.choice([1, 5, 19]),
                converts_at=lambda n, s=seed: random.Random(s * 7 + n).randrange(-3, 19),
                fails=lambda kind, n, in_listing, s=seed: random.Random(f"{s}{kind}{n}{in_listing}").random() < 0.3,
            )
        bound = supplement_wait_max_polls(wait_seconds=wait, listing_poll_seconds=poll)
        assert polls <= bound, (wait, poll, polls, bound)
        assert total >= polls
        equal += polls == bound
    assert equal > 0

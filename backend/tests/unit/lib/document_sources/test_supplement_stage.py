"""The supplement stage of an import (KANBAN-1976 / ALL-1460, P7b).

ABC's answers are its own response shapes at ``3d3ae96``, sent through the real
ABC adapter; the paper's files come from the real listing over ``show_all``
rows. The add (P7c1), the job service and the clock are fakes. All values are
made up.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
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
    """Answers the conversion request from a script; the last answer repeats."""

    def __init__(self, answers: list[Any]):
        super().__init__()
        self.answers = answers
        self.requests = 0

    async def request_referencefile_conversion(self, reference, *, wait=False, request_bearer_token=None):
        self.requests += 1
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


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
    ):
        monkeypatch.delenv(name, raising=False)
    return state


def _context():
    return DocumentSourceRequestContext(
        provider_groups=("WBStaff",), authorized_group_ids=("WB",),
        bearer_kind=ProviderBearerKind.CURATOR, curator_token="curator-token",
    )


def _chosen(provider_file_id, *, converted_md5=None):
    return ChosenSupplement(
        provider_file_id=provider_file_id, md5=f"md5-{provider_file_id}", converted_md5=converted_md5,
    )


async def _run(*, chosen, abc_answers, paper, last_answer_payload, clock, add=None,
               conversion_requested=True, login_expires_in=24 * 3600):
    from agr_ai_curation_alliance.document_sources.abc_literature import _map_conversion_result

    client = _AbcClient(abc_answers)
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
        clock=clock, sleep=clock.sleep,
    )
    return client, add, outcomes


def _final_entries(job):
    return {
        entry["provider_file_id"]: (entry["state"], entry["reason_code"])
        for entry in job.progress[-1]["metadata"]["supplements"]
    }


@pytest.mark.asyncio
async def test_converted_supplements_are_added_without_asking_abc(job):
    clock = _Clock()
    paper = _Paper([_abc_show_all_main_pdf(10, "WB"), *_pair(20, "WB")], payloads={"21": GOOD_TABLE})

    client, add, outcomes = await _run(
        chosen=[_chosen("20", converted_md5="md5-21")], abc_answers=[_answer_payload("converted", [])],
        paper=paper, last_answer_payload=None, conversion_requested=False, clock=clock,
    )

    assert client.requests == 0
    assert [file.provider_file_id for file in add.calls[0]] == ["20"]
    assert outcomes.ended == ["completed"]
    assert _final_entries(job) == {"20": ("added", None)}


@pytest.mark.asyncio
async def test_a_file_abc_converts_during_the_wait_joins_and_abc_is_not_asked_again(job):
    clock = _Clock()
    running = _answer_payload("running", [_job_entry(30, "supplement", "pending")])
    done = _answer_payload("running", [_job_entry(30, "supplement", "success", converted_id=31)])
    paper = _Paper([_abc_show_all_main_pdf(10, "WB"), _unconverted(30, "xlsx")])

    class _Converting(_AbcClient):
        async def request_referencefile_conversion(self, reference, *, wait=False, request_bearer_token=None):
            # ABC stores the converted file, then answers with its success entry.
            paper.rows = [_abc_show_all_main_pdf(10, "WB"), *_pair(30, "WB")]
            paper.payloads = {"31": GOOD_TABLE}
            return await super().request_referencefile_conversion(reference, wait=wait)

    client = _Converting([done])
    provider = provider_from_fake(client)
    client.conversion_payload = running
    last_answer = await FakeABCLiteratureClient.request_referencefile_conversion(
        client, "AGRKB:101"
    )
    from agr_ai_curation_alliance.document_sources.abc_literature import _map_conversion_result

    last_answer = _map_conversion_result(last_answer, provider="abc_literature")
    add, outcomes = _Add(), _Outcomes()
    await stage.run_supplement_stage(
        job_id=JOB_ID, document_id=DOCUMENT_ID, owner_user_id=7, reference="AGRKB:101",
        plan=SupplementPlan(chosen=(_chosen("30"),), request_context=_context(),
                                  login_expires_at=clock() + 86400, owner_user_id=7, reference="AGRKB:101"),
        conversion_requested=True, last_answer=last_answer, main_ready_at=clock(),
        provider=provider, weaviate_client=object(), outcomes=outcomes,
        list_files=paper.list_files, add_files=add, clock=clock, sleep=clock.sleep,
    )

    assert client.requests == 1
    assert [(file.provider_file_id, file.converted_md5) for file in add.calls[0]] == [("30", "md5-31")]
    assert outcomes.ended == ["completed"]
    assert _final_entries(job) == {"30": ("added", None)}


@pytest.mark.asyncio
async def test_a_paper_whose_supplements_abc_does_not_convert_reports_them_at_once(job):
    """Today outside WB, ZFIN and FB (before ABC #1413): converted at once, no supplement work."""

    clock = _Clock()
    paper = _Paper([_abc_show_all_main_pdf(10, "WB"), _unconverted(60, "docx")])

    client, add, outcomes = await _run(
        chosen=[_chosen("60")], abc_answers=[_answer_payload("converted", [])], paper=paper,
        last_answer_payload=_answer_payload("converted", [_job_entry(10, "main", "success", converted_id=11)]),
        clock=clock,
    )

    assert client.requests == 0
    assert add.calls == []
    assert outcomes.ended == ["completed"]
    assert _final_entries(job) == {"60": ("left_out", NOT_CONVERTED)}


@pytest.mark.asyncio
async def test_a_failed_pdf_job_adds_the_converted_files_and_reports_the_failed_one(job):
    clock = _Clock()
    paper = _Paper([_abc_show_all_main_pdf(10, "WB"), _unconverted(30, "pdf"), _unconverted(50, "pdf")])
    failed = _answer_payload("failed", [
        _job_entry(30, "supplement", "failed", error="Supplemental PDF exceeds 5 MB limit"),
        _job_entry(50, "supplement", "success", converted_id=51),
    ], error_message="1 file failed")

    class _Storing(_AbcClient):
        async def request_referencefile_conversion(self, reference, *, wait=False, request_bearer_token=None):
            paper.rows = [_abc_show_all_main_pdf(10, "WB"), _unconverted(30, "pdf"), *_pair(50, "WB")]
            paper.payloads = {"51": GOOD_TABLE}
            return await super().request_referencefile_conversion(reference, wait=wait)

    client = _Storing([failed])
    provider = provider_from_fake(client)
    client.conversion_payload = _answer_payload("running", [_job_entry(30, "supplement", "pending")])
    last_answer = await provider.request_conversion("AGRKB:101", request_bearer_token="curator-token")
    add, outcomes = _Add(), _Outcomes()
    await stage.run_supplement_stage(
        job_id=JOB_ID, document_id=DOCUMENT_ID, owner_user_id=7, reference="AGRKB:101",
        plan=SupplementPlan(chosen=(_chosen("30"), _chosen("50")), request_context=_context(),
                                  login_expires_at=clock() + 86400, owner_user_id=7, reference="AGRKB:101"),
        conversion_requested=True, last_answer=last_answer, main_ready_at=clock(),
        provider=provider, weaviate_client=object(), outcomes=outcomes,
        list_files=paper.list_files, add_files=add, clock=clock, sleep=clock.sleep,
    )

    assert client.requests == 1  # the failed answer is final: never asked again
    assert [file.provider_file_id for file in add.calls[0]] == ["50"]
    assert _final_entries(job) == {"30": ("left_out", FAILED), "50": ("added", None)}


@pytest.mark.asyncio
async def test_the_wait_limit_ends_the_wait_and_the_converted_files_still_join(job):
    clock = _Clock()
    running = _answer_payload("running", [_job_entry(30, "supplement", "pending")])
    paper = _Paper([_abc_show_all_main_pdf(10, "WB"), *_pair(20, "WB"), _unconverted(30, "pdf")],
                   payloads={"21": GOOD_TABLE})

    client, add, outcomes = await _run(
        chosen=[_chosen("20", converted_md5="md5-21"), _chosen("30")], abc_answers=[running],
        paper=paper, last_answer_payload=running, clock=clock,
    )

    # 10,800 s at a 30 s poll: at most 360 requests, then the converted file joins.
    assert client.requests <= 360
    assert [file.provider_file_id for file in add.calls[0]] == ["20"]
    assert _final_entries(job) == {"20": ("added", None), "30": ("left_out", IN_TIME)}
    assert outcomes.ended == ["completed"]


@pytest.mark.asyncio
async def test_the_login_reserve_ends_the_wait_before_the_limit(job):
    clock = _Clock()
    running = _answer_payload("running", [_job_entry(30, "supplement", "pending")])
    paper = _Paper([_abc_show_all_main_pdf(10, "WB"), _unconverted(30, "pdf")])

    client, add, outcomes = await _run(
        chosen=[_chosen("30")], abc_answers=[running], paper=paper, last_answer_payload=running,
        clock=clock, login_expires_in=900 + 600,  # 10 minutes of wait left before the reserve
    )

    assert client.requests <= 20
    assert _final_entries(job) == {"30": ("left_out", IN_TIME)}
    assert job.progress[-1]["metadata"]["supplement_wait_ends_at"] == stage._iso(1_000_000.0 + 600)


@pytest.mark.asyncio
@pytest.mark.parametrize(("error", "expected"), [
    # Any other ABC error: the waiting file is not converted in time; the converted one joins.
    (DocumentSourceUnavailable("ABC Literature or its file storage could not be reached"),
     {"20": ("added", None), "30": ("left_out", IN_TIME)}),
    # The login was refused: nothing more can be asked of ABC with it, the add included.
    (DocumentSourceLoginExpired("ABC Literature conversion request failed"),
     {"20": ("left_out", "login_expired"), "30": ("left_out", "login_expired")}),
], ids=["abc_502", "login_expired"])
async def test_the_first_abc_error_ends_the_wait_without_retrying(job, error, expected):
    clock = _Clock()
    running = _answer_payload("running", [_job_entry(30, "supplement", "pending")])
    paper = _Paper([_abc_show_all_main_pdf(10, "WB"), *_pair(20, "WB"), _unconverted(30, "pdf")],
                   payloads={"21": GOOD_TABLE})

    class _Raising(_AbcClient):
        async def request_referencefile_conversion(self, reference, *, wait=False, request_bearer_token=None):
            self.requests += 1
            raise error

    client = _Raising([running])
    provider = provider_from_fake(client)
    provider.request_conversion = client.request_referencefile_conversion  # the adapter's mapped error
    client.conversion_payload = running
    add, outcomes = _Add(), _Outcomes()
    from agr_ai_curation_alliance.document_sources.abc_literature import _map_conversion_result

    await stage.run_supplement_stage(
        job_id=JOB_ID, document_id=DOCUMENT_ID, owner_user_id=7, reference="AGRKB:101",
        plan=SupplementPlan(chosen=(_chosen("20", converted_md5="md5-21"), _chosen("30")),
                                  request_context=_context(), login_expires_at=clock() + 86400,
                                  owner_user_id=7, reference="AGRKB:101"),
        conversion_requested=True, last_answer=_map_conversion_result(running, provider="abc_literature"),
        main_ready_at=clock(), provider=provider, weaviate_client=object(), outcomes=outcomes,
        list_files=paper.list_files, add_files=add, clock=clock, sleep=clock.sleep,
    )

    assert client.requests == 1
    assert _final_entries(job) == expected
    assert outcomes.ended == ["completed"]


@pytest.mark.asyncio
async def test_a_cancel_during_the_wait_ends_the_job_cancelled_and_adds_nothing(job):
    clock = _Clock()
    running = _answer_payload("running", [_job_entry(30, "supplement", "pending")])
    paper = _Paper([_abc_show_all_main_pdf(10, "WB"), _unconverted(30, "pdf")])

    async def _sleep_then_cancel(seconds):
        await clock.sleep(seconds)
        job.cancel_requested = True

    client = _AbcClient([running])
    provider = provider_from_fake(client)
    client.conversion_payload = running
    last_answer = await provider.request_conversion("AGRKB:101", request_bearer_token="curator-token")
    add, outcomes = _Add(), _Outcomes()
    await stage.run_supplement_stage(
        job_id=JOB_ID, document_id=DOCUMENT_ID, owner_user_id=7, reference="AGRKB:101",
        plan=SupplementPlan(chosen=(_chosen("30"),), request_context=_context(),
                                  login_expires_at=clock() + 86400, owner_user_id=7, reference="AGRKB:101"),
        conversion_requested=True, last_answer=last_answer, main_ready_at=clock(),
        provider=provider, weaviate_client=object(), outcomes=outcomes,
        list_files=paper.list_files, add_files=add, clock=clock, sleep=_sleep_then_cancel,
    )

    assert add.calls == []
    assert outcomes.ended == ["cancelled"]
    assert _final_entries(job) == {"30": ("left_out", "import_cancelled")}


@pytest.mark.asyncio
async def test_a_wait_finalized_elsewhere_writes_nothing_more(job):
    """A dead-wait cancel finalized the job while this stage was still sleeping."""

    clock = _Clock()
    running = _answer_payload("running", [_job_entry(30, "supplement", "pending")])
    paper = _Paper([_abc_show_all_main_pdf(10, "WB"), _unconverted(30, "pdf")])

    async def _sleep_then_finalize(seconds):
        await clock.sleep(seconds)
        job.status = "cancelled"

    client = _AbcClient([running])
    provider = provider_from_fake(client)
    client.conversion_payload = running
    last_answer = await provider.request_conversion("AGRKB:101", request_bearer_token="curator-token")
    client.requests = 0
    add, outcomes = _Add(), _Outcomes()
    await stage.run_supplement_stage(
        job_id=JOB_ID, document_id=DOCUMENT_ID, owner_user_id=7, reference="AGRKB:101",
        plan=SupplementPlan(chosen=(_chosen("30"),), request_context=_context(),
                                  login_expires_at=clock() + 86400, owner_user_id=7, reference="AGRKB:101"),
        conversion_requested=True, last_answer=last_answer, main_ready_at=clock(),
        provider=provider, weaviate_client=object(), outcomes=outcomes,
        list_files=paper.list_files, add_files=add, clock=clock, sleep=_sleep_then_finalize,
    )
    progress_after_start = len(job.progress)

    assert client.requests == 0
    assert add.calls == []
    assert outcomes.ended == []
    assert progress_after_start == 1  # only the first "waiting" record


@pytest.mark.asyncio
async def test_every_poll_records_activity_in_the_supplement_wait_stage(job):
    clock = _Clock()
    running = _answer_payload("running", [_job_entry(30, "supplement", "pending")])
    paper = _Paper([_abc_show_all_main_pdf(10, "WB"), _unconverted(30, "pdf")])

    client, _add, _outcomes = await _run(
        chosen=[_chosen("30")], abc_answers=[running], paper=paper, last_answer_payload=running,
        clock=clock, login_expires_in=900 + 300,
    )

    waiting = [p for p in job.progress if p["stage"] == "waiting_for_supplements"]
    assert len(waiting) >= client.requests + 1


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
    monkeypatch.setattr(stage, "get_document_source_poll_interval_seconds", lambda: 0.01)
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
async def test_a_stage_queued_past_its_wait_limit_ends_at_once_asking_abc_nothing(job):
    clock = _Clock()
    paper = _Paper([_abc_show_all_main_pdf(10, "WB"), *_pair(20, "WB"), _unconverted(30, "pdf")],
                   payloads={"21": GOOD_TABLE})
    client, provider = _plain_provider()

    add, outcomes = await _stage(
        job, chosen=[_chosen("20", converted_md5="md5-21"), _chosen("30")], paper=paper,
        main_ready_at=clock() - 10_801, clock=clock, provider=provider,
    )

    assert (paper.listings, client.requests, add.calls) == (0, 0, [])
    assert _final_entries(job) == {"20": ("left_out", "wait_limit_reached"), "30": ("left_out", IN_TIME)}
    assert outcomes.ended == ["completed"]


@pytest.mark.asyncio
async def test_a_stage_whose_login_is_inside_the_reserve_ends_at_once_asking_abc_nothing(job):
    clock = _Clock()
    paper = _Paper([_abc_show_all_main_pdf(10, "WB"), *_pair(20, "WB")], payloads={"21": GOOD_TABLE})
    client, provider = _plain_provider()

    add, outcomes = await _stage(
        job, chosen=[_chosen("20", converted_md5="md5-21")], paper=paper, main_ready_at=clock(),
        clock=clock, provider=provider, login_expires_at=clock() + 300,  # inside the 900 s reserve
    )

    assert (paper.listings, client.requests, add.calls) == (0, 0, [])
    assert _final_entries(job) == {"20": ("left_out", "login_expired")}
    assert outcomes.ended == ["completed"]


@pytest.mark.asyncio
async def test_a_changed_abc_job_id_stops_every_further_request(job):
    """ABC lost the job this import started and began a new one: ask nothing more."""

    clock = _Clock()
    first = _answer_payload("running", [_job_entry(30, "supplement", "pending")])
    restarted = {**first, "job_id": "made-up-new-job", "previous_job_id": "made-up-job"}
    paper = _Paper([_abc_show_all_main_pdf(10, "WB"), _unconverted(30, "pdf")])

    client, add, outcomes = await _run(
        chosen=[_chosen("30")], abc_answers=[restarted], paper=paper,
        last_answer_payload=first, clock=clock,
    )

    assert client.requests == 1
    assert _final_entries(job) == {"30": ("left_out", "provider_job_restarted")}
    assert outcomes.ended == ["completed"]


@pytest.mark.asyncio
async def test_an_answer_abc_does_not_send_today_ends_the_wait_with_its_own_reason(job):
    clock = _Clock()
    first = _answer_payload("running", [_job_entry(30, "supplement", "pending")])
    unknown = _answer_payload("running", [{**_job_entry(30, "supplement", "pending"), "status": "skipped"}])
    paper = _Paper([_abc_show_all_main_pdf(10, "WB"), _unconverted(30, "pdf")])

    client, _add, outcomes = await _run(
        chosen=[_chosen("30")], abc_answers=[unknown], paper=paper,
        last_answer_payload=first, clock=clock,
    )

    assert client.requests == 1
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
        job, chosen=[_chosen("20", converted_md5="md5-21")], paper=paper, main_ready_at=clock(),
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
        job, chosen=[_chosen("20", converted_md5="md5-21")], paper=paper, main_ready_at=clock(),
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
            job, chosen=[_chosen("20", converted_md5="md5-21")], paper=paper, main_ready_at=clock(),
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
        job, chosen=[_chosen("20", converted_md5="md5-21")], paper=paper, main_ready_at=clock(),
        clock=clock, provider=provider, add=_call,
    )

    (entry,) = job.progress[-1]["metadata"]["supplements"]
    assert (entry["state"], entry["reason_code"], entry["measured_value"], entry["limit_value"]) == (
        "left_out", "too_large", 2_000_000, 1_000_000,
    )


@pytest.mark.asyncio
async def test_queued_stages_are_served_in_the_order_they_arrived(job, monkeypatch):
    monkeypatch.setenv("DOCUMENT_SUPPLEMENT_MAX_CONCURRENT_WAITS", "1")
    monkeypatch.setattr(stage, "get_document_source_poll_interval_seconds", lambda: 0.01)
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

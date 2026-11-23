"""Endpoint wiring for adding files to a paper (P7c1): codes only, one job, owner only."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks, HTTPException
from pydantic import ValidationError
from starlette.requests import Request

from src.api import documents
from src.lib.document_sources import supplement_add
from src.lib.document_sources.access import DocumentSourceRequestContext
from src.lib.document_sources.models import ProviderBearerKind
from src.lib.pdf_jobs.service import ActiveJobExistsError
from src.schemas.document_files import AddFilesRequest
from src.schemas.pdf_jobs import PdfJobResponse

DOCUMENT_ID = str(uuid4())
PAYLOAD = AddFilesRequest(files=[{"provider_file_id": "20", "md5": "md5-20", "converted_md5": "md5-21"}])


def _request() -> Request:
    return Request({"type": "http", "method": "POST", "path": f"/weaviate/documents/{DOCUMENT_ID}/files", "headers": []})


def _context(token: str | None = "curator-token") -> DocumentSourceRequestContext:
    return DocumentSourceRequestContext(
        provider_groups=("group-a",), authorized_group_ids=("group-a",), bearer_kind=ProviderBearerKind.CURATOR,
        curator_token=token,
    )


def _job() -> PdfJobResponse:
    now = datetime(2026, 11, 22, tzinfo=timezone.utc)
    return PdfJobResponse(
        job_id=str(uuid4()), document_id=DOCUMENT_ID, user_id=7, status="pending", created_at=now, updated_at=now,
    )


@pytest.fixture
def wired(monkeypatch):
    monkeypatch.setattr(documents, "external_document_source_import_enabled", lambda: True)

    async def provision(_principal):
        return SimpleNamespace(id=7, auth_sub="owner-subject")

    monkeypatch.setattr(documents, "provision_user_async", provision)
    monkeypatch.setattr(documents, "principal_from_claims", lambda claims: claims)
    contexts = {"value": _context()}

    async def context(**_):
        return contexts["value"]

    monkeypatch.setattr(documents, "_document_source_context_or_503", context)
    return contexts


async def _call(background=None):
    return await documents.add_files_to_paper_endpoint(
        PAYLOAD, background or BackgroundTasks(), _request(), document_id=DOCUMENT_ID, user={"sub": "owner-subject"},
    )


@pytest.mark.asyncio
async def test_an_add_starts_one_job_and_queues_the_add(monkeypatch, wired):
    job = _job()
    started = []
    monkeypatch.setattr(supplement_add, "start_supplement_add", lambda *args: started.append(args) or job)
    background = BackgroundTasks()

    assert await _call(background) is job
    (document_id, owner_id, files), = started
    assert str(document_id) == DOCUMENT_ID and owner_id == 7
    assert files == (supplement_add.ChosenFile("20", "md5-20", "md5-21"),)
    (task,) = background.tasks
    request = task.args[0]
    assert str(request.job_id) == job.job_id and request.user_id == "owner-subject" and request.files == files


@pytest.mark.asyncio
async def test_a_second_active_job_is_a_409(monkeypatch, wired):
    def busy(*_):
        raise ActiveJobExistsError(DOCUMENT_ID)

    monkeypatch.setattr(supplement_add, "start_supplement_add", busy)
    with pytest.raises(HTTPException) as raised:
        await _call()
    assert (raised.value.status_code, raised.value.detail) == (409, {"error": "import_in_progress"})


@pytest.mark.asyncio
async def test_too_many_files_is_a_422_with_the_limit(monkeypatch, wired):
    def refuse(*_):
        raise supplement_add.SupplementAddRequestError("paper_file_limit", limit_value=20)

    monkeypatch.setattr(supplement_add, "start_supplement_add", refuse)
    with pytest.raises(HTTPException) as raised:
        await _call()
    assert raised.value.status_code == 422
    assert raised.value.detail == {"error": "paper_file_limit", "limit_value": 20}


@pytest.mark.asyncio
async def test_no_curator_login_means_no_add(monkeypatch, wired):
    wired["value"] = _context(token=None)
    monkeypatch.setattr(supplement_add, "start_supplement_add", lambda *_: pytest.fail("no job without a login"))
    with pytest.raises(HTTPException) as raised:
        await _call()
    assert raised.value.detail == {"error": "document_source_curator_token_unavailable"}


@pytest.mark.asyncio
async def test_the_add_is_refused_when_provider_import_is_disabled(monkeypatch):
    monkeypatch.setattr(documents, "external_document_source_import_enabled", lambda: False)
    with pytest.raises(HTTPException) as raised:
        await _call()
    assert raised.value.status_code == 503


@pytest.mark.parametrize(
    "body",
    [
        {"files": [{"provider_file_id": "20", "md5": None}]},
        {"files": [{"provider_file_id": "20", "converted_md5": "m", "extra": 1}]},
    ],
)
def test_the_request_shape_is_enforced(body):
    with pytest.raises(ValidationError):
        AddFilesRequest.model_validate(body)


def test_the_same_file_twice_is_refused_before_any_job():
    chosen = supplement_add.ChosenFile("20", "md5-20", "md5-21")
    with pytest.raises(supplement_add.SupplementAddRequestError) as raised:
        supplement_add.start_supplement_add(uuid4(), 7, [chosen, chosen])
    assert raised.value.code == "file_chosen_twice"


@pytest.mark.asyncio
async def test_an_empty_list_is_the_typed_no_files_chosen(monkeypatch, wired):
    with pytest.raises(HTTPException) as raised:
        await documents.add_files_to_paper_endpoint(
            AddFilesRequest(files=[]), BackgroundTasks(), _request(), document_id=DOCUMENT_ID,
            user={"sub": "owner-subject"},
        )
    assert (raised.value.status_code, raised.value.detail["error"]) == (422, "no_files_chosen")

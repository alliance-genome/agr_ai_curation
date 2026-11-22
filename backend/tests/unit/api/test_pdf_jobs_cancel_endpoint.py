"""What the cancel endpoint tells the curator (KANBAN-1976 / ALL-1460, P7w).

A cancel of a provider wait that no task is polling is finalized in the same
call, so the endpoint must report the curator's own cancel, not "already
terminal". All values are made up.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.api import pdf_jobs as api
from src.schemas.pdf_jobs import PdfJobResponse


def _job(status: str) -> PdfJobResponse:
    now = datetime.now(timezone.utc)
    return PdfJobResponse(
        job_id=str(uuid4()), document_id=str(uuid4()), user_id=7, status=status,
        created_at=now, updated_at=now,
    )


async def _cancel(monkeypatch, before: str, after: str):
    async def _user_id(_user):
        return 7

    monkeypatch.setattr(api, "_resolve_db_user_id", _user_id)
    monkeypatch.setattr(api.job_service, "get_job", lambda **_kwargs: _job(before))
    monkeypatch.setattr(api.job_service, "request_cancel", lambda **_kwargs: _job(after))
    return await api.cancel_pdf_job(job_id=str(uuid4()), user={"sub": "curator"})


@pytest.mark.asyncio
async def test_a_cancel_that_ended_a_stopped_wait_is_reported_as_a_cancel(monkeypatch):
    response = await _cancel(monkeypatch, before="running", after="cancelled")

    assert response.message == "Cancelled; the import had stopped running and is now ended"


@pytest.mark.asyncio
async def test_a_cancel_of_a_living_job_is_still_a_request(monkeypatch):
    response = await _cancel(monkeypatch, before="running", after="cancel_requested")

    assert response.message == (
        "Cancellation requested; background processing termination is best-effort"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
async def test_a_job_that_was_already_terminal_still_says_so(monkeypatch, status):
    response = await _cancel(monkeypatch, before=status, after=status)

    assert response.message == f"Job already terminal ({status}); no cancellation needed"

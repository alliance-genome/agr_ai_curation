"""PDF REST and SSE connection setup await the same scalar provisioning boundary."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from src.api import pdf_jobs, documents


@pytest.mark.asyncio
async def test_rest_and_stream_connection_provision_before_dispatch(monkeypatch):
    provision = AsyncMock(return_value=SimpleNamespace(id=23))
    monkeypatch.setattr(pdf_jobs, 'provision_user_async', provision)
    listing = Mock(return_value=SimpleNamespace(jobs=[]))
    monkeypatch.setattr(pdf_jobs.job_service, 'list_jobs', listing)
    user = {'sub': 'verified-subject'}
    result = await pdf_jobs.list_pdf_jobs(user=user, status=None, window_days=7, limit=50, offset=0)
    assert result.jobs == []
    assert listing.call_args.kwargs['user_id'] == 23
    provision.assert_awaited_once()
    response = await pdf_jobs.stream_pdf_jobs(user=user, window_days=7, limit=50)
    assert provision.await_count == 2
    # Connection setup does not start snapshots or reschedule the stream.
    assert listing.call_count == 1
    await response.body_iterator.aclose()
    assert provision.call_args.args[0].subject == 'verified-subject'


@pytest.mark.asyncio
async def test_preprovisioned_ownership_keeps_request_session_in_caller(monkeypatch):
    provision = AsyncMock(return_value=SimpleNamespace(id=29))
    monkeypatch.setattr(documents, 'provision_user_async', provision)
    owned = Mock(return_value=object())
    monkeypatch.setattr(documents, 'require_owned_document', owned)
    request_session = object()
    document_id = uuid4()
    result = documents.verify_document_ownership(
        request_session, str(document_id), {'sub': 'verified-subject'}, for_update=True, provisioned_user_id=29,
    )
    assert result is owned.return_value
    owned.assert_called_once_with(request_session, document_id, 29, for_update=True)
    provision.assert_not_called()

"""Unit tests for batch API endpoints."""

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from src.api import batch as batch_api
from src.models.sql.batch import BatchDocumentStatus, BatchStatus
from src.schemas.batch import BatchCreateRequest, BatchResponse, BatchValidationResponse


def _mock_auth(monkeypatch, user_id=11):
    monkeypatch.setattr(batch_api, "principal_from_claims", lambda claims: SimpleNamespace(subject=claims["sub"]))
    monkeypatch.setattr(batch_api, "provision_user", lambda *_args, **_kwargs: SimpleNamespace(id=user_id))


def _make_batch_response(batch_id=None, flow_id=None, status=BatchStatus.PENDING):
    now = datetime.now(timezone.utc)
    return BatchResponse(
        id=batch_id or uuid4(),
        flow_id=flow_id or uuid4(),
        flow_name="Test Flow",
        status=status,
        total_documents=1,
        completed_documents=0 if status != BatchStatus.COMPLETED else 1,
        failed_documents=0,
        created_at=now,
        started_at=now,
        completed_at=now if status in (BatchStatus.COMPLETED, BatchStatus.CANCELLED) else None,
        documents=[],
    )


def test_batch_create_request_limits_document_ids_to_ten():
    flow_id = uuid4()
    doc_ids = [uuid4() for _ in range(11)]

    with pytest.raises(ValidationError):
        BatchCreateRequest(flow_id=flow_id, document_ids=doc_ids)


@pytest.mark.asyncio
async def test_create_batch_success(monkeypatch):
    _mock_auth(monkeypatch, user_id=42)
    flow_id = uuid4()
    doc_id = uuid4()
    batch_id = uuid4()

    flow = SimpleNamespace(id=flow_id, user_id=42, is_active=True, name="My Flow", flow_definition={"nodes": []})
    found_doc = SimpleNamespace(id=doc_id)

    flow_query = SimpleNamespace(filter=lambda *_args, **_kwargs: SimpleNamespace(first=lambda: flow))
    docs_query = SimpleNamespace(filter=lambda *_args, **_kwargs: SimpleNamespace(all=lambda: [found_doc]))
    db = SimpleNamespace(query=lambda model: flow_query if model is batch_api.CurationFlow else docs_query)

    monkeypatch.setattr(batch_api, "validate_flow_for_batch", lambda *_args, **_kwargs: BatchValidationResponse(valid=True, errors=[]))

    service = SimpleNamespace(
        create_batch=lambda **_kwargs: SimpleNamespace(id=batch_id),
        batch_to_response=lambda *_args, **_kwargs: _make_batch_response(batch_id=batch_id, flow_id=flow_id),
    )
    monkeypatch.setattr(batch_api, "BatchService", lambda _db: service)

    background_tasks = BackgroundTasks()
    request = BatchCreateRequest(flow_id=flow_id, document_ids=[doc_id])
    result = await batch_api.create_batch(request, background_tasks, {"sub": "u-1"}, db)

    assert result.id == batch_id
    assert len(background_tasks.tasks) == 1
    assert background_tasks.tasks[0].func is batch_api.process_batch_task
    assert background_tasks.tasks[0].args == (batch_id,)


@pytest.mark.asyncio
async def test_create_batch_rejects_missing_flow(monkeypatch):
    _mock_auth(monkeypatch)
    flow_id = uuid4()
    doc_id = uuid4()

    flow_query = SimpleNamespace(filter=lambda *_args, **_kwargs: SimpleNamespace(first=lambda: None))
    db = SimpleNamespace(query=lambda _model: flow_query)

    with pytest.raises(HTTPException) as exc:
        await batch_api.create_batch(
            BatchCreateRequest(flow_id=flow_id, document_ids=[doc_id]),
            BackgroundTasks(),
            {"sub": "u-1"},
            db,
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_create_batch_rejects_incompatible_flow(monkeypatch):
    _mock_auth(monkeypatch)
    flow_id = uuid4()
    doc_id = uuid4()
    flow = SimpleNamespace(id=flow_id, user_id=11, is_active=True, name="My Flow", flow_definition={"nodes": []})

    flow_query = SimpleNamespace(filter=lambda *_args, **_kwargs: SimpleNamespace(first=lambda: flow))
    db = SimpleNamespace(query=lambda _model: flow_query)
    monkeypatch.setattr(
        batch_api,
        "validate_flow_for_batch",
        lambda *_args, **_kwargs: BatchValidationResponse(valid=False, errors=["unsupported tool"]),
    )

    with pytest.raises(HTTPException) as exc:
        await batch_api.create_batch(
            BatchCreateRequest(flow_id=flow_id, document_ids=[doc_id]),
            BackgroundTasks(),
            {"sub": "u-1"},
            db,
        )
    assert exc.value.status_code == 400
    assert "unsupported tool" in exc.value.detail


@pytest.mark.asyncio
async def test_create_batch_rejects_empty_document_ids(monkeypatch):
    _mock_auth(monkeypatch)
    flow_id = uuid4()
    flow = SimpleNamespace(id=flow_id, user_id=11, is_active=True, name="My Flow", flow_definition={"nodes": []})

    flow_query = SimpleNamespace(filter=lambda *_args, **_kwargs: SimpleNamespace(first=lambda: flow))
    db = SimpleNamespace(query=lambda _model: flow_query)
    monkeypatch.setattr(batch_api, "validate_flow_for_batch", lambda *_args, **_kwargs: BatchValidationResponse(valid=True, errors=[]))

    request = BatchCreateRequest.model_construct(flow_id=flow_id, document_ids=[])
    with pytest.raises(HTTPException) as exc:
        await batch_api.create_batch(request, BackgroundTasks(), {"sub": "u-1"}, db)
    assert exc.value.status_code == 400
    assert "At least one document ID" in exc.value.detail


@pytest.mark.asyncio
async def test_create_batch_rejects_missing_documents_with_truncation(monkeypatch):
    _mock_auth(monkeypatch)
    flow_id = uuid4()
    doc_ids = [uuid4() for _ in range(7)]
    flow = SimpleNamespace(id=flow_id, user_id=11, is_active=True, name="My Flow", flow_definition={"nodes": []})

    flow_query = SimpleNamespace(filter=lambda *_args, **_kwargs: SimpleNamespace(first=lambda: flow))
    docs_query = SimpleNamespace(filter=lambda *_args, **_kwargs: SimpleNamespace(all=lambda: []))
    db = SimpleNamespace(query=lambda model: flow_query if model is batch_api.CurationFlow else docs_query)
    monkeypatch.setattr(batch_api, "validate_flow_for_batch", lambda *_args, **_kwargs: BatchValidationResponse(valid=True, errors=[]))

    with pytest.raises(HTTPException) as exc:
        await batch_api.create_batch(
            BatchCreateRequest(flow_id=flow_id, document_ids=doc_ids),
            BackgroundTasks(),
            {"sub": "u-1"},
            db,
        )
    assert exc.value.status_code == 400
    assert "and 2 more" in exc.value.detail


@pytest.mark.asyncio
async def test_running_count_endpoint(monkeypatch):
    _mock_auth(monkeypatch, user_id=8)
    service = SimpleNamespace(
        count_running_batches=lambda user_id: 3 if user_id == 8 else 0,
        get_pending_documents_count=lambda user_id: 12 if user_id == 8 else 0,
    )
    monkeypatch.setattr(batch_api, "BatchService", lambda _db: service)

    payload = await batch_api.get_running_batch_count({"sub": "u-1"}, db=object())
    assert payload == {"running_count": 3, "pending_documents": 12}


@pytest.mark.asyncio
async def test_list_batches_endpoint(monkeypatch):
    _mock_auth(monkeypatch, user_id=8)
    batch_one = SimpleNamespace(id=uuid4())
    batch_two = SimpleNamespace(id=uuid4())
    response_one = _make_batch_response(batch_id=batch_one.id)
    response_two = _make_batch_response(batch_id=batch_two.id)

    service = SimpleNamespace(
        list_batches=lambda _user_id: [batch_one, batch_two],
        batch_to_response=lambda batch: response_one if batch.id == batch_one.id else response_two,
    )
    monkeypatch.setattr(batch_api, "BatchService", lambda _db: service)

    payload = await batch_api.list_batches({"sub": "u-1"}, db=object())
    assert payload.total == 2
    assert [b.id for b in payload.batches] == [batch_one.id, batch_two.id]


@pytest.mark.asyncio
async def test_get_batch_endpoint_404(monkeypatch):
    _mock_auth(monkeypatch, user_id=8)
    service = SimpleNamespace(get_batch=lambda *_args, **_kwargs: None)
    monkeypatch.setattr(batch_api, "BatchService", lambda _db: service)

    with pytest.raises(HTTPException) as exc:
        await batch_api.get_batch(uuid4(), {"sub": "u-1"}, db=object())
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_cancel_batch_endpoint_maps_value_error(monkeypatch):
    _mock_auth(monkeypatch, user_id=8)

    def _raise(*_args, **_kwargs):
        raise ValueError("Cannot cancel batch")

    service = SimpleNamespace(cancel_batch=_raise)
    monkeypatch.setattr(batch_api, "BatchService", lambda _db: service)

    with pytest.raises(HTTPException) as exc:
        await batch_api.cancel_batch(uuid4(), {"sub": "u-1"}, db=object())
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_cancel_batch_endpoint_404_when_missing(monkeypatch):
    _mock_auth(monkeypatch, user_id=8)
    service = SimpleNamespace(cancel_batch=lambda *_args, **_kwargs: None)
    monkeypatch.setattr(batch_api, "BatchService", lambda _db: service)

    with pytest.raises(HTTPException) as exc:
        await batch_api.cancel_batch(uuid4(), {"sub": "u-1"}, db=object())
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_delete_batch_placeholder_returns_501():
    with pytest.raises(HTTPException) as exc:
        await batch_api.delete_batch(uuid4(), {"sub": "u-1"}, db=object())
    assert exc.value.status_code == 501


@pytest.mark.asyncio
async def test_validate_flow_for_batch_endpoint_404(monkeypatch):
    _mock_auth(monkeypatch, user_id=8)
    db = SimpleNamespace(
        query=lambda _model: SimpleNamespace(filter=lambda *_args, **_kwargs: SimpleNamespace(first=lambda: None))
    )

    with pytest.raises(HTTPException) as exc:
        await batch_api.validate_flow_for_batch_endpoint(uuid4(), {"sub": "u-1"}, db)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_validate_flow_for_batch_endpoint_success(monkeypatch):
    _mock_auth(monkeypatch, user_id=8)
    flow_id = uuid4()
    flow = SimpleNamespace(id=flow_id, user_id=8, is_active=True, flow_definition={"nodes": []})
    db = SimpleNamespace(
        query=lambda _model: SimpleNamespace(filter=lambda *_args, **_kwargs: SimpleNamespace(first=lambda: flow))
    )
    monkeypatch.setattr(
        batch_api,
        "validate_flow_for_batch",
        lambda *_args, **_kwargs: BatchValidationResponse(valid=True, errors=[]),
    )

    payload = await batch_api.validate_flow_for_batch_endpoint(flow_id, {"sub": "u-1"}, db)
    assert payload.valid is True
    assert payload.errors == []


@pytest.mark.asyncio
async def test_download_batch_zip_404_when_batch_missing(monkeypatch):
    _mock_auth(monkeypatch, user_id=8)
    service = SimpleNamespace(get_batch=lambda *_args, **_kwargs: None)
    monkeypatch.setattr(batch_api, "BatchService", lambda _db: service)

    with pytest.raises(HTTPException) as exc:
        await batch_api.download_batch_zip(uuid4(), request=object(), user={"sub": "u-1"}, db=object())
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_download_batch_zip_400_when_no_completed_docs(monkeypatch):
    _mock_auth(monkeypatch, user_id=8)
    batch = SimpleNamespace(documents=[SimpleNamespace(status=BatchDocumentStatus.PENDING, result_file_path=None)])
    service = SimpleNamespace(get_batch=lambda *_args, **_kwargs: batch)
    monkeypatch.setattr(batch_api, "BatchService", lambda _db: service)

    with pytest.raises(HTTPException) as exc:
        await batch_api.download_batch_zip(uuid4(), request=object(), user={"sub": "u-1"}, db=object())
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_download_batch_zip_success_with_matching_file(monkeypatch, tmp_path):
    _mock_auth(monkeypatch, user_id=8)
    batch_id = uuid4()
    file_id = uuid4()
    file_path = tmp_path / "result.csv"
    file_path.write_text("a,b\n1,2\n")

    completed_doc = SimpleNamespace(
        status=BatchDocumentStatus.COMPLETED,
        result_file_path=f"/api/files/{file_id}/download",
        document_id=uuid4(),
        position=0,
    )
    batch = SimpleNamespace(id=batch_id, documents=[completed_doc])
    service = SimpleNamespace(get_batch=lambda *_args, **_kwargs: batch)
    monkeypatch.setattr(batch_api, "BatchService", lambda _db: service)

    file_output = SimpleNamespace(id=file_id, curator_id="u-1", file_path=str(file_path), filename="result.csv")
    db = SimpleNamespace(
        query=lambda _model: SimpleNamespace(filter=lambda *_args, **_kwargs: SimpleNamespace(first=lambda: file_output))
    )
    monkeypatch.setattr(
        "src.lib.file_outputs.storage.FileOutputStorageService",
        lambda: SimpleNamespace(base_path=tmp_path),
    )

    response = await batch_api.download_batch_zip(batch_id, request=object(), user={"sub": "u-1"}, db=db)
    assert isinstance(response, StreamingResponse)
    assert response.headers["content-disposition"].endswith(f'batch_{batch_id}_results.zip"')

    body = await _read_streaming_response(response)
    assert body.startswith(b"PK")
    assert b"result.csv" in body


@pytest.mark.asyncio
async def test_download_batch_zip_400_when_completed_docs_exist_but_none_downloadable(monkeypatch, tmp_path):
    _mock_auth(monkeypatch, user_id=8)
    batch_id = uuid4()
    file_id = uuid4()
    file_path = tmp_path / "result.csv"
    file_path.write_text("a,b\n1,2\n")

    completed_doc = SimpleNamespace(
        status=BatchDocumentStatus.COMPLETED,
        result_file_path=f"/api/files/{file_id}/download",
        document_id=uuid4(),
        position=0,
    )
    batch = SimpleNamespace(id=batch_id, documents=[completed_doc])
    service = SimpleNamespace(get_batch=lambda *_args, **_kwargs: batch)
    monkeypatch.setattr(batch_api, "BatchService", lambda _db: service)

    # Ownership mismatch means the file is skipped from the ZIP.
    file_output = SimpleNamespace(id=file_id, curator_id="different-user", file_path=str(file_path), filename="result.csv")
    db = SimpleNamespace(
        query=lambda _model: SimpleNamespace(filter=lambda *_args, **_kwargs: SimpleNamespace(first=lambda: file_output))
    )
    monkeypatch.setattr(
        "src.lib.file_outputs.storage.FileOutputStorageService",
        lambda: SimpleNamespace(base_path=tmp_path),
    )

    with pytest.raises(HTTPException) as exc:
        await batch_api.download_batch_zip(batch_id, request=object(), user={"sub": "u-1"}, db=db)
    assert exc.value.status_code == 400
    assert "No downloadable result files" in exc.value.detail


@pytest.mark.asyncio
async def test_stream_batch_progress_404_when_batch_missing(monkeypatch):
    _mock_auth(monkeypatch, user_id=8)
    service = SimpleNamespace(get_batch=lambda *_args, **_kwargs: None)
    monkeypatch.setattr(batch_api, "BatchService", lambda _db: service)

    with pytest.raises(HTTPException) as exc:
        await batch_api.stream_batch_progress(uuid4(), {"sub": "u-1"}, db=object())
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_stream_batch_progress_sanitizes_internal_errors(monkeypatch):
    _mock_auth(monkeypatch, user_id=8)
    batch_id = uuid4()
    batch = SimpleNamespace(
        id=batch_id,
        status=BatchStatus.RUNNING,
        total_documents=1,
        completed_documents=0,
        failed_documents=0,
        documents=[],
    )
    service = SimpleNamespace(get_batch=lambda *_args, **_kwargs: batch)
    monkeypatch.setattr(batch_api, "BatchService", lambda _db: service)

    class _BrokenBroadcaster:
        async def subscribe(self, _batch_id):
            raise RuntimeError("db credentials leaked")

        async def unsubscribe(self, _batch_id, _queue):
            return None

    monkeypatch.setattr(batch_api, "get_batch_broadcaster", lambda: _BrokenBroadcaster())

    response = await batch_api.stream_batch_progress(batch_id, {"sub": "u-1"}, db=object())
    body = await _read_streaming_response(response)
    assert b"Batch stream encountered an internal error" in body
    assert b"db credentials leaked" not in body


async def _read_streaming_response(response):
    chunks = []
    async for chunk in response.body_iterator:
        if isinstance(chunk, bytes):
            chunks.append(chunk)
        else:
            chunks.append(chunk.encode("utf-8"))
    return b"".join(chunks)

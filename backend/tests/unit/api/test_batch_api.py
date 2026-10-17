"""Unit tests for batch API endpoints."""

from unittest.mock import AsyncMock, MagicMock
from contextlib import nullcontext

import asyncio
from datetime import datetime, timezone
import io
import json
import logging
from types import SimpleNamespace
from uuid import uuid4
import zipfile

import pytest
from fastapi import BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from src.api import batch as batch_api
from src.lib.batch import streaming as batch_stream
from src.lib.batch import download as batch_download
from src.models.sql.batch import BatchDocumentStatus, BatchStatus
from src.schemas.batch import (
    BatchCreateRequest,
    BatchResponse,
    BatchResultFile,
    BatchValidationResponse,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "validate"])
async def test_batch_contract_preflight_stops_before_work(monkeypatch, operation):
    _mock_auth(monkeypatch, user_id=42)
    flow = SimpleNamespace(id=uuid4(), user_id=42, flow_definition={"nodes": []})
    db = SimpleNamespace(query=lambda *_args: SimpleNamespace(
        filter=lambda *_args: SimpleNamespace(first=lambda: flow)))
    finding = {"code": "missing_execution_revision", "node_id": "custom_node", "severity": "error"}
    monkeypatch.setattr(batch_api, "flow_execution_revision_findings", lambda *args, **kwargs: [finding])
    monkeypatch.setattr(batch_api, "BatchService", lambda *_args: pytest.fail("Started batch before preflight"))
    tasks = BackgroundTasks()
    with pytest.raises(HTTPException) as error:
        if operation == "create":
            await batch_api.create_batch(
                BatchCreateRequest(flow_id=flow.id, document_ids=[uuid4()]), tasks,
                {"sub": "user"}, db,
            )
        else:
            await batch_api.validate_flow_for_batch_endpoint(flow.id, {"sub": "user"}, db)
    assert error.value.status_code == 422
    assert error.value.detail == {"code": "flow_execution_contract_invalid", "findings": [finding]}
    assert tasks.tasks == []


def _mock_auth(monkeypatch, user_id=11):
    monkeypatch.setattr(batch_api, "principal_from_claims", lambda claims: SimpleNamespace(subject=claims["sub"]))
    monkeypatch.setattr(batch_api, "provision_user_async", AsyncMock(side_effect=lambda *_args, **_kwargs: SimpleNamespace(id=user_id)))


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


def test_batch_document_status_event_includes_review_session_ids():
    batch = SimpleNamespace(id=uuid4())
    doc = SimpleNamespace(
        document_id=uuid4(),
        id=uuid4(),
        position=0,
        status=BatchDocumentStatus.COMPLETED,
        result_files=[],
        review_session_ids=["session-gene"],
        error_message=None,
        processing_time_ms=1200,
    )

    event = batch_stream._batch_document_status_event(
        batch,
        doc,
        {
            "adapter_keys": ["gene"],
            "extraction_result_ids": ["extract-gene"],
            "extraction_result_refs": [
                {
                    "result_ref": "extraction-result:extract-gene",
                    "extraction_result_id": "extract-gene",
                }
            ],
            "flow_run_id": "flow-run-1",
            "origin_session_id": "origin-1",
        },
    )

    assert event["type"] == "DOCUMENT_STATUS"
    assert event["review_session_ids"] == ["session-gene"]
    assert event["adapter_keys"] == ["gene"]
    assert event["extraction_result_ids"] == ["extract-gene"]
    assert event["extraction_result_refs"][0]["result_ref"] == "extraction-result:extract-gene"
    assert event["flow_run_id"] == "flow-run-1"
    assert event["origin_session_id"] == "origin-1"


def test_batch_partial_document_count_is_subset_of_completed_documents():
    batch = SimpleNamespace(
        documents=[
            SimpleNamespace(output_status="complete"),
            SimpleNamespace(output_status="partial"),
            SimpleNamespace(output_status="failed"),
        ]
    )

    assert batch_stream._batch_partial_document_count(batch) == 1


@pytest.mark.parametrize("result_files", [{"file_id": "not-a-list"}, ["not-a-dict"]])
def test_batch_document_result_files_rejects_corrupt_manifests(result_files):
    document = SimpleNamespace(id=uuid4(), result_files=result_files)

    with pytest.raises(ValueError, match="invalid canonical result_files manifest"):
        batch_download.canonical_result_files(document)


def test_batch_create_request_limits_document_ids_to_ten():
    flow_id = uuid4()
    doc_ids = [uuid4() for _ in range(11)]

    with pytest.raises(ValidationError):
        BatchCreateRequest(flow_id=flow_id, document_ids=doc_ids)


@pytest.mark.parametrize("missing_field", ["file_id", "filename", "download_url"])
def test_batch_result_file_requires_canonical_identity_fields(missing_field):
    payload = {
        "file_id": str(uuid4()),
        "filename": "result.json",
        "download_url": "/api/files/example/download",
    }
    payload.pop(missing_field)

    with pytest.raises(ValidationError):
        BatchResultFile.model_validate(payload)


@pytest.mark.asyncio
async def test_create_batch_success(monkeypatch):
    _mock_auth(monkeypatch, user_id=42)
    flow_id = uuid4()
    doc_id = uuid4()
    batch_id = uuid4()

    flow = SimpleNamespace(id=flow_id, user_id=42, is_active=True, name="My Flow", flow_definition={"nodes": []})
    found_doc = SimpleNamespace(id=doc_id)

    flow_query = SimpleNamespace(filter=lambda *_args, **_kwargs: SimpleNamespace(first=lambda: flow))
    document_filters = []

    def filter_documents(*conditions):
        document_filters.extend(conditions)
        return SimpleNamespace(all=lambda: [found_doc])

    docs_query = SimpleNamespace(filter=filter_documents)
    db = SimpleNamespace(query=lambda model: flow_query if model is batch_api.CurationFlow else docs_query)

    monkeypatch.setattr(batch_api, "validate_flow_for_batch", lambda *_args, **_kwargs: BatchValidationResponse(valid=True, errors=[]))
    monkeypatch.setattr(
        batch_api,
        "get_groups_from_provider_groups",
        lambda provider_groups: ["group-a"] if provider_groups == ["provider-a"] else [],
    )

    create_kwargs = {}
    service = SimpleNamespace(
        create_batch=lambda **kwargs: create_kwargs.update(kwargs) or SimpleNamespace(id=batch_id),
        batch_to_response=lambda *_args, **_kwargs: _make_batch_response(batch_id=batch_id, flow_id=flow_id),
    )
    monkeypatch.setattr(batch_api, "BatchService", lambda _db: service)

    background_tasks = BackgroundTasks()
    request = BatchCreateRequest(flow_id=flow_id, document_ids=[doc_id])
    result = await batch_api.create_batch(
        request,
        background_tasks,
        {"sub": "u-1", "cognito:groups": ["provider-a"]},
        db,
    )

    assert result.id == batch_id
    assert len(background_tasks.tasks) == 1
    assert getattr(background_tasks.tasks[0].func, "__observability_original_task__") is batch_api.process_batch_task
    assert getattr(background_tasks.tasks[0].func, "__observability_task_name__") == "batch.process_batch"
    assert background_tasks.tasks[0].args == (batch_id,)
    assert create_kwargs["active_group_ids"] == ["group-a"]
    assert any(condition.compare(
        batch_api.PDFDocument.viewer_mode.is_distinct_from("benchmark_frozen")
    ) for condition in document_filters)


@pytest.mark.asyncio
async def test_create_batch_rejects_inaccessible_flow_agents(monkeypatch):
    _mock_auth(monkeypatch, user_id=42)
    flow_id = uuid4()
    flow = SimpleNamespace(
        id=flow_id,
        user_id=42,
        is_active=True,
        name="Restricted flow",
        flow_definition={"nodes": [{"data": {"agent_id": "restricted-agent"}}]},
    )
    flow_query = SimpleNamespace(
        filter=lambda *_args, **_kwargs: SimpleNamespace(first=lambda: flow)
    )
    db = SimpleNamespace(query=lambda _model: flow_query)
    monkeypatch.setattr(
        batch_api,
        "get_groups_from_provider_groups",
        lambda _groups: ["group-a"],
    )
    monkeypatch.setattr(
        batch_api,
        "inaccessible_flow_agent_keys",
        lambda *_args, **_kwargs: ["restricted-agent"],
    )

    with pytest.raises(HTTPException) as exc_info:
        await batch_api.create_batch(
            BatchCreateRequest(flow_id=flow_id, document_ids=[uuid4()]),
            BackgroundTasks(),
            {"sub": "u-1", "cognito:groups": ["provider-group-a"]},
            db,
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Flow contains unavailable agents"


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
        active_totals=lambda user_id: {"running_count": 3, "pending_documents": 12},
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
        list_batches=lambda *_args: batch_api.BatchListResponse(batches=[response_one, response_two], total=2),
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
async def test_cancel_batch_endpoint_maps_value_error(monkeypatch, caplog):
    _mock_auth(monkeypatch, user_id=8)

    def _raise(*_args, **_kwargs):
        raise ValueError("Cannot cancel batch")

    service = SimpleNamespace(cancel_batch=_raise)
    monkeypatch.setattr(batch_api, "BatchService", lambda _db: service)
    caplog.set_level(logging.WARNING, logger=batch_api.logger.name)

    with pytest.raises(HTTPException) as exc:
        await batch_api.cancel_batch(uuid4(), {"sub": "u-1"}, db=object())
    assert exc.value.status_code == 400
    assert exc.value.detail == "Batch cannot be cancelled"
    assert "Cannot cancel batch" in caplog.text


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
    monkeypatch.setattr(batch_download, "BatchService", lambda _db: service)
    monkeypatch.setattr(batch_download, "SessionLocal", MagicMock())

    with pytest.raises(HTTPException) as exc:
        await batch_api.download_batch_zip(uuid4(), user={"sub": "u-1"})
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_download_batch_zip_400_when_no_completed_docs(monkeypatch):
    _mock_auth(monkeypatch, user_id=8)
    batch = SimpleNamespace(
        documents=[SimpleNamespace(status=BatchDocumentStatus.PENDING, result_files=[])]
    )
    service = SimpleNamespace(get_batch=lambda *_args, **_kwargs: batch)
    monkeypatch.setattr(batch_download, "BatchService", lambda _db: service)
    monkeypatch.setattr(batch_download, "SessionLocal", MagicMock())

    with pytest.raises(HTTPException) as exc:
        await batch_api.download_batch_zip(uuid4(), user={"sub": "u-1"})
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_download_batch_zip_success_with_matching_file(monkeypatch, tmp_path):
    _mock_auth(monkeypatch, user_id=8)
    batch_id = uuid4()
    file_id = uuid4()
    file_path = tmp_path / "result.csv"
    file_path.write_text("a,b\n1,2\n")
    second_file_id = uuid4()
    second_file_path = tmp_path / "result.json"
    second_file_path.write_text('{"a": 1}\n')

    completed_doc = SimpleNamespace(
        status=BatchDocumentStatus.COMPLETED,
        result_files=[
            {
                "file_id": str(file_id),
                "filename": "result.csv",
                "download_url": "/downloads/result.csv",
            },
            {
                "file_id": str(second_file_id),
                "filename": "result.json",
                "download_url": "/downloads/result.json",
            },
        ],
        document_id=uuid4(),
        position=0,
    )
    batch = SimpleNamespace(id=batch_id, documents=[completed_doc])
    service = SimpleNamespace(get_batch=lambda *_args, **_kwargs: batch)
    monkeypatch.setattr(batch_download, "BatchService", lambda _db: service)

    file_output = SimpleNamespace(id=file_id, curator_id="u-1", file_path=str(file_path), filename="result.csv")
    second_file_output = SimpleNamespace(
        id=second_file_id,
        curator_id="u-1",
        file_path=str(second_file_path),
        filename="result.json",
    )
    file_results = iter((file_output, second_file_output))
    db = SimpleNamespace(
        query=lambda *_columns: SimpleNamespace(
            filter=lambda *_args, **_kwargs: SimpleNamespace(
                all=lambda: list(file_results)
            )
        )
    )
    monkeypatch.setattr(
        "src.lib.batch.download.FileOutputStorageService",
        lambda: SimpleNamespace(base_path=tmp_path),
    )

    monkeypatch.setattr(batch_download, "SessionLocal", lambda: nullcontext(db))
    response = await batch_api.download_batch_zip(batch_id, user={"sub": "u-1"})
    assert isinstance(response, StreamingResponse)
    assert response.headers["content-disposition"].endswith(f'batch_{batch_id}_results.zip"')

    body = await _read_streaming_response(response)
    assert body.startswith(b"PK")
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        assert archive.namelist() == ["result.csv", "result.json"]


@pytest.mark.asyncio
async def test_download_batch_zip_preserves_custom_filenames_for_multiple_documents(monkeypatch, tmp_path):
    _mock_auth(monkeypatch, user_id=8)
    batch_id = uuid4()
    filenames = [
        "J-101_AI_tool_results.csv",
        "J-102_AI_tool_results.csv",
        "J-103_AI_tool_results.csv",
        "J-101_AI_tool_results.csv",
    ]
    expected_zip_names = [
        "J-101_AI_tool_results.csv",
        "J-102_AI_tool_results.csv",
        "J-103_AI_tool_results.csv",
        "J-101_AI_tool_results_2.csv",
    ]
    file_outputs = []
    documents = []

    for position, filename in enumerate(filenames):
        file_id = uuid4()
        file_path = tmp_path / filename
        file_path.write_text("gene,value\nexample,1\n")
        file_outputs.append(
            SimpleNamespace(
                id=file_id,
                curator_id="u-1",
                file_path=str(file_path),
                filename=filename,
            )
        )
        documents.append(
            SimpleNamespace(
                status=BatchDocumentStatus.COMPLETED,
                result_files=[
                    {
                        "file_id": str(file_id),
                        "filename": filename,
                        "download_url": f"/api/files/{file_id}/download",
                    }
                ],
                document_id=uuid4(),
                position=position,
            )
        )

    batch = SimpleNamespace(id=batch_id, documents=documents)
    service = SimpleNamespace(get_batch=lambda *_args, **_kwargs: batch)
    monkeypatch.setattr(batch_download, "BatchService", lambda _db: service)

    file_results = iter(file_outputs)
    db = SimpleNamespace(
        query=lambda *_columns: SimpleNamespace(
            filter=lambda *_args, **_kwargs: SimpleNamespace(
                all=lambda: list(file_results)
            )
        )
    )
    monkeypatch.setattr(
        "src.lib.batch.download.FileOutputStorageService",
        lambda: SimpleNamespace(base_path=tmp_path),
    )

    monkeypatch.setattr(batch_download, "SessionLocal", lambda: nullcontext(db))
    response = await batch_api.download_batch_zip(
        batch_id,
        user={"sub": "u-1"},
    )
    body = await _read_streaming_response(response)

    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        assert archive.namelist() == expected_zip_names


def test_unique_zip_member_name_only_suffixes_collisions():
    used_names: set[str] = set()

    assert batch_download._unique_zip_member_name("results.csv", used_names) == "results.csv"
    assert batch_download._unique_zip_member_name("results.csv", used_names) == "results_2.csv"
    assert batch_download._unique_zip_member_name("RESULTS.CSV", used_names) == "RESULTS_3.CSV"
    assert batch_download._unique_zip_member_name("../folder/safe.csv", used_names) == "safe.csv"
    assert batch_download._unique_zip_member_name(r"..\folder\safe.csv", used_names) == "safe_2.csv"
    assert batch_download._unique_zip_member_name("unsafe\x00\n.csv", used_names) == "unsafe.csv"


@pytest.mark.asyncio
async def test_download_batch_zip_400_when_completed_docs_exist_but_none_downloadable(monkeypatch, tmp_path):
    _mock_auth(monkeypatch, user_id=8)
    batch_id = uuid4()
    file_id = uuid4()
    file_path = tmp_path / "result.csv"
    file_path.write_text("a,b\n1,2\n")

    completed_doc = SimpleNamespace(
        status=BatchDocumentStatus.COMPLETED,
        result_files=[
            {
                "file_id": str(file_id),
                "filename": "result.csv",
                "download_url": f"/api/files/{file_id}/download",
            }
        ],
        document_id=uuid4(),
        position=0,
    )
    batch = SimpleNamespace(id=batch_id, documents=[completed_doc])
    service = SimpleNamespace(get_batch=lambda *_args, **_kwargs: batch)
    monkeypatch.setattr(batch_download, "BatchService", lambda _db: service)

    # Ownership mismatch means the file is skipped from the ZIP.
    db = SimpleNamespace(
        query=lambda *_columns: SimpleNamespace(filter=lambda *_args, **_kwargs: SimpleNamespace(all=lambda: []))
    )
    monkeypatch.setattr(
        "src.lib.batch.download.FileOutputStorageService",
        lambda: SimpleNamespace(base_path=tmp_path),
    )

    monkeypatch.setattr(batch_download, "SessionLocal", lambda: nullcontext(db))
    with pytest.raises(HTTPException) as exc:
        await batch_api.download_batch_zip(batch_id, user={"sub": "u-1"})
    assert exc.value.status_code == 400
    assert "No downloadable result files" in exc.value.detail


@pytest.mark.asyncio
async def test_stream_batch_progress_404_when_batch_missing(monkeypatch):
    _mock_auth(monkeypatch, user_id=8)
    monkeypatch.setattr(batch_stream, "SessionLocal", MagicMock())
    service = SimpleNamespace(get_batch=lambda *_args, **_kwargs: None)
    monkeypatch.setattr(batch_stream, "BatchService", lambda _db: service)

    with pytest.raises(HTTPException) as exc:
        await batch_api.stream_batch_progress(uuid4(), {"sub": "u-1"})
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_stream_batch_progress_sanitizes_internal_errors(monkeypatch):
    _mock_auth(monkeypatch, user_id=8)
    monkeypatch.setattr(batch_stream, "SessionLocal", MagicMock())
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
    monkeypatch.setattr(batch_stream, "BatchService", lambda _db: service)

    class _BrokenBroadcaster:
        async def subscribe(self, _batch_id):
            raise RuntimeError("db credentials leaked")

        async def unsubscribe(self, _batch_id, _queue):
            return None

    monkeypatch.setattr(batch_stream, "get_batch_broadcaster", lambda: _BrokenBroadcaster())

    response = await batch_api.stream_batch_progress(batch_id, {"sub": "u-1"})
    body = await _read_streaming_response(response)
    assert b"Batch stream encountered an internal error" in body
    assert b"db credentials leaked" not in body


@pytest.mark.asyncio
async def test_stream_batch_progress_strips_internal_event_fields(monkeypatch):
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
    monkeypatch.setattr(batch_stream, "BatchService", lambda _db: service)

    class _StreamDB:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

        def close(self):
            return None

    monkeypatch.setattr(batch_stream, "SessionLocal", lambda: _StreamDB())

    class _Broadcaster:
        async def subscribe(self, _batch_id):
            queue = asyncio.Queue()
            queue.put_nowait(
                {
                    "type": "TOOL_COMPLETE",
                    "details": {"toolName": "ask_gene_specialist"},
                    "internal": {"tool_output": "{\"selected_gene\":\"TP53\"}"},
                }
            )
            queue.put_nowait({"type": "BATCH_STREAM_COMPLETE", "batch_id": str(batch_id)})
            return queue

        async def unsubscribe(self, _batch_id, _queue):
            return None

    monkeypatch.setattr(batch_stream, "get_batch_broadcaster", lambda: _Broadcaster())

    response = await batch_api.stream_batch_progress(batch_id, {"sub": "u-1"})
    body = await _read_streaming_response(response)

    assert b'"type": "TOOL_COMPLETE"' in body
    assert b'"internal"' not in body


@pytest.mark.asyncio
async def test_stream_batch_progress_replays_completed_document_snapshot_on_reconnect(
    monkeypatch,
):
    _mock_auth(monkeypatch, user_id=8)
    batch_id = uuid4()
    document_id = uuid4()
    batch_document_id = uuid4()
    completed_document = SimpleNamespace(
        id=batch_document_id,
        document_id=document_id,
        position=0,
        status=BatchDocumentStatus.COMPLETED,
        result_files=[],
        review_session_ids=["review-gene"],
        error_message=None,
        processing_time_ms=1200,
    )
    batch = SimpleNamespace(
        id=batch_id,
        status=BatchStatus.COMPLETED,
        total_documents=1,
        completed_documents=1,
        failed_documents=0,
        documents=[completed_document],
        started_at=None,
        completed_at=None,
    )
    handoff_metadata = {
        "adapter_keys": ["gene"],
        "extraction_result_ids": ["extract-gene"],
        "extraction_result_refs": [
            {
                "result_ref": "extraction-result:extract-gene",
                "extraction_result_id": "extract-gene",
                "adapter_key": "gene",
            }
        ],
        "flow_run_id": "flow-run-1",
        "origin_session_id": "origin-1",
    }
    service = SimpleNamespace(
        get_batch=lambda *_args, **_kwargs: batch,
        get_documents_handoff_metadata=lambda _batch, docs: {doc.id: handoff_metadata for doc in docs},
    )
    monkeypatch.setattr(batch_stream, "BatchService", lambda _db: service)

    class _StreamDB:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

        def close(self):
            return None

    monkeypatch.setattr(batch_stream, "SessionLocal", lambda: _StreamDB())

    class _Broadcaster:
        async def subscribe(self, _batch_id):
            return asyncio.Queue()

        async def unsubscribe(self, _batch_id, _queue):
            return None

    monkeypatch.setattr(batch_stream, "get_batch_broadcaster", lambda: _Broadcaster())

    for _connection in range(2):
        response = await batch_api.stream_batch_progress(
            batch_id,
            {"sub": "u-1"},
        )
        body = await _read_streaming_response(response)
        events = [
            json.loads(line.removeprefix("data: "))
            for line in body.decode("utf-8").splitlines()
            if line.startswith("data: ")
        ]
        document_event = next(event for event in events if event["type"] == "DOCUMENT_STATUS")

        assert document_event == {
            "type": "DOCUMENT_STATUS",
            "batch_id": str(batch_id),
            "document_id": str(document_id),
            "batch_document_id": str(batch_document_id),
            "position": 0,
            "status": "completed",
            "result_files": [],
            "output_status": None,
            "output_branches": [],
            "review_session_ids": ["review-gene"],
            **handoff_metadata,
            "error_message": None,
            "processing_time_ms": 1200,
        }


async def _read_streaming_response(response):
    chunks = []
    async for chunk in response.body_iterator:
        if isinstance(chunk, bytes):
            chunks.append(chunk)
        else:
            chunks.append(chunk.encode("utf-8"))
    return b"".join(chunks)


@pytest.mark.asyncio
async def test_summary_routes_dispatch_owner_filters_and_validation(monkeypatch):
    _mock_auth(monkeypatch, user_id=8)
    from unittest.mock import Mock
    from src.schemas.batch import BatchListResponse, BatchTerminalResponse, BatchLookupRequest, BatchLookupResponse
    service = Mock()
    service.list_batches.return_value = BatchListResponse(batches=[], total=42, next_cursor='next')
    monkeypatch.setattr(batch_api, 'BatchService', lambda db: service)
    result = await batch_api.list_batches({'sub': 'u'}, db=object(), limit=5, cursor='page', status='active')
    service.list_batches.assert_called_once_with(8, 5, 'page', 'active')
    assert result.total == 42 and result.next_cursor == 'next'
    service.list_batches.side_effect = batch_api.BatchQueryError('Invalid batch cursor for this query')
    with pytest.raises(HTTPException) as error:
        await batch_api.list_batches({'sub': 'u'}, db=object(), cursor='bad')
    assert error.value.status_code == 422
    now = datetime.now(timezone.utc)
    terminals = Mock(return_value=BatchTerminalResponse(batches=[], window_start=now, window_end=now, coverage='reset'))
    monkeypatch.setattr(batch_api, 'terminal_summaries', terminals)
    db = object()
    result = await batch_api.list_terminal_batches({'sub': 'u'}, db=db, limit=2, since=now)
    terminals.assert_called_once_with(db, 8, 2, None, now)
    assert result.coverage == 'reset'
    identity = uuid4()
    lookup = Mock(return_value=BatchLookupResponse(batches=[], unavailable_ids=[identity]))
    monkeypatch.setattr(batch_api, 'lookup_summaries', lookup)
    result = await batch_api.lookup_batches(BatchLookupRequest(ids=[identity]), {'sub': 'u'}, db=db)
    lookup.assert_called_once_with(db, 8, [identity])
    assert result.unavailable_ids == [identity]
    paths = [route.path for route in batch_api.router.routes]
    assert paths.index('/api/batches/terminals') < paths.index('/api/batches/{batch_id}')
    assert paths.index('/api/batches/lookup') < paths.index('/api/batches/{batch_id}')


@pytest.mark.asyncio
async def test_summary_persisted_data_failure_is_not_misreported_as_bad_request(monkeypatch):
    _mock_auth(monkeypatch)
    from unittest.mock import Mock
    service = Mock()
    service.list_batches.side_effect = ValueError("persisted data is corrupt")
    monkeypatch.setattr(batch_api, 'BatchService', lambda db: service)
    with pytest.raises(ValueError, match="persisted data is corrupt"):
        await batch_api.list_batches({'sub': 'u'}, db=object())


@pytest.mark.asyncio
@pytest.mark.parametrize("claims,owner", [({"sub": "subject", "uid": "other"}, "subject"), ({"uid": "uid-subject"}, "uid-subject")])
async def test_zip_dispatch_keeps_sql_user_and_file_subject_distinct(monkeypatch, claims, owner):
    monkeypatch.setattr(batch_api, "principal_from_claims", lambda value: value)
    monkeypatch.setattr(batch_api, "provision_user_async", AsyncMock(return_value=SimpleNamespace(id=8)))
    prepare = AsyncMock()
    monkeypatch.setattr(batch_api, "prepare_batch_zip", prepare)
    batch_id = uuid4()
    assert await batch_api.download_batch_zip(batch_id, user=claims) is prepare.return_value
    prepare.assert_awaited_once_with(batch_id, 8, owner)

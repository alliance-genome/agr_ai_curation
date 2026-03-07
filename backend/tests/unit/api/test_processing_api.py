"""Unit tests for document processing API endpoints."""

from fastapi import BackgroundTasks, HTTPException
import pytest
from types import SimpleNamespace

from src.api import processing
from src.models.api_schemas import EmbeddingConfiguration, ReembedRequest, ReprocessRequest
from src.models.document import ProcessingStatus
from src.models.pipeline import ProcessingStage


@pytest.mark.asyncio
async def test_reprocess_document_not_found(monkeypatch):
    async def _get_document(_user_id, _doc_id):
        return None

    monkeypatch.setattr(processing, "get_document", _get_document)

    with pytest.raises(HTTPException) as exc:
        await processing.reprocess_document_endpoint(
            BackgroundTasks(),
            document_id="doc-1",
            request=ReprocessRequest(strategy_name="default", force_reparse=False),
            user={"sub": "user-1"},
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_reprocess_document_rejects_when_processing(monkeypatch):
    async def _get_document(_user_id, _doc_id):
        return {"document": {"processing_status": ProcessingStatus.PROCESSING.value, "filename": "paper.pdf"}}

    async def _pipeline_status(_doc_id):
        return type("Status", (), {"current_stage": ProcessingStage.CHUNKING})()

    monkeypatch.setattr(processing, "get_document", _get_document)
    monkeypatch.setattr(processing, "_latest_job_for_user_document", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(processing.pipeline_tracker, "get_pipeline_status", _pipeline_status)

    with pytest.raises(HTTPException) as exc:
        await processing.reprocess_document_endpoint(
            BackgroundTasks(),
            document_id="doc-1",
            request=ReprocessRequest(strategy_name="default", force_reparse=False),
            user={"sub": "user-1"},
        )
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_reprocess_document_rejects_stale_processing_status_without_terminal_job(monkeypatch):
    async def _get_document(_user_id, _doc_id):
        return {"document": {"processing_status": ProcessingStatus.PROCESSING.value, "filename": "paper.pdf"}}

    async def _pipeline_status(_doc_id):
        return None

    monkeypatch.setattr(processing, "get_document", _get_document)
    monkeypatch.setattr(processing, "_latest_job_for_user_document", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(processing.pipeline_tracker, "get_pipeline_status", _pipeline_status)

    with pytest.raises(HTTPException) as exc:
        await processing.reprocess_document_endpoint(
            BackgroundTasks(),
            document_id="doc-1",
            request=ReprocessRequest(strategy_name="default", force_reparse=False),
            user={"sub": "user-1"},
        )
    assert exc.value.status_code == 409
    assert "stage: processing" in str(exc.value.detail).lower()


@pytest.mark.asyncio
async def test_reprocess_document_treats_unknown_status_as_pending(monkeypatch, tmp_path):
    user_id = "user-1"
    document_id = "doc-1"
    filename = "paper.pdf"
    file_dir = tmp_path / user_id / document_id
    file_dir.mkdir(parents=True)
    (file_dir / filename).write_text("pdf", encoding="utf-8")

    async def _get_document(_user_id, _doc_id):
        return {"document": {"processing_status": "custom-status", "filename": filename}}

    async def _update_status(_doc_id, _uid, _status):
        return None

    async def _track(_doc_id, _stage):
        return None

    monkeypatch.setattr(processing, "get_document", _get_document)
    monkeypatch.setattr(processing, "_latest_job_for_user_document", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(processing, "get_pdf_storage_path", lambda: tmp_path)
    monkeypatch.setattr(processing, "update_document_status", _update_status)
    monkeypatch.setattr(processing.pipeline_tracker, "track_pipeline_progress", _track)
    monkeypatch.setattr("src.lib.document_cache.invalidate_cache", lambda *_args, **_kwargs: None)

    result = await processing.reprocess_document_endpoint(
        BackgroundTasks(),
        document_id=document_id,
        request=ReprocessRequest(strategy_name="default", force_reparse=False),
        user={"sub": user_id},
    )

    assert result.success is True


@pytest.mark.asyncio
async def test_reprocess_document_success_schedules_background_task(monkeypatch, tmp_path):
    user_id = "user-1"
    document_id = "doc-1"
    filename = "paper.pdf"
    file_dir = tmp_path / user_id / document_id
    file_dir.mkdir(parents=True)
    (file_dir / filename).write_text("pdf", encoding="utf-8")

    async def _get_document(_user_id, _doc_id):
        return {"document": {"processing_status": "completed", "filename": filename}}

    status_calls = []
    track_calls = []

    async def _update_status(doc_id, uid, status):
        status_calls.append((doc_id, uid, status))

    async def _track(doc_id, stage):
        track_calls.append((doc_id, stage))

    monkeypatch.setattr(processing, "get_document", _get_document)
    monkeypatch.setattr(processing, "_latest_job_for_user_document", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(processing, "get_pdf_storage_path", lambda: tmp_path)
    monkeypatch.setattr(processing, "update_document_status", _update_status)
    monkeypatch.setattr(processing.pipeline_tracker, "track_pipeline_progress", _track)
    monkeypatch.setattr("src.lib.document_cache.invalidate_cache", lambda *_args, **_kwargs: None)

    background = BackgroundTasks()
    result = await processing.reprocess_document_endpoint(
        background,
        document_id=document_id,
        request=ReprocessRequest(strategy_name="default", force_reparse=True),
        user={"sub": user_id},
    )

    assert result.success is True
    assert result.document_id == document_id
    assert "reprocessing initiated" in result.message.lower()
    assert len(background.tasks) == 1
    assert status_calls == [(document_id, user_id, ProcessingStatus.PROCESSING)]
    assert track_calls == [(document_id, ProcessingStage.PARSING)]


@pytest.mark.asyncio
async def test_reembed_document_no_chunks(monkeypatch):
    async def _get_document(_user_id, _doc_id):
        return {"document": {"processing_status": "completed"}, "total_chunks": 0}

    monkeypatch.setattr(processing, "get_document", _get_document)
    monkeypatch.setattr(processing, "_latest_job_for_user_document", lambda *_args, **_kwargs: None)

    with pytest.raises(HTTPException) as exc:
        await processing.reembed_document_endpoint(
            document_id="doc-1",
            request=None,
            user={"sub": "user-1"},
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_reembed_document_rejects_stale_processing_status_without_terminal_job(monkeypatch):
    async def _get_document(_user_id, _doc_id):
        return {"document": {"processing_status": ProcessingStatus.PROCESSING.value}, "total_chunks": 5}

    async def _pipeline_status(_doc_id):
        return None

    monkeypatch.setattr(processing, "get_document", _get_document)
    monkeypatch.setattr(processing, "_latest_job_for_user_document", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(processing.pipeline_tracker, "get_pipeline_status", _pipeline_status)

    with pytest.raises(HTTPException) as exc:
        await processing.reembed_document_endpoint(
            document_id="doc-1",
            request=None,
            user={"sub": "user-1"},
        )
    assert exc.value.status_code == 409
    assert "stage: processing" in str(exc.value.detail).lower()


@pytest.mark.asyncio
async def test_reembed_document_success_default_request(monkeypatch):
    async def _get_document(_user_id, _doc_id):
        return {"document": {"processing_status": "completed"}, "total_chunks": 5}

    status_calls = []
    reembed_calls = []
    track_calls = []

    async def _update_status(doc_id, uid, status):
        status_calls.append((doc_id, uid, status))

    async def _reembed(doc_id, uid, embedding_config=None, batch_size=10):
        reembed_calls.append((doc_id, uid, embedding_config, batch_size))
        return {"total_chunks": 5}

    async def _track(doc_id, stage):
        track_calls.append((doc_id, stage))

    monkeypatch.setattr(processing, "get_document", _get_document)
    monkeypatch.setattr(processing, "_latest_job_for_user_document", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(processing, "update_document_status", _update_status)
    monkeypatch.setattr(processing, "re_embed_document", _reembed)
    monkeypatch.setattr(processing.pipeline_tracker, "track_pipeline_progress", _track)

    result = await processing.reembed_document_endpoint(
        document_id="doc-1",
        request=None,
        user={"sub": "user-1"},
    )

    assert result.success is True
    assert result.document_id == "doc-1"
    assert "re-embedding initiated" in result.message.lower()
    assert status_calls == [("doc-1", "user-1", ProcessingStatus.PROCESSING)]
    assert reembed_calls == [("doc-1", "user-1", None, 10)]
    assert track_calls == [("doc-1", ProcessingStage.EMBEDDING)]


@pytest.mark.asyncio
async def test_reembed_document_success_with_custom_config(monkeypatch):
    async def _get_document(_user_id, _doc_id):
        return {"document": {"processing_status": "completed"}, "total_chunks": 3}

    reembed_calls = []

    async def _update_status(_doc_id, _uid, _status):
        return None

    async def _reembed(doc_id, uid, embedding_config=None, batch_size=10):
        reembed_calls.append((doc_id, uid, embedding_config, batch_size))
        return {"total_chunks": 3}

    async def _track(_doc_id, _stage):
        return None

    monkeypatch.setattr(processing, "get_document", _get_document)
    monkeypatch.setattr(processing, "_latest_job_for_user_document", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(processing, "update_document_status", _update_status)
    monkeypatch.setattr(processing, "re_embed_document", _reembed)
    monkeypatch.setattr(processing.pipeline_tracker, "track_pipeline_progress", _track)

    request = ReembedRequest(
        embedding_config=EmbeddingConfiguration(
            model_provider="openai",
            model_name="text-embedding-3-small",
            dimensions=1536,
            batch_size=16,
        ),
        batch_size=32,
    )

    await processing.reembed_document_endpoint(
        document_id="doc-1",
        request=request,
        user={"sub": "user-1"},
    )

    assert len(reembed_calls) == 1
    assert reembed_calls[0][3] == 32
    assert reembed_calls[0][2].model_name == "text-embedding-3-small"


@pytest.mark.asyncio
async def test_reprocess_document_rejects_when_latest_pdf_job_is_active(monkeypatch):
    async def _get_document(_user_id, _doc_id):
        return {"document": {"processing_status": "completed", "filename": "paper.pdf"}}

    monkeypatch.setattr(processing, "get_document", _get_document)
    monkeypatch.setattr(
        processing,
        "_latest_job_for_user_document",
        lambda *_args, **_kwargs: SimpleNamespace(status="running", current_stage="parsing"),
    )

    with pytest.raises(HTTPException) as exc:
        await processing.reprocess_document_endpoint(
            BackgroundTasks(),
            document_id="doc-1",
            request=ReprocessRequest(strategy_name="default", force_reparse=False),
            user={"sub": "user-1"},
        )
    assert exc.value.status_code == 409
    assert "job status: running" in str(exc.value.detail).lower()


@pytest.mark.asyncio
async def test_reembed_document_rejects_when_latest_pdf_job_is_active(monkeypatch):
    async def _get_document(_user_id, _doc_id):
        return {"document": {"processing_status": "completed"}, "total_chunks": 8}

    monkeypatch.setattr(processing, "get_document", _get_document)
    monkeypatch.setattr(
        processing,
        "_latest_job_for_user_document",
        lambda *_args, **_kwargs: SimpleNamespace(status="cancel_requested", current_stage="parsing"),
    )

    with pytest.raises(HTTPException) as exc:
        await processing.reembed_document_endpoint(
            document_id="doc-1",
            request=None,
            user={"sub": "user-1"},
        )
    assert exc.value.status_code == 409
    assert "job status: cancel_requested" in str(exc.value.detail).lower()


@pytest.mark.asyncio
async def test_reprocess_document_allows_stale_processing_status_when_job_terminal(monkeypatch, tmp_path):
    user_id = "user-1"
    document_id = "doc-1"
    filename = "paper.pdf"
    file_dir = tmp_path / user_id / document_id
    file_dir.mkdir(parents=True)
    (file_dir / filename).write_text("pdf", encoding="utf-8")

    async def _get_document(_user_id, _doc_id):
        return {"document": {"processing_status": "processing", "filename": filename}}

    async def _pipeline_status(_doc_id):
        return None

    async def _update_status(_doc_id, _uid, _status):
        return None

    async def _track(_doc_id, _stage):
        return None

    monkeypatch.setattr(processing, "get_document", _get_document)
    monkeypatch.setattr(processing, "_latest_job_for_user_document", lambda *_args, **_kwargs: SimpleNamespace(status="failed"))
    monkeypatch.setattr(processing.pipeline_tracker, "get_pipeline_status", _pipeline_status)
    monkeypatch.setattr(processing, "get_pdf_storage_path", lambda: tmp_path)
    monkeypatch.setattr(processing, "update_document_status", _update_status)
    monkeypatch.setattr(processing.pipeline_tracker, "track_pipeline_progress", _track)
    monkeypatch.setattr("src.lib.document_cache.invalidate_cache", lambda *_args, **_kwargs: None)

    result = await processing.reprocess_document_endpoint(
        BackgroundTasks(),
        document_id=document_id,
        request=ReprocessRequest(strategy_name="default", force_reparse=False),
        user={"sub": user_id},
    )

    assert result.success is True


@pytest.mark.asyncio
async def test_reembed_document_allows_stale_processing_status_when_job_terminal(monkeypatch):
    async def _get_document(_user_id, _doc_id):
        return {"document": {"processing_status": "processing"}, "total_chunks": 4}

    async def _pipeline_status(_doc_id):
        return None

    async def _update_status(_doc_id, _uid, _status):
        return None

    async def _reembed(_doc_id, _uid, embedding_config=None, batch_size=10):
        return {"total_chunks": 4}

    async def _track(_doc_id, _stage):
        return None

    monkeypatch.setattr(processing, "get_document", _get_document)
    monkeypatch.setattr(processing, "_latest_job_for_user_document", lambda *_args, **_kwargs: SimpleNamespace(status="cancelled"))
    monkeypatch.setattr(processing.pipeline_tracker, "get_pipeline_status", _pipeline_status)
    monkeypatch.setattr(processing, "update_document_status", _update_status)
    monkeypatch.setattr(processing, "re_embed_document", _reembed)
    monkeypatch.setattr(processing.pipeline_tracker, "track_pipeline_progress", _track)

    result = await processing.reembed_document_endpoint(
        document_id="doc-1",
        request=None,
        user={"sub": "user-1"},
    )

    assert result.success is True

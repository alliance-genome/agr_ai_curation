"""Endpoints for managing ontology ingestion lifecycle."""

from __future__ import annotations

import json
from pathlib import Path
from typing import List

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool

from app.config import get_settings
from app.database import SessionLocal
from app.jobs.ingest_ontology import ingest_ontology
from app.models import IngestionState, IngestionStatus
from app.repositories.ontology_repository import (
    OntologyRepository,
    OntologyStatusRow,
)
from app.schemas.ontology import (
    OntologyEmbeddingResponse,
    OntologyIngestionRequest,
    OntologyIngestionResponse,
    OntologyIngestionSummary,
    OntologyStatusResponse,
)
from app.services.embedding_service_factory import get_embedding_service


router = APIRouter(prefix="/api/ontology", tags=["ontology"])


def get_repository() -> OntologyRepository:
    return OntologyRepository()


@router.get("/ingestions", response_model=List[OntologyStatusResponse])
async def list_ingestions(
    repo: OntologyRepository = Depends(get_repository),
):
    rows = repo.list_statuses()
    return [_serialize_status(row) for row in rows]


@router.get(
    "/ingestions/{ontology_type}/{source_id}",
    response_model=OntologyStatusResponse,
)
async def get_ingestion(
    ontology_type: str,
    source_id: str,
    repo: OntologyRepository = Depends(get_repository),
):
    row = repo.get_status(ontology_type, source_id)
    if row is None:
        raise HTTPException(
            status_code=404, detail="Ontology ingestion status not found"
        )
    return _serialize_status(row)


@router.post(
    "/ingestions",
    response_model=OntologyIngestionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_ingestion(
    request: OntologyIngestionRequest,
    repo: OntologyRepository = Depends(get_repository),
):
    ontology_type = request.ontology_type
    source_id = request.source_id
    obo_path = _resolve_ontology_path(ontology_type, request.obo_path)

    try:
        summary_dict = await run_in_threadpool(
            ingest_ontology,
            ontology_type=ontology_type,
            source_id=source_id,
            obo_path=obo_path,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    summary = OntologyIngestionSummary.model_validate(summary_dict)
    status_row = repo.get_status(ontology_type, source_id)

    return OntologyIngestionResponse(
        ontology_type=ontology_type,
        source_id=source_id,
        summary=summary,
        status=_serialize_status(status_row) if status_row else None,
    )


@router.post(
    "/ingestions/{ontology_type}/{source_id}/embeddings",
    response_model=OntologyEmbeddingResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def run_embeddings(
    ontology_type: str,
    source_id: str,
    background_tasks: BackgroundTasks,
    repo: OntologyRepository = Depends(get_repository),
):
    settings = get_settings()
    embedding_service = get_embedding_service()

    model_name = settings.ontology_embedding_model_name or settings.embedding_model_name
    batch_size = settings.ontology_embedding_batch_size or None

    source_type = f"ontology_{ontology_type}"

    summary = {
        "queued": True,
        "model": model_name,
        "source_type": source_type,
        "source_id": source_id,
    }

    with SessionLocal() as session:
        record = (
            session.query(IngestionStatus)
            .filter(
                IngestionStatus.source_type == source_type,
                IngestionStatus.source_id == source_id,
            )
            .first()
        )
        if record:
            payload: dict = {}
            if record.message:
                try:
                    loaded = json.loads(record.message)
                    if isinstance(loaded, dict):
                        payload = loaded
                except (json.JSONDecodeError, TypeError):
                    payload = {}
            payload.update({"stage": "embedding_running"})
            record.status = IngestionState.INDEXING
            record.message = json.dumps(payload)
            session.commit()

    background_tasks.add_task(
        _execute_embedding_job,
        ontology_type,
        source_id,
        model_name,
        batch_size,
    )

    status_row = repo.get_status(ontology_type, source_id)

    return OntologyEmbeddingResponse(
        ontology_type=ontology_type,
        source_id=source_id,
        summary=summary,
        status=_serialize_status(status_row) if status_row else None,
    )


def _execute_embedding_job(
    ontology_type: str,
    source_id: str,
    model_name: str,
    batch_size: int | None,
) -> None:
    embedding_service = get_embedding_service()
    source_type = f"ontology_{ontology_type}"

    try:
        summary = embedding_service.embed_unified_chunks(
            source_type=source_type,
            source_id=source_id,
            model_name=model_name,
            batch_size=batch_size,
            force=True,
        )
        stage = "ready"
        status_value = IngestionState.READY
    except Exception as exc:  # pragma: no cover - defensive
        summary = {"error": str(exc)}
        stage = "error"
        status_value = IngestionState.ERROR

    with SessionLocal() as session:
        record = (
            session.query(IngestionStatus)
            .filter(
                IngestionStatus.source_type == source_type,
                IngestionStatus.source_id == source_id,
            )
            .first()
        )
        if record:
            payload: dict = {}
            if record.message:
                try:
                    loaded = json.loads(record.message)
                    if isinstance(loaded, dict):
                        payload = loaded
                except (json.JSONDecodeError, TypeError):
                    payload = {}
            payload.update({"stage": stage, "embedding": summary})
            record.status = status_value
            record.message = json.dumps(payload)
            session.commit()


def _serialize_status(row: OntologyStatusRow) -> OntologyStatusResponse:
    return OntologyStatusResponse(
        ontology_type=row.ontology_type,
        source_id=row.source_id,
        state=row.state,
        created_at=row.created_at,
        updated_at=row.updated_at,
        message=row.message,
        term_count=row.term_count,
        relation_count=row.relation_count,
        chunk_count=row.chunk_count,
        embedded_count=row.embedded_count,
    )


def _resolve_ontology_path(ontology_type: str, override: str | None) -> Path:
    if override:
        return Path(override)

    settings = get_settings()
    if ontology_type == "disease":
        return Path(settings.disease_ontology_path)

    raise HTTPException(
        status_code=400,
        detail=(
            "No default ontology path configured; provide 'obo_path' to ingest this ontology."
        ),
    )


__all__ = ["router", "get_repository"]

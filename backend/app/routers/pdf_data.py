"""Routes exposing PDF ingest and chunk metadata for browsing."""

from __future__ import annotations

from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from app.repositories.pdf_data_repository import PDFDataRepository
from app.schemas.pdf_data import (
    LangGraphNodeRow,
    LangGraphRunRow,
    PDFChunkRow,
    PDFDocumentDetail,
    PDFDocumentSummary,
    EmbeddingSummaryRow,
)

router = APIRouter(prefix="/api/pdf-data", tags=["pdf-data"])


def get_repository() -> PDFDataRepository:
    return PDFDataRepository()


@router.get("/documents", response_model=List[PDFDocumentSummary])
async def list_documents(
    limit: int = Query(50, ge=1, le=500),
    repo: PDFDataRepository = Depends(get_repository),
):
    documents = repo.list_documents(limit=limit)
    return [
        PDFDocumentSummary(
            id=doc.id,
            filename=doc.filename,
            upload_timestamp=doc.upload_timestamp,
            last_accessed=doc.last_accessed,
            page_count=doc.page_count,
            chunk_count=doc.chunk_count,
            table_count=doc.table_count,
            figure_count=doc.figure_count,
            embeddings_generated=doc.embeddings_generated,
        )
        for doc in documents
    ]


@router.get("/documents/{pdf_id}", response_model=PDFDocumentDetail)
async def get_document(
    pdf_id: UUID,
    repo: PDFDataRepository = Depends(get_repository),
):
    document = repo.get_document(pdf_id)
    if not document:
        raise HTTPException(status_code=404, detail="PDF document not found")
    return PDFDocumentDetail(
        id=document.id,
        filename=document.filename,
        upload_timestamp=document.upload_timestamp,
        last_accessed=document.last_accessed,
        page_count=document.page_count,
        chunk_count=document.chunk_count,
        table_count=document.table_count,
        figure_count=document.figure_count,
        embeddings_generated=document.embeddings_generated,
        file_size=document.file_size,
        extraction_method=document.extraction_method,
        preproc_version=document.preproc_version,
        meta_data=document.meta_data or {},
    )


@router.get("/documents/{pdf_id}/chunks", response_model=List[PDFChunkRow])
async def list_chunks(
    pdf_id: UUID,
    limit: int = Query(200, ge=1, le=5000),
    repo: PDFDataRepository = Depends(get_repository),
):
    document = repo.get_document(pdf_id)
    if not document:
        raise HTTPException(status_code=404, detail="PDF document not found")

    chunks = repo.list_chunks(pdf_id=pdf_id, limit=limit)
    return [
        PDFChunkRow(
            id=chunk.id,
            chunk_index=chunk.chunk_index,
            text_preview=chunk.text[:300],
            page_start=chunk.page_start,
            page_end=chunk.page_end,
            section_path=chunk.section_path,
            element_type=getattr(chunk, "element_type", None),
            is_reference=chunk.is_reference,
            is_caption=chunk.is_caption,
            is_table=getattr(chunk, "is_table", None),
            is_figure=getattr(chunk, "is_figure", None),
            token_count=chunk.token_count,
        )
        for chunk in chunks
    ]


@router.get("/documents/{pdf_id}/langgraph-runs", response_model=List[LangGraphRunRow])
async def list_langgraph_runs(
    pdf_id: UUID,
    repo: PDFDataRepository = Depends(get_repository),
):
    document = repo.get_document(pdf_id)
    if not document:
        raise HTTPException(status_code=404, detail="PDF document not found")

    runs = repo.list_langgraph_runs(pdf_id=pdf_id)
    return [
        LangGraphRunRow(
            id=run.id,
            workflow_name=run.workflow_name,
            input_query=run.input_query,
            status=run.status,
            started_at=run.started_at,
            completed_at=run.completed_at,
            latency_ms=run.latency_ms,
            specialists_invoked=run.specialists_invoked or [],
        )
        for run in runs
    ]


@router.get(
    "/langgraph-runs/{graph_run_id}/nodes", response_model=List[LangGraphNodeRow]
)
async def list_langgraph_node_runs(
    graph_run_id: UUID,
    repo: PDFDataRepository = Depends(get_repository),
):
    nodes = repo.list_langgraph_node_runs(graph_run_id=graph_run_id)
    if not nodes:
        raise HTTPException(status_code=404, detail="No node runs found")

    return [
        LangGraphNodeRow(
            id=node.id,
            graph_run_id=node.graph_run_id,
            node_key=node.node_key,
            node_type=node.node_type,
            status=node.status,
            started_at=node.started_at,
            completed_at=node.completed_at,
            latency_ms=node.latency_ms,
            error=node.error,
        )
        for node in nodes
    ]


@router.get("/documents/{pdf_id}/embeddings", response_model=List[EmbeddingSummaryRow])
async def list_embeddings(
    pdf_id: UUID,
    repo: PDFDataRepository = Depends(get_repository),
):
    document = repo.get_document(pdf_id)
    if not document:
        raise HTTPException(status_code=404, detail="PDF document not found")

    summary = repo.list_embedding_summary(pdf_id=pdf_id)
    return [
        EmbeddingSummaryRow(
            model_name=row["model_name"],
            count=row["count"],
            latest_created_at=row["latest_created_at"],
        )
        for row in summary
    ]


__all__ = ["router"]

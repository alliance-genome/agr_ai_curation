"""Utility for running embedding generation across multiple PDFs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, List, Optional, Sequence
from uuid import UUID

from app.models import PDFDocument
from lib.embedding_service import EmbeddingService


@dataclass
class EmbeddingBatchTask:
    """Represents a single PDF embedding job."""

    pdf_id: UUID
    model_name: str
    version: Optional[str] = None
    batch_size: Optional[int] = None
    force: bool = False


@dataclass
class EmbeddingBatchSummary:
    """Aggregated summary for batch execution."""

    processed: List[dict] = field(default_factory=list)
    failed: List[dict] = field(default_factory=list)

    def add_success(self, pdf_id: UUID, result: dict) -> None:
        payload = {"pdf_id": str(pdf_id)}
        payload.update(result)
        self.processed.append(payload)

    def add_failure(self, pdf_id: UUID, error: Exception) -> None:
        self.failed.append({"pdf_id": str(pdf_id), "error": str(error)})


ProgressCallback = Callable[[EmbeddingBatchTask, dict], None]
ErrorCallback = Callable[[EmbeddingBatchTask, Exception], None]


class EmbeddingBatchProcessor:
    """Runs embedding generation sequentially for a list of tasks."""

    def __init__(self, service: EmbeddingService) -> None:
        self._service = service

    def run(
        self,
        tasks: Sequence[EmbeddingBatchTask],
        *,
        on_progress: ProgressCallback | None = None,
        on_error: ErrorCallback | None = None,
        document_loader: Callable[[UUID], PDFDocument | None] | None = None,
    ) -> EmbeddingBatchSummary:
        summary = EmbeddingBatchSummary()

        for task in tasks:
            try:
                if document_loader is not None:
                    document = document_loader(task.pdf_id)
                    if document is None:
                        raise ValueError(f"PDF document {task.pdf_id} not found")

                result = self._service.embed_pdf(
                    pdf_id=task.pdf_id,
                    model_name=task.model_name,
                    version=task.version,
                    batch_size=task.batch_size,
                    force=task.force,
                )
                summary.add_success(task.pdf_id, result)

                if on_progress is not None:
                    on_progress(task, result)
            except Exception as exc:  # noqa: BLE001 - propagate to summary and continue
                summary.add_failure(task.pdf_id, exc)
                if on_error is not None:
                    on_error(task, exc)

        return summary


__all__ = [
    "EmbeddingBatchProcessor",
    "EmbeddingBatchTask",
    "EmbeddingBatchSummary",
]

"""Main pipeline orchestrator for document processing."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Optional, Dict, Any
from dataclasses import dataclass, field
import logging
import asyncio
import time

from src.models.pipeline import ProcessingStage
from .tracker import PipelineTracker
from src.models.strategy import ChunkingStrategy
from ..exceptions import PDFCancellationError, PDFParsingError
from src.lib.openai_agents.config import get_pdf_document_error_message_max_chars
from src.lib.observability.cost_context import costed_document_processing
from src.lib.observability.runtime import report_runtime_exception, sanitized_runtime_error
from .processing_receipt import PDFProcessingReceipt

logger = logging.getLogger(__name__)


def _report_pipeline_failure(error: Exception, *, operation: str, context: dict[str, Any]) -> bool:
    try:
        return report_runtime_exception(
            error, component="document_pipeline", operation=operation, context=context,
        )
    except Exception:
        logger.warning("Pipeline failure reporting unavailable for %s", operation, extra={"sentry_skip_event": True})
        return False


def _public_pipeline_error_message(error: Exception) -> str:
    """Return a stable public message when an exception provides one."""
    if isinstance(error, PDFParsingError):
        public_message = error.details.get("public_message")
        if isinstance(public_message, str) and public_message.strip():
            return public_message.strip()
    return str(error)


def _pdfx_runtime_context(error: Exception) -> dict[str, Any]:
    """Return only the content-free PDFX failure fields approved for telemetry."""
    if not isinstance(error, PDFParsingError):
        return {}
    failure = error.details.get("pdfx_failure")
    if not isinstance(failure, dict):
        return {}
    allowed_fields = {
        "failure_category",
        "failure_boundary",
        "process_id",
        "provider_status",
        "provider_error_code",
        "http_status",
        "submit_attempt_count",
        "poll_attempt_count",
        "timeout_seconds",
    }
    return {
        f"pdfx_{key}": value
        for key, value in failure.items()
        if key in allowed_fields and isinstance(value, (str, int))
    }


class PipelineError(Exception):
    """Base exception for pipeline processing errors."""
    pass


class StageError(PipelineError):
    """Error during a specific processing stage."""
    def __init__(self, stage: ProcessingStage, message: str, cause: Optional[Exception] = None):
        self.stage = stage
        self.cause = cause
        super().__init__(f"Stage {stage.value} failed: {message}")


@dataclass
class ProcessingResult:
    """Result of document processing pipeline."""
    success: bool
    document_id: str
    stages_completed: list[ProcessingStage]
    total_chunks: int = 0
    total_embeddings: int = 0
    error: Optional[str] = None
    duration_seconds: float = 0.0
    cancelled: bool = False
    observability_receipt: dict[str, Any] = field(default_factory=dict)


class DocumentPipelineOrchestrator:
    """Orchestrates the document processing pipeline."""

    def __init__(
        self,
        weaviate_client: Any,
        tracker: Optional[PipelineTracker] = None,
    ):
        """Initialize pipeline orchestrator.

        Args:
            weaviate_client: Weaviate database client
        """
        self.weaviate_client = weaviate_client
        self.tracker = tracker or PipelineTracker()

    @costed_document_processing
    async def process_pdf_document(
        self,
        file_path: Path,
        document_id: str,
        user_id: str,
        strategy: Optional[ChunkingStrategy] = None,
        validate_first: bool = True,
        extraction_strategy: str = "auto",
        cancel_requested_callback: Optional[Callable[[], Awaitable[bool]]] = None,
        process_id_callback: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> ProcessingResult:
        """Process a PDF document through all pipeline stages.

        Args:
            file_path: Path to PDF file
            document_id: Document UUID
            user_id: User identifier for tenant scoping (FR-011, FR-014)
            strategy: Chunking strategy to use (defaults to GENERAL)
            validate_first: Whether to validate PDF before processing
            extraction_strategy: Strategy for PDF extraction ("auto", "hi_res", "fast")

        Returns:
            ProcessingResult with pipeline outcome
        """
        # Store user_id for use in error handling
        self._current_user_id = user_id
        # The PDF pipeline processes a paper's main file.
        self._current_file_id = None

        start_time = datetime.now()
        stages_completed = []
        receipt = PDFProcessingReceipt(document_id=document_id)

        # Always use research strategy
        if strategy is None:
            strategy = ChunkingStrategy.get_research_strategy()
        chunking_method = getattr(strategy, "chunking_method", None)
        receipt.set_selection(
            chunking_method=getattr(chunking_method, "value", chunking_method),
        )

        try:
            # Stage 0: Validate PDF if requested
            if validate_first:
                from .upload import validate_pdf
                validation = validate_pdf(file_path)
                if not validation["is_valid"]:
                    raise PipelineError(f"PDF validation failed: {validation['errors']}")
                logger.info("PDF validation passed for document %s", document_id)

            from src.lib.document_files.service import (
                element_text_bytes,
                main_file_id,
                mark_file_ready,
                reserve_file_chunk_range,
            )

            self._current_file_id = await asyncio.to_thread(main_file_id, document_id)

            # Initialize pipeline status
            await self._initialize_pipeline(document_id)
            await self._raise_if_cancel_requested(cancel_requested_callback)

            # Stage 1: Parse PDF
            logger.info("Starting PDF parsing for document %s", document_id)
            await self._update_status(document_id, ProcessingStage.PARSING)

            from .pdfx_parser import ReaderProgress, parse_pdf_document

            async def _track_parser_progress(progress: ReaderProgress) -> None:
                # The PDF reader's own state: waking up (a cold start takes minutes),
                # waiting, or reading with its reported percent.
                await self._raise_if_cancel_requested(cancel_requested_callback)
                await self.tracker.track_pipeline_progress(
                    document_id,
                    ProcessingStage.PARSING,
                    message=progress.message,
                )

            def _record_external_observation(observation: Dict[str, Any]) -> None:
                receipt.record_external_observation(observation)

            parse_start = time.monotonic()
            # T032: Pass user_id to enable user-specific file storage (FR-012)
            parse_result = await parse_pdf_document(
                file_path,
                document_id,
                user_id,
                extraction_strategy,
                progress_callback=_track_parser_progress,
                process_id_callback=process_id_callback,
                cancel_requested_callback=cancel_requested_callback,
                observability_callback=_record_external_observation,
            )

            # Extract elements and file paths from the result
            elements = parse_result["elements"]
            # Measured from the elements saved as the processed JSON.
            text_bytes = element_text_bytes(elements)
            pdfx_json_path = parse_result.get("pdfx_json_path")
            processed_json_path = parse_result.get("processed_json_path")
            await self._raise_if_cancel_requested(cancel_requested_callback)

            # Update database with file paths
            await self._update_file_paths(self._current_file_id, pdfx_json_path, processed_json_path)

            stages_completed.append(ProcessingStage.PARSING)
            parse_duration_ms = (time.monotonic() - parse_start) * 1000
            logger.info(
                "Parsed %s elements from document %s",
                len(elements),
                document_id,
                extra={"duration_ms": round(parse_duration_ms, 1), "operation": "pdf_parse"},
            )

            # Stage 1.5: Resolve Hierarchy
            hierarchy_metadata = None
            try:
                hierarchy_start = time.monotonic()
                with receipt.observe_stage("hierarchy"):
                    logger.info("Resolving section hierarchy for document %s", document_id)
                    await self._raise_if_cancel_requested(cancel_requested_callback)
                    # Send progress update to prevent UI freeze perception
                    await self.tracker.track_pipeline_progress(
                        document_id,
                        ProcessingStage.PARSING,
                        message="Resolving document structure..."
                    )
                    from .hierarchy_resolution import resolve_document_hierarchy
                    elements, hierarchy_metadata = await resolve_document_hierarchy(elements)

                    # Store hierarchy metadata in document record for later trace injection
                    if hierarchy_metadata:
                        await self._store_hierarchy_metadata(self._current_file_id, hierarchy_metadata)
                        hierarchy_duration_ms = (time.monotonic() - hierarchy_start) * 1000
                        logger.info(
                            "Resolved hierarchy with %s top-level sections",
                            len(hierarchy_metadata.top_level_sections),
                            extra={"duration_ms": round(hierarchy_duration_ms, 1), "operation": "hierarchy_resolution"},
                        )
            except Exception as e:
                logger.warning("Hierarchy resolution failed (continuing with flat structure): %s", e)

            # Stage 2: Chunk document
            logger.info("Starting chunking for document %s", document_id)
            await self._raise_if_cancel_requested(cancel_requested_callback)
            await self._update_status(document_id, ProcessingStage.CHUNKING)

            from .chunk import chunk_parsed_document, offset_chunk_indices
            chunk_start = time.monotonic()
            with receipt.observe_stage("chunking"):
                chunks = await chunk_parsed_document(elements, strategy, document_id)
            range_start = await asyncio.to_thread(
                reserve_file_chunk_range, document_id, self._current_file_id, len(chunks)
            )
            chunks = offset_chunk_indices(chunks, range_start)
            stages_completed.append(ProcessingStage.CHUNKING)
            chunk_duration_ms = (time.monotonic() - chunk_start) * 1000
            logger.info(
                "Created %s chunks from document %s",
                len(chunks),
                document_id,
                extra={"duration_ms": round(chunk_duration_ms, 1), "operation": "document_chunking"},
            )

            from .figure_locator_resolution import resolve_figure_locators

            with receipt.observe_stage("figure_locator"):
                chunks = await resolve_figure_locators(chunks)

            # Stage 3: Store to Weaviate (Weaviate handles embeddings)
            logger.info("Storing to Weaviate for document %s", document_id)
            await self._raise_if_cancel_requested(cancel_requested_callback)
            await self._update_status(document_id, ProcessingStage.STORING)

            from .store import store_to_weaviate, update_document_metadata
            store_start = time.monotonic()
            with receipt.observe_stage("embedding_storage"):
                await store_to_weaviate(
                    chunks,
                    document_id,
                    self.weaviate_client,
                    user_id,
                    chunk_range=(range_start, range_start + len(chunks) - 1),
                )
            await asyncio.to_thread(mark_file_ready, self._current_file_id, text_bytes=text_bytes)
            # The paper part: the paper's only file is stored.
            await update_document_metadata(
                document_id,
                {"stored_chunks": len(chunks), "failed_chunks": 0},
                self.weaviate_client,
                user_id,
            )
            stages_completed.append(ProcessingStage.STORING)
            store_duration_ms = (time.monotonic() - store_start) * 1000
            logger.info(
                "Stored document %s in Weaviate",
                document_id,
                extra={"duration_ms": round(store_duration_ms, 1), "operation": "weaviate_store"},
            )

            # Mark as completed
            await self._update_status(document_id, ProcessingStage.COMPLETED)

            duration = (datetime.now() - start_time).total_seconds()

            return ProcessingResult(
                success=True,
                document_id=document_id,
                stages_completed=stages_completed,
                total_chunks=len(chunks),
                total_embeddings=len(chunks),
                duration_seconds=duration,
                observability_receipt=receipt.finalize("completed"),
            )

        except PDFCancellationError as e:
            logger.info("Pipeline cancelled for document %s: %s", document_id, e)
            await self._fail_current_file(str(e))
            await self._sync_sql_document_status(
                document_id,
                status="failed",
                error_message=str(e),
            )
            try:
                from src.lib.weaviate_client.documents import update_document_status_detailed
                await update_document_status_detailed(document_id, self._current_user_id, embedding_status="failed")
            except Exception as status_error:
                logger.warning(
                    "Failed to mark embedding status as failed for cancelled document %s: %s",
                    document_id,
                    status_error,
                )
            await self.tracker.track_pipeline_progress(
                document_id,
                ProcessingStage.FAILED,
                message=str(e),
            )

            duration = (datetime.now() - start_time).total_seconds()
            return ProcessingResult(
                success=False,
                document_id=document_id,
                stages_completed=stages_completed,
                error=str(e),
                duration_seconds=duration,
                cancelled=True,
                observability_receipt=receipt.finalize("cancelled"),
            )

        except Exception as e:
            runtime_context = {
                "document_id": document_id,
                "stages_completed_count": len(stages_completed),
                "stages_completed": [stage.value for stage in stages_completed],
                "validate_first": validate_first,
                "extraction_strategy": extraction_strategy,
            }
            runtime_context.update(_pdfx_runtime_context(e))
            sentry_reported = _report_pipeline_failure(
                e,
                operation="process_pdf_document_failed",
                context=runtime_context,
            )
            # The explicit report owns this failure; the tracker ERROR log is
            # the backup if capture fails, even when WARNING logs are promoted.
            logger.warning(
                "Pipeline failed for document %s: %s", document_id, e, exc_info=True,
                extra={"sentry_skip_event": True},
            )
            public_error_message = _public_pipeline_error_message(e)

            # Mark as failed
            await self._handle_failure(
                document_id,
                e,
                public_error_message=public_error_message,
                sentry_reported=sentry_reported,
            )

            duration = (datetime.now() - start_time).total_seconds()

            return ProcessingResult(
                success=False,
                document_id=document_id,
                stages_completed=stages_completed,
                error=public_error_message,
                duration_seconds=duration,
                observability_receipt=receipt.finalize("failed"),
            )

    async def _raise_if_cancel_requested(
        self,
        cancel_requested_callback: Optional[Callable[[], Awaitable[bool]]],
    ) -> None:
        """Raise cancellation exception when caller requested cancellation."""
        if not cancel_requested_callback:
            return
        if await cancel_requested_callback():
            raise PDFCancellationError("Processing cancelled by user request")

    def validate_pipeline_requirements(self) -> Dict[str, bool]:
        """Validate all pipeline requirements are met.

        Returns:
            Dictionary of requirement checks
        """
        checks = {
            "weaviate_connected": False,
            "embedding_service_available": True,  # Managed by Weaviate now
            "storage_writable": False
        }

        # Check Weaviate connection
        try:
            if self.weaviate_client:
                # Assuming client has is_ready() method
                checks["weaviate_connected"] = True
        except Exception:
            pass

        # Check storage permissions
        try:
            from pathlib import Path
            temp_path = Path("/tmp/pipeline_test")
            temp_path.touch()
            temp_path.unlink()
            checks["storage_writable"] = True
        except Exception:
            pass

        return checks

    async def retry_stage(
        self,
        document_id: str,
        stage: ProcessingStage
    ) -> ProcessingResult:
        """Retry a specific pipeline stage.

        FUTURE FEATURE: This method is a placeholder for pipeline stage retry functionality.
        When implemented, it would allow retrying a failed stage (e.g., embedding) without
        re-running earlier successful stages (e.g., parsing, chunking).

        Requirements for implementation:
        - State persistence: Save intermediate results after each stage
        - Stage isolation: Each stage must be independently re-runnable
        - Error recovery: Handle partial failures gracefully

        Args:
            document_id: Document UUID
            stage: Stage to retry (PARSING, CHUNKING, EMBEDDING, STORING)

        Returns:
            ProcessingResult with retry outcome

        Raises:
            NotImplementedError: This feature is not yet implemented
        """
        logger.warning(
            "Stage retry requested for %s on document %s, but feature not yet implemented",
            stage.value,
            document_id,
        )

        raise NotImplementedError(
            "Stage retry not yet implemented. "
            "Currently, failed documents must be reprocessed from the beginning."
        )

    async def _initialize_pipeline(self, document_id: str):
        """Initialize pipeline tracking for a document."""
        await self.tracker.track_pipeline_progress(
            document_id,
            ProcessingStage.PENDING,
            progress_percentage=0,
            message="Pipeline initialized"
        )

    async def _update_status(self, document_id: str, stage: ProcessingStage):
        """Update processing status for a document."""
        messages = {
            ProcessingStage.PARSING: "Parsing PDF document...",
            ProcessingStage.CHUNKING: "Splitting document into chunks...",
            ProcessingStage.EMBEDDING: "Generating embeddings...",
            ProcessingStage.STORING: "Storing in Weaviate database...",
            ProcessingStage.COMPLETED: "Processing completed successfully"
        }
        sql_status = None
        if stage in {
            ProcessingStage.PARSING,
            ProcessingStage.CHUNKING,
            ProcessingStage.EMBEDDING,
            ProcessingStage.STORING,
        }:
            sql_status = "processing"
        elif stage == ProcessingStage.COMPLETED:
            sql_status = "completed"

        if sql_status:
            await self._sync_sql_document_status(document_id, status=sql_status)

        await self.tracker.track_pipeline_progress(
            document_id,
            stage,
            message=messages.get(stage, f"Processing {stage.value}...")
        )

    async def _sync_sql_document_status(
        self,
        document_id: str,
        *,
        status: str,
        error_message: Optional[str] = None,
    ) -> None:
        # A source binding briefly locks the document generation row.
        # Never wait on that row from the event-loop thread.
        await asyncio.to_thread(
            self._write_sql_document_status, document_id,
            status=status, error_message=error_message,
        )

    def _write_sql_document_status(
        self, document_id: str, *, status: str, error_message: Optional[str] = None,
    ) -> None:
        """Persist pipeline status fields in PostgreSQL for PDF viewer and ops visibility."""
        from src.models.sql.database import SessionLocal
        from src.models.sql.pdf_document import PDFDocument
        from uuid import UUID

        now = datetime.now(timezone.utc)
        session = SessionLocal()
        try:
            doc = session.query(PDFDocument).filter(PDFDocument.id == UUID(document_id)).first()
            if not doc:
                raise PipelineError("Document row is unavailable for source processing")

            doc.status = status

            if status == "processing":
                if doc.processing_started_at is None:
                    doc.processing_started_at = now
                doc.processing_completed_at = None
                doc.error_message = None
            elif status == "completed":
                if doc.processing_started_at is None:
                    doc.processing_started_at = now
                doc.processing_completed_at = now
                doc.error_message = None
            elif status == "failed":
                if doc.processing_started_at is None:
                    doc.processing_started_at = now
                doc.processing_completed_at = now
                doc.error_message = (error_message or "Pipeline failed")[
                    :get_pdf_document_error_message_max_chars()
                ]

            session.commit()
            logger.info("Updated SQL document status for %s to %s", document_id, status)
        except Exception as e:
            logger.error("Error updating SQL status for document %s: %s", document_id, e)
            session.rollback()
            # Processing/completion commits protect source generation. A failed
            # status notification must not replace the original pipeline error.
            if status != "failed":
                raise
        finally:
            session.close()

    async def _update_file_paths(
        self,
        file_id: Any,
        pdfx_json_path: Optional[str],
        processed_json_path: Optional[str],
    ):
        """Record the main file's extraction artifact paths."""
        from src.models.sql.database import SessionLocal
        from src.models.sql.pdf_document import PDFDocumentFile

        try:
            with SessionLocal.begin() as session:
                file_row = session.get_one(PDFDocumentFile, file_id)
                file_row.pdfx_json_path = pdfx_json_path
                file_row.processed_json_path = processed_json_path
        except Exception as exc:
            logger.warning(
                "Could not persist file paths (%s)", type(exc).__name__, extra={"sentry_skip_event": True},
            )
        else:
            logger.info("Updated file paths for file %s", file_id)
            return

        # Raise outside the handler so the database exception cannot reach the failure owner.
        raise PipelineError("The paper's processed files could not be saved. Please upload it again.")

    async def _store_hierarchy_metadata(self, file_id: Any, hierarchy_metadata):
        """Store the main file's hierarchy metadata for later trace injection."""
        from src.models.sql.database import SessionLocal
        from src.models.sql.pdf_document import PDFDocumentFile

        try:
            with SessionLocal.begin() as session:
                file_row = session.get_one(PDFDocumentFile, file_id)
                file_row.hierarchy_metadata = hierarchy_metadata.model_dump()
        except Exception as exc:
            exception_type = type(exc).__name__
            logger.warning(
                "Could not persist hierarchy metadata (%s)", exception_type, extra={"sentry_skip_event": True},
            )
            _report_pipeline_failure(
                sanitized_runtime_error("Pipeline hierarchy metadata write failed"),
                operation="pipeline_hierarchy_metadata_write_failed",
                context={"exception_type": exception_type},
            )
        else:
            logger.info("Stored hierarchy metadata for file %s", file_id)

    async def _fail_current_file(self, error_message: str) -> None:
        """Record that the file being processed failed; the paper status is set separately."""
        if self._current_file_id is None:
            return
        from src.lib.document_files.service import mark_file_failed

        try:
            await asyncio.to_thread(
                mark_file_failed, self._current_file_id, error_message=error_message
            )
        except Exception as status_error:
            # Like the paper status write below, a failed status notification
            # must not replace the original pipeline error.
            logger.error(
                "Failed to mark file %s failed: %s", self._current_file_id, status_error
            )

    async def _handle_failure(
        self,
        document_id: str,
        error: Exception,
        *,
        public_error_message: Optional[str] = None,
        sentry_reported: bool = False,
    ):
        """Handle pipeline failure."""
        safe_message = public_error_message or _public_pipeline_error_message(error)
        await self._fail_current_file(safe_message)
        await self._sync_sql_document_status(
            document_id,
            status="failed",
            error_message=safe_message,
        )

        try:
            from src.lib.weaviate_client.documents import update_document_status_detailed
            await update_document_status_detailed(document_id, self._current_user_id, embedding_status="failed")
        except Exception as status_error:
            logger.warning(
                "Failed to mark embedding status as failed for %s: %s",
                document_id,
                status_error,
            )

        tracker_error = error
        if isinstance(error, PDFParsingError) and safe_message != str(error):
            tracker_error = PDFParsingError(safe_message, error_code=error.error_code)
        await self.tracker.handle_pipeline_failure(
            document_id,
            tracker_error,
            stage=ProcessingStage.FAILED,
            sentry_reported=sentry_reported,
        )
        await self.tracker.track_pipeline_progress(
            document_id,
            ProcessingStage.FAILED,
            message=f"Pipeline failed: {safe_message}"
        )


async def process_pdf_document(
    file_path: Path,
    document_id: str,
    weaviate_client: Any,
    user_id: str,
    strategy: Optional[ChunkingStrategy] = None
) -> ProcessingResult:
    """Convenience function to process a PDF document.

    Args:
        file_path: Path to PDF file
        document_id: Document UUID
        weaviate_client: Weaviate database client
        user_id: User identifier for tenant scoping (FR-011, FR-014)
        strategy: Chunking strategy to use

    Returns:
        ProcessingResult with pipeline outcome
    """
    orchestrator = DocumentPipelineOrchestrator(weaviate_client)
    return await orchestrator.process_pdf_document(file_path, document_id, user_id, strategy)

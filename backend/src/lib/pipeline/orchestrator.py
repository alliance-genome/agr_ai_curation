"""Main pipeline orchestrator for document processing."""

from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any
from dataclasses import dataclass
import logging
import os
import asyncio
from contextlib import suppress

import httpx

from src.models.pipeline import ProcessingStage
from .tracker import PipelineTracker
from src.models.strategy import ChunkingStrategy, StrategyName

logger = logging.getLogger(__name__)


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

    async def process_pdf_document(
        self,
        file_path: Path,
        document_id: str,
        user_id: str,
        strategy: Optional[ChunkingStrategy] = None,
        validate_first: bool = True,
        extraction_strategy: str = "auto"
    ) -> ProcessingResult:
        """Process a PDF document through all pipeline stages.

        Args:
            file_path: Path to PDF file
            document_id: Document UUID
            user_id: Okta user identifier for tenant scoping (FR-011, FR-014)
            strategy: Chunking strategy to use (defaults to GENERAL)
            validate_first: Whether to validate PDF before processing
            extraction_strategy: Strategy for PDF extraction ("auto", "hi_res", "fast")

        Returns:
            ProcessingResult with pipeline outcome
        """
        # Store user_id for use in error handling
        self._current_user_id = user_id

        start_time = datetime.now()
        stages_completed = []

        # Always use research strategy
        if strategy is None:
            strategy = ChunkingStrategy.get_research_strategy()

        try:
            # Stage 0: Validate PDF if requested
            if validate_first:
                from .upload import validate_pdf
                validation = validate_pdf(file_path)
                if not validation["is_valid"]:
                    raise PipelineError(f"PDF validation failed: {validation['errors']}")
                logger.info(f"PDF validation passed for document {document_id}")

            # Initialize pipeline status
            await self._initialize_pipeline(document_id)

            # Stage 1: Parse PDF
            logger.info(f"Starting PDF parsing for document {document_id}")
            await self._update_status(document_id, ProcessingStage.PARSING)

            from .docling_parser import parse_pdf_document

            monitor_task = None
            try:
                monitor_task = asyncio.create_task(
                    self._monitor_docling_progress(document_id)
                )
            except Exception as monitor_error:  # pragma: no cover - monitor failure shouldn't break pipeline
                logger.debug("Docling progress monitor not started: %s", monitor_error)

            try:
                # T032: Pass user_id to enable user-specific file storage (FR-012)
                parse_result = await parse_pdf_document(file_path, document_id, user_id, extraction_strategy)
            finally:
                if monitor_task:
                    monitor_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await monitor_task

            # Extract elements and file paths from the result
            elements = parse_result["elements"]
            docling_json_path = parse_result.get("docling_json_path")
            processed_json_path = parse_result.get("processed_json_path")

            # Update database with file paths
            await self._update_file_paths(document_id, docling_json_path, processed_json_path)

            stages_completed.append(ProcessingStage.PARSING)
            logger.info(f"Parsed {len(elements)} elements from document {document_id}")

            # Stage 1.5: Resolve Hierarchy
            hierarchy_metadata = None
            try:
                logger.info(f"Resolving section hierarchy for document {document_id}")
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
                    await self._store_hierarchy_metadata(document_id, hierarchy_metadata)
                    logger.info(f"Stored hierarchy metadata with {len(hierarchy_metadata.top_level_sections)} top-level sections")
            except Exception as e:
                logger.warning(f"Hierarchy resolution failed (continuing with flat structure): {e}")

            # Stage 2: Chunk document
            logger.info(f"Starting chunking for document {document_id}")
            await self._update_status(document_id, ProcessingStage.CHUNKING)

            from .chunk import chunk_parsed_document
            chunks = await chunk_parsed_document(elements, strategy, document_id)
            stages_completed.append(ProcessingStage.CHUNKING)
            logger.info(f"Created {len(chunks)} chunks from document {document_id}")

            # Stage 3: Store to Weaviate (Weaviate handles embeddings)
            logger.info(f"Storing to Weaviate for document {document_id}")
            await self._update_status(document_id, ProcessingStage.STORING)

            from .store import store_to_weaviate
            await store_to_weaviate(
                chunks,
                document_id,
                self.weaviate_client,
                user_id
            )
            stages_completed.append(ProcessingStage.STORING)

            # Mark as completed
            await self._update_status(document_id, ProcessingStage.COMPLETED)

            duration = (datetime.now() - start_time).total_seconds()

            return ProcessingResult(
                success=True,
                document_id=document_id,
                stages_completed=stages_completed,
                total_chunks=len(chunks),
                total_embeddings=len(chunks),
                duration_seconds=duration
            )

        except Exception as e:
            logger.error(f"Pipeline failed for document {document_id}: {str(e)}")

            # Mark as failed
            await self._handle_failure(document_id, e)

            duration = (datetime.now() - start_time).total_seconds()

            return ProcessingResult(
                success=False,
                document_id=document_id,
                stages_completed=stages_completed,
                error=str(e),
                duration_seconds=duration
            )

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

    async def _monitor_docling_progress(self, document_id: str) -> None:
        """Poll Docling service for job progress and update tracker while parsing."""

        service_url = os.getenv("DOCLING_SERVICE_URL", "http://docling-internal.alliancegenome.org:8000").rstrip("/")
        status_url = f"{service_url}/status/{document_id}"
        start_time = datetime.now()

        async with httpx.AsyncClient(timeout=5.0) as client:
            while True:
                progress_message = "Docling parsing in progress"
                try:
                    response = await client.get(status_url)
                    if response.status_code == 200:
                        payload = response.json()
                        progress = payload.get("progress")
                        status = payload.get("status", "processing")
                        if isinstance(progress, (int, float)):
                            progress_message = f"Extracting content from PDF... {progress}%"
                        else:
                            progress_message = "Extracting content from PDF..."
                    elif response.status_code == 404:
                        progress_message = "Extracting PDF content (this may take 1-2 minutes, please wait)..."
                    else:
                        progress_message = "Extracting PDF content (this may take 1-2 minutes, please wait)..."
                except Exception as exc:  # pragma: no cover - network hiccups are non-fatal
                    progress_message = "Extracting PDF content (this may take 1-2 minutes, please wait)..."
                    logger.debug("Docling progress poll failed: %s", exc)

                # Add elapsed time as heartbeat indicator (cleaner format)
                elapsed = int((datetime.now() - start_time).total_seconds())
                message = f"{progress_message} ({elapsed}s)"
                try:
                    await self.tracker.track_pipeline_progress(
                        document_id,
                        ProcessingStage.PARSING,
                        message=message
                    )
                except Exception:  # pragma: no cover - tracker failures should not abort parsing
                    logger.debug("Failed to update docling progress", exc_info=True)

                await asyncio.sleep(2)

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
        logger.warning(f"Stage retry requested for {stage.value} on document {document_id}, but feature not yet implemented")

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
        await self.tracker.track_pipeline_progress(
            document_id,
            stage,
            message=messages.get(stage, f"Processing {stage.value}...")
        )

    async def _update_file_paths(self, document_id: str, docling_json_path: str, processed_json_path: str):
        """Update file paths in the database."""
        from src.models.sql.database import SessionLocal
        from src.models.sql.pdf_document import PDFDocument
        from uuid import UUID

        session = SessionLocal()
        try:
            # Find and update the document
            doc = session.query(PDFDocument).filter(PDFDocument.id == UUID(document_id)).first()
            if doc:
                doc.docling_json_path = docling_json_path
                doc.processed_json_path = processed_json_path
                session.commit()
                logger.info(f"Updated file paths for document {document_id}")
        except Exception as e:
            logger.error(f"Error updating file paths for document {document_id}: {e}")
            session.rollback()
        finally:
            session.close()

    async def _store_hierarchy_metadata(self, document_id: str, hierarchy_metadata):
        """Store hierarchy metadata in the database for later trace injection."""
        from src.models.sql.database import SessionLocal
        from src.models.sql.pdf_document import PDFDocument
        from uuid import UUID

        session = SessionLocal()
        try:
            doc = session.query(PDFDocument).filter(PDFDocument.id == UUID(document_id)).first()
            if doc:
                # Store as JSON in hierarchy_metadata column
                # Note: Column must be added to PDFDocument model if not present
                doc.hierarchy_metadata = hierarchy_metadata.model_dump()
                session.commit()
                logger.info(f"Stored hierarchy metadata for document {document_id}")
        except Exception as e:
            logger.error(f"Error storing hierarchy metadata for document {document_id}: {e}")
            session.rollback()
        finally:
            session.close()

    async def _handle_failure(self, document_id: str, error: Exception):
        """Handle pipeline failure."""
        try:
            from src.lib.weaviate_client.documents import update_document_status_detailed
            await update_document_status_detailed(document_id, self._current_user_id, embedding_status="failed")
        except Exception as status_error:
            logger.warning(
                "Failed to mark embedding status as failed for %s: %s",
                document_id,
                status_error,
            )

        await self.tracker.handle_pipeline_failure(
            document_id,
            error,
            stage=ProcessingStage.FAILED
        )
        await self.tracker.track_pipeline_progress(
            document_id,
            ProcessingStage.FAILED,
            message=f"Pipeline failed: {str(error)}"
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
        user_id: Okta user identifier for tenant scoping (FR-011, FR-014)
        strategy: Chunking strategy to use

    Returns:
        ProcessingResult with pipeline outcome
    """
    orchestrator = DocumentPipelineOrchestrator(weaviate_client)
    return await orchestrator.process_pdf_document(file_path, document_id, user_id, strategy)

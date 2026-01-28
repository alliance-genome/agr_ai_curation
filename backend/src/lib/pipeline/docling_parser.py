"""Docling PDF parser using REST API to flysql26."""

import os
import logging
import asyncio
import aiohttp
import json
from pathlib import Path
from typing import List, Dict, Any, Optional

from ..exceptions import PDFParsingError, ConfigurationError
from pydantic import ValidationError

from ...schemas.docling_schema import (
    DoclingResponse,
    normalize_elements,
    build_pipeline_elements,
)

logger = logging.getLogger(__name__)


class DoclingParser:
    """Parser for PDFs using Docling on flysql26."""

    def __init__(self):
        """Initialize Docling parser."""
        # Get service URL from environment or use default
        self.service_url = os.getenv("DOCLING_SERVICE_URL", "http://docling-internal.alliancegenome.org:8000")

        # Circuit breaker to prevent runaway requests
        self.invocation_count = 0
        self.max_invocations_per_session = 50  # Safety limit

        # Timeout configuration (5 minutes default for larger scientific papers)
        self.timeout_seconds = int(os.getenv("DOCLING_TIMEOUT", "300"))

        logger.info(
            "Initialized Docling parser with service %s, timeout %ds, max invocations %d",
            self.service_url,
            self.timeout_seconds,
            self.max_invocations_per_session
        )

    async def parse_pdf_document(
        self,
        file_path: Path,
        document_id: str,
        user_id: str,
        extraction_strategy: Optional[str] = None,
        enable_table_extraction: Optional[bool] = None
    ) -> List[Dict[str, Any]]:
        """
        Parse PDF using Docling REST API.

        Args:
            file_path: Path to PDF file
            document_id: Document UUID
            user_id: User identifier for tenant-scoped file storage (T032, FR-012)
            extraction_strategy: Extraction strategy (optional)
            enable_table_extraction: Enable table extraction (optional)

        This is a drop-in replacement for the unstructured.io parse_pdf_document.
        """
        if not file_path.exists():
            raise PDFParsingError(f"File not found: {file_path}")

        if not file_path.suffix.lower() == '.pdf':
            raise PDFParsingError(f"File is not a PDF: {file_path}")

        # Check circuit breaker
        if self.invocation_count >= self.max_invocations_per_session:
            raise PDFParsingError(
                f"Circuit breaker: Too many invocations ({self.invocation_count}). "
                f"Create a new parser instance or restart service."
            )
        self.invocation_count += 1

        # Prepare multipart form data
        extract_tables = enable_table_extraction if enable_table_extraction is not None else True
        extract_equations = os.getenv("ENABLE_EQUATION_EXTRACTION", "true").lower() == "true"

        logger.info(f"Sending {file_path.name} to Docling service for {document_id}")

        try:
            # Use aiohttp for async HTTP request
            timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                # Prepare form data
                with open(file_path, 'rb') as f:
                    data = aiohttp.FormData()
                    data.add_field('file',
                                   f,
                                   filename=file_path.name,
                                   content_type='application/pdf')
                    data.add_field('document_id', document_id)
                    data.add_field('extract_tables', str(extract_tables))
                    data.add_field('extract_equations', str(extract_equations))

                    # Send request
                    async with session.post(
                        f"{self.service_url}/parse-pdf",
                        data=data
                    ) as response:
                        if response.status != 200:
                            error_text = await response.text()
                            raise PDFParsingError(f"Service error: {response.status} - {error_text}")

                        result = await response.json()

            if not result.get("success"):
                raise PDFParsingError(f"Parsing failed: {result.get('error', 'Unknown error')}")

            # Save raw Docling JSON output to user-specific directory (T032, FR-012)
            docling_json_path = await self._save_docling_json(result, document_id, user_id)

            try:
                response_model = DoclingResponse.model_validate(result)
            except ValidationError as exc:
                raise PDFParsingError(f"Docling response validation failed: {exc}") from exc

            normalized = normalize_elements(response_model)
            logger.info(
                "Normalized %d elements (content breakdown: %s)",
                len(normalized),
                response_model.metadata.get("content_breakdown", {}),
            )

            cleaned_elements = build_pipeline_elements(normalized)
            logger.info(
                "Prepared %d elements for chunking from %s",
                len(cleaned_elements),
                file_path.name,
            )

            # Save processed/cleaned JSON output to user-specific directory (T032, FR-012)
            processed_json_path = await self._save_processed_json(cleaned_elements, document_id, user_id)

            # Store file paths in database (this will be done in the orchestrator)
            # Return the paths along with the elements
            cleaned_elements_with_paths = {
                "elements": cleaned_elements,
                "docling_json_path": str(docling_json_path),
                "processed_json_path": str(processed_json_path)
            }

            return cleaned_elements_with_paths

        except asyncio.TimeoutError:
            raise PDFParsingError(f"Docling service timeout after {self.timeout_seconds} seconds")
        except aiohttp.ClientError as e:
            raise PDFParsingError(f"Network error calling Docling service: {str(e)}")
        except Exception as e:
            raise PDFParsingError(f"Unexpected error: {str(e)}")

    async def _save_docling_json(self, result: Dict[str, Any], document_id: str, user_id: str) -> Path:
        """Save raw Docling JSON output to user-specific directory.

        Args:
            result: Docling JSON response
            document_id: Document UUID
            user_id: User identifier for tenant-scoped storage (T032, FR-012)

        Returns:
            Path relative to pdf_storage root: {user_id}/docling_json/{doc_id}.json
        """
        from ...config import get_pdf_storage_path

        # Create user-specific directory: pdf_storage/{user_id}/docling_json/
        pdf_storage = get_pdf_storage_path()
        user_docling_path = pdf_storage / user_id / "docling_json"
        user_docling_path.mkdir(parents=True, exist_ok=True)

        file_path = user_docling_path / f"{document_id}.json"

        # Write JSON file asynchronously
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: file_path.write_text(json.dumps(result, indent=2))
        )

        logger.info(f"Saved raw Docling JSON to {file_path}")
        # Return path relative to pdf_storage root: {user_id}/docling_json/{doc_id}.json
        return file_path.relative_to(pdf_storage)

    async def _save_processed_json(self, elements: List[Dict[str, Any]], document_id: str, user_id: str) -> Path:
        """Save processed/cleaned JSON output to user-specific directory.

        Args:
            elements: Processed/cleaned elements
            document_id: Document UUID
            user_id: User identifier for tenant-scoped storage (T032, FR-012)

        Returns:
            Path relative to pdf_storage root: {user_id}/processed_json/{doc_id}.json
        """
        from ...config import get_pdf_storage_path

        # Create user-specific directory: pdf_storage/{user_id}/processed_json/
        pdf_storage = get_pdf_storage_path()
        user_processed_path = pdf_storage / user_id / "processed_json"
        user_processed_path.mkdir(parents=True, exist_ok=True)

        file_path = user_processed_path / f"{document_id}.json"

        # Write JSON file asynchronously
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: file_path.write_text(json.dumps(elements, indent=2))
        )

        logger.info(f"Saved processed JSON to {file_path}")
        # Return path relative to pdf_storage root: {user_id}/processed_json/{doc_id}.json
        return file_path.relative_to(pdf_storage)


# Module-level function to match unstructured.io interface
async def parse_pdf_document(
    file_path: Path,
    document_id: str,
    user_id: str,
    extraction_strategy: Optional[str] = None,
    enable_table_extraction: Optional[bool] = None
) -> Dict[str, Any]:
    """
    Parse PDF document using Docling.

    Args:
        file_path: Path to PDF file
        document_id: Document UUID
        user_id: User identifier for tenant-scoped file storage (T032, FR-012)
                 **REQUIRED in multi-tenant mode** - ensures all derived files
                 (Docling JSON, processed JSON) are stored in user-specific directories
        extraction_strategy: Extraction strategy (optional)
        enable_table_extraction: Enable table extraction (optional)

    Returns dict with elements and file paths.
    Drop-in replacement for unstructured.io parse_pdf_document.
    """
    parser = DoclingParser()
    return await parser.parse_pdf_document(
        file_path=file_path,
        document_id=document_id,
        user_id=user_id,
        extraction_strategy=extraction_strategy,
        enable_table_extraction=enable_table_extraction
    )


# Keep the same validation functions
def validate_pdf_file(file_path: Path) -> Dict[str, Any]:
    """Validate PDF file before parsing."""
    validation = {
        "is_valid": True,
        "file_exists": False,
        "is_pdf": False,
        "file_size": 0,
        "errors": []
    }

    if not file_path.exists():
        validation["is_valid"] = False
        validation["errors"].append(f"File not found: {file_path}")
        return validation

    validation["file_exists"] = True

    if not file_path.suffix.lower() == '.pdf':
        validation["is_valid"] = False
        validation["errors"].append(f"Not a PDF file: {file_path.suffix}")
    else:
        validation["is_pdf"] = True

    file_size = file_path.stat().st_size
    validation["file_size"] = file_size

    if file_size == 0:
        validation["is_valid"] = False
        validation["errors"].append("File is empty")
    elif file_size > 100 * 1024 * 1024:  # 100MB limit
        validation["errors"].append("File exceeds 100MB limit - parsing may be slow")

    # Check PDF header
    try:
        with open(file_path, 'rb') as f:
            header = f.read(5)
            if header != b'%PDF-':
                validation["is_valid"] = False
                validation["errors"].append("Invalid PDF header - file may be corrupted")
    except Exception as e:
        validation["is_valid"] = False
        validation["errors"].append(f"Cannot read file: {str(e)}")

    return validation


def handle_parsing_errors(error: Exception) -> None:
    """Handle and log parsing errors."""
    error_message = str(error)

    if "timeout" in error_message.lower():
        logger.warning("Docling service timed out. Check service health or increase timeout.")

    elif "network" in error_message.lower():
        logger.error("Network error accessing Docling service. Check VPN connection and service status.")

    elif "service error" in error_message.lower():
        logger.error("Docling service returned an error. Check service logs.")

    else:
        logger.error(f"Unhandled parsing error: {error_message}")


def get_extraction_strategy() -> str:
    """Get PDF extraction strategy from environment."""
    return os.getenv("DOCLING_INFERENCE_MODE", "full-page")


def validate_extraction_strategy(strategy: str) -> None:
    """Validate extraction strategy."""
    valid_strategies = ["full-page", "partial", "fast"]
    if strategy not in valid_strategies:
        raise ConfigurationError(f"Invalid extraction strategy: {strategy}. Must be one of {valid_strategies}")


def is_table_extraction_enabled() -> bool:
    """Check if table extraction is enabled."""
    value = os.getenv("ENABLE_TABLE_EXTRACTION", "true")
    return value.lower() == "true"


async def update_processing_status(
    document_id: str,
    status: str
) -> None:
    """Update the processing status of a document."""
    logger.info(f"Updating document {document_id} status to: {status}")
    timestamp = datetime.now().isoformat()
    logger.debug(f"Status update: {document_id} -> {status} at {timestamp}")

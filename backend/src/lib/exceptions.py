"""Custom exception classes for AI Curation Platform."""

from typing import Optional, Dict, Any


class BasePipelineError(Exception):
    """Base exception for pipeline errors."""

    ERROR_CODE = "PIPELINE_001"

    def __init__(
        self,
        message: str,
        error_code: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None
    ):
        """Initialize exception with message, code, and optional details.

        Args:
            message: Error message
            error_code: Optional error code override
            details: Optional dictionary with additional error details
        """
        super().__init__(message)
        self.error_code = error_code or self.ERROR_CODE
        self.details = details or {}


class WeaviateConnectionError(BasePipelineError):
    """Raised when Weaviate connection fails."""

    ERROR_CODE = "WEAVIATE_CONN_001"


class CollectionNotFoundError(BasePipelineError):
    """Raised when a required Weaviate collection doesn't exist."""

    ERROR_CODE = "WEAVIATE_COLL_001"


class BatchInsertError(BasePipelineError):
    """Raised when batch insert operations fail."""

    ERROR_CODE = "BATCH_INSERT_001"

    def __init__(
        self,
        message: str,
        error_code: Optional[str] = None,
        failed_objects: Optional[list] = None
    ):
        """Initialize with failed objects list."""
        super().__init__(message, error_code)
        self.failed_objects = failed_objects or []
        self.details["failed_objects"] = self.failed_objects


class PDFParsingError(BasePipelineError):
    """Raised when PDF parsing fails."""

    ERROR_CODE = "PDF_PARSE_001"


class EmbeddingError(BasePipelineError):
    """Raised when embedding generation fails."""

    ERROR_CODE = "EMBED_001"


class StorageError(BasePipelineError):
    """Raised during storage operations."""

    ERROR_CODE = "STORAGE_001"


class ConfigurationError(BasePipelineError):
    """Raised when configuration is invalid."""

    ERROR_CODE = "CONFIG_001"


class ValidationError(BasePipelineError):
    """Raised when validation fails."""

    ERROR_CODE = "VALIDATION_001"
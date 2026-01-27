"""Unit tests for custom exception classes."""

import pytest
import sys
import os

# Add backend directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.lib.exceptions import (
    BasePipelineError,
    WeaviateConnectionError,
    CollectionNotFoundError,
    BatchInsertError,
    PDFParsingError,
    EmbeddingError,
    StorageError,
    ConfigurationError,
    ValidationError
)


class TestBasePipelineError:
    """Test cases for BasePipelineError base class."""

    def test_create_with_message_only(self):
        """Test creating exception with just message."""
        error = BasePipelineError("Test error message")
        assert str(error) == "Test error message"
        assert error.error_code == "PIPELINE_001"
        assert error.details == {}

    def test_create_with_custom_error_code(self):
        """Test creating exception with custom error code."""
        error = BasePipelineError("Test error", error_code="CUSTOM_001")
        assert str(error) == "Test error"
        assert error.error_code == "CUSTOM_001"

    def test_create_with_details(self):
        """Test creating exception with additional details."""
        details = {"file": "test.pdf", "size": 1024}
        error = BasePipelineError("Test error", details=details)
        assert error.details == details
        assert error.details["file"] == "test.pdf"
        assert error.details["size"] == 1024


class TestWeaviateConnectionError:
    """Test cases for WeaviateConnectionError."""

    def test_has_correct_error_code(self):
        """Test that WeaviateConnectionError has correct default error code."""
        error = WeaviateConnectionError("Connection failed")
        assert error.error_code == "WEAVIATE_CONN_001"

    def test_inherits_from_base(self):
        """Test that exception inherits from BasePipelineError."""
        error = WeaviateConnectionError("Connection failed")
        assert isinstance(error, BasePipelineError)

    def test_can_override_error_code(self):
        """Test that error code can be overridden."""
        error = WeaviateConnectionError("Connection failed", error_code="CONN_999")
        assert error.error_code == "CONN_999"


class TestCollectionNotFoundError:
    """Test cases for CollectionNotFoundError."""

    def test_has_correct_error_code(self):
        """Test that CollectionNotFoundError has correct default error code."""
        error = CollectionNotFoundError("Collection missing")
        assert error.error_code == "WEAVIATE_COLL_001"

    def test_with_collection_name_detail(self):
        """Test adding collection name in details."""
        error = CollectionNotFoundError(
            "Collection not found",
            details={"collection_name": "DocumentChunk"}
        )
        assert error.details["collection_name"] == "DocumentChunk"


class TestBatchInsertError:
    """Test cases for BatchInsertError."""

    def test_has_correct_error_code(self):
        """Test that BatchInsertError has correct default error code."""
        error = BatchInsertError("Batch failed")
        assert error.error_code == "BATCH_INSERT_001"

    def test_with_failed_objects(self):
        """Test BatchInsertError with failed objects list."""
        failed_objects = [
            {"id": "1", "error": "Invalid vector"},
            {"id": "2", "error": "Missing property"}
        ]
        error = BatchInsertError(
            "Batch insert failed",
            failed_objects=failed_objects
        )
        assert error.failed_objects == failed_objects
        assert len(error.failed_objects) == 2
        assert error.details["failed_objects"] == failed_objects

    def test_empty_failed_objects_list(self):
        """Test BatchInsertError with empty failed objects list."""
        error = BatchInsertError("Batch failed", failed_objects=None)
        assert error.failed_objects == []
        assert error.details["failed_objects"] == []


class TestPDFParsingError:
    """Test cases for PDFParsingError."""

    def test_has_correct_error_code(self):
        """Test that PDFParsingError has correct default error code."""
        error = PDFParsingError("Parsing failed")
        assert error.error_code == "PDF_PARSE_001"

    def test_with_file_details(self):
        """Test PDFParsingError with file details."""
        error = PDFParsingError(
            "Failed to parse PDF",
            details={"file_path": "/path/to/file.pdf", "page": 5}
        )
        assert error.details["file_path"] == "/path/to/file.pdf"
        assert error.details["page"] == 5


class TestEmbeddingError:
    """Test cases for EmbeddingError."""

    def test_has_correct_error_code(self):
        """Test that EmbeddingError has correct default error code."""
        error = EmbeddingError("Embedding generation failed")
        assert error.error_code == "EMBED_001"

    def test_with_model_details(self):
        """Test EmbeddingError with model details."""
        error = EmbeddingError(
            "OpenAI API error",
            details={"model": "text-embedding-ada-002", "status_code": 429}
        )
        assert error.details["model"] == "text-embedding-ada-002"
        assert error.details["status_code"] == 429


class TestStorageError:
    """Test cases for StorageError."""

    def test_has_correct_error_code(self):
        """Test that StorageError has correct default error code."""
        error = StorageError("Storage operation failed")
        assert error.error_code == "STORAGE_001"

    def test_with_storage_details(self):
        """Test StorageError with storage operation details."""
        error = StorageError(
            "Failed to store chunks",
            details={"chunks_attempted": 100, "chunks_stored": 75}
        )
        assert error.details["chunks_attempted"] == 100
        assert error.details["chunks_stored"] == 75


class TestConfigurationError:
    """Test cases for ConfigurationError."""

    def test_has_correct_error_code(self):
        """Test that ConfigurationError has correct default error code."""
        error = ConfigurationError("Invalid configuration")
        assert error.error_code == "CONFIG_001"

    def test_with_config_details(self):
        """Test ConfigurationError with configuration details."""
        error = ConfigurationError(
            "Invalid extraction strategy",
            details={"provided": "super_fast", "valid": ["fast", "auto", "hi_res"]}
        )
        assert error.details["provided"] == "super_fast"
        assert "fast" in error.details["valid"]


class TestValidationError:
    """Test cases for ValidationError."""

    def test_has_correct_error_code(self):
        """Test that ValidationError has correct default error code."""
        error = ValidationError("Validation failed")
        assert error.error_code == "VALIDATION_001"

    def test_with_validation_details(self):
        """Test ValidationError with validation details."""
        error = ValidationError(
            "Input validation failed",
            details={
                "field": "chunk_size",
                "value": -100,
                "constraint": "must be positive"
            }
        )
        assert error.details["field"] == "chunk_size"
        assert error.details["value"] == -100
        assert error.details["constraint"] == "must be positive"


class TestExceptionInheritance:
    """Test exception inheritance hierarchy."""

    def test_all_exceptions_inherit_from_base(self):
        """Test that all custom exceptions inherit from BasePipelineError."""
        exceptions = [
            WeaviateConnectionError("test"),
            CollectionNotFoundError("test"),
            BatchInsertError("test"),
            PDFParsingError("test"),
            EmbeddingError("test"),
            StorageError("test"),
            ConfigurationError("test"),
            ValidationError("test")
        ]

        for exc in exceptions:
            assert isinstance(exc, BasePipelineError)
            assert isinstance(exc, Exception)

    def test_exception_can_be_caught_by_base(self):
        """Test that specific exceptions can be caught by base class."""
        try:
            raise PDFParsingError("PDF parsing failed")
        except BasePipelineError as e:
            assert str(e) == "PDF parsing failed"
            assert e.error_code == "PDF_PARSE_001"

    def test_exception_can_be_caught_specifically(self):
        """Test that specific exceptions can be caught individually."""
        try:
            raise ConfigurationError("Bad config")
        except ConfigurationError as e:
            assert str(e) == "Bad config"
        except BasePipelineError:
            pytest.fail("Should have been caught by ConfigurationError")


class TestErrorCodes:
    """Test that all error codes are unique."""

    def test_unique_error_codes(self):
        """Test that each exception class has a unique error code."""
        error_codes = {
            BasePipelineError.ERROR_CODE,
            WeaviateConnectionError.ERROR_CODE,
            CollectionNotFoundError.ERROR_CODE,
            BatchInsertError.ERROR_CODE,
            PDFParsingError.ERROR_CODE,
            EmbeddingError.ERROR_CODE,
            StorageError.ERROR_CODE,
            ConfigurationError.ERROR_CODE,
            ValidationError.ERROR_CODE
        }

        # All codes should be unique
        assert len(error_codes) == 9

    def test_error_code_format(self):
        """Test that error codes follow expected format."""
        exceptions = [
            BasePipelineError,
            WeaviateConnectionError,
            CollectionNotFoundError,
            BatchInsertError,
            PDFParsingError,
            EmbeddingError,
            StorageError,
            ConfigurationError,
            ValidationError
        ]

        for exc_class in exceptions:
            # Check format: CATEGORY_NUMBER
            assert "_" in exc_class.ERROR_CODE
            parts = exc_class.ERROR_CODE.split("_")
            assert len(parts) >= 2
            assert parts[-1].isdigit() or parts[-1].isalnum()
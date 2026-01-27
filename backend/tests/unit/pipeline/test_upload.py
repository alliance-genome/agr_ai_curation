"""Unit tests for PDF upload handler."""

import pytest
from pathlib import Path
import tempfile
import hashlib
from unittest.mock import Mock, AsyncMock, patch
from datetime import datetime

from src.lib.pipeline.upload import (
    PDFUploadHandler,
    save_uploaded_pdf,
    validate_pdf,
    generate_checksum,
    store_raw_pdf,
    cleanup_temp_files,
    UploadError
)
from src.models.document import ProcessingStatus, EmbeddingStatus


@pytest.fixture
def upload_handler(tmp_path):
    return PDFUploadHandler(storage_path=tmp_path)


@pytest.fixture
def mock_pdf_file(tmp_path):
    pdf_path = tmp_path / "test.pdf"
    pdf_path.write_bytes(b'%PDF-1.5\n%Test PDF content\n%%EOF')
    return pdf_path


@pytest.fixture
def mock_upload_file(mock_pdf_file):
    mock_file = Mock()
    mock_file.filename = "test.pdf"
    mock_file.read = AsyncMock(return_value=mock_pdf_file.read_bytes())
    return mock_file


class TestPDFUploadHandler:
    """Test PDFUploadHandler class."""

    @pytest.mark.asyncio
    async def test_save_uploaded_pdf_success(self, upload_handler, mock_upload_file):
        """Test successful PDF upload and save."""
        metadata = {
            "author": "Test Author",
            "title": "Test Document",
            "document_type": "research"
        }

        saved_path, document = await upload_handler.save_uploaded_pdf(
            mock_upload_file, metadata
        )

        # Check saved file exists
        assert saved_path.exists()
        assert saved_path.name == "test.pdf"

        # Check document model
        assert document.filename == "test.pdf"
        assert document.processing_status == ProcessingStatus.PENDING
        assert document.embedding_status == EmbeddingStatus.PENDING
        assert document.metadata.author == "Test Author"
        assert document.metadata.title == "Test Document"
        assert document.metadata.document_type == "research"

    @pytest.mark.asyncio
    async def test_save_uploaded_pdf_invalid_extension(self, upload_handler):
        """Test upload rejection for non-PDF file."""
        mock_file = Mock()
        mock_file.filename = "test.txt"
        mock_file.read = AsyncMock(return_value=b"Not a PDF")

        with pytest.raises(UploadError, match="File must be a PDF"):
            await upload_handler.save_uploaded_pdf(mock_file)

    def test_validate_pdf_valid(self, mock_pdf_file):
        """Test PDF validation with valid file."""
        handler = PDFUploadHandler()
        validation = handler.validate_pdf(mock_pdf_file)

        assert validation["is_valid"] is True
        assert validation["checks"]["has_pdf_extension"] is True
        assert validation["checks"]["has_pdf_header"] is True
        assert validation["checks"]["not_empty"] is True
        assert validation["checks"]["file_size_ok"] is True

    def test_validate_pdf_invalid_header(self, tmp_path):
        """Test PDF validation with invalid header."""
        invalid_pdf = tmp_path / "invalid.pdf"
        invalid_pdf.write_bytes(b'Not a PDF header\n%%EOF')

        handler = PDFUploadHandler()
        validation = handler.validate_pdf(invalid_pdf)

        assert validation["is_valid"] is False
        assert validation["checks"]["has_pdf_header"] is False
        assert "valid PDF header" in str(validation["errors"])

    def test_validate_pdf_empty_file(self, tmp_path):
        """Test PDF validation with empty file."""
        empty_pdf = tmp_path / "empty.pdf"
        empty_pdf.write_bytes(b'')

        handler = PDFUploadHandler()
        validation = handler.validate_pdf(empty_pdf)

        assert validation["is_valid"] is False
        assert validation["checks"]["not_empty"] is False
        assert "File is empty" in str(validation["errors"])

    def test_validate_pdf_encrypted(self, tmp_path):
        """Test PDF validation with encrypted file."""
        encrypted_pdf = tmp_path / "encrypted.pdf"
        encrypted_pdf.write_bytes(b'%PDF-1.5\n/Encrypt <</test>>\n%%EOF')

        handler = PDFUploadHandler()
        validation = handler.validate_pdf(encrypted_pdf)

        assert validation["is_valid"] is False
        assert validation["checks"]["not_encrypted"] is False
        assert "encrypted" in str(validation["errors"])

    @pytest.mark.asyncio
    async def test_store_raw_pdf_success(self, upload_handler, mock_pdf_file):
        """Test storing raw PDF in permanent storage."""
        document_id = "test-doc-123"

        storage_metadata = await upload_handler.store_raw_pdf(
            mock_pdf_file, document_id
        )

        # Check metadata
        assert storage_metadata["document_id"] == document_id
        assert "permanent_path" in storage_metadata
        assert "checksum" in storage_metadata
        assert storage_metadata["storage_type"] == "filesystem"

        # Check file exists in permanent storage
        permanent_path = Path(storage_metadata["permanent_path"])
        assert permanent_path.exists()

    @pytest.mark.asyncio
    async def test_store_raw_pdf_file_not_found(self, upload_handler):
        """Test storing non-existent file."""
        non_existent = Path("/tmp/does_not_exist.pdf")

        with pytest.raises(UploadError, match="File not found"):
            await upload_handler.store_raw_pdf(non_existent, "doc-123")


class TestUploadUtilityFunctions:
    """Test standalone utility functions."""

    @pytest.mark.asyncio
    async def test_generate_checksum_sha256(self, tmp_path):
        """Test SHA256 checksum generation."""
        test_file = tmp_path / "test.txt"
        test_content = b"Test content for checksum"
        test_file.write_bytes(test_content)

        checksum = await generate_checksum(test_file)

        # Verify checksum
        expected = hashlib.sha256(test_content).hexdigest()
        assert checksum == expected

    @pytest.mark.asyncio
    async def test_generate_checksum_md5(self, tmp_path):
        """Test MD5 checksum generation."""
        test_file = tmp_path / "test.txt"
        test_content = b"Test content for checksum"
        test_file.write_bytes(test_content)

        checksum = await generate_checksum(test_file, algorithm="md5")

        # Verify checksum
        expected = hashlib.md5(test_content).hexdigest()
        assert checksum == expected

    @pytest.mark.asyncio
    async def test_generate_checksum_invalid_algorithm(self, tmp_path):
        """Test checksum with invalid algorithm."""
        test_file = tmp_path / "test.txt"
        test_file.write_bytes(b"content")

        with pytest.raises(UploadError, match="Failed to generate checksum"):
            await generate_checksum(test_file, algorithm="invalid")

    def test_cleanup_temp_files_success(self, tmp_path):
        """Test cleaning up temporary files."""
        document_id = "test-doc-456"
        temp_dir = tmp_path / document_id
        temp_dir.mkdir()
        temp_file = temp_dir / "temp.pdf"
        temp_file.write_bytes(b"temp content")

        # Cleanup
        result = cleanup_temp_files(document_id, storage_path=tmp_path)

        assert result is True
        assert not temp_dir.exists()

    def test_cleanup_temp_files_not_found(self, tmp_path):
        """Test cleanup when directory doesn't exist."""
        result = cleanup_temp_files("non-existent", storage_path=tmp_path)
        assert result is False

    @pytest.mark.asyncio
    async def test_save_uploaded_pdf_function(self, tmp_path, mock_upload_file):
        """Test convenience function save_uploaded_pdf."""
        saved_path, document = await save_uploaded_pdf(
            mock_upload_file,
            metadata={"title": "Test"},
            storage_path=tmp_path
        )

        assert saved_path.exists()
        assert document.metadata.title == "Test"

    def test_validate_pdf_function(self, mock_pdf_file):
        """Test convenience function validate_pdf."""
        validation = validate_pdf(mock_pdf_file)
        assert validation["is_valid"] is True

    @pytest.mark.asyncio
    async def test_store_raw_pdf_function(self, tmp_path, mock_pdf_file):
        """Test convenience function store_raw_pdf."""
        storage_metadata = await store_raw_pdf(
            mock_pdf_file,
            "doc-789",
            storage_path=tmp_path
        )

        assert storage_metadata["document_id"] == "doc-789"
        assert "permanent_path" in storage_metadata

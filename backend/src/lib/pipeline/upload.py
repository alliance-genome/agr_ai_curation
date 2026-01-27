"""PDF upload handler for document processing pipeline."""

import hashlib
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, Tuple
import uuid

from src.models.document import PDFDocument, ProcessingStatus, EmbeddingStatus, DocumentMetadata

logger = logging.getLogger(__name__)


class UploadError(Exception):
    """Exception raised during file upload operations."""
    pass


class PDFUploadHandler:
    """Handles PDF file uploads and initial processing."""

    def __init__(self, storage_path: Optional[Path] = None):
        """Initialize upload handler.

        Args:
            storage_path: Base path for storing uploaded PDFs (defaults to /tmp/pdf_uploads)
        """
        self.storage_path = storage_path or Path("/tmp/pdf_uploads")
        self.storage_path.mkdir(parents=True, exist_ok=True)
        logger.info(f"Upload handler initialized with storage path: {self.storage_path}")

    async def save_uploaded_pdf(
        self,
        file: Any,  # FastAPI UploadFile or file-like object
        metadata: Optional[Dict[str, Any]] = None
    ) -> Tuple[Path, PDFDocument]:
        """Save an uploaded PDF file and create document record.

        Args:
            file: Uploaded file object (FastAPI UploadFile or similar)
            metadata: Optional metadata for the document

        Returns:
            Tuple of (saved file path, PDFDocument model)

        Raises:
            UploadError: If upload or save fails
        """
        try:
            # Generate document ID
            document_id = str(uuid.uuid4())

            # Get original filename
            if hasattr(file, 'filename'):
                original_filename = file.filename
            else:
                original_filename = f"document_{document_id}.pdf"

            # Validate file is PDF
            if not original_filename.lower().endswith('.pdf'):
                raise UploadError(f"File must be a PDF, got: {original_filename}")

            # Create document-specific directory
            doc_dir = self.storage_path / document_id
            doc_dir.mkdir(parents=True, exist_ok=True)

            # Save file
            saved_path = doc_dir / original_filename

            if hasattr(file, 'read'):
                # File-like object
                content = await file.read() if hasattr(file.read, '__call__') else file.read()
                with open(saved_path, 'wb') as f:
                    f.write(content)
            else:
                # Path-like object
                shutil.copy2(file, saved_path)

            # Get file size
            file_size = saved_path.stat().st_size

            # Generate checksum
            checksum = await generate_checksum(saved_path)

            # Create document metadata
            doc_metadata = DocumentMetadata(
                page_count=1,  # Will be updated during parsing, default to 1
                author=metadata.get('author') if metadata else None,
                title=metadata.get('title', original_filename) if metadata else original_filename,
                checksum=checksum,
                document_type=metadata.get('document_type', 'general') if metadata else 'general',
                last_processed_stage='upload'
            )

            # Create PDFDocument model
            document = PDFDocument(
                id=document_id,
                filename=original_filename,
                file_size=file_size,
                creation_date=datetime.now(),
                last_accessed_date=datetime.now(),
                processing_status=ProcessingStatus.PENDING,
                embedding_status=EmbeddingStatus.PENDING,
                chunk_count=0,
                vector_count=0,
                metadata=doc_metadata
            )

            logger.info(f"Successfully saved PDF: {original_filename} with ID: {document_id}")
            return saved_path, document

        except Exception as e:
            error_msg = f"Failed to save uploaded PDF: {str(e)}"
            logger.error(error_msg)
            raise UploadError(error_msg) from e

    def validate_pdf(self, file: Any) -> Dict[str, Any]:
        """Validate a PDF file before processing.

        Args:
            file: File to validate (path, file-like object, or UploadFile)

        Returns:
            Validation results dictionary

        Raises:
            UploadError: If validation fails critically
        """
        validation = {
            "is_valid": True,
            "checks": {
                "has_pdf_extension": False,
                "has_pdf_header": False,
                "file_size_ok": False,
                "not_corrupted": False,
                "not_empty": False,
                "not_encrypted": False
            },
            "warnings": [],
            "errors": []
        }

        try:
            # Get file path or content
            if isinstance(file, (str, Path)):
                file_path = Path(file)
                if not file_path.exists():
                    validation["is_valid"] = False
                    validation["errors"].append(f"File not found: {file_path}")
                    return validation

                # Read content for validation
                with open(file_path, 'rb') as f:
                    content = f.read(1024)  # Read first 1KB for header check
                    f.seek(0)
                    full_content = f.read()

                file_size = len(full_content)
                filename = file_path.name

            elif hasattr(file, 'read'):
                # File-like object
                content = file.read(1024)
                file.seek(0)
                full_content = file.read()
                file.seek(0)  # Reset for later use

                file_size = len(full_content)
                filename = getattr(file, 'filename', 'unknown.pdf')

            else:
                validation["is_valid"] = False
                validation["errors"].append("Invalid file object type")
                return validation

            # Check 1: PDF extension
            if filename.lower().endswith('.pdf'):
                validation["checks"]["has_pdf_extension"] = True
            else:
                validation["warnings"].append(f"File does not have .pdf extension: {filename}")

            # Check 2: PDF header
            if content.startswith(b'%PDF-'):
                validation["checks"]["has_pdf_header"] = True
            else:
                validation["is_valid"] = False
                validation["errors"].append("File does not have valid PDF header")

            # Check 3: File size
            if 0 < file_size <= 100 * 1024 * 1024:  # Max 100MB
                validation["checks"]["file_size_ok"] = True
            elif file_size == 0:
                validation["is_valid"] = False
                validation["errors"].append("File is empty")
            else:
                validation["warnings"].append(f"File size ({file_size / 1024 / 1024:.2f}MB) exceeds 100MB limit")

            # Check 4: Not empty
            if file_size > 0:
                validation["checks"]["not_empty"] = True
            else:
                validation["is_valid"] = False
                validation["errors"].append("File is empty")

            # Check 5: Check for encryption (basic check)
            if b'/Encrypt' not in full_content:
                validation["checks"]["not_encrypted"] = True
            else:
                validation["is_valid"] = False
                validation["errors"].append("PDF appears to be encrypted")

            # Check 6: Not corrupted (basic check - look for EOF marker)
            if b'%%EOF' in full_content[-1024:]:
                validation["checks"]["not_corrupted"] = True
            else:
                validation["warnings"].append("PDF may be corrupted (missing EOF marker)")

            # Additional info
            validation["file_size_bytes"] = file_size
            validation["filename"] = filename

        except Exception as e:
            validation["is_valid"] = False
            validation["errors"].append(f"Validation error: {str(e)}")

        return validation

    async def store_raw_pdf(
        self,
        file_path: Path,
        document_id: str
    ) -> Dict[str, Any]:
        """Store raw PDF file in permanent storage.

        Args:
            file_path: Path to the PDF file
            document_id: Document UUID

        Returns:
            Storage metadata dictionary

        Raises:
            UploadError: If storage fails
        """
        try:
            if not file_path.exists():
                raise UploadError(f"File not found: {file_path}")

            # Create permanent storage location
            permanent_dir = self.storage_path / "permanent" / document_id
            permanent_dir.mkdir(parents=True, exist_ok=True)

            # Copy file to permanent storage
            permanent_path = permanent_dir / file_path.name
            shutil.copy2(file_path, permanent_path)

            # Generate storage metadata
            storage_metadata = {
                "document_id": document_id,
                "original_path": str(file_path),
                "permanent_path": str(permanent_path),
                "storage_timestamp": datetime.now().isoformat(),
                "file_size": permanent_path.stat().st_size,
                "checksum": await generate_checksum(permanent_path),
                "storage_type": "filesystem"
            }

            logger.info(f"Stored raw PDF for document {document_id} at {permanent_path}")
            return storage_metadata

        except Exception as e:
            error_msg = f"Failed to store raw PDF: {str(e)}"
            logger.error(error_msg)
            raise UploadError(error_msg) from e


async def save_uploaded_pdf(
    file: Any,
    metadata: Optional[Dict[str, Any]] = None,
    storage_path: Optional[Path] = None
) -> Tuple[Path, PDFDocument]:
    """Convenience function to save an uploaded PDF file.

    Args:
        file: Uploaded file object
        metadata: Optional metadata for the document
        storage_path: Optional custom storage path

    Returns:
        Tuple of (saved file path, PDFDocument model)

    Raises:
        UploadError: If upload fails
    """
    handler = PDFUploadHandler(storage_path)
    return await handler.save_uploaded_pdf(file, metadata)


def validate_pdf(file: Any) -> Dict[str, Any]:
    """Convenience function to validate a PDF file.

    Args:
        file: File to validate

    Returns:
        Validation results dictionary
    """
    handler = PDFUploadHandler()
    return handler.validate_pdf(file)


async def generate_checksum(
    file_path: Path,
    algorithm: str = "sha256"
) -> str:
    """Generate checksum for a file.

    Args:
        file_path: Path to the file
        algorithm: Hash algorithm to use (default: sha256)

    Returns:
        Hexadecimal checksum string

    Raises:
        UploadError: If checksum generation fails
    """
    try:
        if algorithm == "sha256":
            hasher = hashlib.sha256()
        elif algorithm == "md5":
            hasher = hashlib.md5()
        elif algorithm == "sha1":
            hasher = hashlib.sha1()
        else:
            raise ValueError(f"Unsupported algorithm: {algorithm}")

        with open(file_path, 'rb') as f:
            # Read in chunks to handle large files
            chunk_size = 8192
            while chunk := f.read(chunk_size):
                hasher.update(chunk)

        checksum = hasher.hexdigest()
        logger.debug(f"Generated {algorithm} checksum for {file_path.name}: {checksum}")
        return checksum

    except Exception as e:
        error_msg = f"Failed to generate checksum: {str(e)}"
        logger.error(error_msg)
        raise UploadError(error_msg) from e


async def store_raw_pdf(
    file_path: Path,
    document_id: str,
    storage_path: Optional[Path] = None
) -> Dict[str, Any]:
    """Convenience function to store raw PDF file.

    Args:
        file_path: Path to the PDF file
        document_id: Document UUID
        storage_path: Optional custom storage path

    Returns:
        Storage metadata dictionary

    Raises:
        UploadError: If storage fails
    """
    handler = PDFUploadHandler(storage_path)
    return await handler.store_raw_pdf(file_path, document_id)


def cleanup_temp_files(document_id: str, storage_path: Optional[Path] = None) -> bool:
    """Clean up temporary files for a document.

    Args:
        document_id: Document UUID
        storage_path: Optional custom storage path

    Returns:
        True if cleanup successful
    """
    try:
        base_path = storage_path or Path("/tmp/pdf_uploads")
        temp_dir = base_path / document_id

        if temp_dir.exists():
            shutil.rmtree(temp_dir)
            logger.info(f"Cleaned up temporary files for document {document_id}")
            return True

        return False

    except Exception as e:
        logger.error(f"Failed to cleanup temp files: {str(e)}")
        return False
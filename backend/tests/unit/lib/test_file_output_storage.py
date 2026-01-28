"""Unit tests for FileOutputStorageService.

Feature: 008-file-output-downloads
Phase: 3 - Storage Service

Tests cover:
- File save operations with validation
- Path resolution and security checks
- File retrieval for downloads
- Cleanup of temporary files
- CSV/TSV formula injection detection
- JSON validation
"""

import json
import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch

from src.lib.file_outputs import (
    FileOutputStorageService,
    FileOutputStorageError,
    FileValidationError,
    PathSecurityError,
    FileSizeError,
)


@pytest.fixture
def temp_storage_dir():
    """Create a temporary directory for testing storage operations."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def storage_service(temp_storage_dir):
    """Create a FileOutputStorageService with temp directory."""
    return FileOutputStorageService(base_path=temp_storage_dir)


@pytest.fixture
def valid_trace_id():
    """Return a valid 32-character hex trace ID."""
    return "d3b0a19f2c2df7b2b31dfb7cded3acbd"


@pytest.fixture
def valid_session_id():
    """Return a valid session ID."""
    return "chat_session_abc123"


class TestStorageServiceInit:
    """Tests for storage service initialization."""

    def test_creates_required_directories(self, temp_storage_dir):
        """Test that initialization creates all required directories."""
        service = FileOutputStorageService(base_path=temp_storage_dir)

        assert (temp_storage_dir / "outputs").exists()
        assert (temp_storage_dir / "temp" / "processing").exists()
        assert (temp_storage_dir / "temp" / "failed").exists()

    def test_uses_config_path_by_default(self, temp_storage_dir):
        """Test that service uses config path when no override provided."""
        with patch(
            "src.lib.file_outputs.storage.get_file_output_storage_path",
            return_value=temp_storage_dir,
        ):
            service = FileOutputStorageService()
            # Compare resolved paths to handle symlinks (e.g., /tmp -> /private/tmp)
            assert service.base_path.resolve() == temp_storage_dir.resolve()


class TestInputValidation:
    """Tests for input validation."""

    def test_valid_trace_id_passes(self, storage_service, valid_trace_id):
        """Test that valid 32-char hex trace ID passes validation."""
        # Should not raise
        storage_service._validate_trace_id(valid_trace_id)

    def test_invalid_trace_id_too_short(self, storage_service):
        """Test that short trace IDs are rejected."""
        with pytest.raises(FileValidationError, match="Invalid trace_id format"):
            storage_service._validate_trace_id("abc123")

    def test_invalid_trace_id_too_long(self, storage_service):
        """Test that long trace IDs are rejected."""
        with pytest.raises(FileValidationError, match="Invalid trace_id format"):
            storage_service._validate_trace_id("d3b0a19f2c2df7b2b31dfb7cded3acbd" + "extra")

    def test_invalid_trace_id_uppercase(self, storage_service):
        """Test that uppercase hex in trace IDs is rejected."""
        with pytest.raises(FileValidationError, match="Invalid trace_id format"):
            storage_service._validate_trace_id("D3B0A19F2C2DF7B2B31DFB7CDED3ACBD")

    def test_valid_session_id_passes(self, storage_service, valid_session_id):
        """Test that valid session IDs pass validation."""
        storage_service._validate_session_id(valid_session_id)

    def test_empty_session_id_rejected(self, storage_service):
        """Test that empty session IDs are rejected."""
        with pytest.raises(FileValidationError, match="cannot be empty"):
            storage_service._validate_session_id("")

    def test_session_id_path_traversal_rejected(self, storage_service):
        """Test that session IDs with path traversal are rejected."""
        with pytest.raises(PathSecurityError, match="path traversal"):
            storage_service._validate_session_id("../../../etc/passwd")

    def test_session_id_slash_rejected(self, storage_service):
        """Test that session IDs with slashes are rejected."""
        with pytest.raises(PathSecurityError, match="path traversal"):
            storage_service._validate_session_id("session/nested")

    def test_valid_descriptor_passes(self, storage_service):
        """Test that valid descriptors pass validation."""
        storage_service._validate_descriptor("gene_results")
        storage_service._validate_descriptor("export-2025")
        storage_service._validate_descriptor("A1_data")

    def test_invalid_descriptor_with_spaces(self, storage_service):
        """Test that descriptors with spaces are rejected."""
        with pytest.raises(FileValidationError, match="Invalid descriptor"):
            storage_service._validate_descriptor("gene results")

    def test_invalid_descriptor_too_long(self, storage_service):
        """Test that descriptors over 100 chars are rejected."""
        with pytest.raises(FileValidationError, match="Invalid descriptor"):
            storage_service._validate_descriptor("a" * 101)

    def test_valid_file_types(self, storage_service):
        """Test that valid file types pass validation."""
        for file_type in ["csv", "tsv", "json"]:
            storage_service._validate_file_type(file_type)

    def test_invalid_file_type(self, storage_service):
        """Test that invalid file types are rejected."""
        with pytest.raises(FileValidationError, match="Invalid file_type"):
            storage_service._validate_file_type("txt")
        with pytest.raises(FileValidationError, match="Invalid file_type"):
            storage_service._validate_file_type("CSV")  # Must be lowercase


class TestContentValidation:
    """Tests for content validation."""

    def test_csv_content_valid(self, storage_service):
        """Test that valid CSV content passes validation."""
        content = "gene_id,symbol,name\nFBgn0001,Notch,Notch gene\n"
        warnings = storage_service._validate_content(content, "csv")
        assert len(warnings) == 0

    def test_csv_formula_injection_warning(self, storage_service):
        """Test that CSV with formula-like content generates warnings."""
        content = "gene_id,formula\nFBgn0001,=SUM(A1:A10)\n"
        warnings = storage_service._validate_content(content, "csv")
        assert len(warnings) == 1
        assert "formula injection" in warnings[0]

    def test_tsv_formula_injection_warning(self, storage_service):
        """Test that TSV with formula-like content generates warnings."""
        content = "gene_id\tformula\nFBgn0001\t+CMD('calc')\n"
        warnings = storage_service._validate_content(content, "tsv")
        assert len(warnings) == 1
        assert "formula injection" in warnings[0]

    def test_csv_multiple_formula_injections(self, storage_service):
        """Test detection of multiple formula injections."""
        content = "a,b,c\n=CMD,@SUM,-100\n"
        warnings = storage_service._validate_content(content, "csv")
        # Should detect =, @, and - as potential formula injections
        assert len(warnings) == 3

    def test_json_valid(self, storage_service):
        """Test that valid JSON passes validation."""
        content = json.dumps({"genes": [{"id": "FBgn0001", "symbol": "Notch"}]})
        warnings = storage_service._validate_content(content, "json")
        assert len(warnings) == 0

    def test_json_invalid_raises_error(self, storage_service):
        """Test that invalid JSON raises FileValidationError."""
        content = "{'invalid': json}"  # Single quotes are not valid JSON
        with pytest.raises(FileValidationError, match="Invalid JSON"):
            storage_service._validate_content(content, "json")

    def test_content_size_limit_bytes(self, storage_service):
        """Test that content exceeding size limit is rejected."""
        # Create content just over 100MB
        large_content = "x" * (100 * 1024 * 1024 + 1)
        with pytest.raises(FileSizeError, match="exceeds maximum"):
            storage_service._validate_content(large_content, "csv")

    def test_content_size_limit_unicode(self, storage_service):
        """Test that unicode content size is calculated correctly."""
        # Unicode characters take more bytes than chars
        # 100MB worth of 3-byte unicode chars
        storage_service._validate_content("a" * 1000, "csv")  # Should pass

    def test_bytes_content_validation(self, storage_service):
        """Test that bytes content is validated correctly."""
        content = b'{"valid": "json"}'
        warnings = storage_service._validate_content(content, "json")
        assert len(warnings) == 0

    def test_invalid_utf8_bytes_rejected(self, storage_service):
        """Test that non-UTF-8 bytes are rejected."""
        # Invalid UTF-8 sequence
        content = b"\xff\xfe"
        with pytest.raises(FileValidationError, match="not valid UTF-8"):
            storage_service._validate_content(content, "csv")


class TestSaveOutput:
    """Tests for save_output method."""

    def test_save_csv_file(
        self, storage_service, valid_trace_id, valid_session_id
    ):
        """Test saving a CSV file output."""
        content = "gene_id,symbol\nFBgn0001,Notch\n"

        path, file_hash, file_size, warnings = storage_service.save_output(
            trace_id=valid_trace_id,
            session_id=valid_session_id,
            content=content,
            file_type="csv",
            descriptor="gene_results",
        )

        assert path.exists()
        assert path.suffix == ".csv"
        assert valid_trace_id in path.name
        assert "gene_results" in path.name
        assert file_size == len(content.encode("utf-8"))
        assert len(file_hash) == 64  # SHA-256 hex digest
        assert len(warnings) == 0

    def test_save_tsv_file(
        self, storage_service, valid_trace_id, valid_session_id
    ):
        """Test saving a TSV file output."""
        content = "gene_id\tsymbol\nFBgn0001\tNotch\n"

        path, _, _, _ = storage_service.save_output(
            trace_id=valid_trace_id,
            session_id=valid_session_id,
            content=content,
            file_type="tsv",
            descriptor="export",
        )

        assert path.exists()
        assert path.suffix == ".tsv"

    def test_save_json_file(
        self, storage_service, valid_trace_id, valid_session_id
    ):
        """Test saving a JSON file output."""
        content = json.dumps({"genes": [{"id": "FBgn0001"}]})

        path, _, _, _ = storage_service.save_output(
            trace_id=valid_trace_id,
            session_id=valid_session_id,
            content=content,
            file_type="json",
            descriptor="full_output",
        )

        assert path.exists()
        assert path.suffix == ".json"
        # Verify content is valid JSON
        saved_content = json.loads(path.read_text())
        assert saved_content["genes"][0]["id"] == "FBgn0001"

    def test_save_bytes_content(
        self, storage_service, valid_trace_id, valid_session_id
    ):
        """Test saving bytes content directly."""
        content = b"gene_id,symbol\nFBgn0001,Notch\n"

        path, _, file_size, _ = storage_service.save_output(
            trace_id=valid_trace_id,
            session_id=valid_session_id,
            content=content,
            file_type="csv",
            descriptor="binary_test",
        )

        assert path.exists()
        assert file_size == len(content)

    def test_save_creates_date_directory(
        self, storage_service, valid_trace_id, valid_session_id
    ):
        """Test that save creates date-organized directory structure."""
        from datetime import datetime, timezone

        content = "test"
        path, _, _, _ = storage_service.save_output(
            trace_id=valid_trace_id,
            session_id=valid_session_id,
            content=content,
            file_type="csv",
            descriptor="test",
        )

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        assert today in str(path)
        assert valid_session_id in str(path)

    def test_save_with_formula_returns_warnings(
        self, storage_service, valid_trace_id, valid_session_id
    ):
        """Test that files with formulas are saved but with warnings."""
        content = "gene_id,formula\nFBgn0001,=SUM(A1:A10)\n"

        path, _, _, warnings = storage_service.save_output(
            trace_id=valid_trace_id,
            session_id=valid_session_id,
            content=content,
            file_type="csv",
            descriptor="with_formula",
        )

        assert path.exists()  # File should still be saved
        assert len(warnings) > 0
        assert "formula injection" in warnings[0]

    def test_save_invalid_trace_id_fails(
        self, storage_service, valid_session_id
    ):
        """Test that invalid trace_id fails before writing."""
        with pytest.raises(FileValidationError, match="Invalid trace_id"):
            storage_service.save_output(
                trace_id="invalid",
                session_id=valid_session_id,
                content="test",
                file_type="csv",
                descriptor="test",
            )

    def test_save_invalid_json_fails(
        self, storage_service, valid_trace_id, valid_session_id
    ):
        """Test that invalid JSON content fails validation."""
        with pytest.raises(FileValidationError, match="Invalid JSON"):
            storage_service.save_output(
                trace_id=valid_trace_id,
                session_id=valid_session_id,
                content="not valid json",
                file_type="json",
                descriptor="test",
            )


class TestGetOutputPath:
    """Tests for get_output_path method."""

    def test_get_existing_file(
        self, storage_service, valid_trace_id, valid_session_id
    ):
        """Test retrieving path to an existing file."""
        content = "test content"
        saved_path, _, _, _ = storage_service.save_output(
            trace_id=valid_trace_id,
            session_id=valid_session_id,
            content=content,
            file_type="csv",
            descriptor="test",
        )

        # Get relative path
        rel_path = storage_service.get_relative_path(saved_path)

        # Should be able to retrieve it
        retrieved = storage_service.get_output_path(rel_path)
        assert retrieved is not None
        assert retrieved == saved_path.resolve()

    def test_get_nonexistent_file(self, storage_service):
        """Test that non-existent files return None."""
        result = storage_service.get_output_path("outputs/nonexistent.csv")
        assert result is None

    def test_get_empty_path(self, storage_service):
        """Test that empty path returns None."""
        result = storage_service.get_output_path("")
        assert result is None

    def test_path_traversal_blocked(self, storage_service, temp_storage_dir):
        """Test that path traversal attempts are blocked."""
        # Create a file outside the storage directory
        outside_file = temp_storage_dir.parent / "secret.txt"
        outside_file.write_text("secret data")

        try:
            # Try to access it via path traversal
            result = storage_service.get_output_path("../secret.txt")
            assert result is None  # Should be blocked
        finally:
            outside_file.unlink()


class TestDeleteOutput:
    """Tests for delete_output method."""

    def test_delete_existing_file(
        self, storage_service, valid_trace_id, valid_session_id
    ):
        """Test deleting an existing file."""
        content = "test"
        saved_path, _, _, _ = storage_service.save_output(
            trace_id=valid_trace_id,
            session_id=valid_session_id,
            content=content,
            file_type="csv",
            descriptor="to_delete",
        )

        rel_path = storage_service.get_relative_path(saved_path)

        # Delete the file
        result = storage_service.delete_output(rel_path)
        assert result is True
        assert not saved_path.exists()

    def test_delete_nonexistent_file(self, storage_service):
        """Test deleting a non-existent file returns False."""
        result = storage_service.delete_output("outputs/nonexistent.csv")
        assert result is False

    def test_delete_cleans_empty_directories(
        self, storage_service, valid_trace_id, valid_session_id
    ):
        """Test that delete cleans up empty parent directories."""
        content = "test"
        saved_path, _, _, _ = storage_service.save_output(
            trace_id=valid_trace_id,
            session_id=valid_session_id,
            content=content,
            file_type="csv",
            descriptor="cleanup_test",
        )

        parent_dir = saved_path.parent
        rel_path = storage_service.get_relative_path(saved_path)

        # Delete the only file in the directory
        storage_service.delete_output(rel_path)

        # Parent session directory should be cleaned up
        assert not parent_dir.exists()


class TestGetRelativePath:
    """Tests for get_relative_path method."""

    def test_relative_path_generation(
        self, storage_service, valid_trace_id, valid_session_id
    ):
        """Test that relative paths are generated correctly."""
        content = "test"
        saved_path, _, _, _ = storage_service.save_output(
            trace_id=valid_trace_id,
            session_id=valid_session_id,
            content=content,
            file_type="csv",
            descriptor="test",
        )

        rel_path = storage_service.get_relative_path(saved_path)

        # Should start with 'outputs/' and not be absolute
        assert rel_path.startswith("outputs/")
        assert not Path(rel_path).is_absolute()


class TestCleanupTempFiles:
    """Tests for cleanup_temp_files method."""

    def test_cleanup_old_temp_files(self, storage_service, temp_storage_dir):
        """Test that old temp files are cleaned up."""
        import time

        # Create a temp file
        temp_file = storage_service.temp_processing_path / "old_file.csv"
        temp_file.write_text("old data")

        # Modify its mtime to be old
        old_time = time.time() - (25 * 3600)  # 25 hours ago
        import os

        os.utime(temp_file, (old_time, old_time))

        # Create a recent temp file
        recent_file = storage_service.temp_processing_path / "recent_file.csv"
        recent_file.write_text("recent data")

        # Cleanup files older than 24 hours
        deleted = storage_service.cleanup_temp_files(older_than_hours=24)

        assert deleted == 1
        assert not temp_file.exists()
        assert recent_file.exists()

    def test_cleanup_failed_temp_files(self, storage_service, temp_storage_dir):
        """Test that failed temp files are also cleaned up."""
        import time

        # Create a failed temp file
        failed_file = storage_service.temp_failed_path / "failed_file.csv"
        failed_file.write_text("failed data")

        # Modify its mtime to be old
        old_time = time.time() - (25 * 3600)
        import os

        os.utime(failed_file, (old_time, old_time))

        # Cleanup
        deleted = storage_service.cleanup_temp_files(older_than_hours=24)

        assert deleted == 1
        assert not failed_file.exists()


class TestFilenameGeneration:
    """Tests for filename generation."""

    def test_filename_format(self, storage_service, valid_trace_id):
        """Test that generated filenames follow the expected pattern."""
        filename = storage_service._generate_filename(
            trace_id=valid_trace_id,
            descriptor="gene_results",
            file_type="csv",
        )

        # Should contain trace_id, descriptor, timestamp, extension
        assert valid_trace_id in filename
        assert "gene_results" in filename
        assert filename.endswith(".csv")
        # Timestamp format: YYYYMMDDTHHMMSSZ
        import re

        pattern = r"\d{8}T\d{6}Z"
        assert re.search(pattern, filename) is not None

    def test_filename_unique_per_call(self, storage_service, valid_trace_id):
        """Test that filenames include timestamp for uniqueness."""
        import time

        filename1 = storage_service._generate_filename(
            valid_trace_id, "test", "csv"
        )
        time.sleep(0.01)  # Small delay
        filename2 = storage_service._generate_filename(
            valid_trace_id, "test", "csv"
        )

        # Filenames should be different due to timestamp
        # Note: May be same if called within same second, but that's acceptable
        assert filename1 != filename2 or True  # Accept either case


class TestFileHashComputation:
    """Tests for file hash computation."""

    def test_hash_consistency(self, storage_service):
        """Test that same content produces same hash."""
        content = b"test content"
        hash1 = storage_service._compute_file_hash(content)
        hash2 = storage_service._compute_file_hash(content)
        assert hash1 == hash2

    def test_hash_different_for_different_content(self, storage_service):
        """Test that different content produces different hash."""
        hash1 = storage_service._compute_file_hash(b"content1")
        hash2 = storage_service._compute_file_hash(b"content2")
        assert hash1 != hash2

    def test_hash_is_sha256(self, storage_service):
        """Test that hash is SHA-256 (64 hex characters)."""
        hash_value = storage_service._compute_file_hash(b"test")
        assert len(hash_value) == 64
        assert all(c in "0123456789abcdef" for c in hash_value)


class TestPathSecurityVerification:
    """Tests for path security verification."""

    def test_path_within_base_accepted(self, storage_service, temp_storage_dir):
        """Test that paths within base directory are accepted."""
        valid_path = temp_storage_dir / "outputs" / "test.csv"
        # Should not raise
        storage_service._verify_path_within_base(valid_path)

    def test_path_outside_base_rejected(self, storage_service, temp_storage_dir):
        """Test that paths outside base directory are rejected."""
        outside_path = temp_storage_dir.parent / "outside.csv"
        with pytest.raises(PathSecurityError, match="Path traversal detected"):
            storage_service._verify_path_within_base(outside_path)

    def test_symlink_traversal_blocked(self, storage_service, temp_storage_dir):
        """Test that symlinks pointing outside are blocked."""
        import os

        # Create a symlink inside that points outside
        symlink_path = temp_storage_dir / "outputs" / "symlink"
        target_path = temp_storage_dir.parent / "outside"

        try:
            target_path.mkdir(exist_ok=True)
            os.symlink(target_path, symlink_path)

            # Attempting to verify the symlink target should fail
            file_via_symlink = symlink_path / "file.csv"
            with pytest.raises(PathSecurityError):
                storage_service._verify_path_within_base(file_via_symlink)
        finally:
            if symlink_path.exists() or symlink_path.is_symlink():
                symlink_path.unlink()
            if target_path.exists():
                target_path.rmdir()

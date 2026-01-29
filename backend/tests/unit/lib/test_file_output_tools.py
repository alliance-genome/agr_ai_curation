"""Unit tests for file_output_tools module.

Tests the tool implementation functions and their context variable integration.
"""
import json
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

from src.lib.context import (
    set_current_trace_id,
    set_current_session_id,
    set_current_user_id,
    clear_context,
)


class TestGetContextFromContextvars:
    """Tests for _get_context_from_contextvars helper."""

    def setup_method(self):
        clear_context()

    def teardown_method(self):
        clear_context()

    def test_returns_none_when_no_context_set(self):
        """All values should be None when no context is set."""
        from src.lib.openai_agents.tools.file_output_tools import (
            _get_context_from_contextvars,
        )

        trace_id, session_id, curator_id = _get_context_from_contextvars()

        assert trace_id is None
        assert session_id is None
        assert curator_id is None

    def test_returns_values_when_context_set(self):
        """Returns the context values that have been set."""
        from src.lib.openai_agents.tools.file_output_tools import (
            _get_context_from_contextvars,
        )

        set_current_trace_id("trace-abc123")
        set_current_session_id("session-xyz789")
        set_current_user_id("curator@example.com")

        trace_id, session_id, curator_id = _get_context_from_contextvars()

        assert trace_id == "trace-abc123"
        assert session_id == "session-xyz789"
        assert curator_id == "curator@example.com"


class TestBuildDownloadUrl:
    """Tests for _build_download_url helper."""

    def test_builds_correct_url(self):
        from src.lib.openai_agents.tools.file_output_tools import _build_download_url

        file_id = "abc123-def456"
        url = _build_download_url(file_id)

        assert url == "/api/files/abc123-def456/download"


class TestSaveCsvImpl:
    """Tests for _save_csv_impl function."""

    def setup_method(self):
        clear_context()

    def teardown_method(self):
        clear_context()

    @pytest.mark.asyncio
    async def test_raises_error_on_empty_data(self):
        """Should raise ValueError for empty data."""
        from src.lib.openai_agents.tools.file_output_tools import _save_csv_impl

        with pytest.raises(ValueError, match="empty"):
            await _save_csv_impl(data_json="[]", filename="test")

    @pytest.mark.asyncio
    async def test_raises_error_on_invalid_json(self):
        """Should raise ValueError for invalid JSON."""
        from src.lib.openai_agents.tools.file_output_tools import _save_csv_impl

        with pytest.raises(ValueError, match="Invalid JSON"):
            await _save_csv_impl(data_json="not valid json", filename="test")

    @pytest.mark.asyncio
    async def test_raises_error_on_non_array(self):
        """Should raise ValueError if data is not an array."""
        from src.lib.openai_agents.tools.file_output_tools import _save_csv_impl

        with pytest.raises(ValueError, match="must be a JSON array"):
            await _save_csv_impl(data_json='{"key": "value"}', filename="test")

    @pytest.mark.asyncio
    async def test_saves_file_with_storage_service(self):
        """Should use storage service to save file."""
        from src.lib.openai_agents.tools.file_output_tools import _save_csv_impl

        # Set context
        set_current_trace_id("d3b0a19f2c2df7b2b31dfb7cded3acbd")
        set_current_session_id("session-123")
        set_current_user_id("user@test.com")

        # Mock storage service
        mock_storage = MagicMock()
        mock_storage.save_output.return_value = (
            Path("/tmp/test.csv"),
            "abc123hash",
            42,
            [],
        )

        # Mock database session and FileOutput model
        mock_db = MagicMock()
        mock_file_output = MagicMock()
        mock_file_output.id = "test-file-id-001"

        with patch(
            "src.lib.openai_agents.tools.file_output_tools.FileOutputStorageService",
            return_value=mock_storage,
        ), patch(
            "src.lib.openai_agents.tools.file_output_tools.SessionLocal",
            return_value=mock_db,
        ), patch(
            "src.lib.openai_agents.tools.file_output_tools.FileOutput",
            return_value=mock_file_output,
        ):
            result = await _save_csv_impl(
                data_json=json.dumps([{"gene": "FBgn0001", "symbol": "Notch"}]),
                filename="gene_results",
            )

        # Verify storage service was called
        mock_storage.save_output.assert_called_once()
        call_args = mock_storage.save_output.call_args

        assert call_args.kwargs["file_type"] == "csv"
        assert call_args.kwargs["descriptor"] == "gene_results"

    @pytest.mark.asyncio
    async def test_returns_file_info_dict(self):
        """Should return a FileInfo-compatible dictionary."""
        from src.lib.openai_agents.tools.file_output_tools import _save_csv_impl

        set_current_trace_id("d3b0a19f2c2df7b2b31dfb7cded3acbd")
        set_current_session_id("session-123")
        set_current_user_id("user@test.com")

        mock_storage = MagicMock()
        mock_storage.save_output.return_value = (
            Path("/tmp/gene_results_20250107.csv"),
            "abc123hash456",
            100,
            [],
        )

        # Mock database session and FileOutput model
        mock_db = MagicMock()
        mock_file_output = MagicMock()
        mock_file_output.id = "test-file-id-002"

        with patch(
            "src.lib.openai_agents.tools.file_output_tools.FileOutputStorageService",
            return_value=mock_storage,
        ), patch(
            "src.lib.openai_agents.tools.file_output_tools.SessionLocal",
            return_value=mock_db,
        ), patch(
            "src.lib.openai_agents.tools.file_output_tools.FileOutput",
            return_value=mock_file_output,
        ):
            result = await _save_csv_impl(
                data_json=json.dumps([{"gene": "FBgn0001"}]),
                filename="gene_results",
            )

        # Verify result structure
        assert "file_id" in result
        assert "filename" in result
        assert "format" in result
        assert result["format"] == "csv"
        assert "size_bytes" in result
        assert "download_url" in result
        assert "/api/files/" in result["download_url"]


class TestSaveTsvImpl:
    """Tests for _save_tsv_impl function."""

    def setup_method(self):
        clear_context()

    def teardown_method(self):
        clear_context()

    @pytest.mark.asyncio
    async def test_saves_tsv_file(self):
        """Should save TSV format file."""
        from src.lib.openai_agents.tools.file_output_tools import _save_tsv_impl

        set_current_trace_id("d3b0a19f2c2df7b2b31dfb7cded3acbd")
        set_current_session_id("session-123")

        mock_storage = MagicMock()
        mock_storage.save_output.return_value = (
            Path("/tmp/alleles.tsv"),
            "hash123",
            50,
            [],
        )

        # Mock database session and FileOutput model
        mock_db = MagicMock()
        mock_file_output = MagicMock()
        mock_file_output.id = "test-file-id-tsv"

        with patch(
            "src.lib.openai_agents.tools.file_output_tools.FileOutputStorageService",
            return_value=mock_storage,
        ), patch(
            "src.lib.openai_agents.tools.file_output_tools.SessionLocal",
            return_value=mock_db,
        ), patch(
            "src.lib.openai_agents.tools.file_output_tools.FileOutput",
            return_value=mock_file_output,
        ):
            result = await _save_tsv_impl(
                data_json=json.dumps([{"allele": "FBal0001"}]),
                filename="alleles",
            )

        # Verify TSV type
        call_args = mock_storage.save_output.call_args
        assert call_args.kwargs["file_type"] == "tsv"
        assert result["format"] == "tsv"


class TestSaveJsonImpl:
    """Tests for _save_json_impl function."""

    def setup_method(self):
        clear_context()

    def teardown_method(self):
        clear_context()

    @pytest.mark.asyncio
    async def test_saves_json_file(self):
        """Should save JSON format file."""
        from src.lib.openai_agents.tools.file_output_tools import _save_json_impl

        set_current_trace_id("d3b0a19f2c2df7b2b31dfb7cded3acbd")
        set_current_session_id("session-123")

        mock_storage = MagicMock()
        mock_storage.save_output.return_value = (
            Path("/tmp/results.json"),
            "hash456",
            200,
            [],
        )

        # Mock database session and FileOutput model
        mock_db = MagicMock()
        mock_file_output = MagicMock()
        mock_file_output.id = "test-file-id-json"

        with patch(
            "src.lib.openai_agents.tools.file_output_tools.FileOutputStorageService",
            return_value=mock_storage,
        ), patch(
            "src.lib.openai_agents.tools.file_output_tools.SessionLocal",
            return_value=mock_db,
        ), patch(
            "src.lib.openai_agents.tools.file_output_tools.FileOutput",
            return_value=mock_file_output,
        ):
            result = await _save_json_impl(
                data_json=json.dumps({"genes": ["FBgn0001", "FBgn0002"]}),
                filename="results",
            )

        call_args = mock_storage.save_output.call_args
        assert call_args.kwargs["file_type"] == "json"
        assert result["format"] == "json"

    @pytest.mark.asyncio
    async def test_pretty_formatting_option(self):
        """Should respect pretty formatting option."""
        from src.lib.openai_agents.tools.file_output_tools import _save_json_impl

        set_current_trace_id("d3b0a19f2c2df7b2b31dfb7cded3acbd")
        set_current_session_id("session-123")

        captured_content = {}

        def capture_save_output(**kwargs):
            captured_content["content"] = kwargs["content"]
            return (Path("/tmp/test.json"), "hash", 10, [])

        mock_storage = MagicMock()
        mock_storage.save_output.side_effect = capture_save_output

        # Mock database session and FileOutput model
        mock_db = MagicMock()
        mock_file_output = MagicMock()
        mock_file_output.id = "test-file-id-123"

        # Test with pretty=True (default)
        with patch(
            "src.lib.openai_agents.tools.file_output_tools.FileOutputStorageService",
            return_value=mock_storage,
        ), patch(
            "src.lib.openai_agents.tools.file_output_tools.SessionLocal",
            return_value=mock_db,
        ), patch(
            "src.lib.openai_agents.tools.file_output_tools.FileOutput",
            return_value=mock_file_output,
        ):
            await _save_json_impl(
                data_json=json.dumps({"a": 1}), filename="test", pretty=True
            )

        # Pretty output should have newlines
        assert "\n" in captured_content["content"]

        # Test with pretty=False
        with patch(
            "src.lib.openai_agents.tools.file_output_tools.FileOutputStorageService",
            return_value=mock_storage,
        ), patch(
            "src.lib.openai_agents.tools.file_output_tools.SessionLocal",
            return_value=mock_db,
        ), patch(
            "src.lib.openai_agents.tools.file_output_tools.FileOutput",
            return_value=mock_file_output,
        ):
            await _save_json_impl(
                data_json=json.dumps({"a": 1}), filename="test", pretty=False
            )

        # Compact output should not have newlines
        assert "\n" not in captured_content["content"]


class TestContextCaptureAtInvocationTime:
    """Tests to verify context is captured at invocation time, not creation time."""

    def setup_method(self):
        clear_context()

    def teardown_method(self):
        clear_context()

    @pytest.mark.asyncio
    async def test_context_captured_at_invocation(self):
        """Context should be captured when tool is invoked, not when created."""
        from src.lib.openai_agents.tools.file_output_tools import _save_csv_impl

        # Context is not set yet at this point

        # Now set context (like runner.py does after trace is created)
        set_current_trace_id("late-trace-id-12345678901234567890")
        set_current_session_id("late-session-id")
        set_current_user_id("late-user@test.com")

        mock_storage = MagicMock()
        mock_storage.save_output.return_value = (
            Path("/tmp/test.csv"),
            "hash",
            10,
            [],
        )

        # Mock database session and FileOutput model
        mock_db = MagicMock()
        mock_file_output = MagicMock()
        mock_file_output.id = "test-file-id-456"

        with patch(
            "src.lib.openai_agents.tools.file_output_tools.FileOutputStorageService",
            return_value=mock_storage,
        ), patch(
            "src.lib.openai_agents.tools.file_output_tools.SessionLocal",
            return_value=mock_db,
        ), patch(
            "src.lib.openai_agents.tools.file_output_tools.FileOutput",
            return_value=mock_file_output,
        ):
            result = await _save_csv_impl(
                data_json=json.dumps([{"test": "data"}]),
                filename="test",
            )

        # The result should have the context that was set before invocation
        assert result["trace_id"] == "late-trace-id-12345678901234567890"
        assert result["session_id"] == "late-session-id"
        assert result["curator_id"] == "late-user@test.com"


class TestToolFactories:
    """Tests for tool factory functions that create FunctionTool objects."""

    def test_create_csv_tool_returns_function_tool(self):
        """create_csv_tool should return a FunctionTool object."""
        from src.lib.openai_agents.tools.file_output_tools import create_csv_tool
        from agents import FunctionTool

        tool = create_csv_tool()
        assert isinstance(tool, FunctionTool)
        assert tool.name == "save_csv_file"

    def test_create_tsv_tool_returns_function_tool(self):
        """create_tsv_tool should return a FunctionTool object."""
        from src.lib.openai_agents.tools.file_output_tools import create_tsv_tool
        from agents import FunctionTool

        tool = create_tsv_tool()
        assert isinstance(tool, FunctionTool)
        assert tool.name == "save_tsv_file"

    def test_create_json_tool_returns_function_tool(self):
        """create_json_tool should return a FunctionTool object."""
        from src.lib.openai_agents.tools.file_output_tools import create_json_tool
        from agents import FunctionTool

        tool = create_json_tool()
        assert isinstance(tool, FunctionTool)
        assert tool.name == "save_json_file"

"""Tools for CSV/TSV/JSON file output formatters.

These tools are used by file formatter agents to save structured data
as downloadable files. Context (trace_id, session_id, curator_id) is
captured at INVOCATION time from context variables.

Why Invocation Time?
    The trace_id isn't available until after the Langfuse trace is created,
    which happens AFTER agents (and their tools) are created. Therefore,
    context must be captured when the tool is invoked, not when it's created.

Why JSON String Parameters?
    The OpenAI Agents SDK's function_tool decorator requires strict JSON schema.
    Dynamic types like List[dict] don't satisfy strict mode requirements.
    Instead, we accept data as JSON strings and parse them internally.
    This allows arbitrary tabular data with varying columns.

Usage:
    # Tools are created (context not captured yet)
    csv_tool = create_csv_tool()
    tsv_tool = create_tsv_tool()
    json_tool = create_json_tool()

    # Agent uses these tools to save files
    # Data is passed as JSON strings
    file_info = await csv_tool(
        data_json='[{"gene_id": "FBgn0001", "symbol": "Notch"}]',
        filename="genes"
    )

Integration with File API:
    After saving, tools return FileInfo which includes a file_id.
    The file is also registered with the database via the storage service,
    enabling download via /api/files/{file_id}/download endpoint.
"""

import csv
import io
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, List, Optional

from agents import function_tool

from src.lib.file_outputs.storage import FileOutputStorageService
from src.models.sql.database import SessionLocal
from src.models.sql.file_output import FileOutput
from ..models import FileInfo

logger = logging.getLogger(__name__)


def _get_context_from_contextvars() -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Get trace_id, session_id, curator_id from context variables.

    These are set by the API layer (chat.py, executor.py) at the start
    of each request and by the runner when the Langfuse trace is created.

    Returns:
        Tuple of (trace_id, session_id, curator_id) - any may be None
    """
    from src.lib.context import (
        get_current_trace_id,
        get_current_session_id,
        get_current_user_id,
    )

    return (
        get_current_trace_id(),
        get_current_session_id(),
        get_current_user_id(),
    )


def _build_download_url(file_id: str) -> str:
    """Build the download URL for a file."""
    return f"/api/files/{file_id}/download"


async def _save_csv_impl(
    data_json: str,
    filename: str,
    columns: Optional[str] = None,
) -> dict:
    """Internal implementation for CSV save (testable without SDK wrapper).

    Args:
        data_json: JSON string containing a list of objects to convert to CSV rows.
        filename: Desired filename (without extension).
        columns: Optional JSON array string of column names.

    Returns:
        Dictionary with file information including download URL
    """
    # Get context at invocation time (trace_id is set after agent creation)
    trace_id, session_id, curator_id = _get_context_from_contextvars()

    # Parse JSON input
    try:
        data = json.loads(data_json)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in data_json: {e}")

    if not isinstance(data, list):
        raise ValueError("data_json must be a JSON array")

    if not data:
        raise ValueError("No data to export - data list is empty")

    # Validate first row is a dict
    if not isinstance(data[0], dict):
        raise ValueError(
            f"data_json array items must be objects, got {type(data[0]).__name__}"
        )

    # Parse columns if provided as JSON string
    column_list: List[str]
    if columns is not None:
        try:
            column_list = json.loads(columns)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in columns: {e}")
    else:
        column_list = list(data[0].keys())

    # Generate CSV content
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=column_list, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(data)
    content = output.getvalue()

    # Generate a fallback trace_id if not available (32-char hex)
    fallback_id = str(uuid.uuid4()).replace("-", "")
    effective_trace_id = trace_id or fallback_id[:32]
    effective_session_id = session_id or fallback_id[:8]
    effective_curator_id = curator_id or "unknown"

    # Use the storage service to save with validation
    storage = FileOutputStorageService()
    file_path, file_hash, file_size, warnings = storage.save_output(
        trace_id=effective_trace_id,
        session_id=effective_session_id,
        content=content,
        file_type="csv",
        descriptor=filename,
    )

    # Log any warnings from validation
    for warning in warnings:
        logger.warning(f"[CSV Tool] Validation warning: {warning}")

    # Extract just the filename from the path
    full_filename = file_path.name

    # Register file in database for download API
    db = SessionLocal()
    try:
        file_output = FileOutput(
            filename=full_filename,
            file_path=str(file_path),
            file_type="csv",
            file_size=file_size,
            file_hash=file_hash,
            curator_id=effective_curator_id,
            session_id=effective_session_id,
            trace_id=effective_trace_id,
            agent_name="csv_formatter",
        )
        db.add(file_output)
        db.commit()
        db.refresh(file_output)
        file_id = str(file_output.id)
        logger.info(
            f"[CSV Tool] Registered file in database: {file_id}, "
            f"filename={full_filename}, size={file_size} bytes"
        )
    except Exception as e:
        db.rollback()
        logger.error(f"[CSV Tool] Failed to register file in database: {e}")
        raise
    finally:
        db.close()

    file_info = FileInfo(
        file_id=file_id,
        filename=full_filename,
        format="csv",
        size_bytes=file_size,
        hash_sha256=file_hash,
        mime_type="text/csv",
        download_url=_build_download_url(file_id),
        created_at=datetime.now(timezone.utc),
        trace_id=trace_id,
        session_id=session_id,
        curator_id=curator_id,
    )

    # Return as dict for JSON serialization in tool response
    return file_info.model_dump(mode="json")


def create_csv_tool():
    """Create a CSV save tool for use with OpenAI Agents SDK.

    Returns:
        A FunctionTool for saving CSV files
    """

    @function_tool
    async def save_csv_file(
        data_json: str,
        filename: str,
        columns: Optional[str] = None,
    ) -> dict:
        """
        Save data as CSV file and return download information.

        Args:
            data_json: JSON string containing a list of objects to convert to CSV rows.
                       Each object represents a row with keys as column names.
                       Example: '[{"gene_id": "FBgn0001", "symbol": "Notch"}]'
            filename: Desired filename (without extension or timestamp).
                      Example: "gene_results" will produce "gene_results_20250107T123456Z.csv"
            columns: Optional JSON array string of column names to include (in order).
                     Example: '["gene_id", "symbol", "name"]'
                     If not provided, uses keys from first data row.

        Returns:
            Dictionary with file information including download URL
        """
        return await _save_csv_impl(data_json, filename, columns)

    return save_csv_file


async def _save_tsv_impl(
    data_json: str,
    filename: str,
    columns: Optional[str] = None,
) -> dict:
    """Internal implementation for TSV save (testable without SDK wrapper).

    Args:
        data_json: JSON string containing a list of objects to convert to TSV rows.
        filename: Desired filename (without extension).
        columns: Optional JSON array string of column names.

    Returns:
        Dictionary with file information including download URL
    """
    # Get context at invocation time
    trace_id, session_id, curator_id = _get_context_from_contextvars()

    # Parse JSON input
    try:
        data = json.loads(data_json)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in data_json: {e}")

    if not isinstance(data, list):
        raise ValueError("data_json must be a JSON array")

    if not data:
        raise ValueError("No data to export - data list is empty")

    # Validate first row is a dict
    if not isinstance(data[0], dict):
        raise ValueError(
            f"data_json array items must be objects, got {type(data[0]).__name__}"
        )

    # Parse columns if provided as JSON string
    column_list: List[str]
    if columns is not None:
        try:
            column_list = json.loads(columns)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in columns: {e}")
    else:
        column_list = list(data[0].keys())

    # Generate TSV content
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=column_list,
        delimiter="\t",
        extrasaction="ignore",
    )
    writer.writeheader()
    writer.writerows(data)
    content = output.getvalue()

    # Generate a fallback trace_id if not available (32-char hex)
    fallback_id = str(uuid.uuid4()).replace("-", "")
    effective_trace_id = trace_id or fallback_id[:32]
    effective_session_id = session_id or fallback_id[:8]
    effective_curator_id = curator_id or "unknown"

    storage = FileOutputStorageService()
    file_path, file_hash, file_size, warnings = storage.save_output(
        trace_id=effective_trace_id,
        session_id=effective_session_id,
        content=content,
        file_type="tsv",
        descriptor=filename,
    )

    for warning in warnings:
        logger.warning(f"[TSV Tool] Validation warning: {warning}")

    full_filename = file_path.name

    # Register file in database for download API
    db = SessionLocal()
    try:
        file_output = FileOutput(
            filename=full_filename,
            file_path=str(file_path),
            file_type="tsv",
            file_size=file_size,
            file_hash=file_hash,
            curator_id=effective_curator_id,
            session_id=effective_session_id,
            trace_id=effective_trace_id,
            agent_name="tsv_formatter",
        )
        db.add(file_output)
        db.commit()
        db.refresh(file_output)
        file_id = str(file_output.id)
        logger.info(
            f"[TSV Tool] Registered file in database: {file_id}, "
            f"filename={full_filename}, size={file_size} bytes"
        )
    except Exception as e:
        db.rollback()
        logger.error(f"[TSV Tool] Failed to register file in database: {e}")
        raise
    finally:
        db.close()

    file_info = FileInfo(
        file_id=file_id,
        filename=full_filename,
        format="tsv",
        size_bytes=file_size,
        hash_sha256=file_hash,
        mime_type="text/tab-separated-values",
        download_url=_build_download_url(file_id),
        created_at=datetime.now(timezone.utc),
        trace_id=trace_id,
        session_id=session_id,
        curator_id=curator_id,
    )

    return file_info.model_dump(mode="json")


def create_tsv_tool():
    """Create a TSV save tool for use with OpenAI Agents SDK.

    Returns:
        A FunctionTool for saving TSV files
    """

    @function_tool
    async def save_tsv_file(
        data_json: str,
        filename: str,
        columns: Optional[str] = None,
    ) -> dict:
        """
        Save data as TSV file (tab-separated values).

        Args:
            data_json: JSON string containing a list of objects to convert to TSV rows.
                       Each object represents a row with keys as column names.
                       Example: '[{"allele_id": "FBal0001", "symbol": "N[1]"}]'
            filename: Desired filename (without extension or timestamp).
                      Example: "alleles" will produce "alleles_20250107T123456Z.tsv"
            columns: Optional JSON array string of column names to include (in order).
                     Example: '["allele_id", "symbol", "gene"]'
                     If not provided, uses keys from first data row.

        Returns:
            Dictionary with file information including download URL
        """
        return await _save_tsv_impl(data_json, filename, columns)

    return save_tsv_file


async def _save_json_impl(
    data_json: str,
    filename: str,
    pretty: bool = True,
) -> dict:
    """Internal implementation for JSON save (testable without SDK wrapper).

    Args:
        data_json: JSON string containing the data to save.
        filename: Desired filename (without extension).
        pretty: If True, format with indentation for readability.

    Returns:
        Dictionary with file information including download URL
    """
    # Get context at invocation time
    trace_id, session_id, curator_id = _get_context_from_contextvars()

    # Parse and re-serialize JSON (validates input and applies formatting)
    try:
        data = json.loads(data_json)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in data_json: {e}")

    # Serialize JSON with optional formatting
    indent = 2 if pretty else None
    content = json.dumps(data, indent=indent, ensure_ascii=False)

    # Generate a fallback trace_id if not available (32-char hex)
    fallback_id = str(uuid.uuid4()).replace("-", "")
    effective_trace_id = trace_id or fallback_id[:32]
    effective_session_id = session_id or fallback_id[:8]
    effective_curator_id = curator_id or "unknown"

    storage = FileOutputStorageService()
    file_path, file_hash, file_size, warnings = storage.save_output(
        trace_id=effective_trace_id,
        session_id=effective_session_id,
        content=content,
        file_type="json",
        descriptor=filename,
    )

    for warning in warnings:
        logger.warning(f"[JSON Tool] Validation warning: {warning}")

    full_filename = file_path.name

    # Register file in database for download API
    db = SessionLocal()
    try:
        file_output = FileOutput(
            filename=full_filename,
            file_path=str(file_path),
            file_type="json",
            file_size=file_size,
            file_hash=file_hash,
            curator_id=effective_curator_id,
            session_id=effective_session_id,
            trace_id=effective_trace_id,
            agent_name="json_formatter",
        )
        db.add(file_output)
        db.commit()
        db.refresh(file_output)
        file_id = str(file_output.id)
        logger.info(
            f"[JSON Tool] Registered file in database: {file_id}, "
            f"filename={full_filename}, size={file_size} bytes"
        )
    except Exception as e:
        db.rollback()
        logger.error(f"[JSON Tool] Failed to register file in database: {e}")
        raise
    finally:
        db.close()

    file_info = FileInfo(
        file_id=file_id,
        filename=full_filename,
        format="json",
        size_bytes=file_size,
        hash_sha256=file_hash,
        mime_type="application/json",
        download_url=_build_download_url(file_id),
        created_at=datetime.now(timezone.utc),
        trace_id=trace_id,
        session_id=session_id,
        curator_id=curator_id,
    )

    return file_info.model_dump(mode="json")


def create_json_tool():
    """Create a JSON save tool for use with OpenAI Agents SDK.

    Returns:
        A FunctionTool for saving JSON files
    """

    @function_tool
    async def save_json_file(
        data_json: str,
        filename: str,
        pretty: bool = True,
    ) -> dict:
        """
        Save data as JSON file.

        Args:
            data_json: JSON string containing the data to save.
                       Can be any valid JSON (object, array, string, number, etc.).
                       Example: '{"genes": ["FBgn0001", "FBgn0002"], "count": 2}'
            filename: Desired filename (without extension or timestamp).
                      Example: "results" will produce "results_20250107T123456Z.json"
            pretty: If True (default), format with indentation for readability.
                    If False, output compact single-line JSON.

        Returns:
            Dictionary with file information including download URL
        """
        return await _save_json_impl(data_json, filename, pretty)

    return save_json_file

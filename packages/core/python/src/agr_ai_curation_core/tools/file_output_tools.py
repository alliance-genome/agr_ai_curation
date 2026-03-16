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
from datetime import datetime, timezone
from typing import List, Optional

from agents import function_tool
from agr_ai_curation_runtime.file_outputs import (
    FileOutputRequestContext,
    PersistedFileOutput,
    get_current_file_output_context,
    persist_file_output,
)
from ..models import FileInfo

logger = logging.getLogger(__name__)


def _get_context_from_contextvars() -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Get trace_id, session_id, curator_id from context variables.

    These are set by the API layer (chat.py, executor.py) at the start
    of each request and by the runner when the Langfuse trace is created.

    Returns:
        Tuple of (trace_id, session_id, curator_id) - any may be None
    """
    context = get_current_file_output_context()
    return (context.trace_id, context.session_id, context.curator_id)
def _persist_output(
    *,
    content: str,
    file_type: str,
    filename: str,
    agent_name: str,
    tool_label: str,
    mime_type: str,
    trace_id: str | None,
    session_id: str | None,
    curator_id: str | None,
) -> dict:
    persisted_output = persist_file_output(
        content=content,
        file_type=file_type,
        descriptor=filename,
        agent_name=agent_name,
        context=FileOutputRequestContext(
            trace_id=trace_id,
            session_id=session_id,
            curator_id=curator_id,
        ),
    )
    _log_validation_warnings(tool_label, persisted_output)
    return _build_file_info(
        persisted_output=persisted_output,
        file_format=file_type,
        mime_type=mime_type,
        trace_id=trace_id,
        session_id=session_id,
        curator_id=curator_id,
    )


def _log_validation_warnings(
    tool_label: str,
    persisted_output: PersistedFileOutput,
) -> None:
    for warning in persisted_output.warnings:
        logger.warning("[%s Tool] Validation warning: %s", tool_label, warning)


def _build_file_info(
    *,
    persisted_output: PersistedFileOutput,
    file_format: str,
    mime_type: str,
    trace_id: str | None,
    session_id: str | None,
    curator_id: str | None,
) -> dict:
    file_info = FileInfo(
        file_id=persisted_output.file_id,
        filename=persisted_output.filename,
        format=file_format,
        size_bytes=persisted_output.size_bytes,
        hash_sha256=persisted_output.hash_sha256,
        mime_type=mime_type,
        download_url=persisted_output.download_url,
        created_at=datetime.now(timezone.utc),
        trace_id=trace_id,
        session_id=session_id,
        curator_id=curator_id,
    )

    return file_info.model_dump(mode="json")


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

    return _persist_output(
        content=content,
        file_type="csv",
        filename=filename,
        agent_name="csv_formatter",
        tool_label="CSV",
        mime_type="text/csv",
        trace_id=trace_id,
        session_id=session_id,
        curator_id=curator_id,
    )


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

    return _persist_output(
        content=content,
        file_type="tsv",
        filename=filename,
        agent_name="tsv_formatter",
        tool_label="TSV",
        mime_type="text/tab-separated-values",
        trace_id=trace_id,
        session_id=session_id,
        curator_id=curator_id,
    )


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

    return _persist_output(
        content=content,
        file_type="json",
        filename=filename,
        agent_name="json_formatter",
        tool_label="JSON",
        mime_type="application/json",
        trace_id=trace_id,
        session_id=session_id,
        curator_id=curator_id,
    )


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

"""File format capability from a saved exporter, never from curator labels."""
from collections.abc import Mapping
from typing import Any

FILE_FORMATTERS = {"csv_formatter": "csv", "tsv_formatter": "tsv", "json_formatter": "json"}


def snapshot_formatter_format(snapshot: Any) -> str | None:
    parent = snapshot.template_source
    if parent not in FILE_FORMATTERS or snapshot.output_contract.output_state != "none":
        return None
    if "finalize_and_save" not in snapshot.tool_ids:
        return None
    return FILE_FORMATTERS[parent]


def resolved_formatter_format(agent_id: str, entry: Mapping[str, Any] | None = None) -> str | None:
    if agent_id in FILE_FORMATTERS:
        return FILE_FORMATTERS[agent_id]
    value = entry.get("output_formatter_format") if entry else None
    return value if value in {"csv", "tsv", "json"} else None

"""Canonical batch result-file manifest access."""

from typing import Any


def canonical_result_files(document: Any) -> list[dict[str, Any]]:
    """Return a validated copy of a batch document's result-file manifest."""

    raw_result_files = document.result_files
    if raw_result_files is None:
        return []
    if not isinstance(raw_result_files, list) or not all(
        isinstance(item, dict) for item in raw_result_files
    ):
        raise ValueError(
            f"Batch document {document.id} has an invalid canonical result_files manifest"
        )
    return [dict(item) for item in raw_result_files]

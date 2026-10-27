"""Measured export facts, separate from scientific rows and download metadata."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.lib.openai_agents.config import (
    get_flow_projection_max_list_items,
    get_flow_projection_max_text_chars,
    get_output_tool_catalog_page_size,
)

EXPORT_ACCOUNTING_GUIDANCE = (
    "EXPORT ACCOUNTING: Use export_accounting from the successful save receipt to explain "
    "candidate rows, applied-filter removals, explicit override exclusions and exported rows. "
    "Synthetic literal-only rows are separate from measured source candidates. "
    "Keep object, evidence, finding and group counts distinct; source rows are not necessarily "
    "the extractor's primary annotation count. Say applied filters, not curator-requested filters "
    "unless their origin is known. Preview paging is not export truncation. An intentional empty "
    "export is a saved file with zero rows. Warnings describe diagnostics, not proof of missing "
    "export data; their scope may include unselected source artifacts. Follow export_details.read "
    "with read_export_details for exact applied filters and complete warnings. Unavailable "
    "accounting is unknown, never zero; never repeat a successful save to recover its metadata."
)


class ExportAccounting(BaseModel):
    """Counts use the selected row source, before any JSON grouping."""

    model_config = ConfigDict(extra="forbid", strict=True)
    row_unit: Literal["artifact", "object", "evidence", "validation_finding"]
    candidate_row_count: int = Field(ge=0)
    synthetic_row_count: int = Field(default=0, ge=0)
    rows_removed_by_filters: int = Field(ge=0)
    rows_removed_by_overrides: int = Field(ge=0)
    matched_row_count: int = Field(ge=0)
    exported_row_count: int = Field(ge=0)
    group_count: int | None = Field(ge=0)
    requested_max_rows: int | None = Field(gt=0)
    truncated: bool
    limited_by_max_rows: bool
    filter_count: int = Field(ge=0)
    warning_count: int = Field(ge=0)
    warning_scope: Literal["source_bundle_and_projection"] = (
        "source_bundle_and_projection"
    )

    @model_validator(mode="after")
    def consistent_counts(self):
        if (
            self.candidate_row_count + self.synthetic_row_count
            != self.rows_removed_by_filters
            + self.rows_removed_by_overrides
            + self.matched_row_count
        ):
            raise ValueError("Export stages do not reconcile")
        if self.exported_row_count > self.matched_row_count:
            raise ValueError("Exported rows exceed matched rows")
        if self.truncated != (self.exported_row_count < self.matched_row_count):
            raise ValueError("Truncation disagrees with measured rows")
        if self.limited_by_max_rows != (
            self.requested_max_rows is not None
            and self.matched_row_count > self.requested_max_rows
        ):
            raise ValueError("Requested limit disagrees with measured rows")
        if self.group_count is not None and self.group_count > self.exported_row_count:
            raise ValueError("Groups exceed exported rows")
        return self


def exact_diagnostics_json(diagnostics: dict[str, Any]) -> str:
    return json.dumps(
        diagnostics,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def diagnostics_sha256(diagnostics: dict[str, Any]) -> str:
    return hashlib.sha256(exact_diagnostics_json(diagnostics).encode()).hexdigest()


def export_diagnostics(result: Any) -> dict[str, Any]:
    return {
        "accounting": result.export_accounting.model_dump(mode="json")
        if result.export_accounting is not None
        else None,
        "filters": result.applied_filters,
        "warnings": list(result.warnings),
        "warning_scope": "source_bundle_and_projection",
    }


def compact_accounting(result: Any) -> dict[str, Any]:
    if result.export_accounting is None:
        return {
            "status": "unavailable",
            "reason": "Projection stage accounting was not recorded",
        }
    limit = get_flow_projection_max_list_items()
    text_limit = get_flow_projection_max_text_chars()
    warnings = result.warnings[:limit]
    return {
        "status": "ok",
        "counts": result.export_accounting.model_dump(mode="json"),
        "warnings": [warning[:text_limit] for warning in warnings],
        "warnings_omitted": len(result.warnings) - len(warnings),
        "warning_text_truncated": any(
            len(warning) > text_limit for warning in warnings
        ),
    }


def projection_summary(result: Any) -> dict[str, Any]:
    """One summary contract for the saved metadata and formatter receipts."""
    headers = [column.header or column.key for column in result.columns]
    column_limit = get_output_tool_catalog_page_size()
    return {
        "format": result.format,
        "empty_reason": result.empty_reason,
        "row_source": result.row_source,
        "columns": headers[:column_limit],
        "column_count": len(headers),
        **(
            {"columns_omitted": len(headers) - column_limit}
            if len(headers) > column_limit
            else {}
        ),
        "total_count": result.total_count,
        "row_count": len(result.rows),
        "truncated": result.truncated,
        "limited_by_max_rows": result.limited_by_max_rows,
        "overrides_applied": result.overrides_applied,
        "rows_excluded": result.rows_excluded,
        "group_by": list(result.group_by),
        "warning_count": len(result.warnings),
        "warnings": [
            warning[: get_flow_projection_max_text_chars()]
            for warning in result.warnings[: get_flow_projection_max_list_items()]
        ],
        "export_accounting": compact_accounting(result),
        "selection_manifests": list(result.selection_manifests),
    }


def export_details_reference(file_id: str, result: Any) -> dict[str, Any]:
    return {
        "read": {
            "file_id": file_id,
            "diagnostics_sha256": diagnostics_sha256(export_diagnostics(result)),
            "offset": 0,
        }
    }


class AccountingReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: Literal["ok"]
    counts: ExportAccounting
    warnings: list[str]
    warnings_omitted: int = Field(ge=0)
    warning_text_truncated: bool

    @model_validator(mode="after")
    def warning_coverage(self):
        if len(self.warnings) + self.warnings_omitted != self.counts.warning_count:
            raise ValueError("Warning coverage does not reconcile")
        return self


def validated_accounting_handoff(file_info: dict[str, Any]) -> dict[str, Any]:
    """Whitelist accounting independently of the safe FILE_READY identity fields."""
    from pydantic import ValidationError

    try:
        receipt = AccountingReceipt.model_validate(file_info.get("export_accounting"))
        # Even a structurally valid unexpected payload cannot widen the preview.
        limit, chars = (
            get_flow_projection_max_list_items(),
            get_flow_projection_max_text_chars(),
        )
        warnings = receipt.warnings[:limit]
        receipt.warnings_omitted += len(receipt.warnings) - len(warnings)
        receipt.warning_text_truncated |= any(len(w) > chars for w in warnings)
        receipt.warnings = [w[:chars] for w in warnings]
        details = file_info.get("export_details")
        read = details.get("read") if isinstance(details, dict) else None
        digest = read.get("diagnostics_sha256") if isinstance(read, dict) else None
        if (
            not isinstance(read, dict)
            or read.get("file_id") != file_info.get("file_id")
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(c not in "0123456789abcdef" for c in digest)
        ):
            return {
                "export_accounting": {
                    "status": "unavailable",
                    "reason": "Export diagnostic reference is incomplete",
                }
            }
        return {
            "export_accounting": receipt.model_dump(mode="json"),
            "export_details": {
                "read": {
                    "file_id": file_info["file_id"],
                    "diagnostics_sha256": digest,
                    "offset": 0,
                }
            },
        }
    except (ValidationError, TypeError):
        return {
            "export_accounting": {
                "status": "unavailable",
                "reason": "Export accounting is missing or invalid",
            }
        }

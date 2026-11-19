"""Response of the read-only viewer of records a flow run's review set aside (ALL-1561)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class SetAsideField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_ref: str | None
    label: str
    # Rendered exactly as a CSV file shows the value; None when empty.
    value: Any = None


class SetAsideRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    extraction_result_id: str | None
    label: str | None
    review_status: str
    # The review's own stored reason for setting the record aside.
    review_reason: str | None
    fields: list[SetAsideField]


class SetAsideResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    extraction_result_id: str
    node_id: str | None
    step: int | None
    agent_name: str | None
    # False for general results with no saved output structure: no review
    # fields are offered, so files of this source never include these records.
    review_fields_available: bool
    records: list[SetAsideRecord]


class FlowRunSetAsideRecordsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    flow_run_id: str
    document_id: str
    set_aside_count: int
    results: list[SetAsideResult]

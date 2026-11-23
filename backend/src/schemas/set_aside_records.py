"""Response of the read-only viewer of records a flow run's review set aside (ALL-1561)."""

from __future__ import annotations

from typing import Any, TypedDict

from pydantic import BaseModel, ConfigDict


class OutputSetAsideReceipt(TypedDict):
    """A formatter output's exact membership after source selection, filters and row limits."""

    included: int
    left_out: int
    included_keys: list[str]


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
    # True when a curator has since deferred, rejected or otherwise changed one of
    # these records in Review & Curate: records is empty, and exports of this
    # result wait until the curator accepts or removes them.
    held_by_curator: bool
    records: list[SetAsideRecord]


class FlowRunSetAsideRecordsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    flow_run_id: str
    document_id: str
    set_aside_count: int
    results: list[SetAsideResult]

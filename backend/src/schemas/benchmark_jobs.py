"""Explicit, token-free lifecycle response contracts."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.lib.benchmarks.models import BenchmarkSuite, FrozenStrictModel, ResolvedBenchmarkPlan

from src.models.sql.benchmark import BenchmarkInvocationStatus
from src.schemas.cost_ledger import CostLedgerReference


class BenchmarkSubmitRequest(FrozenStrictModel):
    suite: BenchmarkSuite
    plan: ResolvedBenchmarkPlan


class BenchmarkRerunRequest(FrozenStrictModel):
    cell_ids: tuple[UUID, ...] = Field(default=(), strict=False)


class BenchmarkErrorDetail(BaseModel):
    code: str
    message: str
    resume_after: str | None = None


class BenchmarkErrorResponse(BaseModel):
    detail: BenchmarkErrorDetail


def lifecycle_error_responses() -> dict[int | str, dict[str, Any]]:
    """Document the lifecycle error envelope, including shared auth failures."""
    cases = {
        400: ("invalid_delegated_authorization", "Invalid delegated source authorization"),
        401: ("authorization_required", "Verified benchmark identity required"),
        403: ("capability_required", "Benchmark capability required"),
        404: ("not_found", "Benchmark resource not found or API disabled"),
        409: ("lifecycle_conflict", "Benchmark lifecycle or idempotency conflict"),
        410: ("event_history_expired", "Replay history expired; refresh status and use resume_after"),
        413: ("oversize_submission", "Benchmark admission body exceeds configured limit"),
        415: ("invalid_content_type", "Benchmark admission requires application/json"),
        422: ("invalid_request", "Invalid benchmark request"),
        429: ("event_connection_limit", "Principal event connection limit reached"),
        503: ("authorization_unavailable", "Benchmark dependency unavailable"),
    }
    return {
        status: {
            "model": BenchmarkErrorResponse,
            "description": message,
            "content": {"application/json": {"example": {"detail": {
                "code": code, "message": message,
            }}}},
        }
        for status, (code, message) in cases.items()
    }


def admission_body_schema(model: type[BaseModel], *, example: Any = None) -> dict[str, Any]:
    """Inline these acyclic admission models for a self-contained OpenAPI body.

    Request streaming is manual so authentication precedes bounded body reads.
    Local Pydantic $defs references otherwise point at the OpenAPI document root.
    """
    schema = model.model_json_schema()
    definitions = schema.pop("$defs", {})

    def inline(value: Any) -> Any:
        if isinstance(value, list):
            return [inline(item) for item in value]
        if isinstance(value, dict):
            if "$ref" in value:
                return inline(definitions[value["$ref"].removeprefix("#/$defs/")])
            return {key: inline(item) for key, item in value.items()}
        return value

    return {"requestBody": {"required": True, "content": {
        "application/json": {"schema": inline(schema), **({"example": example} if example is not None else {})},
    }}}


class BenchmarkInvocationExecution(BaseModel):
    """Execution evidence only; quantities and charges belong to the ledger."""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    cell_id: UUID
    ordinal: int
    attempt: int
    stage_execution_id: UUID | None = None
    parent_invocation_sequence: int | None = None
    route_slot: str
    request_digest: str
    response_digest: str | None
    requested_provider: str | None
    requested_model: str | None
    reasoning_effort: str | None
    actual_provider: str | None
    actual_model: str | None
    routing_attempt: int | None
    sequence: int
    latency_ms: int | None
    status: BenchmarkInvocationStatus
    failure: dict[str, Any] | None
    started_at: datetime
    completed_at: datetime | None


class BenchmarkInvocationResponse(BenchmarkInvocationExecution):
    accounting_reference: CostLedgerReference


class BenchmarkInvocationPage(BaseModel):
    schema_version: Literal[2] = 2
    items: tuple[BenchmarkInvocationResponse, ...]
    next_after_ordinal: int | None


class BenchmarkStageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    cell_id: UUID
    attempt: int
    ordinal: int
    stage_id: str
    role: Literal["extraction", "validation", "output", "supervisor", "other"]
    node_id: str | None
    source_node_id: str | None
    binding_id: str | None
    agent_id: str | None
    parent_execution_id: UUID | None
    parent_invocation_sequence: int | None
    status: Literal["running", "succeeded", "failed", "interrupted"]
    started_at: datetime
    completed_at: datetime | None
    elapsed_ms: int | None
    failure_type: str | None


class BenchmarkStagePage(BaseModel):
    schema_version: Literal[1] = 1
    items: tuple[BenchmarkStageResponse, ...]
    next_after_ordinal: int | None

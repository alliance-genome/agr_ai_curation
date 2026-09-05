"""Explicit, token-free lifecycle response contracts."""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.lib.benchmarks.models import BenchmarkSuite, FrozenStrictModel, ResolvedBenchmarkPlan

from src.models.sql.benchmark import BenchmarkInvocationStatus


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


class BenchmarkInvocationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    cell_id: UUID
    ordinal: int
    attempt: int
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
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    billed_amount: Decimal | None
    billed_unit: str | None
    billed_source: str | None
    status: BenchmarkInvocationStatus
    failure: dict[str, Any] | None
    started_at: datetime
    completed_at: datetime | None


class BenchmarkInvocationPage(BaseModel):
    items: tuple[BenchmarkInvocationResponse, ...]
    next_after_ordinal: int | None

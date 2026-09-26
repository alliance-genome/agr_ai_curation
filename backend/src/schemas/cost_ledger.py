"""Versioned shared read contract; no benchmark-owned usage or money store.

Consumers persist the reference, not the returned projection. Reference revision
zero pins unknown facts before the first addition; it never means latest. API
authorization must establish owner/execution scope independently of this UUID.
"""

from typing import Literal
from decimal import Decimal, InvalidOperation
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.lib.cost_ledger.facts import RecordedCharge, TokenUsage, UsageStatus


class CostLedgerReference(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: Literal[1]
    deployment_id: str = Field(min_length=1)
    attempt_id: UUID
    fact_revision: int = Field(ge=0)

    @field_validator("deployment_id")
    @classmethod
    def nonblank_scope(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Ledger deployment scope must be nonblank")
        return value


class CostFactsProjection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)

    schema_version: Literal[1]
    reference: CostLedgerReference
    usage: TokenUsage
    usage_status: UsageStatus
    usage_issues: tuple[str, ...]
    recorded_charge: RecordedCharge | None

    @field_validator("recorded_charge", mode="before")
    @classmethod
    def exact_charge_transport(cls, value):
        if isinstance(value, dict):
            amount = value.get("amount")
            if not isinstance(amount, (str, Decimal)):
                raise ValueError("Transported monetary amounts must be decimal strings")
            try:
                value = {**value, "amount": Decimal(amount)}
            except InvalidOperation as exc:
                raise ValueError("Invalid decimal amount") from exc
        return value

    @model_validator(mode="after")
    def consistent_facts(self) -> "CostFactsProjection":
        if self.usage_status != self.usage.status or self.usage_issues != self.usage.issues:
            raise ValueError("Usage status must describe the actual retained facts")
        empty = self.usage.status == "missing" and self.recorded_charge is None
        if (self.reference.fact_revision == 0) != empty:
            raise ValueError("Empty snapshot must use revision zero; known facts require a revision")
        return self


class KnownTokenTotal(BaseModel):
    model_config = ConfigDict(frozen=True)
    known_total: int | None
    known_attempts: int
    unknown_attempts: int


class RecordedChargeTotal(BaseModel):
    model_config = ConfigDict(frozen=True)
    unit: str
    source: str
    amount: Decimal
    attempts: int


class BenchmarkJobAccounting(BaseModel):
    """Current snapshot, not a stored valuation or a complete provider bill."""

    model_config = ConfigDict(frozen=True)
    schema_version: Literal[1] = 1
    job_id: UUID
    scope: Literal["benchmark_invocations"] = "benchmark_invocations"
    valuation: Literal["recorded_charges_only"] = "recorded_charges_only"
    shared_preparation_accounting: Literal["not_included"] = "not_included"
    invocation_count: int
    attempt_count: int
    usage: dict[str, KnownTokenTotal]
    inconsistent_usage_attempts: int
    recorded_charges: tuple[RecordedChargeTotal, ...]
    unknown_charge_attempts: int

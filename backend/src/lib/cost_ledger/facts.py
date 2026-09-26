"""Lossless normalized accounting facts, before storage or price calculation.

Adapters must translate provider-inclusive or telemetry-exclusive buckets
explicitly. Missing fields remain unknown; this module does not guess aliases,
currency conversion, pricing or source authority.
"""

from dataclasses import dataclass, fields
from decimal import Decimal
from typing import Literal

UsageStatus = Literal["missing", "partial", "recorded", "inconsistent"]


class CostFactConflict(ValueError):
    """Late evidence contradicts a known fact and requires reconciliation."""


@dataclass(frozen=True)
class TokenUsage:
    """Inclusive input/output totals with disjoint input cache subsets.

Reasoning is included in output, not added to it. Unknown cache/reasoning
details do not invalidate known inclusive totals, but prevent exact bucket
pricing. Missing usage reasons (failure, cancellation, provider omission) are
separate execution/provenance facts, not token quantities.
"""

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    reasoning_tokens: int | None = None

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f"{field.name} must be a nonnegative integer or unknown")

    @property
    def issues(self) -> tuple[str, ...]:
        issues = []
        if self.input_tokens is not None:
            # Known subsets exceeding the total are inconsistent even when
            # another subset is unknown. Zero here is only a lower bound.
            if (self.cache_read_tokens or 0) + (self.cache_write_tokens or 0) > self.input_tokens:
                issues.append("cache_subsets_exceed_input")
        if self.output_tokens is not None and self.reasoning_tokens is not None:
            if self.reasoning_tokens > self.output_tokens:
                issues.append("reasoning_exceeds_output")
        if self.total_tokens is not None:
            minimum_input = max(self.input_tokens or 0, (self.cache_read_tokens or 0) + (self.cache_write_tokens or 0))
            minimum_output = max(self.output_tokens or 0, self.reasoning_tokens or 0)
            if minimum_input + minimum_output > self.total_tokens:
                issues.append("total_tokens_mismatch")
            elif self.input_tokens is not None and self.output_tokens is not None:
                if self.input_tokens + self.output_tokens != self.total_tokens:
                    issues.append("total_tokens_mismatch")
        return tuple(issues)

    @property
    def status(self) -> UsageStatus:
        if self.issues:
            return "inconsistent"
        if all(getattr(self, field.name) is None for field in fields(self)):
            return "missing"
        return "recorded" if self.input_tokens is not None and self.output_tokens is not None else "partial"

    @property
    def uncached_input_tokens(self) -> int | None:
        if self.issues or any(value is None for value in (
            self.input_tokens, self.cache_read_tokens, self.cache_write_tokens,
        )):
            return None
        assert self.input_tokens is not None
        assert self.cache_read_tokens is not None
        assert self.cache_write_tokens is not None
        return self.input_tokens - self.cache_read_tokens - self.cache_write_tokens

    @property
    def nonreasoning_output_tokens(self) -> int | None:
        if self.issues or self.output_tokens is None or self.reasoning_tokens is None:
            return None
        return self.output_tokens - self.reasoning_tokens


def enrich_usage(existing: TokenUsage, incoming: TokenUsage) -> TokenUsage:
    """Fill unknown facts only; replay is idempotent, not additive.

Contradictory known fields require a reviewed correction, never last-write-wins.
New combinations can expose inconsistent buckets; retain them as inconsistent
rather than clamp, fabricate or price them. Storage must retain source provenance
and revision identity when applying this pure operation.
"""
    values = {}
    for field in fields(existing):
        previous, new = getattr(existing, field.name), getattr(incoming, field.name)
        if previous is not None and new is not None and previous != new:
            raise CostFactConflict(f"Conflicting accounting field: {field.name}")
        values[field.name] = previous if previous is not None else new
    return TokenUsage(**values)


@dataclass(frozen=True)
class RecordedCharge:
    """Provider-recorded amount; not an estimate and not implicitly USD.

Unit can be a currency code or provider credits. Source preserves the exact
billing provenance; different units/sources must not share a summed bucket.
Currency conversion and invoice adjustments are separate accounting operations.
"""

    amount: Decimal
    unit: str
    source: str

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Decimal) or not self.amount.is_finite() or self.amount < 0:
            raise ValueError("Recorded charge must be a finite nonnegative Decimal")
        if any(not isinstance(value, str) or not value.strip() for value in (self.unit, self.source)):
            raise ValueError("Recorded charge requires explicit unit and source")


def enrich_charge(existing: RecordedCharge | None, incoming: RecordedCharge | None) -> RecordedCharge | None:
    """Unknown is not free; matching evidence never adds another charge."""
    if existing is not None and incoming is not None and existing != incoming:
        raise CostFactConflict("Conflicting recorded charge requires reconciliation")
    return existing if existing is not None else incoming

"""Safe, request-scoped usage telemetry for routed model providers."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterator, Mapping, Optional


@dataclass(frozen=True)
class BilledCost:
    """An authoritative billed amount returned by the upstream routing API."""

    amount: Decimal
    unit: str
    source: str


@dataclass(frozen=True)
class ProviderUsageRecord:
    """Small content-free provider route and usage record."""

    requested_provider: str
    requested_model: str
    actual_provider: Optional[str]
    actual_model: Optional[str]
    routing_attempt: Optional[int]
    latency_ms: int
    input_tokens: Optional[int]
    output_tokens: Optional[int]
    total_tokens: Optional[int]
    billed_cost: Optional[BilledCost]


_provider_usage_records: ContextVar[Optional[list[ProviderUsageRecord]]] = ContextVar(
    "provider_usage_records",
    default=None,
)


@contextmanager
def capture_provider_usage() -> Iterator[list[ProviderUsageRecord]]:
    """Capture normalized provider usage emitted in the current async context."""

    records: list[ProviderUsageRecord] = []
    token = _provider_usage_records.set(records)
    try:
        yield records
    finally:
        _provider_usage_records.reset(token)


def emit_provider_usage(record: ProviderUsageRecord) -> None:
    """Append a record when a caller has opted into request-scoped capture."""

    records = _provider_usage_records.get()
    if records is not None:
        records.append(record)


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        if isinstance(dumped, Mapping):
            return dumped
    return {}


def _optional_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    return None


def _optional_text(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _openrouter_billed_cost(usage: Mapping[str, Any]) -> Optional[BilledCost]:
    raw_cost = usage.get("cost")
    if isinstance(raw_cost, bool) or raw_cost is None:
        return None
    try:
        amount = Decimal(str(raw_cost))
    except (InvalidOperation, ValueError):
        return None
    if not amount.is_finite() or amount < 0:
        return None
    return BilledCost(
        amount=amount,
        unit="credits",
        source="openrouter_usage",
    )


def normalize_openrouter_usage(
    payload: Any,
    *,
    requested_model: str,
    latency_ms: int,
) -> ProviderUsageRecord:
    """Decode only OpenRouter's safe authoritative routing and usage fields.

    The metadata contract is additive, so unknown fields (including free-form
    summaries and pipeline data) are intentionally ignored.
    """

    body = _as_mapping(payload)
    usage = _as_mapping(body.get("usage"))
    metadata = _as_mapping(body.get("openrouter_metadata"))
    endpoints = _as_mapping(metadata.get("endpoints"))
    available = endpoints.get("available")
    selected: Mapping[str, Any] = {}
    if isinstance(available, list):
        for candidate in available:
            candidate_mapping = _as_mapping(candidate)
            if candidate_mapping.get("selected") is True:
                selected = candidate_mapping
                break

    return ProviderUsageRecord(
        requested_provider="openrouter",
        requested_model=requested_model,
        actual_provider=_optional_text(selected.get("provider")),
        actual_model=_optional_text(selected.get("model")),
        routing_attempt=_optional_int(metadata.get("attempt")),
        latency_ms=max(0, int(latency_ms)),
        input_tokens=_optional_int(usage.get("prompt_tokens")),
        output_tokens=_optional_int(usage.get("completion_tokens")),
        total_tokens=_optional_int(usage.get("total_tokens")),
        billed_cost=_openrouter_billed_cost(usage),
    )


def normalize_provider_usage(
    adapter: str,
    payload: Any,
    *,
    requested_model: str,
    latency_ms: int,
) -> ProviderUsageRecord:
    """Dispatch provider-specific response decoding at the adapter boundary."""

    if adapter == "openrouter":
        return normalize_openrouter_usage(
            payload,
            requested_model=requested_model,
            latency_ms=latency_ms,
        )
    raise ValueError(f"Unsupported provider telemetry adapter '{adapter}'")

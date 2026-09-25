"""Safe, request-scoped usage telemetry for routed model providers."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field as dataclass_field, fields, is_dataclass, replace
from decimal import Decimal, InvalidOperation
import logging
from threading import Lock
from typing import Any, Iterator, Mapping, Optional, Protocol

from src.lib.cost_ledger.facts import TokenUsage


logger = logging.getLogger(__name__)


class _ProviderUsageEmissionError(RuntimeError):
    """Sanitized provider-usage telemetry failure safe for reporting."""


def _sanitized_emission_error(orig_type_name: str) -> _ProviderUsageEmissionError:
    try:
        raise _ProviderUsageEmissionError(
            f"Provider usage trace event emission failed ({orig_type_name})"
        ) from None
    except _ProviderUsageEmissionError as sanitized:
        sanitized.__context__ = None
        sanitized.__cause__ = None
        return sanitized


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
    route_slot: Optional[str] = None
    reasoning_effort: Optional[str] = None
    sequence: Optional[int] = None
    status: str = "completed"
    failure_detail: Optional[str] = None
    stage_execution_id: Optional[str] = None
    parent_invocation_sequence: Optional[int] = None
    model_request_id: Optional[str] = None
    # Canonical facts for the ledger adapter, not the legacy artifact projection.
    accounting_usage: TokenUsage = dataclass_field(default_factory=TokenUsage)


@dataclass(frozen=True)
class PendingProviderInvocation:
    route_slot: Optional[str]
    requested_provider: str
    requested_model: str
    reasoning_effort: Optional[str]
    sequence: int
    started_at: float
    stage_execution_id: Optional[str] = None
    parent_invocation_sequence: Optional[int] = None
    model_request_id: Optional[str] = None


class ProviderInvocationObserver(Protocol):
    """Durable benchmark checkpoint sink invoked around provider dispatch."""

    def started(self, pending: PendingProviderInvocation) -> None: ...

    def completed(
        self, pending: PendingProviderInvocation, record: ProviderUsageRecord
    ) -> None: ...


@dataclass
class _ProviderUsageCapture:
    records: list[ProviderUsageRecord]
    max_records: int
    max_failure_detail_chars: int
    reserved: int = 0
    lock: Any = dataclass_field(default_factory=Lock, repr=False)
    tool_parents: dict[tuple[str | None, str], int | None] = dataclass_field(default_factory=dict)


_provider_usage_records: ContextVar[Optional[_ProviderUsageCapture]] = ContextVar(
    "provider_usage_records",
    default=None,
)
_provider_invocation_observer: ContextVar[Optional[ProviderInvocationObserver]] = (
    ContextVar("provider_invocation_observer", default=None)
)


@contextmanager
def observe_provider_invocations(
    observer: ProviderInvocationObserver,
) -> Iterator[None]:
    """Install a worker-owned durable start/finish checkpoint observer."""

    token = _provider_invocation_observer.set(observer)
    try:
        yield
    finally:
        _provider_invocation_observer.reset(token)


@contextmanager
def capture_provider_usage(
    *, max_records: int | None = None, max_failure_detail_chars: int | None = None
) -> Iterator[list[ProviderUsageRecord]]:
    """Capture normalized provider usage emitted in the current async context."""

    if max_records is None or max_failure_detail_chars is None:
        from src.lib.openai_agents.config import (
            get_benchmark_max_failure_detail_chars,
            get_benchmark_max_invocations_per_cell,
        )

        max_records = max_records or get_benchmark_max_invocations_per_cell()
        max_failure_detail_chars = (
            max_failure_detail_chars or get_benchmark_max_failure_detail_chars()
        )
    records: list[ProviderUsageRecord] = []
    capture = _ProviderUsageCapture(
        records=records,
        max_records=max_records,
        max_failure_detail_chars=max_failure_detail_chars,
    )
    token = _provider_usage_records.set(capture)
    try:
        yield records
    finally:
        _provider_usage_records.reset(token)


def emit_provider_usage(record: ProviderUsageRecord) -> None:
    """Capture a record and publish its bounded fields to the active trace."""

    capture = _provider_usage_records.get()
    if capture is not None:
        with capture.lock:
            if len(capture.records) >= capture.max_records:
                raise RuntimeError(
                    f"Benchmark cell exceeded {capture.max_records} provider invocations"
                )
            capture.records.append(record)
    _emit_provider_usage_trace_event(record)


def begin_provider_invocation(
    *,
    requested_provider: str,
    requested_model: str,
    route_slot: str | None = None,
    reasoning_effort: str | None = None,
    started_at: float,
) -> PendingProviderInvocation | None:
    """Reserve a stable sequence number before making a provider call."""

    capture = _provider_usage_records.get()
    if capture is None:
        return None
    # Copied contexts share this capture across parallel validator threads.
    # Reserve and snapshot the sequence together, before any observer I/O.
    with capture.lock:
        if capture.reserved >= capture.max_records:
            raise RuntimeError(
                f"Benchmark cell exceeded {capture.max_records} provider invocations"
            )
        capture.reserved += 1
        sequence = capture.reserved
    from src.lib.benchmarks.stage_measurements import current_stage
    from src.lib.observability.cost_context import current_model_request

    stage = current_stage()
    pending = PendingProviderInvocation(
        route_slot=route_slot,
        requested_provider=requested_provider,
        requested_model=requested_model,
        reasoning_effort=reasoning_effort,
        sequence=sequence,
        started_at=started_at,
        stage_execution_id=str(stage.execution_id) if stage is not None else None,
        parent_invocation_sequence=stage.parent_invocation_sequence if stage is not None else None,
        model_request_id=current_model_request().get("model_request_id"),
    )
    observer = _provider_invocation_observer.get()
    if observer is not None:
        observer.started(pending)
    return pending


def complete_provider_invocation(
    pending: PendingProviderInvocation | None,
    record: ProviderUsageRecord,
) -> None:
    if pending is None:
        emit_provider_usage(record)
        return
    completed = replace(
        record,
        route_slot=pending.route_slot,
        requested_provider=pending.requested_provider,
        requested_model=pending.requested_model,
        reasoning_effort=pending.reasoning_effort,
        sequence=pending.sequence,
        status="completed",
        failure_detail=None,
        stage_execution_id=pending.stage_execution_id,
        parent_invocation_sequence=pending.parent_invocation_sequence,
        model_request_id=pending.model_request_id,
    )
    emit_provider_usage(completed)
    observer = _provider_invocation_observer.get()
    if observer is not None:
        observer.completed(pending, completed)


def fail_provider_invocation(
    pending: PendingProviderInvocation | None,
    exc: BaseException,
    *,
    latency_ms: int,
) -> None:
    """Record bounded, content-free failure detail without swallowing it."""

    if pending is None:
        return
    capture = _provider_usage_records.get()
    max_chars = capture.max_failure_detail_chars if capture is not None else 0
    detail_parts = [type(exc).__name__]
    for field in ("status_code", "code"):
        value = getattr(exc, field, None)
        if isinstance(value, (int, str)) and str(value).strip():
            detail_parts.append(f"{field}={str(value).strip()}")
    detail = "; ".join(detail_parts)[:max_chars]
    failed = ProviderUsageRecord(
        route_slot=pending.route_slot,
        requested_provider=pending.requested_provider,
        requested_model=pending.requested_model,
        reasoning_effort=pending.reasoning_effort,
        actual_provider=None,
        actual_model=None,
        routing_attempt=None,
        latency_ms=max(0, latency_ms),
        input_tokens=None,
        output_tokens=None,
        total_tokens=None,
        billed_cost=None,
        sequence=pending.sequence,
        status="failed",
        failure_detail=detail,
        stage_execution_id=pending.stage_execution_id,
        parent_invocation_sequence=pending.parent_invocation_sequence,
        model_request_id=pending.model_request_id,
    )
    emit_provider_usage(failed)
    observer = _provider_invocation_observer.get()
    if observer is not None:
        observer.completed(pending, failed)


def complete_generic_provider_invocation(
    pending: PendingProviderInvocation | None,
    response: Any,
    *,
    latency_ms: int,
    sdk_normalized_usage: bool = False,
) -> None:
    """Complete a native/SDK call from its content-free response metadata."""

    if pending is None:
        return
    payload = _as_mapping(response)
    raw_usage = payload.get("usage") or getattr(response, "usage", None)
    usage = _as_mapping(raw_usage)
    input_tokens = _optional_int(
        usage.get("input_tokens", usage.get("prompt_tokens"))
    )
    output_tokens = _optional_int(
        usage.get("output_tokens", usage.get("completion_tokens"))
    )
    total_tokens = _optional_int(usage.get("total_tokens"))
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens
    complete_provider_invocation(
        pending,
        ProviderUsageRecord(
            requested_provider=pending.requested_provider,
            requested_model=pending.requested_model,
            actual_provider=pending.requested_provider,
            actual_model=(
                _optional_text(
                    payload.get("model")
                    or payload.get("model_id")
                    or getattr(response, "model", None)
                    or getattr(response, "model_id", None)
                )
                or pending.requested_model
            ),
            routing_attempt=0,
            latency_ms=max(0, latency_ms),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            billed_cost=None,
            accounting_usage=_accounting_usage(raw_usage, sdk_normalized=sdk_normalized_usage),
        ),
    )
    register_provider_tool_calls(pending, response)


def register_provider_tool_calls(
    pending: PendingProviderInvocation | None, response: Any,
) -> None:
    """Retain only tool IDs, scoped to their emitting stage, never arguments.

    The SDK response output carries call_id, unlike the response item's own id.
    Reused IDs from different invocations are ambiguous and remain unknown.
    """
    capture = _provider_usage_records.get()
    if capture is None or pending is None:
        return
    from .config import get_benchmark_max_tool_call_links_per_cell

    payload = _as_mapping(response)
    output = payload.get("output") or getattr(response, "output", None)
    if output is None and isinstance(payload.get("choices"), (list, tuple)):
        # Chat Completions uses tool_calls[].id, which the SDK maps to call_id.
        # Streaming deltas need only the initial ID-bearing chunk, not arguments.
        output = []
        for choice in payload["choices"]:
            choice_payload = _as_mapping(choice)
            message = _as_mapping(choice_payload.get("message") or choice_payload.get("delta"))
            calls = message.get("tool_calls")
            if isinstance(calls, (list, tuple)):
                for call in calls:
                    call_payload = _as_mapping(call)
                    output.append({"type": "function_call", "call_id": call_payload.get("id")})
    if not isinstance(output, (list, tuple)):
        return
    for item in output:
        value = _as_mapping(item)
        if value.get("type") != "function_call":
            continue
        call_id = value.get("call_id")
        if not isinstance(call_id, str) or not call_id:
            continue
        key = (pending.stage_execution_id, call_id)
        with capture.lock:
            if key in capture.tool_parents:
                if capture.tool_parents[key] != pending.sequence:
                    capture.tool_parents[key] = None
            else:
                if len(capture.tool_parents) >= get_benchmark_max_tool_call_links_per_cell():
                    raise RuntimeError("Benchmark cell exceeded provider tool-call link limit")
                capture.tool_parents[key] = pending.sequence


def provider_parent_for_tool_call(call_id: str | None) -> int | None:
    """Resolve an exact SDK tool-call identity in the current parent stage."""
    capture = _provider_usage_records.get()
    if capture is None or not isinstance(call_id, str) or not call_id:
        return None
    from src.lib.benchmarks.stage_measurements import current_stage

    stage = current_stage()
    key = (str(stage.execution_id) if stage is not None else None, call_id)
    with capture.lock:
        return capture.tool_parents.get(key)


def provider_usage_metadata(record: ProviderUsageRecord) -> dict[str, Any]:
    """Serialize only the normalized, content-free provider usage contract."""

    billed_cost = record.billed_cost
    metadata = {
        "route_slot": record.route_slot,
        "requested_provider": record.requested_provider,
        "requested_model": record.requested_model,
        "reasoning_effort": record.reasoning_effort,
        "actual_provider": record.actual_provider,
        "actual_model": record.actual_model,
        "routing_attempt": record.routing_attempt,
        "latency_ms": record.latency_ms,
        "input_tokens": record.input_tokens,
        "output_tokens": record.output_tokens,
        "total_tokens": record.total_tokens,
        "billed_cost": (
            {
                "amount": str(billed_cost.amount),
                "unit": billed_cost.unit,
                "source": billed_cost.source,
            }
            if billed_cost is not None
            else None
        ),
        "sequence": record.sequence,
        "status": record.status,
        "failure_detail": record.failure_detail,
    }
    if record.stage_execution_id is not None:
        metadata["stage_execution_id"] = record.stage_execution_id
    if record.parent_invocation_sequence is not None:
        metadata["parent_invocation_sequence"] = record.parent_invocation_sequence
    return metadata


def _emit_provider_usage_trace_event(record: ProviderUsageRecord) -> None:
    """Attach safe provider usage to the active Langfuse trace when available."""

    from src.lib.context import get_current_trace_id
    from src.lib.openai_agents.langfuse_client import get_langfuse

    trace_id = get_current_trace_id()
    langfuse = get_langfuse()
    if not trace_id or langfuse is None:
        return

    try:
        metadata: dict[str, Any] = {"provider_usage": provider_usage_metadata(record)}
        # Correlation belongs to the telemetry envelope, not the immutable
        # benchmark result schema. That schema changes with the ledger cutover.
        if record.model_request_id is not None:
            metadata["model_request_id"] = record.model_request_id
        metadata["accounting_usage"] = {
            field.name: getattr(record.accounting_usage, field.name)
            for field in fields(TokenUsage)
        }
        langfuse.create_event(
            name="provider_usage",
            metadata=metadata,
            trace_context={"trace_id": trace_id},
        )
    except Exception as exc:
        # Telemetry transport must not change the model-call result. Retain only
        # the exception type because provider errors can contain request data.
        sanitized_exc = _sanitized_emission_error(type(exc).__name__)
        logger.warning(
            "Failed to emit provider usage trace event (%s)",
            type(exc).__name__,
        )
        try:
            from src.lib.observability.runtime import report_runtime_exception

            report_runtime_exception(
                sanitized_exc,
                component="provider_usage",
                operation="trace_event_emission_failed",
                tags={"provider": record.requested_provider},
            )
        except Exception as report_exc:
            logger.warning(
                "Failed to report provider usage trace event loss (%s)",
                type(report_exc).__name__,
            )


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if is_dataclass(value) and not isinstance(value, type):
        # Agents SDK ModelResponse and Usage are dataclasses. Keep this shallow:
        # response content is not telemetry and must not be recursively copied.
        return {field.name: getattr(value, field.name) for field in fields(value)}
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


def _accounting_usage(raw_usage: Any, *, sdk_normalized: bool = False) -> TokenUsage:
    """Preserve inclusive facts without synthesizing totals or absent details.

    Agents SDK Usage inserts zeros for missing counts and nested details before
    our non-streaming observer sees them. Those zeros are ambiguous, not proof
    of free work. Raw response mappings/Pydantic objects preserve explicit zero.
    This limitation needs earlier raw capture to recover exact zero on SDK calls.
    """
    from agents.usage import Usage

    usage = _as_mapping(raw_usage)
    sdk_normalized = sdk_normalized or isinstance(raw_usage, Usage)

    def count(value: Any) -> int | None:
        parsed = _optional_int(value)
        return None if sdk_normalized and parsed == 0 else parsed

    inputs = _as_mapping(usage.get("input_tokens_details", usage.get("prompt_tokens_details")))
    outputs = _as_mapping(usage.get("output_tokens_details", usage.get("completion_tokens_details")))
    return TokenUsage(
        input_tokens=count(usage.get("input_tokens", usage.get("prompt_tokens"))),
        output_tokens=count(usage.get("output_tokens", usage.get("completion_tokens"))),
        total_tokens=count(usage.get("total_tokens")),
        cache_read_tokens=count(inputs.get("cached_tokens")),
        cache_write_tokens=count(inputs.get("cache_write_tokens")),
        reasoning_tokens=count(outputs.get("reasoning_tokens")),
    )


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
        accounting_usage=_accounting_usage(usage),
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

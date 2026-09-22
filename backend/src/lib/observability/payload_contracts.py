"""Shared reporting contract for model-facing payload size and delivery failures.

Application code owns full datasets; models receive bounded inspections and
compact receipts. When that contract cannot be met (a known-invalid provider
request is blocked, a tool result escapes its budget, or rendered output fails
to reach the curator), producers raise or build a ``PayloadContractViolation``
and report it once through ``report_payload_contract_violation``.

Ordinary pagination, clamping of an oversized page request, and expected user
input errors are not violations and must not be reported here.
"""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any, Literal

from src.lib.observability.runtime import report_runtime_exception
from src.lib.observability.sentry import hash_sentry_identifier

logger = logging.getLogger(__name__)

PayloadContractCategory = Literal[
    "provider_request_blocked",
    "tool_result_budget_escape",
    "output_delivery_failure",
    "contract_serialization_failure",
]


class PayloadContractViolation(RuntimeError):
    """A model-facing payload could not satisfy its configured contract."""

    def __init__(
        self,
        *,
        category: PayloadContractCategory,
        component: str,
        message: str,
        measured: int | None = None,
        unit: str = "characters",
        limit: int | None = None,
        setting: str | None = None,
        field: str | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.component = component
        self.message = message
        self.measured = measured
        self.unit = unit
        self.limit = limit
        self.setting = setting
        self.field = field

    def diagnostic(self) -> dict[str, Any]:
        """Compact, model- and curator-safe description of the violation."""

        return {
            "category": self.category,
            "component": self.component,
            "field": self.field,
            "measured": self.measured,
            "unit": self.unit,
            "limit": self.limit,
            "setting": self.setting,
            "message": self.message,
        }


def report_payload_contract_violation(
    violation: PayloadContractViolation,
    *,
    phase: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    agent: str | None = None,
    tool_name: str | None = None,
    trace_id: str | None = None,
    session_id: str | None = None,
    flow_id: str | None = None,
    correlation: Mapping[str, Any] | None = None,
    level: Literal["error", "warning"] = "error",
) -> bool:
    """Log and capture one violation; return whether Sentry accepted it.

    The structured log is always written so the failure survives Sentry being
    unavailable. Capture is skipped when this violation (or a cause it wraps)
    was already captured, so wrapper and retry boundaries do not duplicate it.
    High-cardinality references go into context, never the grouping
    fingerprint, which stays stable per category and component.
    """

    context: dict[str, Any] = {
        key: value
        for key, value in violation.diagnostic().items()
        if key != "message" and value is not None
    }
    if model:
        context["model"] = model
    if agent:
        context["agent"] = agent
    for key, value in (correlation or {}).items():
        if value is not None:
            context[str(key)] = value

    logger.warning(
        "payload contract violation category=%s component=%s field=%s "
        "measured=%s unit=%s limit=%s setting=%s phase=%s provider=%s model=%s "
        "agent=%s tool=%s trace_id=%s",
        violation.category,
        violation.component,
        violation.field,
        violation.measured,
        violation.unit,
        violation.limit,
        violation.setting,
        phase,
        provider,
        model,
        agent,
        tool_name,
        trace_id,
    )

    tags: dict[str, Any] = {"failure_category": violation.category}
    if phase:
        tags["phase"] = phase
    if provider:
        tags["provider"] = provider
    if tool_name:
        tags["tool_name"] = tool_name
    if trace_id:
        tags["ai_curation.trace.id_hash"] = hash_sentry_identifier(trace_id)
    if session_id:
        tags["ai_curation.chat.session_id_hash"] = hash_sentry_identifier(session_id)
    if flow_id:
        tags["ai_curation.flow.id_hash"] = hash_sentry_identifier(flow_id)

    try:
        captured = report_runtime_exception(
            violation,
            component=f"payload_contract.{violation.component}",
            operation=violation.category,
            tags=tags,
            context=context,
            level=level,
            fingerprint=[
                "payload_contract",
                violation.category,
                violation.component,
            ],
        )
    except Exception as capture_exc:  # reporting must never replace the original failure
        logger.warning("Payload contract violation capture failed: %s", capture_exc)
        captured = False
    if captured:
        setattr(violation, "_ai_curation_sentry_captured", True)
    return captured

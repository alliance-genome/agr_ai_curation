"""Durable measured-request accounting for authenticated conversation runtimes.

Reserve before dispatch (failure prevents an unaccounted request). Completion
failure leaves a visible pending/unknown attempt, never a fabricated zero cost.
No prompts, responses or scientific contents are stored here.
"""
from dataclasses import dataclass
from contextlib import contextmanager
from contextvars import ContextVar
import logging
from uuid import UUID

from src.lib.cost_ledger.facts import RecordedCharge, TokenUsage
from src.lib.cost_ledger.identity import bind_cost_source
from src.lib.cost_ledger.persistence import record_cost_facts
from src.lib.cost_ledger.runtime_context import current_runtime_cost_context
from src.lib.openai_agents.config import (
    get_cost_ledger_deployment_id, get_cost_ledger_runtime_enabled,
    get_cost_ledger_runtime_source_namespace,
)
from src.models.sql.cost_ledger import RuntimeCostRequest
from src.models.sql.database import SessionLocal
from src.lib.observability.runtime import report_runtime_exception, sanitized_runtime_error

logger = logging.getLogger(__name__)

_active_attempt: ContextVar["RuntimeAccountingAttempt | None"] = ContextVar("active_runtime_cost_attempt", default=None)


def _report_failure(operation: str, run_id: str) -> None:
    report_runtime_exception(
        sanitized_runtime_error("Runtime cost accounting unavailable"),
        component="runtime_cost_ledger", operation=operation, context={"run_id": run_id},
    )
    logger.error("Runtime cost accounting unavailable operation=%s", operation,
                 extra={"sentry_skip_event": True})


@contextmanager
def runtime_attempt_scope(attempt):
    token = _active_attempt.set(attempt)
    try:
        yield
    finally:
        _active_attempt.reset(token)


def record_runtime_provider_usage(record) -> None:
    """Enrich the same request from raw routed-provider evidence before SDK loss."""
    attempt = _active_attempt.get()
    if attempt is None:
        return
    billed = record.billed_cost
    attempt.finish(
        usage=record.accounting_usage,
        charge=RecordedCharge(billed.amount, billed.unit, billed.source) if billed else None,
        outcome=record.status,
    )


def finish_runtime_request(attempt, *, raw_usage, provider, sdk_normalized, outcome) -> None:
    from src.lib.openai_agents.provider_usage import _accounting_usage, _as_mapping, _openrouter_billed_cost
    try:
        usage = _accounting_usage(raw_usage, sdk_normalized=sdk_normalized)
        billed = _openrouter_billed_cost(_as_mapping(raw_usage)) if provider == "openrouter" else None
        charge = RecordedCharge(billed.amount, billed.unit, billed.source) if billed else None
    except (TypeError, ValueError):
        _report_failure("usage_unavailable", attempt.run_id)
        return
    attempt.finish(usage=usage, charge=charge, outcome=outcome)


@dataclass(frozen=True)
class RuntimeAccountingAttempt:
    deployment: str
    namespace: str
    owner: str
    attempt_id: UUID
    run_id: str

    def finish(self, *, usage: TokenUsage, charge: RecordedCharge | None, outcome: str) -> None:
        try:
            with SessionLocal() as db:
                record_cost_facts(
                    db, deployment_id=self.deployment, source_namespace=self.namespace,
                    source_system="runtime", source_id=str(self.attempt_id),
                    attempt_id=self.attempt_id, owner_subject=self.owner, usage=usage, charge=charge,
                )
                row = db.get(RuntimeCostRequest, (self.deployment, self.attempt_id))
                if row is None:
                    raise LookupError("Runtime accounting reservation missing")
                row.outcome = outcome
                db.commit()
        except Exception:
            # The model has already executed: preserve its result/error. The
            # committed reservation/facts remain available; no missing value is
            # converted to zero, including after an earlier raw-usage enrichment.
            _report_failure("completion_failed", self.run_id)


def reserve_runtime_request(measurement: dict) -> RuntimeAccountingAttempt | None:
    if not get_cost_ledger_runtime_enabled():
        return None
    context = current_runtime_cost_context()
    if context is None:
        return None
    # Benchmark owns its lease-fenced transaction and service principal. Never
    # create a second writer/owner for a benchmark model request.
    from src.lib.openai_agents.provider_usage import has_provider_invocation_observer
    if has_provider_invocation_observer():
        return None
    deployment, namespace = get_cost_ledger_deployment_id(), get_cost_ledger_runtime_source_namespace()
    if not deployment or not namespace:
        raise RuntimeError("Runtime cost accounting scope is not configured")
    attempt = RuntimeAccountingAttempt(deployment, namespace, context.owner_subject,
                                       UUID(measurement["measurement_id"]), context.run_id)
    with SessionLocal() as db:
        bind_cost_source(
            db, deployment_id=deployment, source_namespace=namespace, source_system="runtime",
            source_id=str(attempt.attempt_id), attempt_id=attempt.attempt_id,
            owner_subject=context.owner_subject,
        )
        db.add(RuntimeCostRequest(
            deployment_id=deployment, attempt_id=attempt.attempt_id,
            session_id=context.session_id, run_id=context.run_id, activity=context.activity,
            workflow_id=context.workflow_id, flow_run_id=context.flow_run_id,
            provider=measurement["provider"], model=measurement.get("model"),
            agent_id=measurement.get("agent_id"), outcome="pending",
        ))
        db.commit()
    return attempt

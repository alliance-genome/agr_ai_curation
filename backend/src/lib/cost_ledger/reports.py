"""Bounded admin projections over canonical attempts, not secondary totals."""
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal

from agr_cost_pricing import ALGORITHM_REVISION
from sqlalchemy import and_, select, text

from src.lib.cost_ledger.decimal_math import add_exact
from src.lib.cost_ledger.pricing import load_snapshot, value_usage
from src.lib.cost_ledger.summary import fold_fact_revisions
from src.lib.openai_agents.config import (
    get_cost_ledger_deployment_id, get_cost_pricing_snapshot_id,
    get_cost_report_max_attempts, get_cost_report_timeout_ms,
)
from src.models.sql.cost_ledger import CostAttempt, CostFactRevision, RuntimeCostRequest


class ReportTooLarge(ValueError):
    pass


def aggregate_requests(requests):
    charges, charge_counts, usage, known, outcomes, unavailable = {}, Counter(), Counter(), Counter(), Counter(), Counter()
    lower = upper = Decimal(0)
    estimated = unknown_charges = inconsistent = 0
    for row in requests:
        outcomes[row["outcome"]] += 1
        inconsistent += row["usage_status"] == "inconsistent"
        for key, value in row["usage"].items():
            if value is not None:
                usage[key] += value
                known[key] += 1
        charge = row["recorded_charge"]
        if charge is None:
            unknown_charges += 1
        else:
            key = (charge["unit"], charge["source"])
            charges[key] = add_exact(charges.get(key, Decimal(0)), Decimal(charge["amount"]))
            charge_counts[key] += 1
        valuation = row["estimate"]
        if "cost" in valuation:
            estimated += 1
            lower = add_exact(lower, Decimal(valuation["cost"]))
            upper = add_exact(upper, Decimal(valuation["estimated_cost_upper"]))
        else:
            unavailable[valuation["estimate_unavailable_reason"]] += 1
    count = len(requests)
    return {"attempt_count": count, "outcomes": dict(outcomes), "inconsistent_usage_attempts": inconsistent,
            "usage": {key: {"known_total": usage[key] if known[key] else None,
                            "known_attempts": known[key], "unknown_attempts": count - known[key]}
                      for key in ("input_tokens", "output_tokens", "total_tokens", "cache_read_tokens", "cache_write_tokens", "reasoning_tokens")},
            "recorded_charges": [{"unit": key[0], "source": key[1], "amount": str(amount), "attempts": charge_counts[key]}
                                 for key, amount in sorted(charges.items())],
            "unknown_charge_attempts": unknown_charges,
            "estimates": {"currency": "USD", "lower": str(lower) if estimated else None,
                          "upper": str(upper) if estimated else None, "priced_attempts": estimated,
                          "unpriced_attempts": count - estimated, "unavailable_reasons": dict(unavailable),
                          "scope": "independent_estimates_not_added_to_recorded_charges"}}


def runtime_report(db, *, start=None, end=None, session_id=None, run_id=None, provider=None,
                   model=None, activity=None, agent_id=None, flow_run_id=None, snapshot_id=None):
    if db.scalar(text("SHOW transaction_read_only")) != "on" or db.scalar(text("SHOW transaction_isolation")) != "repeatable read":
        raise ValueError("Reports require read-only repeatable-read transactions")
    db.execute(text("SELECT set_config('statement_timeout', :timeout, true)"), {"timeout": str(get_cost_report_timeout_ms())})
    deployment = get_cost_ledger_deployment_id()
    if not deployment:
        raise ValueError("Accounting deployment not configured")
    snapshot_id = snapshot_id or get_cost_pricing_snapshot_id() or None
    snapshot = load_snapshot(db, snapshot_id)
    scope = [RuntimeCostRequest.deployment_id == deployment]
    if start is not None:
        scope.append(CostAttempt.created_at >= start)
    if end is not None:
        scope.append(CostAttempt.created_at < end)
    filters = dict(session_id=session_id, run_id=run_id, provider=provider, model=model,
                   activity=activity, agent_id=agent_id, flow_run_id=flow_run_id)
    for key, value in filters.items():
        if value is not None:
            column = getattr(RuntimeCostRequest, key)
            scope.append(column.is_(None) if value == "__unknown__" else column == value)
    joined = and_(RuntimeCostRequest.deployment_id == CostAttempt.deployment_id, RuntimeCostRequest.attempt_id == CostAttempt.id)
    if session_id:
        # Check full session identity even when report filters select one turn.
        owners = db.scalars(select(CostAttempt.owner_subject).join(RuntimeCostRequest, joined).where(
            RuntimeCostRequest.deployment_id == deployment, RuntimeCostRequest.session_id == session_id).distinct()).all()
        if len(owners) > 1:
            raise ValueError("Ambiguous runtime session ownership")
    selected = db.execute(select(RuntimeCostRequest, CostAttempt.created_at, CostAttempt.owner_subject).join(CostAttempt, joined).where(*scope)
                          .order_by(CostAttempt.created_at.desc(), RuntimeCostRequest.attempt_id)
                          .limit(get_cost_report_max_attempts() + 1)).all()
    if len(selected) > get_cost_report_max_attempts():
        raise ReportTooLarge("Report exceeds request limit; narrow the date window or filters")
    owners_by_session = defaultdict(set)
    for row, _, owner in selected:
        owners_by_session[row.session_id].add(owner)
    if any(len(owners) > 1 for owners in owners_by_session.values()):
        raise ValueError("Ambiguous runtime session ownership")
    revisions = defaultdict(list)
    if selected:
        for revision in db.scalars(select(CostFactRevision).where(
            CostFactRevision.deployment_id == deployment,
            CostFactRevision.attempt_id.in_([row.attempt_id for row, _, _ in selected]),
        ).order_by(CostFactRevision.attempt_id, CostFactRevision.revision)):
            revisions[revision.attempt_id].append(revision)
    requests = []
    for row, created, _ in selected:
        usage, charge, revision = fold_fact_revisions(revisions[row.attempt_id])
        requests.append({"attempt_id": str(row.attempt_id), "fact_revision": revision,
                         "created_at": created.isoformat(), "session_id": row.session_id, "run_id": row.run_id,
                         "activity": row.activity, "workflow_id": row.workflow_id, "flow_run_id": row.flow_run_id,
                         "provider": row.provider, "model": row.model, "agent_id": row.agent_id, "outcome": row.outcome,
                         "agent_name": row.agent_name, "agent_role": row.agent_role,
                         "agent_revision": row.agent_revision, "node_id": row.node_id,
                         "requested_service_tier": row.requested_service_tier,
                         "effective_service_tier": row.effective_service_tier,
                         "usage": asdict(usage), "usage_status": usage.status,
                         "recorded_charge": {**asdict(charge), "amount": str(charge.amount)} if charge else None,
                         "estimate": value_usage(usage, provider=row.provider, model=row.model, timestamp=created,
                                                 snapshot=snapshot, effective_service_tier=row.effective_service_tier)})
    groups = defaultdict(list)
    for row in requests:
        groups[(row["session_id"], row["run_id"], row["activity"], row["flow_run_id"])].append(row)
    return {"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
            "deployment_id": deployment, "scope": "time_window" if start is not None else "full_recorded_session_or_turn",
            "filters": {**filters, "start": start.isoformat() if start else None, "end": end.isoformat() if end else None},
            "pricing_snapshot_id": snapshot_id, "valuation_algorithm": ALGORITHM_REVISION,
            "pricing_source": snapshot["source"] if snapshot else None,
            "pricing_captured_at": snapshot["captured_at"] if snapshot else None,
            "coverage": {"history": "since_runtime_accounting_enabled", "included": "ordinary_chat_and_flow_model_attempts",
                         "excluded": ["pre_enablement_history", "benchmark_attempts", "document_processing", "infrastructure", "invoice_reconciliation"],
                         "service_tier": "provider_reported_only_unknown_uses_ranges", "truncated": False},
            "totals": aggregate_requests(requests),
            "runs": [{"session_id": key[0], "run_id": key[1], "activity": key[2], "flow_run_id": key[3],
                      "started_at": min(row["created_at"] for row in rows), **aggregate_requests(rows)} for key, rows in groups.items()],
            "requests": requests}

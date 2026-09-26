"""Admin projections of canonical runtime facts; no secondary money storage."""
from sqlalchemy import and_, func, select, text
from sqlalchemy.orm import Session

from src.lib.cost_ledger.summary import summarize_fact_rows
from src.lib.openai_agents.config import get_cost_ledger_deployment_id
from src.models.sql.cost_ledger import CostAttempt, CostFactRevision, RuntimeCostRequest


def read_runtime_accounting(db: Session, *, session_id: str, run_id: str | None = None) -> dict:
    if db.scalar(text("SHOW transaction_read_only")) != "on" or db.scalar(
        text("SHOW transaction_isolation")
    ) != "repeatable read":
        raise ValueError("Accounting reads require a read-only repeatable-read transaction")
    deployment = get_cost_ledger_deployment_id()
    if not deployment:
        raise ValueError("Runtime accounting deployment is not configured")
    scope = [RuntimeCostRequest.deployment_id == deployment,
             RuntimeCostRequest.session_id == session_id]
    if run_id is not None:
        scope.append(RuntimeCostRequest.run_id == run_id)
    owners = db.scalars(select(CostAttempt.owner_subject).join(
        RuntimeCostRequest, and_(RuntimeCostRequest.deployment_id == CostAttempt.deployment_id,
                                RuntimeCostRequest.attempt_id == CostAttempt.id),
    ).where(*scope).distinct()).all()
    if not owners:
        raise LookupError("No recorded runtime accounting for this session")
    # Administrative access is intentional, but a collided session identity is
    # not an authorized instruction to combine two curators' conversations.
    if len(owners) != 1:
        raise ValueError("Ambiguous runtime session ownership")
    query = select(RuntimeCostRequest.attempt_id, CostFactRevision).outerjoin(
        CostFactRevision, and_(CostFactRevision.deployment_id == RuntimeCostRequest.deployment_id,
                              CostFactRevision.attempt_id == RuntimeCostRequest.attempt_id),
    ).where(*scope).order_by(RuntimeCostRequest.attempt_id, CostFactRevision.revision)
    return {
        "schema_version": 1, "scope": "recorded_runtime_requests",
        "session_id": session_id, "run_id": run_id,
        "valuation": "recorded_charges_only",
        "historical_coverage": "since_runtime_accounting_enabled",
        "outcomes": dict(db.execute(select(RuntimeCostRequest.outcome, func.count()).where(
            *scope).group_by(RuntimeCostRequest.outcome)).all()),
        **summarize_fact_rows(db, query),
    }

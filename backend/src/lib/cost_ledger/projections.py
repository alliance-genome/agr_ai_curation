"""Owner-scoped resolution of pinned references, without copied accounting."""

from sqlalchemy.orm import Session

from src.schemas.cost_ledger import CostFactsProjection, CostLedgerReference
from .persistence import read_cost_facts


def resolve_cost_reference(
    session: Session, *, reference: CostLedgerReference, owner_subject: str,
) -> CostFactsProjection:
    """Resolve exact revision only; no implicit latest or stale-local fallback.

Owner must come from caller authentication and verified execution membership,
not client-supplied metadata. Missing scope/revision raises LookupError. Database
errors propagate: an unavailable ledger must not become a zero/unknown cost.
This internal projection is not an HTTP authorization boundary.
"""
    facts = read_cost_facts(
        session, deployment_id=reference.deployment_id, attempt_id=reference.attempt_id,
        owner_subject=owner_subject, revision=reference.fact_revision,
    )
    return CostFactsProjection(
        schema_version=1, reference=reference, usage=facts.usage,
        usage_status=facts.usage.status, usage_issues=facts.usage.issues,
        recorded_charge=facts.charge,
    )

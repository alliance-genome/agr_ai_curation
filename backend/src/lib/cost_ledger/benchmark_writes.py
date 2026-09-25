"""Benchmark accounting writes inside the repository's lease-fenced transaction."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.lib.openai_agents.config import (
    get_cost_ledger_benchmark_source_namespace, get_cost_ledger_deployment_id,
)
from src.models.sql.cost_ledger import CostSourceReference
from .benchmark_identity import benchmark_attempt_id
from .facts import RecordedCharge, TokenUsage
from .identity import bind_cost_source
from .persistence import CostFacts, record_cost_facts


def _scope() -> tuple[str, str]:
    deployment = get_cost_ledger_deployment_id()
    namespace = get_cost_ledger_benchmark_source_namespace()
    if not deployment or not namespace:
        raise ValueError("Benchmark ledger scope is not configured")
    return deployment, namespace


def reserve_benchmark_accounting(session: Session, *, invocation_id: UUID,
                                 model_request_id: UUID | None, owner_subject: str) -> None:
    deployment, namespace = _scope()
    bind_cost_source(
        session, deployment_id=deployment, source_namespace=namespace,
        source_system="benchmark", source_id=str(invocation_id), owner_subject=owner_subject,
        attempt_id=benchmark_attempt_id(invocation_id=invocation_id, model_request_id=model_request_id,
                                       deployment_id=deployment, source_namespace=namespace),
    )


def complete_benchmark_accounting(session: Session, *, invocation_id: UUID,
                                  owner_subject: str, usage: TokenUsage,
                                  charge: RecordedCharge | None) -> CostFacts:
    deployment, namespace = _scope()
    attempt_id = session.scalar(select(CostSourceReference.attempt_id).where(
        CostSourceReference.deployment_id == deployment,
        CostSourceReference.source_namespace == namespace,
        CostSourceReference.source_system == "benchmark",
        CostSourceReference.source_id == str(invocation_id),
    ))
    if attempt_id is None:
        raise ValueError("Benchmark invocation has no pre-dispatch ledger binding")
    return record_cost_facts(
        session, deployment_id=deployment, source_namespace=namespace,
        source_system="benchmark", source_id=str(invocation_id), attempt_id=attempt_id,
        owner_subject=owner_subject, usage=usage, charge=charge,
    )

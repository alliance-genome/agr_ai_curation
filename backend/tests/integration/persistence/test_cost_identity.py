"""Real PostgreSQL identity, transaction, and concurrent-delivery guarantees."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from src.lib.cost_ledger.identity import CostIdentityConflict, bind_cost_source
from src.models.sql.cost_ledger import CostAttempt, CostSourceReference
from src.models.sql.database import SessionLocal


@pytest.fixture(scope="module", autouse=True)
def migrate():
    command.upgrade(Config(str(Path(__file__).resolve().parents[3] / "alembic.ini")), "head")


@pytest.fixture
def binding():
    return dict(
        deployment_id=uuid4().hex, owner_subject="fixture-owner", attempt_id=uuid4(),
        source_system="benchmark", source_namespace="execution-target", source_id=uuid4().hex,
    )


def counts(db, deployment):
    return tuple(db.scalar(select(func.count()).select_from(model).where(
        model.deployment_id == deployment
    )) for model in (CostAttempt, CostSourceReference))


def test_replay_multiple_sources_and_real_retry(binding):
    with SessionLocal() as db:
        first = bind_cost_source(db, **binding)
        assert bind_cost_source(db, **binding) == first
        assert bind_cost_source(db, **{**binding, "source_system": "telemetry"}) == first
        retry = uuid4()
        assert bind_cost_source(db, **{**binding, "source_id": "retry", "attempt_id": retry}) == retry
        assert counts(db, binding["deployment_id"]) == (2, 3)
        db.rollback()
    with SessionLocal() as db:
        assert counts(db, binding["deployment_id"]) == (0, 0)


def test_conflict_does_not_leave_orphan_or_reassign_owner(binding):
    with SessionLocal() as db:
        bind_cost_source(db, **binding)
        with pytest.raises(CostIdentityConflict):
            bind_cost_source(db, **{**binding, "attempt_id": uuid4()})
        with pytest.raises(CostIdentityConflict):
            bind_cost_source(db, **{**binding, "owner_subject": "another-owner", "source_id": "other"})
        assert counts(db, binding["deployment_id"]) == (1, 1)
        db.commit()
    with SessionLocal() as db:
        assert bind_cost_source(db, **binding) == binding["attempt_id"]
        assert counts(db, binding["deployment_id"]) == (1, 1)


def test_deployment_and_namespace_are_identity_boundaries(binding):
    with SessionLocal() as db:
        bind_cost_source(db, **binding)
        # Even the same request UUID does not merge authenticated deployments.
        bind_cost_source(db, **{**binding, "deployment_id": uuid4().hex, "owner_subject": "other"})
        bind_cost_source(db, **{**binding, "source_namespace": "other-account", "attempt_id": uuid4()})
        assert counts(db, binding["deployment_id"]) == (2, 2)


def test_reference_prevents_accidental_attempt_deletion(binding):
    with SessionLocal() as db:
        bind_cost_source(db, **binding)
        with pytest.raises(IntegrityError):
            with db.begin_nested():
                db.execute(delete(CostAttempt).where(CostAttempt.deployment_id == binding["deployment_id"]))
        assert counts(db, binding["deployment_id"]) == (1, 1)


@pytest.mark.parametrize("conflicting", [False, True])
def test_concurrent_delivery_is_atomic(binding, conflicting):
    barrier = Barrier(2)

    def deliver(identity):
        with SessionLocal() as db:
            barrier.wait(timeout=10)
            try:
                result = bind_cost_source(db, **{**binding, "attempt_id": identity})
            except CostIdentityConflict:
                db.commit()  # Catching a conflict still must not commit an orphan.
                return None
            db.commit()
            return result

    ids = [binding["attempt_id"], uuid4() if conflicting else binding["attempt_id"]]
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(deliver, ids))
    assert outcomes.count(None) == (1 if conflicting else 0)
    with SessionLocal() as db:
        assert counts(db, binding["deployment_id"]) == (1, 1)


@pytest.mark.parametrize("field", ["deployment_id", "owner_subject", "source_system", "source_namespace", "source_id"])
def test_unknown_scope_cannot_be_silently_defaulted(binding, field):
    with SessionLocal() as db:
        with pytest.raises(ValueError):
            bind_cost_source(db, **{**binding, field: " "})
        assert counts(db, binding["deployment_id"]) == (0, 0)

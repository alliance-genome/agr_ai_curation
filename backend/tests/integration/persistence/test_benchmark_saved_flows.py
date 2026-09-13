"""Benchmark discovery uses the real PostgreSQL flow-sharing predicates."""

from uuid import uuid4

from fastapi import HTTPException
import pytest
from sqlalchemy import text

from src.lib.benchmarks.execution_context import BenchmarkCuratorContext
from src.lib.benchmarks.saved_flows import saved_flow_contracts, visible_saved_flows
from src.models.sql import CurationFlow
from tests.integration.persistence.test_flow_sharing import sharing_db  # noqa: F401


def curator(user_id):
    return BenchmarkCuratorContext(
        subject=f"curator-{user_id}", auth_provider="oidc", db_user_id=user_id, active_groups=(),
    )


def test_benchmark_saved_flow_visibility_and_revocation(request):
    db, _ = request.getfixturevalue("sharing_db")
    source = db.query(CurationFlow).one()
    assert [row.id for row in visible_saved_flows(db, curator(7)).all()] == [source.id]
    assert visible_saved_flows(db, curator(99)).count() == 0
    with pytest.raises(HTTPException) as exc:
        saved_flow_contracts(db, curator(99), source.id)
    assert exc.value.status_code == 403

    project = uuid4()
    db.execute(text("INSERT INTO projects VALUES (:id)"), {"id": project})
    db.execute(text("INSERT INTO project_members (project_id, user_id) VALUES (:id, 99)"), {"id": project})
    db.execute(text("UPDATE curation_flows SET visibility='project', project_id=:id, shared_at=now()"), {"id": project})
    db.commit()
    db.expire_all()
    assert [row.id for row in visible_saved_flows(db, curator(99)).all()] == [source.id]
    # An authorized but invalid legacy flow is inspectable, never guessed.
    detail = saved_flow_contracts(db, curator(99), source.id)
    assert detail.status == "not_verified" and detail.nodes == ()
    assert visible_saved_flows(db, curator(100)).count() == 0

    db.execute(text("DELETE FROM project_members WHERE user_id=99"))
    db.commit()
    assert visible_saved_flows(db, curator(99)).count() == 0
    with pytest.raises(HTTPException) as exc:
        saved_flow_contracts(db, curator(99), source.id)
    assert exc.value.status_code == 403
    source.is_active = False
    db.commit()
    assert visible_saved_flows(db, curator(7)).count() == 0
    with pytest.raises(HTTPException) as exc:
        saved_flow_contracts(db, curator(7), source.id)
    assert exc.value.status_code == 404

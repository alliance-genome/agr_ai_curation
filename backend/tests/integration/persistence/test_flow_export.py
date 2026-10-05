"""Main AI Curation exports exactly what a curator can run, and nothing else."""

from datetime import datetime, timezone
from uuid import uuid4

import sqlalchemy as sa

from src.lib.agent_studio.execution_revision_service import append_execution_revision
from src.lib.agent_studio.execution_snapshot import capture_execution_snapshot
from src.lib.flow_transfer import bundle as bundle_module
from src.lib.flow_transfer.bundle import check_bundle
from src.lib.flow_transfer.export import ExportCurator, evaluate_flow, exportable_flows
from src.models.sql.curation_flow import CurationFlow
from src.schemas.agent_execution_revision import AgentOutputContract
from src.schemas.generic_extraction_profile import GenericProfileContract
from .test_agent_execution_revision_persistence import builder_policies, execution_db  # noqa: F401
from .test_generic_profile_persistence import profile_db  # noqa: F401
from .test_retired_model_conversion import make_agent, make_flow, save_on_retired, world  # noqa: F401

ISSUER = "https://ai-curation-dev.alliancegenome.org"
CURATOR_ISS = "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_synthetic"


def curator(user_id, groups=()):
    return ExportCurator(subject=f"sub-{user_id}", issuer=CURATOR_ISS, user_id=user_id,
                         groups=list(groups))


def evaluate(db, flow, user_id=1, groups=()):
    return evaluate_flow(db, flow, curator(user_id, groups), issuer=ISSUER, app_version="0.10.1",
                         exported_at="2026-10-06T10:00:00Z")


def head(db, agent):
    from src.models.sql.agent_execution_revision import AgentExecutionRevision
    return db.get(AgentExecutionRevision, agent.execution_revision_id)


def share(db, *rows):
    project = uuid4()
    db.execute(sa.text("INSERT INTO projects VALUES (:id)"), {"id": project})
    for user_id in (1, 2):
        db.execute(sa.text("INSERT INTO project_members (project_id, user_id) VALUES (:p, :u)"),
                   {"p": project, "u": user_id})
    for row in rows:
        row.visibility, row.project_id = "project", project
        if isinstance(row, CurationFlow):
            row.shared_at = datetime.now(timezone.utc)
    db.flush()


def test_an_own_flow_exports_exactly_its_pinned_revision(world):  # noqa: F811
    db = world
    agent = make_agent(db, "Finder")
    older = head(db, agent)
    from src.lib.agent_studio import custom_agent_service as service
    service.update_custom_agent(db, agent, expected_revision_id=agent.execution_revision_id,
                                custom_prompt="Newer instructions")
    flow = make_flow(db, [(agent, older)])
    evaluated = evaluate(db, flow)
    assert evaluated.reason is None and evaluated.owned
    checked = check_bundle(evaluated.bundle)
    [exported] = checked.bundle.agents
    assert exported.source_agent_id == agent.id and exported.name == "Finder"
    assert [revision.source_revision_id for revision in exported.revisions] == [older.id]
    assert checked.bundle.profiles == [] and checked.bundle.flow.source_version == evaluated.version


def test_a_teammates_shared_flow_is_importable_but_not_owned(world):  # noqa: F811
    db = world
    agent = make_agent(db, "Shared finder")
    flow = make_flow(db, [(agent, head(db, agent))])
    share(db, agent, flow)
    evaluated = evaluate(db, flow, user_id=2)
    assert evaluated.reason is None and not evaluated.owned
    assert check_bundle(evaluated.bundle).bundle.exported_for.sub == "sub-2"


def test_a_step_the_curator_cannot_open_is_refused(world):  # noqa: F811
    db = world
    agent = make_agent(db, "Private finder")
    flow = make_flow(db, [(agent, head(db, agent))])
    share(db, flow)
    assert evaluate(db, flow, user_id=2).reason == "step_unavailable"


def test_a_retired_model_is_refused(world):  # noqa: F811
    db = world
    agent = make_agent(db, "Old finder")
    flow = make_flow(db, [(agent, save_on_retired(db, agent))])
    assert evaluate(db, flow).reason == "model_unavailable"


def test_a_flexible_output_agent_is_refused(world):  # noqa: F811
    db = world
    agent = make_agent(db, "Flexible finder")
    snapshot = capture_execution_snapshot(db, agent, AgentOutputContract(
        output_state="structured_extraction", output_mode="unprofiled_generic"))
    row = append_execution_revision(db, agent, snapshot, user_id=1,
                                    expected_revision_id=agent.execution_revision_id)
    flow = make_flow(db, [(agent, row)])
    assert evaluate(db, flow).reason == "flexible_output"


def test_a_too_large_flow_is_refused(world, monkeypatch):  # noqa: F811
    db = world
    agent = make_agent(db, "Finder")
    flow = make_flow(db, [(agent, head(db, agent))])
    monkeypatch.setattr(bundle_module, "FLOW_BUNDLE_MAX_BYTES", 10)
    assert evaluate(db, flow).reason == "too_large"


def test_a_broken_flow_is_listed_as_cannot_run(world):  # noqa: F811
    db = world
    agent = make_agent(db, "Finder")
    good = make_flow(db, [(agent, head(db, agent))], name="Good")
    broken = CurationFlow(user_id=1, name="Broken", flow_definition={"nodes": []})
    db.add(broken)
    db.flush()
    flows, total = exportable_flows(db, 1, offset=0, limit=50)
    assert total == 2 and [flow.name for flow in flows] == ["Broken", "Good"]
    reasons = {flow.name: evaluate(db, flow).reason for flow in flows}
    assert reasons == {"Broken": "cannot_run", "Good": None}
    assert good.id in {flow.id for flow in flows}


def profile_bound(db, name):
    from src.lib.agent_studio import custom_agent_service as service
    agent = make_agent(db, name)
    service.update_custom_agent(
        db, agent, expected_revision_id=agent.execution_revision_id,
        new_generic_profile=GenericProfileContract.model_validate(
            {"name": "Things", "semantic_class": "thing", "fields": []}))
    return agent


def test_a_pinned_output_structure_exports_its_exact_revision(world, builder_policies):  # noqa: F811
    db = world
    agent = profile_bound(db, "Structured finder")
    row = head(db, agent)
    flow = make_flow(db, [(agent, row)])
    evaluated = evaluate(db, flow)
    assert evaluated.reason is None
    checked = check_bundle(evaluated.bundle)
    ref = checked.snapshots[row.id].output_contract.generic_profile_ref
    [profile] = checked.bundle.profiles
    assert profile.source_profile_id == ref.profile_id
    assert [revision.source_revision_id for revision in profile.revisions] == [ref.profile_revision_id]


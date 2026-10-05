"""The resolver imports a curator's flow as their own private copy (design 8.3 and 15.2)."""

from copy import deepcopy
from uuid import UUID

import pytest
import sqlalchemy as sa
from fastapi import HTTPException
from sqlalchemy.orm.attributes import flag_modified

from src.lib.agent_studio import custom_agent_service as service
from src.lib.agent_studio.execution_revision_service import (append_execution_revision,
                                                             authorize_execution_receipt)
from src.lib.agent_studio.execution_snapshot import capture_execution_snapshot
from src.lib.benchmarks.saved_flows import flow_summary
from src.lib.flow_transfer import importer
from src.lib.flow_transfer.bundle import check_bundle
from src.lib.flow_transfer.export import ExportCurator, evaluate_flow
from src.lib.flow_transfer.ids import derived_agent_key, derived_id
from src.lib.flow_transfer.importer import (ImportContext, ImportRefused, import_dependencies,
                                            import_flow, unchanged_import)
from src.lib.flows.execution_revisions import flow_execution_revision_findings
from src.models.sql import Agent, AgentExecutionRevision, BenchmarkFlowImport, CurationFlow
from src.models.sql.generic_extraction_profile import (GenericExtractionProfile,
                                                       GenericExtractionProfileRevision)
from src.schemas.agent_execution_revision import AgentExecutionSnapshot, AgentOutputContract
from .test_agent_execution_revision_persistence import builder_policies, execution_db  # noqa: F401
from .test_benchmark_flow_import_migration import run_flow_import_migration
from .test_flow_export import profile_bound
from .test_generic_profile_persistence import profile_db  # noqa: F401
from .test_retired_model_conversion import make_agent, make_flow, receipt, world  # noqa: F401

ISSUER = "https://ai-curation-dev.example.org"
CURATOR_ISS = "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_synthetic"


@pytest.fixture
def resolver(world):  # noqa: F811
    run_flow_import_migration(world)
    return world


def head(db, agent):
    return db.get(AgentExecutionRevision, agent.execution_revision_id)


def exported(db, flow, owner=1, groups=()):
    curator = ExportCurator(subject=f"sub-{owner}", issuer=CURATOR_ISS, user_id=owner,
                            groups=list(groups))
    evaluated = evaluate_flow(db, flow, curator, issuer=ISSUER, app_version="0.10.1",
                              exported_at="2026-10-06T10:00:00Z")
    assert evaluated.reason is None
    return check_bundle(evaluated.bundle)


def context(user_id=2, groups=()):
    return ImportContext(user_id=user_id, subject=f"sub-{user_id}", groups=list(groups),
                         export_issuer=ISSUER)


def run_import(db, ctx, checked):
    done = unchanged_import(db, ctx, checked)
    if done is not None:
        return done
    import_dependencies(db, ctx, checked)
    return import_flow(db, ctx, checked)


def count(db, model):
    return db.scalar(sa.select(sa.func.count()).select_from(model))


def copy_of(db, agent, user_id=2):
    return db.scalar(sa.select(Agent).where(Agent.agent_key == derived_agent_key(
        agent.id, export_issuer=ISSUER, importer_sub=f"sub-{user_id}")))


def test_a_first_import_makes_private_copies_and_records_version_1(resolver):
    db = resolver
    agent = make_agent(db, "Finder")
    source = make_flow(db, [(agent, head(db, agent))])
    result = run_import(db, context(), exported(db, source))
    flow_id = derived_id("flow", source.id, export_issuer=ISSUER, importer_sub="sub-2")
    assert (result.outcome, result.version, result.flow_id) == ("imported", 1, flow_id)
    copy = db.get(CurationFlow, flow_id)
    assert (copy.user_id, copy.visibility, copy.name) == (2, "private", "Flow")
    agent_copy = copy_of(db, agent)
    assert (agent_copy.user_id, agent_copy.visibility, agent_copy.name) == (2, "private", "Finder")
    step = copy.flow_definition["nodes"][1]["data"]
    assert step["agent_id"] == agent_copy.agent_key
    assert step["agent_revision_id"] == str(agent_copy.execution_revision_id)
    revision = db.get(AgentExecutionRevision, agent_copy.execution_revision_id)
    assert revision.creator_id == 2 and revision.revision == head(db, agent).revision
    assert flow_execution_revision_findings(db, copy.flow_definition, user_id=2,
                                            active_group_ids=[]) == []
    [record] = db.scalars(sa.select(BenchmarkFlowImport)).all()
    assert (record.user_id, record.version, record.source_flow_id) == (2, 1, source.id)
    assert record.flow_revision == flow_summary(copy).revision
    assert record.pins == [{"node_id": "node_0", "source_agent_revision_id": str(head(db, agent).id),
                            "agent_revision_id": str(revision.id)}]


def test_reimporting_the_same_version_changes_nothing(resolver):
    db = resolver
    agent = make_agent(db, "Finder")
    source = make_flow(db, [(agent, head(db, agent))])
    first = run_import(db, context(), exported(db, source))
    digest = flow_summary(db.get(CurationFlow, first.flow_id)).revision
    before = (count(db, Agent), count(db, AgentExecutionRevision), count(db, BenchmarkFlowImport))
    again = run_import(db, context(), exported(db, source))
    assert (again.outcome, again.version, again.flow_id) == ("unchanged", 1, first.flow_id)
    assert (count(db, Agent), count(db, AgentExecutionRevision), count(db, BenchmarkFlowImport)) == before
    assert flow_summary(db.get(CurationFlow, first.flow_id)).revision == digest


def test_a_concurrent_repeat_of_the_same_version_says_nothing_changed(resolver):
    # Two tabs: both pass the early check, the second waits on the lock, then finds version 1.
    db = resolver
    agent = make_agent(db, "Finder")
    source = make_flow(db, [(agent, head(db, agent))])
    checked, ctx = exported(db, source), context()
    first = run_import(db, ctx, checked)
    import_dependencies(db, ctx, checked)
    again = import_flow(db, ctx, checked)
    assert (again.outcome, again.version, again.flow_id) == ("unchanged", 1, first.flow_id)
    assert count(db, BenchmarkFlowImport) == 1


def test_an_update_changes_the_same_flow_and_keeps_old_revisions(resolver):
    db = resolver
    agent = make_agent(db, "Finder")
    source = make_flow(db, [(agent, head(db, agent))])
    first = run_import(db, context(), exported(db, source))
    old_receipt = deepcopy(db.get(CurationFlow, first.flow_id).flow_definition["nodes"][1]["data"]
                           ["execution_receipt"])
    service.update_custom_agent(db, agent, expected_revision_id=agent.execution_revision_id,
                                custom_prompt="Better instructions")
    newer = head(db, agent)
    definition = deepcopy(source.flow_definition)
    definition["nodes"][1]["data"]["agent_revision_id"] = str(newer.id)
    definition["nodes"][1]["data"]["execution_receipt"] = receipt(agent, newer)
    source.flow_definition = definition
    flag_modified(source, "flow_definition")
    db.flush()
    second = run_import(db, context(), exported(db, source))
    assert (second.outcome, second.version, second.flow_id) == ("updated", 2, first.flow_id)
    assert copy_of(db, agent).execution_revision_id == derived_id(
        "agent_revision", newer.id, export_issuer=ISSUER, importer_sub="sub-2")
    assert db.get(AgentExecutionRevision, UUID(old_receipt["agent_revision_id"])) is not None
    # A run frozen before the update still resolves its old receipt.
    authorize_execution_receipt(db, old_receipt, 2, active_group_ids=[])
    versions = db.scalars(sa.select(BenchmarkFlowImport.version)
                          .order_by(BenchmarkFlowImport.version)).all()
    assert versions == [1, 2]


def test_each_importer_gets_a_separate_copy_and_names_never_collide(resolver):
    db = resolver
    agent = make_agent(db, "Finder")
    source = make_flow(db, [(agent, head(db, agent))])
    checked = exported(db, source)
    teammate = run_import(db, context(user_id=2), checked)
    owner = run_import(db, context(user_id=1), checked)
    assert teammate.flow_id != owner.flow_id
    assert db.get(CurationFlow, owner.flow_id).name == "Flow (Copy)"
    assert copy_of(db, agent, user_id=1).name == "Finder (Copy)"
    assert db.get(CurationFlow, teammate.flow_id).name == "Flow"


def test_existing_names_of_the_importer_get_copy_names(resolver):
    db = resolver
    mine = make_agent(db, "Finder", user_id=2)
    make_flow(db, [(mine, head(db, mine))], user_id=2, name="Flow")
    agent = make_agent(db, "Finder")
    source = make_flow(db, [(agent, head(db, agent))])
    result = run_import(db, context(), exported(db, source))
    assert db.get(CurationFlow, result.flow_id).name == "Flow (Copy)"
    assert copy_of(db, agent).name == "Finder (Copy)"


def test_a_refused_save_changes_no_flow_and_a_retry_reuses_the_copies(resolver, monkeypatch):
    db = resolver
    agent = make_agent(db, "Finder")
    source = make_flow(db, [(agent, head(db, agent))])
    checked, ctx = exported(db, source), context()
    import_dependencies(db, ctx, checked)
    agents, revisions = count(db, Agent), count(db, AgentExecutionRevision)
    original = importer.save_flow_definition

    def refuse(*args, **kwargs):
        raise HTTPException(422, {"findings": [{"code": "unavailable_model", "severity": "error"}]})

    monkeypatch.setattr(importer, "save_flow_definition", refuse)
    savepoint = db.begin_nested()
    with pytest.raises(ImportRefused) as refused:
        import_flow(db, ctx, checked)
    savepoint.rollback()
    assert refused.value.reason == "model_unavailable"
    flow_id = derived_id("flow", source.id, export_issuer=ISSUER, importer_sub="sub-2")
    assert db.get(CurationFlow, flow_id) is None and count(db, BenchmarkFlowImport) == 0
    monkeypatch.setattr(importer, "save_flow_definition", original)
    assert run_import(db, ctx, checked).outcome == "imported"
    assert (count(db, Agent), count(db, AgentExecutionRevision)) == (agents, revisions)


def test_layouts_move_with_the_exported_receipts(resolver, monkeypatch):
    db = resolver
    agent = make_agent(db, "Finder")
    checked = exported(db, make_flow(db, [(agent, head(db, agent))], name="First"))
    seen = {}

    def spy(definition, catalogs, old_receipts):
        seen.update(old_receipts)
        return []

    monkeypatch.setattr(importer, "move_layouts", spy)
    run_import(db, context(), checked)
    assert seen == checked.receipts

    def stale(definition, catalogs, old_receipts):
        raise ValueError("stale layout")

    monkeypatch.setattr(importer, "move_layouts", stale)
    other = exported(db, make_flow(db, [(agent, head(db, agent))], name="Second"))
    with pytest.raises(ImportRefused) as refused:
        run_import(db, context(), other)
    assert refused.value.reason == "fields_need_choosing"


def test_a_group_restricted_step_is_refused_outside_the_group(resolver):
    db = resolver
    agent = make_agent(db, "Group finder")
    agent.allowed_group_ids = ["FB"]
    snapshot = capture_execution_snapshot(db, agent, AgentOutputContract(output_state="none"))
    row = append_execution_revision(db, agent, snapshot, user_id=1,
                                    expected_revision_id=agent.execution_revision_id)
    checked = exported(db, make_flow(db, [(agent, row)]), groups=("FB",))
    with pytest.raises(ImportRefused) as refused:
        run_import(db, context(groups=()), checked)
    assert refused.value.reason == "step_unavailable"


def test_a_pinned_output_structure_gets_a_private_copy(resolver, builder_policies):  # noqa: F811
    db = resolver
    agent = profile_bound(db, "Structured finder")
    source = make_flow(db, [(agent, head(db, agent))])
    checked = exported(db, source)
    ref = checked.snapshots[head(db, agent).id].output_contract.generic_profile_ref
    run_import(db, context(), checked)
    profile_id = derived_id("profile", ref.profile_id, export_issuer=ISSUER, importer_sub="sub-2")
    revision_id = derived_id("profile_revision", ref.profile_revision_id, export_issuer=ISSUER,
                             importer_sub="sub-2")
    profile = db.get(GenericExtractionProfile, profile_id)
    assert (profile.owner_id, profile.visibility, profile.head_revision) == (2, "private", ref.revision)
    copied = db.get(GenericExtractionProfileRevision, revision_id)
    assert (copied.creator_id, copied.fingerprint) == (2, ref.fingerprint)
    pinned = AgentExecutionSnapshot.model_validate(
        head(db, copy_of(db, agent)).snapshot).output_contract.generic_profile_ref
    assert (pinned.profile_id, pinned.profile_revision_id) == (profile_id, revision_id)
    [record] = db.scalars(sa.select(BenchmarkFlowImport)).all()
    assert record.pins[0]["profile_revision_id"] == str(revision_id)
    assert run_import(db, context(), exported(db, source)).outcome == "unchanged"

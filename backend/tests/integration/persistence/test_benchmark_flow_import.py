"""The resolver imports a curator's flow as their own private copy (design 8.3 and 15.2)."""

from copy import deepcopy
from dataclasses import replace
from uuid import UUID

import pytest
import sqlalchemy as sa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import HTTPException
from pydantic import create_model
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.orm import Session

from src.api import benchmark_flow_imports as api
from src.lib.agent_studio import custom_agent_service as service
from src.lib.agent_studio import runtime_validation
from src.lib.benchmarks.execution_context import BenchmarkCuratorContext
from src.lib.config.models_loader import get_model
from src.lib.config import schema_discovery
from src.lib.agent_studio.execution_revision_service import (append_execution_revision,
                                                             authorize_execution_receipt)
from src.lib.agent_studio.execution_snapshot import capture_execution_snapshot
from src.lib.benchmarks.saved_flows import flow_summary
from src.lib.flow_transfer import importer
from src.lib.flow_transfer.bundle import check_bundle
from src.lib.flow_transfer.config import FlowImportConfig
from src.lib.flow_transfer.export import ExportCurator, evaluate_flow
from src.lib.flow_transfer.ids import derived_agent_key, derived_id
from src.lib.flow_transfer.importer import (ImportContext, ImportRefused, import_dependencies,
                                            import_flow, unchanged_import)
from src.lib.flows.execution_revisions import (flow_execution_revision_findings,
                                               resolve_flow_execution_revisions)
from src.lib.flows import execution_revisions
from src.models.sql import database
from src.models.sql import Agent, AgentExecutionRevision, BenchmarkFlowImport, CurationFlow
from src.models.sql.generic_extraction_profile import (GenericExtractionProfile,
                                                       GenericExtractionProfileRevision)
from src.schemas.agent_execution_revision import AgentExecutionSnapshot, AgentOutputContract
from src.schemas.flows import FlowDefinition
from src.schemas.generic_extraction_profile import GenericProfileContract
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
    assert evaluated.bundle is not None
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


@pytest.fixture
def committed_import(resolver, monkeypatch):
    """Run the endpoint's real phase commits in fresh sessions, isolated by the fixture."""
    connection = resolver.get_bind()
    commits = []

    def sessions():
        session = Session(bind=connection, join_transaction_mode="create_savepoint")
        sa.event.listen(session, "after_commit", lambda _: commits.append(True))
        return session

    monkeypatch.setattr(api, "SessionLocal", sessions)
    monkeypatch.setattr(database, "SessionLocal", sessions)
    monkeypatch.setattr(runtime_validation, "SessionLocal", sessions)
    monkeypatch.setattr(runtime_validation, "_load_expected_system_agent_keys", lambda: (set(), None))
    curator = BenchmarkCuratorContext(subject="sub-2", auth_provider="oidc", auth_issuer=CURATOR_ISS,
                                     db_user_id=2, active_groups=())
    config = FlowImportConfig(public_key=Ed25519PrivateKey.generate().public_key(), issuer=ISSUER)

    def run(checked):
        resolver.commit()
        commits.clear()
        return api._import(curator, config, checked)

    return run, sessions, commits


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("incompatible,reason", [
    ("model", "model_unavailable"), ("reasoning", "model_unavailable"), ("schema", "cannot_run"),
])
def test_destination_incompatibility_refuses_before_dependency_commit(
    resolver, committed_import, monkeypatch, existing, incompatible, reason,
):
    db = resolver
    run, sessions, commits = committed_import
    agent = make_agent(db, "Source-only configuration")
    source = make_flow(db, [(agent, head(db, agent))])
    first = run(exported(db, source)) if existing else None
    if first is not None:
        assert (first.outcome, first.version) == ("imported", 1)
        assert len(commits) == 2

    # Export is valid in the source catalog; the resolver lacks this configuration.
    saved = capture_execution_snapshot(db, agent, AgentOutputContract(output_state="none"))
    changes = {
        "model": {"model_id": "source-only-model"},
        "reasoning": {"model_reasoning": "minimal"},
        "schema": {"output_contract": AgentOutputContract(
            output_state="structured_extraction", output_mode="domain",
            output_schema_key="SourceOnlyEnvelope")},
    }
    saved = saved.model_copy(update=changes[incompatible])
    newer = append_execution_revision(db, agent, saved, user_id=1,
                                      expected_revision_id=agent.execution_revision_id)
    agent.model_id = "gpt-6.1-sol"  # Source rows are fixture scaffolding, not resolver copies.
    agent.output_schema_key = None
    definition = deepcopy(source.flow_definition)
    definition["nodes"][1]["data"]["agent_revision_id"] = str(newer.id)
    definition["nodes"][1]["data"]["execution_receipt"] = receipt(agent, newer)
    source.flow_definition = definition
    db.flush()
    destination_model = get_model("gpt-6.1-sol")
    assert destination_model is not None
    with monkeypatch.context() as source_catalog:
        source_model = replace(destination_model, reasoning_options=["minimal", "low", "medium", "high"])
        source_catalog.setattr(execution_revisions, "get_model", lambda _: source_model)
        resolve_schema = schema_discovery.resolve_output_schema
        source_schema = create_model("SourceOnlyEnvelope", value=(str, ...))
        source_catalog.setattr(schema_discovery, "resolve_output_schema", lambda key: (
            source_schema if key == "SourceOnlyEnvelope" else resolve_schema(key)))
        checked = exported(db, source)
    db.commit()
    with sessions() as session:
        before = (count(session, Agent), count(session, AgentExecutionRevision),
                  count(session, BenchmarkFlowImport), count(session, CurationFlow))
        copy = copy_of(session, agent)
        old_head = copy.execution_revision_id if copy else None

    refused = run(checked)

    assert (refused.outcome, refused.reason) == ("refused", reason)
    assert commits == []
    assert (refused.flow_id, refused.version) == ((first.flow_id, 1) if first is not None else (None, None))
    with sessions() as session:
        assert (count(session, Agent), count(session, AgentExecutionRevision),
                count(session, BenchmarkFlowImport), count(session, CurationFlow)) == before
        copy = copy_of(session, agent)
        if existing:
            assert first is not None
            assert copy.is_active and copy.model_id == "gpt-6.1-sol"
            assert copy.execution_revision_id == old_head
            flow = session.get(CurationFlow, first.flow_id)
            assert flow_execution_revision_findings(session, flow.flow_definition, user_id=2,
                                                    active_group_ids=[]) == []
        else:
            assert copy is None
    report = runtime_validation.validate_and_cache_agent_runtime_contracts(strict_mode=False)
    assert report["errors"] == []


@pytest.mark.parametrize("active", [False, True])
def test_compatible_bundle_cannot_promote_an_incompatible_existing_head(
    resolver, committed_import, active,
):
    db = resolver
    run, sessions, commits = committed_import
    agent = make_agent(db, "Finder")
    source = make_flow(db, [(agent, head(db, agent))])
    first = run(exported(db, source))
    assert first.outcome == "imported" and len(commits) == 2

    # The local copy has newer history than the imported pin. Import selects that
    # history as its active head, even though the incoming snapshot is compatible.
    copy = copy_of(db, agent)
    snapshot = AgentExecutionSnapshot.model_validate(head(db, copy).snapshot)
    newer = append_execution_revision(
        db, copy, snapshot.model_copy(update={"model_id": "source-only-model"}),
        user_id=2, expected_revision_id=copy.execution_revision_id)
    copy.model_id = "gpt-6.1-sol"
    copy.is_active = active
    copy.description = "Keep local metadata on refusal"
    new_head = newer.id
    source.description = "New source version with the original compatible pin"
    checked = exported(db, source)
    db.commit()
    assert runtime_validation.validate_and_cache_agent_runtime_contracts(strict_mode=False)["errors"] == []

    refused = run(checked)

    assert (refused.outcome, refused.reason, refused.flow_id, refused.version) == (
        "refused", "model_unavailable", first.flow_id, 1)
    assert commits == []
    with sessions() as session:
        copy = copy_of(session, agent)
        assert copy.is_active == active and copy.model_id == "gpt-6.1-sol"
        assert copy.execution_revision_id == new_head
        assert copy.description == "Keep local metadata on refusal"
        assert count(session, BenchmarkFlowImport) == 1
    assert runtime_validation.validate_and_cache_agent_runtime_contracts(strict_mode=False)["errors"] == []


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


def test_an_update_changes_the_same_flow_and_keeps_old_revisions(resolver, committed_import):
    db = resolver
    run, _, commits = committed_import
    agent = make_agent(db, "Finder")
    source = make_flow(db, [(agent, head(db, agent))])
    first = run(exported(db, source))
    assert len(commits) == 2
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
    second = run(exported(db, source))
    assert len(commits) == 2
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


def _with_selected_fields_output(db, source, source_node="node_0"):
    """Add a JSON file output step whose layout copies a field current for ``source_node``."""
    definition = deepcopy(source.flow_definition)
    plan = {"format": "json", "row_source": "object", "row_strategy": "wide_union",
            "selection_mode": "selected_fields", "missing_value": None,
            "selected_sources": [{"node_id": source_node, "schema_fingerprint": "sha256:unset"}],
            "columns": [{"key": "Name in article", "field_ref": "object.attribute.paper_name",
                         "source_node_id": source_node}]}
    definition["nodes"].append({
        "id": "output", "type": "output", "position": {"x": 300, "y": 0},
        "data": {"agent_id": "json_formatter", "agent_display_name": "JSON File Formatter",
                 "output_key": "export", "projection_plan": plan}})
    definition["edges"].append({"id": "e_out", "source": source_node, "target": "output",
                                "role": "output_attachment"})
    # Choosing the fields records the source's current catalog fingerprint.
    resolved = resolve_flow_execution_revisions(
        db, FlowDefinition.model_validate(definition), user_id=source.user_id, active_group_ids=[])
    catalog = resolved.projection_catalogs[source_node]
    plan["selected_sources"][0]["schema_fingerprint"] = catalog["schema_fingerprint"]
    source.flow_definition = definition
    flag_modified(source, "flow_definition")
    db.flush()
    assert flow_execution_revision_findings(db, source.flow_definition, user_id=source.user_id,
                                            active_group_ids=[]) == []
    return catalog


def test_a_selected_fields_layout_follows_the_copied_step(resolver, builder_policies):  # noqa: F811
    db = resolver
    agent = make_agent(db, "Structured finder")
    service.update_custom_agent(
        db, agent, expected_revision_id=agent.execution_revision_id,
        new_generic_profile=GenericProfileContract.model_validate({
            "name": "Things", "semantic_class": "thing", "fields": [{
                "key": "paper_name", "required": True, "nullable": True,
                "source_labels": ["Name in article"], "value_schema": {"kind": "string"}}]}))
    source = make_flow(db, [(agent, head(db, agent))])
    before = _with_selected_fields_output(db, source)

    result = run_import(db, context(), exported(db, source))

    assert result.outcome == "imported"
    copy = db.get(CurationFlow, result.flow_id)
    resolved = resolve_flow_execution_revisions(
        db, FlowDefinition.model_validate(copy.flow_definition), user_id=2, active_group_ids=[])
    current = resolved.projection_catalogs["node_0"]["schema_fingerprint"]
    output = next(node for node in copy.flow_definition["nodes"] if node["id"] == "output")
    [selected] = output["data"]["projection_plan"]["selected_sources"]
    assert selected["schema_fingerprint"] == current != before["schema_fingerprint"]
    assert flow_execution_revision_findings(db, copy.flow_definition, user_id=2,
                                            active_group_ids=[]) == []

"""Consumer impact reads real immutable pins and filters before pagination."""

import pytest
from sqlalchemy import select, text

from src.lib.agent_studio import custom_agent_service, generic_profile_service
from src.lib.agent_studio import profile_consumers as service
from src.lib.agent_studio.execution_revision_service import append_execution_revision
from src.lib.agent_studio.execution_snapshot import capture_execution_snapshot
from src.models.sql.agent import Agent
from src.models.sql.curation_flow import CurationFlow
from src.models.sql.curation_flow_agent_revision import CurationFlowAgentRevision
from src.schemas.agent_execution_revision import AgentOutputContract
from .test_agent_execution_revision_persistence import execution_db  # noqa: F401
from .test_generic_profile_persistence import profile_db  # noqa: F401


@pytest.fixture
def consumers_db(execution_db, monkeypatch):  # noqa: F811
    db, agent_id, other_id, profile = execution_db
    monkeypatch.setattr(custom_agent_service, "_system_managed_tool_ids", lambda *_: [])
    monkeypatch.setattr(service, "get_project_ids_for_user", lambda *_: [])
    CurationFlow.__table__.create(db.connection())
    CurationFlowAgentRevision.__table__.create(db.connection())
    agent = db.get(Agent, agent_id)
    other = db.get(Agent, other_id)
    other.name = "Hidden agent"
    other.user_id = 2

    def save(head, revision, groups=()):
        head.allowed_group_ids = list(groups)
        head.inherited_allowed_group_ids = list(groups)
        output = AgentOutputContract(output_state="structured_extraction", output_mode="profile_bound_generic",
            generic_profile_ref={"profile_id": revision.profile_id, "profile_revision_id": revision.id,
                                 "revision": revision.revision, "fingerprint": revision.fingerprint})
        saved = capture_execution_snapshot(db, head, output)
        return append_execution_revision(db, head, saved, user_id=head.user_id,
                                         expected_revision_id=head.execution_revision_id)

    first = save(agent, profile)
    # The other curator could have saved this pin before visibility changed.
    other.user_id = 1
    hidden = save(other, profile)
    other.user_id = 2
    _, second_profile, _ = generic_profile_service.revise_profile(
        db, profile.profile_id, 1, {**profile.contract, "description": "New revision"}, expected_revision=1,
    )
    second = save(agent, second_profile, ["FB"])
    flows = [CurationFlow(user_id=owner, name=name, flow_definition={"nodes": [], "edges": []})
             for owner, name in [(1, "Older pin"), (2, "Hidden flow"), (1, "Restricted pin")]]
    db.add_all(flows)
    db.flush()
    for flow, revision in zip(flows, [first, hidden, second]):
        db.add(CurationFlowAgentRevision(flow_id=flow.id, node_id="extract", agent_revision_id=revision.id))
    db.flush()
    return db, agent, first, second, profile, flows


def test_consumer_history_flow_pins_and_group_filtering(consumers_db):
    db, agent, first, second, profile, flows = consumers_db
    before = [(node.flow_id, node.agent_revision_id) for node in db.scalars(select(CurationFlowAgentRevision))]
    page = service.list_profile_consumers(db, profile.profile_id, 1, active_group_ids=[])
    assert page.head_revision == 2 and page.next_cursor is None
    assert [(row.kind, row.agent_revision_id, row.profile_revision) for row in page.consumers] == [
        ("agent", first.id, 1), ("flow", first.id, 1),
    ]
    assert all(not row.is_current_agent_revision for row in page.consumers)
    visible = service.list_profile_consumers(db, profile.profile_id, 1, active_group_ids=["FB"])
    assert len(visible.consumers) == 4
    assert {row.name for row in visible.consumers} == {agent.name, "Older pin", "Restricted pin"}
    assert any(row.is_current_agent_revision and row.agent_revision_id == second.id for row in visible.consumers)
    assert before == [(node.flow_id, node.agent_revision_id) for node in db.scalars(select(CurationFlowAgentRevision))]
    assert agent.execution_revision_id == second.id
    assert not db.new and not db.dirty


def test_consumer_pagination_only_counts_authorized_rows(consumers_db, monkeypatch):
    db, _, first, _, profile, _ = consumers_db
    monkeypatch.setenv("GENERIC_PROFILE_LIST_PAGE_SIZE", "1")
    page = service.list_profile_consumers(db, profile.profile_id, 1, active_group_ids=[])
    assert len(page.consumers) == 1 and page.next_cursor == page.consumers[0].key
    last = service.list_profile_consumers(db, profile.profile_id, 1, active_group_ids=[], after=page.next_cursor)
    assert len(last.consumers) == 1 and last.consumers[0].kind == "flow"
    assert last.consumers[0].agent_revision_id == first.id and last.next_cursor is None


def test_archived_profiles_and_consumers_remain_inspectable(consumers_db):
    db, agent, _, _, profile, flows = consumers_db
    agent.is_active = False
    flows[0].is_active = False
    generic_profile_service.archive_profile(db, profile.profile_id, 1, expected_revision=2)
    db.flush()
    page = service.list_profile_consumers(db, profile.profile_id, 1, active_group_ids=[])
    assert len(page.consumers) == 2 and all(row.archived for row in page.consumers)
    with pytest.raises(generic_profile_service.ProfileNotFoundError):
        service.list_profile_consumers(db, profile.profile_id, 3, active_group_ids=["FB"])


def test_project_membership_is_required_even_for_a_flow_owner(consumers_db, monkeypatch):
    from src.lib.agent_studio import execution_revision_service

    db, agent, _, _, profile, _ = consumers_db
    project_id = db.execute(text("SELECT id FROM projects")).scalar_one()
    agent.visibility = "project"
    agent.project_id = project_id
    db.flush()
    assert service.list_profile_consumers(db, profile.profile_id, 1, active_group_ids=[]).consumers == []
    monkeypatch.setattr(service, "get_project_ids_for_user", lambda *_: [project_id])
    monkeypatch.setattr(execution_revision_service, "get_project_ids_for_user", lambda *_: [project_id])
    assert len(service.list_profile_consumers(db, profile.profile_id, 1, active_group_ids=[]).consumers) == 2

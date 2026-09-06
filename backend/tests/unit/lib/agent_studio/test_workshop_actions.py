"""Chat actions authorize a destination but never edit or save by themselves."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.lib.agent_studio import workshop_actions as actions
from src.lib.agent_studio.capability_catalog import CapabilityRecord


def context(tab="flows", nodes=None):
    return SimpleNamespace(active_tab=tab, flow_id="flow-1", flow_draft_fingerprint="flow-fingerprint",
        flow_definition=SimpleNamespace(nodes=nodes if nodes is not None else [node("first")]),
        agent_workshop=SimpleNamespace(draft_fingerprint="workshop-fingerprint") if tab == "agent_workshop" else None)


def node(identity):
    return SimpleNamespace(id=identity, agent_id="ca_reader", agent_revision_id="old-revision")


@pytest.fixture
def catalog(monkeypatch):
    records = [CapabilityRecord(kind="agent", resource_id="ca_reader", name="Stock reader", description="",
        authorization_scope="owned", detail={"updated_at": "now", "identity_contract": {"agent_revision_id": "current-revision"}}),
        CapabilityRecord(kind="agent", resource_id="pdf_reader", name="PDF reader", description="", authorization_scope="system")]
    build = MagicMock(return_value=records)
    monkeypatch.setattr(actions, "build_authorized_capability_catalog", build)
    return build


def prepare(request, ctx=None):
    db = MagicMock()
    result = actions.prepare_workshop_action(db, context=ctx or context(), user_id=7,
        active_group_ids=["TEAM"], request=actions.WorkshopActionRequest.model_validate(request))
    db.add.assert_not_called()
    db.commit.assert_not_called()
    return result


def test_open_from_flow_preserves_exact_origin_and_explains_current_head(catalog):
    result = prepare({"action": "open_agent", "agent_id": "ca_reader", "node_id": "first"})
    assert result["source"]["agent_revision_id"] == "current-revision"
    assert result["origin"] == {"flow_id": "flow-1", "flow_draft_fingerprint": "flow-fingerprint",
                                "node_id": "first", "agent_id": "ca_reader", "agent_revision_id": "old-revision"}
    assert result["saved"] is False
    assert result["label"] == "Open Stock reader"
    assert catalog.call_args.kwargs["context"].user_id == 7
    assert catalog.call_args.kwargs["context"].active_group_ids == ("TEAM",)


def test_multiple_uses_require_an_exact_step(catalog):
    ctx = context(nodes=[node("first"), node("second")])
    with pytest.raises(ValueError, match="exact flow step"):
        prepare({"action": "open_agent", "agent_id": "ca_reader"}, ctx)
    result = prepare({"action": "open_agent", "agent_id": "ca_reader", "node_id": "second"}, ctx)
    assert result["origin"]["node_id"] == "second"


@pytest.mark.parametrize("scope", ["shared", "system"])
def test_open_cannot_edit_another_owners_agent(catalog, scope):
    catalog.return_value = [CapabilityRecord(kind="agent", resource_id="ca_reader", name="Shared", description="", authorization_scope=scope)]
    with pytest.raises(ValueError, match="your own custom agents"):
        prepare({"action": "open_agent", "agent_id": "ca_reader"})


def test_revoked_agent_cannot_be_opened(catalog):
    catalog.return_value = []
    with pytest.raises(ValueError, match="authorized catalog"):
        prepare({"action": "open_agent", "agent_id": "ca_reader"})


@pytest.mark.parametrize("mode,agent", [("scratch", None), ("template", "pdf_reader"), ("clone", "ca_reader")])
def test_new_agent_opens_a_draft_without_replacing_a_flow_node(catalog, mode, agent):
    request = {"action": "new_agent", "mode": mode}
    if agent: request["agent_id"] = agent
    result = prepare(request)
    assert result["saved"] is False
    assert "node_id" not in result["origin"]


@pytest.mark.parametrize("action_input", [
    {"action": "save"}, {"action": "save_as"}, {"action": "return_to_flow"},
    {"action": "show_section", "section": "versions"},
    {"action": "show_section", "section": "output_structure"},
])
def test_current_editor_actions_require_workshop_context(action_input):
    with pytest.raises(ValueError, match="Workshop draft first"):
        prepare(action_input)
    assert prepare(action_input, context("agent_workshop"))["saved"] is False

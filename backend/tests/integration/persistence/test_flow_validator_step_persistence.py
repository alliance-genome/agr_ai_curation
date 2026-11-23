"""A curator's own copy of a validator as an ordinary flow step (ALL-1648, shape C).

The copy is a real saved agent with a real pinned execution revision whose
output schema is a validator result, in PostgreSQL. The save validator, open
path and refusal text are the real ones; only the system agents' database
metadata lookup is replaced by their packaged registry entries (this schema has
no system agent rows).
"""

from __future__ import annotations

import importlib
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.lib.agent_studio.catalog_service import AGENT_REGISTRY
from src.lib.agent_studio.execution_revision_service import current_execution_receipt
from src.models.sql.agent import Agent
from src.schemas.agent_execution_revision import initial_output_contract
from src.schemas.flows import FlowDefinition
from .test_flow_revision_persistence import add_revision, flow_db, migrate  # noqa: F401
from .test_agent_execution_revision_persistence import execution_db  # noqa: F401
from .test_generic_profile_persistence import profile_db  # noqa: F401

flows = importlib.import_module("src.api.flows")


@pytest.fixture
def system_agent_rows(monkeypatch):
    def metadata(agent_id, **_kwargs):
        entry = AGENT_REGISTRY.get(agent_id)
        if entry is None:
            raise ValueError(f"Agent '{agent_id}' is not an active unified agent visible to this user.")
        return {"display_name": entry["name"], "category": entry.get("category"),
                "subcategory": entry.get("subcategory"), "output_schema_key": entry.get("output_schema_key"),
                "supervisor": entry.get("supervisor") or {}, "curation": entry.get("curation"),
                "is_active": True, "visible": True, "visibility": "system"}

    monkeypatch.setattr(flows, "get_active_visible_agent_metadata", metadata)


def _node(node_id, agent_id, name, node_type="agent", **data):
    return {"id": node_id, "type": node_type, "position": {"x": 0, "y": 0},
            "data": {"agent_id": agent_id, "agent_display_name": name, "output_key": f"{node_id}_output",
                     **data}}


@pytest.mark.parametrize("template_source,offers_custom_detail", [
    ("allele_validation", True), ("controlled_vocabulary_validation", False),
])
def test_a_curators_validator_copy_is_refused_marked_and_removable(
    flow_db, system_agent_rows, template_source, offers_custom_detail,  # noqa: F811
):
    db, agent, _first, _profile = flow_db
    migrate(db)
    agent.name = "My allele check"
    agent.template_source = template_source
    add_revision(db, agent, initial_output_contract("AlleleResultEnvelope"))
    receipt = current_execution_receipt(db, agent.agent_key, 1, active_group_ids=[])
    copy = _node("val_1", agent.agent_key, "My allele check",
                 agent_revision_id=str(receipt.agent_revision_id),
                 execution_receipt=receipt.model_dump(mode="json"))
    shape_c = {
        "version": "1.1", "entry_node_id": "task_1",
        "nodes": [_node("task_1", "task_input", "Initial Instructions", "task_input",
                        task_instructions="Find the alleles in this paper"),
                  _node("pdf_1", "pdf_extraction", "General PDF Extraction Agent"), copy,
                  _node("out_1", "chat_output", "Chat Output Agent", "output")],
        "edges": [{"id": "e1", "source": "task_1", "target": "pdf_1"},
                  {"id": "e2", "source": "pdf_1", "target": "val_1"},
                  {"id": "o1", "source": "val_1", "target": "out_1", "role": "output_attachment"}],
    }

    def save(payload):
        return flows._validated_flow_definition_payload(
            FlowDefinition.model_validate(deepcopy(payload)), db_user_id=1,
            enforce_agent_references=True, enforce_agent_step_policy=True, active_group_ids=[], db=db,
        )

    now = datetime.now(timezone.utc)
    stored = SimpleNamespace(id=uuid4(), user_id=1, visibility="private", project_id=None, shared_at=None,
                             name="Saved flow", description=None, flow_definition=deepcopy(shape_c),
                             execution_count=0, last_executed_at=None, created_at=now, updated_at=now)
    opened = flows._flow_to_response(stored, viewer_user_id=1, active_group_ids=[], db=db)
    marked = [w for w in opened.validation_warnings if w.code == "custom_validator_in_control_flow"]
    assert [w.node_id for w in marked] == ["val_1"]
    assert marked[0].message.startswith(
        "My allele check is your copy of a validator, and a copy of a validator cannot run in a flow."
    )
    assert ("Custom Output Structure" in marked[0].message) is offers_custom_detail

    with pytest.raises(HTTPException) as refused:
        save(shape_c)
    codes = {(f["code"], f.get("node_id")) for f in refused.value.detail["findings"]}
    assert ("custom_validator_in_control_flow", "val_1") in codes

    repaired = deepcopy(shape_c)
    repaired["nodes"] = [node for node in repaired["nodes"] if node["id"] != "val_1"]
    repaired["edges"] = [{"id": "e1", "source": "task_1", "target": "pdf_1"},
                         {"id": "o1", "source": "pdf_1", "target": "out_1", "role": "output_attachment"}]
    saved = save(repaired)
    assert agent.agent_key not in {node["data"]["agent_id"] for node in saved["nodes"]}
    assert isinstance(db.get(Agent, agent.id), Agent)

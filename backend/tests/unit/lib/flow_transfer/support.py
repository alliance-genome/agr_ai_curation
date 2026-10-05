"""Small valid flow-export payloads for unit tests: no database, no network, no real keys."""

import hashlib
from uuid import UUID, uuid4

from src.lib.prompts.assembly import _bundle, _make_layer
from src.schemas.agent_execution_revision import AgentExecutionSnapshot
from src.schemas.generic_extraction_profile import normalize_profile_contract

ISSUER = "https://ai-curation-dev.alliancegenome.org"
CURATOR_ISS = "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_synthetic"
CURATOR_SUB = "curator-sub-1"
PROFILE_CONTRACT = {
    "name": "Evidence record",
    "description": "Example",
    "semantic_class": "example_record",
    "fields": [{
        "key": "paper_name", "required": True, "nullable": True,
        "source_labels": ["Name in article"], "value_schema": {"kind": "string"},
    }],
}


def snapshot_data(instructions: str = "Find the genes.", output_contract: dict | None = None) -> dict:
    layers = _bundle("example", [_make_layer(
        layer_id="example:base_prompt", kind="base_prompt", title="Custom instructions",
        content=instructions, provenance="custom_agent", editable=True, locked=False,
        source_ref="custom_agent:test",
    )])
    return {
        "model_id": "gpt-6.1-sol", "model_temperature": 0.0, "model_reasoning": None,
        "instructions": instructions,
        "instructions_hash": "sha256:" + hashlib.sha256(instructions.encode()).hexdigest(),
        "prompt_layer_manifest": layers.to_manifest(), "group_prompt_layers": {},
        "tool_ids": [], "system_managed_tool_ids": [], "group_tool_policy": {"rules": []},
        "allowed_group_ids": [], "inherited_allowed_group_ids": [],
        "group_rules_enabled": False, "group_rules_component": None, "group_prompt_overrides": {},
        "template_source": None,
        "output_contract": output_contract or {"output_state": "none"},
        "curation": None, "structured_finalization": None,
    }


def task_node() -> dict:
    return {"id": "task", "type": "task_input", "position": {"x": 0, "y": 0},
            "data": {"agent_id": "task_input", "agent_display_name": "Task",
                     "output_key": "task", "task_instructions": "Extract"}}


def custom_node(node_id: str, *, agent_id: UUID, agent_key: str, revision_id: UUID, revision: int,
                fingerprint: str, output_contract: dict) -> dict:
    return {"id": node_id, "type": "agent", "position": {"x": 100, "y": 100},
            "data": {"agent_id": agent_key, "agent_display_name": "Gene finder",
                     "output_key": f"result_{node_id}", "agent_revision_id": str(revision_id),
                     "execution_receipt": {
                         "agent_id": str(agent_id), "agent_key": agent_key,
                         "agent_revision_id": str(revision_id), "revision": revision,
                         "fingerprint": fingerprint, "output_contract": output_contract,
                     }}}


def make_bundle(*, with_agent: bool = True, with_profile: bool = False,
                flow_id: UUID | None = None) -> dict:
    nodes, edges, agents, profiles = [task_node()], [], [], []
    if with_agent:
        contract = {"output_state": "none"}
        if with_profile:
            parsed = normalize_profile_contract(PROFILE_CONTRACT)
            profile_id, profile_revision_id = uuid4(), uuid4()
            contract = {
                "output_state": "structured_extraction", "output_mode": "profile_bound_generic",
                "generic_profile_ref": {"profile_id": str(profile_id),
                                        "profile_revision_id": str(profile_revision_id),
                                        "revision": 1, "fingerprint": parsed.fingerprint()},
            }
            profiles.append({"source_profile_id": str(profile_id), "revisions": [{
                "source_revision_id": str(profile_revision_id), "revision": 1,
                "fingerprint": parsed.fingerprint(), "contract": parsed.model_dump(mode="json"),
            }]})
        saved = AgentExecutionSnapshot.model_validate(snapshot_data(output_contract=contract))
        agent_id, revision_id = uuid4(), uuid4()
        key = f"ca_{agent_id}"
        agents.append({
            "source_agent_id": str(agent_id), "source_agent_key": key, "name": "Gene finder",
            "description": "Finds genes", "icon": "G", "category": "Extraction",
            "revisions": [{"source_revision_id": str(revision_id), "revision": 3,
                           "fingerprint": saved.fingerprint(),
                           "snapshot": saved.model_dump(mode="json")}],
        })
        nodes.append(custom_node("node_0", agent_id=agent_id, agent_key=key, revision_id=revision_id,
                                 revision=3, fingerprint=saved.fingerprint(),
                                 output_contract=saved.output_contract.model_dump(mode="json")))
        edges.append({"id": "e0", "source": "task", "target": "node_0"})
    return {
        "format": "aic-flow-export", "format_version": 1,
        "source": {"issuer": ISSUER, "app_version": "0.10.1"},
        "exported_for": {"iss": CURATOR_ISS, "sub": CURATOR_SUB},
        "exported_at": "2026-10-06T10:00:00Z",
        "flow": {"source_flow_id": str(flow_id or uuid4()), "source_version": "sha256:" + "1" * 64,
                 "owned": True, "name": "Gene flow", "description": "Finds genes",
                 "definition": {"version": "1.1", "nodes": nodes, "edges": edges,
                                "entry_node_id": "task"}},
        "agents": agents, "profiles": profiles,
    }

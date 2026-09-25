from copy import deepcopy

import pytest

from src.lib.agent_studio.flow_tools import _compile_flow_operations
from src.lib.agent_studio.flow_restoration import inspect_instruction_restoration


def draft():
    return {"version": "1.1", "entry_node_id": "node_1", "nodes": [
        {"id": f"node_{i}", "type": "agent", "position": {"x": 100, "y": i * 100},
         "data": {"agent_id": agent, "agent_display_name": agent, "output_key": f"result_{i}"}}
        for i, agent in enumerate(("allele_extractor", "gene_extractor"), 1)
    ], "edges": []}


def restored():
    candidate = draft()
    _compile_flow_operations(candidate=candidate, metadata={}, accessible_agents={}, semantic_refs={},
        operations=[{"operation": "restore_initial_instructions", "task_instructions": "Study genes and alleles, including controls."}])
    return candidate


def test_restoration_preserves_disconnected_steps_and_reports_remaining_findings():
    candidate = restored()
    assert candidate["nodes"][:-1] == draft()["nodes"]
    assert candidate["edges"] == []
    findings = inspect_instruction_restoration(draft(), candidate)
    assert findings is not None
    assert "disconnected" in {f.code for f in findings}
    from src.schemas.flows import FlowDefinition
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        FlowDefinition.model_validate(candidate)  # Save/Run is still blocked.


@pytest.mark.parametrize("edit", ["existing_node", "edge", "duplicate", "extra_task_data"])
def test_restoration_never_approves_unrelated_edits(edit):
    candidate = restored()
    if edit == "existing_node":
        candidate["nodes"][0]["data"]["custom_instructions"] = "unapproved"
    elif edit == "edge":
        candidate["edges"].append({"id": "e", "source": "node_1", "target": "node_2"})
    elif edit == "duplicate":
        candidate["nodes"].append(deepcopy(candidate["nodes"][-1]))
    else:
        candidate["nodes"][-1]["data"]["tools"] = ["unapproved"]
    assert inspect_instruction_restoration(draft(), candidate) is None


@pytest.mark.parametrize("rename", [False, True])
def test_proposal_offers_review_without_claiming_disconnected_flow_valid(monkeypatch, rename):
    from src.lib.agent_studio import flow_tools
    fingerprint = "sha256:" + "a" * 64
    flow_tools.set_workflow_user_context(123)
    flow_tools.set_current_flow_context({**draft(), "flow_name": "New Flow", "flow_description": "", "flow_draft_fingerprint": fingerprint})
    monkeypatch.setattr(flow_tools, "_accessible_flow_agents", lambda: {
        "allele_extractor": {"name": "Alleles"}, "gene_extractor": {"name": "Genes"},
    })
    try:
        result = flow_tools._propose_flow_draft_update_handler()(
            base_draft_fingerprint=fingerprint, change_summary="Restore instructions only",
            operations=[{"operation": "restore_initial_instructions", "task_instructions": "Include experimentally studied genes and alleles."}]
            + ([{"operation": "update_flow", "name": "Unrelated rename"}] if rename else []),
        )
        if rename:
            assert not result["success"] and not result["valid"] and not result["pending_user_approval"]
            return
        assert result["success"] and result["pending_user_approval"] and result["restoration_only"], str(result)
        assert not result["valid"]
        assert result["candidate"]["flow_definition"]["nodes"][:-1] == draft()["nodes"]
    finally:
        flow_tools.set_current_flow_context(None)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["pre_apply", "post_apply"])
@pytest.mark.parametrize("failure", [None, "stale", "unauthorized", "unrelated", "revision", "attachments"])
async def test_restoration_validation_endpoint_is_scoped_and_read_only(monkeypatch, phase, failure):
    from types import SimpleNamespace
    from fastapi import HTTPException
    from src.api import flows

    class DB:
        def query(self, *args):
            return self
        def filter(self, *args):
            return self
        def one_or_none(self):
            return SimpleNamespace(id=123)
        def commit(self):
            raise AssertionError("must not write")

    monkeypatch.setattr(flows, "_flow_agent_policy_entry", lambda *args, **kwargs: None if failure == "unauthorized" else {"name": "Agent"})
    if failure == "attachments":
        def reject_attachments(*args, **kwargs):
            raise ValueError("Unknown attachment selection")
        monkeypatch.setattr(flows, "apply_flow_validation_attachment_defaults", reject_attachments)
    if failure == "revision":
        from src.lib.agent_studio.authoring_validation import AuthoringValidationFinding
        monkeypatch.setattr(flows, "resolve_flow_execution_revisions", lambda db, definition, **kwargs: SimpleNamespace(
            definition=definition, entries_by_node={}, projection_catalogs={},
            findings=(AuthoringValidationFinding(code="unavailable_execution_revision", severity="error", path="flow_definition.nodes.node_1", message="Revision is unavailable."),),
        ))
    candidate = restored()
    if failure == "unrelated":
        candidate["nodes"][0]["position"]["x"] += 1
    request = flows.FlowDraftValidationRequest(
        flow_definition=candidate, restoration_base=draft(), phase=phase,
        expected_draft_fingerprint="sha256:" + "a" * 64,
        current_draft_fingerprint="sha256:" + ("b" if failure == "stale" else "a") * 64,
    )
    if failure in {"stale", "unrelated"}:
        with pytest.raises(HTTPException):
            await flows.validate_flow_draft(request, user={"sub": "owner"}, db=DB())
    else:
        result = await flows.validate_flow_draft(request, user={"sub": "owner"}, db=DB())
        assert result["restoration_only"] is (failure is None), result
        assert not result["valid"]
        assert result["findings"]
        if failure == "revision":
            assert "unavailable_execution_revision" in {finding["code"] for finding in result["findings"]}
        if failure == "attachments":
            assert "invalid_validation_attachment_configuration" in {finding["code"] for finding in result["findings"]}


@pytest.mark.parametrize("nodes", [None, [None], ["node"], [{"data": None}]])
def test_restoration_rejects_malformed_base(nodes):
    assert inspect_instruction_restoration({**draft(), "nodes": nodes}, restored()) is None

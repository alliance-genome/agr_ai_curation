"""Flows with a step the flow policy refuses (ALL-1648): every shape, one description.

The real save validator, open path and refusal text run here. Only the database
metadata lookup (``get_active_visible_agent_metadata``) is replaced, by the real
packaged registry entry of each agent: the fields a system agent row carries.
"""

from __future__ import annotations

import importlib
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.lib.agent_studio.catalog_service import AGENT_REGISTRY
from src.lib.agent_studio.refused_flow_steps import RefusedFlowStep, refused_flow_step
from src.schemas.flows import FlowDefinition

flows = importlib.import_module("src.api.flows")


@pytest.fixture(autouse=True)
def system_agent_rows(monkeypatch):
    def metadata(agent_id, **_kwargs):
        entry = AGENT_REGISTRY.get(agent_id)
        if entry is None:
            raise ValueError(f"Agent '{agent_id}' is not an active unified agent visible to this user.")
        return {
            "display_name": entry["name"],
            "category": entry.get("category"),
            "subcategory": entry.get("subcategory"),
            "output_schema_key": entry.get("output_schema_key"),
            "supervisor": entry.get("supervisor") or {},
            "curation": entry.get("curation"),
            "is_active": True,
            "visible": True,
            "visibility": "system",
        }

    monkeypatch.setattr(flows, "get_active_visible_agent_metadata", metadata)


def _node(node_id, agent_id, node_type="agent"):
    return {
        "id": node_id,
        "type": node_type,
        "position": {"x": 0, "y": 0},
        "data": {
            "agent_id": agent_id,
            "agent_display_name": AGENT_REGISTRY[agent_id]["name"] if agent_id in AGENT_REGISTRY else agent_id,
            "output_key": f"{node_id}_output",
            **({"task_instructions": "Find the genes in this paper"} if agent_id == "task_input" else {}),
        },
    }


def _flow(nodes, edges):
    return {"version": "1.1", "nodes": nodes, "edges": edges, "entry_node_id": "task_1"}


def shape_a(validator="gene_validation"):
    """A validator feeding the output step: the shape of the old recipes."""
    return _flow(
        [_node("task_1", "task_input", "task_input"), _node("pdf_1", "pdf_extraction"),
         _node("val_1", validator), _node("out_1", "chat_output", "output")],
        [{"id": "e1", "source": "task_1", "target": "pdf_1"},
         {"id": "e2", "source": "pdf_1", "target": "val_1"},
         {"id": "o1", "source": "val_1", "target": "out_1", "role": "output_attachment"}],
    )


def shape_b():
    """A validator in the middle of the chain."""
    return _flow(
        [_node("task_1", "task_input", "task_input"), _node("pdf_1", "pdf_extraction"),
         _node("val_1", "gene_validation"), _node("ext_1", "gene_extractor"),
         _node("out_1", "chat_output", "output")],
        [{"id": "e1", "source": "task_1", "target": "pdf_1"},
         {"id": "e2", "source": "pdf_1", "target": "val_1"},
         {"id": "e3", "source": "val_1", "target": "ext_1"},
         {"id": "o1", "source": "ext_1", "target": "out_1", "role": "output_attachment"}],
    )


def repaired_a():
    """Shape A after the curator deletes the step and re-points the output."""
    payload = shape_a()
    payload["nodes"] = [node for node in payload["nodes"] if node["id"] != "val_1"]
    payload["edges"] = [
        {"id": "e1", "source": "task_1", "target": "pdf_1"},
        {"id": "o1", "source": "pdf_1", "target": "out_1", "role": "output_attachment"},
    ]
    return payload


def repaired_b():
    payload = shape_b()
    payload["nodes"] = [node for node in payload["nodes"] if node["id"] != "val_1"]
    payload["edges"] = [
        {"id": "e1", "source": "task_1", "target": "pdf_1"},
        {"id": "e3", "source": "pdf_1", "target": "ext_1"},
        {"id": "o1", "source": "ext_1", "target": "out_1", "role": "output_attachment"},
    ]
    return payload


def _save(payload):
    return flows._validated_flow_definition_payload(
        FlowDefinition.model_validate(deepcopy(payload)),
        db_user_id=7, enforce_agent_references=True, enforce_agent_step_policy=True,
        active_group_ids=[],
    )


def _open(payload):
    now = datetime.now(timezone.utc)
    stored = SimpleNamespace(
        id=uuid4(), user_id=7, visibility="private", project_id=None, shared_at=None,
        name="A saved flow", description=None, flow_definition=deepcopy(payload),
        execution_count=0, last_executed_at=None, created_at=now, updated_at=now,
    )
    return flows._flow_to_response(stored, viewer_user_id=7, active_group_ids=[])


GENE_CHECKERS = ("Disease Extraction Agent", "Gene Expression Extractor", "Gene Extraction Agent")


def test_a_validator_is_described_with_the_extraction_agents_that_run_its_check():
    refused = refused_flow_step("gene_validation", AGENT_REGISTRY["gene_validation"])

    assert refused == RefusedFlowStep("validator", "Gene Validation Agent", checking_extractors=GENE_CHECKERS)
    assert refused.save_code == "attachment_only_agent_in_control_flow"
    assert refused.run_code == "attachment_only_validator"
    assert refused.message == (
        "Gene Validation Agent is a validator. Validators check an extraction step's records "
        "automatically and cannot be flow steps."
    )
    assert refused.fix_hint == (
        "Remove this step. Disease Extraction Agent, Gene Expression Extractor and Gene Extraction "
        "Agent run the Gene Validation Agent check automatically; keep or add the one for your records."
    )


def test_a_validator_no_extraction_agent_runs_says_so_and_claims_none():
    refused = refused_flow_step("disease_validation", AGENT_REGISTRY["disease_validation"])

    assert refused.checking_extractors == ()
    assert refused.fix_hint == (
        "Remove this step. No extraction step runs the Disease Ontology Agent check at present; the "
        "checks each extraction step does run are listed under Automatic checks on that step in Flow Builder."
    )


def test_group_restricted_extraction_agents_are_named_only_for_their_groups():
    without = refused_flow_step("gene_validation", AGENT_REGISTRY["gene_validation"])
    rgd = refused_flow_step("gene_validation", AGENT_REGISTRY["gene_validation"], active_group_ids=["RGD"])

    assert "RGD GO Paper Curator" not in without.checking_extractors
    assert "RGD GO Paper Curator" in rgd.checking_extractors


def test_an_internal_agent_cannot_be_a_flow_step_at_all():
    """FlowDefinition refuses supervisor and task_input, so no refusal text exists for them."""
    payload = repaired_a()
    payload["nodes"].append(_node("sup_1", "supervisor"))
    payload["edges"].append({"id": "e9", "source": "pdf_1", "target": "sup_1"})
    with pytest.raises(Exception, match="cannot be used in flows"):
        FlowDefinition.model_validate(payload)
    with pytest.raises(ValueError, match="FlowDefinition refuses it"):
        refused_flow_step("supervisor", AGENT_REGISTRY["supervisor"])


def test_a_validator_reached_through_subject_routing_names_the_extractor_that_routes_to_it():
    """The disease pack routes a disease record's allele or model subject to those validators."""
    agm = refused_flow_step("agm_validation", AGENT_REGISTRY["agm_validation"])
    allele = refused_flow_step("allele_validation", AGENT_REGISTRY["allele_validation"])

    assert agm.checking_extractors == ("Disease Extraction Agent",)
    assert "No extraction step" not in agm.fix_hint
    assert allele.checking_extractors == ("Allele/Variant Extraction Agent", "Disease Extraction Agent")


def test_a_binding_still_being_built_is_never_named():
    """Reference checks in the disease, allele and phenotype packs are under development."""
    reference = refused_flow_step("reference_validation", AGENT_REGISTRY["reference_validation"])

    assert "Gene Expression Extractor" in reference.checking_extractors
    for not_running in ("Disease Extraction Agent", "Allele/Variant Extraction Agent", "Phenotype Extraction Agent"):
        assert not_running not in reference.checking_extractors
    # The phenotype pack's subject routing is under development too.
    agm = refused_flow_step("agm_validation", AGENT_REGISTRY["agm_validation"])
    assert "Phenotype Extraction Agent" not in agm.checking_extractors


@pytest.mark.parametrize(
    ("template_source", "custom_detail"),
    [("allele_validation", True), ("gene_validation", True), ("reference_validation", True),
     ("controlled_vocabulary_validation", False), ("disease_validation", False)],
)
def test_a_curators_validator_copy_cannot_run_and_custom_detail_is_offered_only_where_true(
    template_source, custom_detail,
):
    # The entry a pinned revision with a validator-result schema resolves to.
    entry = {"name": "My allele check", "category": "Validation", "supervisor": {},
             "output_schema_key": "AlleleResultEnvelope", "template_source": template_source}
    refused = refused_flow_step("ca_11111111-1111-4111-8111-111111111111", entry)

    assert refused.kind == "custom_validator"
    assert refused.save_code == "custom_validator_in_control_flow"
    assert refused.run_code == "custom_validator_step"
    assert refused.message == (
        "My allele check is your copy of a validator, and a copy of a validator cannot run in a flow."
    )
    if custom_detail:
        assert refused.fix_hint == (
            "Remove this step. You can attach your copy as the validator on a detail of your own "
            "Custom Output Structure, as long as it keeps the answer format of the validator it was "
            "copied from."
        )
    else:
        assert refused.fix_hint == "Remove this step."


def test_a_step_the_policy_allows_is_not_refused():
    for agent_id in ("pdf_extraction", "gene_extractor", "chemical_validation", "chat_output"):
        assert refused_flow_step(agent_id, AGENT_REGISTRY[agent_id]) is None


@pytest.mark.parametrize(
    ("shape", "repaired"), [(shape_a, repaired_a), (shape_b, repaired_b)], ids=["feeds_output", "mid_chain"],
)
def test_open_see_the_marked_step_remove_it_and_save(shape, repaired):
    """The acceptance path: such a flow opens, names its step, cannot be re-saved
    unchanged, and saves once the step is removed."""
    opened = _open(shape())
    marked = [w for w in opened.validation_warnings if w.code == "attachment_only_agent_in_control_flow"]
    assert opened.has_critical_issues is True
    assert [(w.node_id, w.type) for w in marked] == [("val_1", "CRITICAL")]
    expected = refused_flow_step("gene_validation", AGENT_REGISTRY["gene_validation"])
    assert marked[0].message == expected.explanation

    with pytest.raises(HTTPException) as refused:
        _save(opened.flow_definition.model_dump(mode="json"))
    assert refused.value.status_code == 422
    findings = [f for f in refused.value.detail["findings"] if f["code"] == "attachment_only_agent_in_control_flow"]
    assert [(f["node_id"], f["message"], f["fix_hint"]) for f in findings] == [
        ("val_1", expected.message, expected.fix_hint)
    ]

    saved = _save(repaired())
    assert "val_1" not in {node["id"] for node in saved["nodes"]}
    reopened = _open(saved)
    assert not [w for w in reopened.validation_warnings if w.node_id]


def test_a_flow_without_such_a_step_saves_and_opens_as_before():
    saved = _save(repaired_a())
    assert _open(saved).has_critical_issues is False


def test_duplicating_such_a_flow_refuses_with_the_save_explanation(monkeypatch):
    payload = shape_a()
    source = SimpleNamespace(
        id=uuid4(), user_id=7, visibility="private", project_id=None, shared_at=None,
        name="A saved flow", description=None, flow_definition=payload, execution_count=0,
        last_executed_at=None, created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    )
    monkeypatch.setattr(flows, "provision_user_async", lambda *_a, **_k: _awaitable(SimpleNamespace(id=7)))
    monkeypatch.setattr(flows, "principal_from_claims", lambda claims: claims)
    monkeypatch.setattr(flows, "get_visible_flow", lambda *_a, **_k: source)

    import asyncio

    with pytest.raises(HTTPException) as refused:
        asyncio.run(flows.clone_flow(source.id, flows.CloneFlowRequest(name=None), user={"sub": "u"}, db=None))
    assert refused.value.status_code == 422
    assert refused.value.detail == refused_flow_step("gene_validation", AGENT_REGISTRY["gene_validation"]).explanation


async def _coro(value):
    return value


def _awaitable(value):
    return _coro(value)


@pytest.mark.parametrize("key", ["reference_validation", "reference"])
def test_a_validator_is_found_under_either_of_its_keys(key):
    refused = refused_flow_step(key, AGENT_REGISTRY[key])
    assert refused.kind == "validator"
    assert "Gene Expression Extractor" in refused.checking_extractors


def test_a_batch_is_refused_at_set_up_with_the_save_code_and_explanation():
    from src.lib.batch.validation import validate_flow_for_batch

    payload = shape_a()
    payload["nodes"][-1]["data"]["agent_id"] = "csv_formatter"
    result = validate_flow_for_batch(payload)

    expected = refused_flow_step("gene_validation", AGENT_REGISTRY["gene_validation"])
    assert result.valid is False
    assert result.error_codes == ["attachment_only_agent_in_control_flow"]
    assert expected.explanation in result.errors
    assert validate_flow_for_batch({**repaired_a(), "nodes": [
        {**node, "data": {**node["data"], "agent_id": "csv_formatter"}} if node["id"] == "out_1" else node
        for node in repaired_a()["nodes"]]}).error_codes == []


def test_studio_flow_state_reports_the_save_refusal_including_a_curators_copy(monkeypatch):
    from src.lib.agent_studio import flow_tools

    monkeypatch.setattr(flow_tools, "get_current_active_group_ids", lambda: ())
    validator = {"id": "val_1", "type": "agent", "data": {"agent_id": "gene_validation"}}
    finding = flow_tools._refused_step_finding(validator, {})
    expected = refused_flow_step("gene_validation", AGENT_REGISTRY["gene_validation"])
    assert finding == {"severity": "CRITICAL", "code": "attachment_only_agent_in_control_flow",
                       "node_ids": ["val_1"], "message": expected.explanation}

    copy_id = "ca_11111111-1111-4111-8111-111111111111"
    copy = {"id": "val_2", "type": "agent", "data": {"agent_id": copy_id}}
    revision_entry = {"name": "My allele check", "category": "Validation", "supervisor": {},
                      "output_schema_key": "AlleleResultEnvelope", "template_source": "allele_validation"}
    copy_finding = flow_tools._refused_step_finding(copy, {"val_2": revision_entry})
    assert copy_finding["code"] == "custom_validator_in_control_flow"
    assert copy_finding["node_ids"] == ["val_2"]
    # A copy no registry knows is still judged by its pinned revision.
    assert copy_id not in AGENT_REGISTRY


def test_a_viewer_who_is_not_the_owner_is_told_only_the_owner_can_change_the_flow():
    copy = {"name": "Owner's allele check", "category": "Validation", "supervisor": {},
            "output_schema_key": "AlleleResultEnvelope", "template_source": "allele_validation"}
    shared_copy = refused_flow_step("ca_11111111-1111-4111-8111-111111111111", copy, owner=False)
    assert shared_copy.explanation == (
        "Owner's allele check is a copy of a validator saved by the flow's owner, and a copy of a "
        "validator cannot run in a flow. Only the flow's owner can remove this step."
    )
    shared_validator = refused_flow_step("gene_validation", AGENT_REGISTRY["gene_validation"], owner=False)
    # The viewer cannot change the flow, so they are not told to keep or add a step.
    assert shared_validator.fix_hint == (
        "Only the flow's owner can remove this step. Disease Extraction Agent, Gene Expression Extractor "
        "and Gene Extraction Agent run the Gene Validation Agent check automatically."
    )
    single = refused_flow_step("gene_validation", AGENT_REGISTRY["gene_validation"], owner=False)
    single = replace(single, checking_extractors=("Gene Extraction Agent",))
    assert single.fix_hint == (
        "Only the flow's owner can remove this step. Gene Extraction Agent runs the Gene Validation "
        "Agent check automatically."
    )
    none_run = refused_flow_step("disease_validation", AGENT_REGISTRY["disease_validation"], owner=False)
    assert none_run.fix_hint == (
        "Only the flow's owner can remove this step. No extraction step runs the Disease Ontology Agent "
        "check at present; the checks each extraction step does run are listed under Automatic checks "
        "on that step in Flow Builder."
    )

    now = datetime.now(timezone.utc)
    stored = SimpleNamespace(id=uuid4(), user_id=8, visibility="project", project_id=uuid4(), shared_at=now,
                             name="Shared flow", description=None, flow_definition=shape_a(),
                             execution_count=0, last_executed_at=None, created_at=now, updated_at=now)
    opened = flows._flow_to_response(stored, viewer_user_id=7, active_group_ids=[])
    marked = [w for w in opened.validation_warnings if w.node_id == "val_1"]
    assert marked[0].message == shared_validator.explanation


def test_a_member_running_a_shared_flow_is_told_only_the_owner_can_change_it(monkeypatch):
    """The runner (7) is not the flow's owner (8): the run card says so and offers no edit."""
    from src.lib.flows import executor
    from src.lib.flows.unavailable_steps import build_flow_step_unavailable_message

    copy_id = "ca_11111111-1111-4111-8111-111111111111"
    revision_entry = {"agent_id": copy_id, "name": "Owner's allele check", "display_name": "Owner's allele check",
                      "category": "Validation", "subcategory": "", "supervisor": {}, "is_active": True,
                      "visible": True, "output_schema_key": "AlleleResultEnvelope",
                      "template_source": "allele_validation", "requires_document": False,
                      "required_params": []}
    flow = SimpleNamespace(id=uuid4(), user_id=8, flow_definition=_flow(
        [_node("task_1", "task_input", "task_input"),
         {"id": "val_1", "type": "agent", "position": {"x": 0, "y": 0},
          "data": {"agent_id": copy_id, "agent_display_name": "Owner's allele check", "output_key": "val_out"}}],
        [{"id": "e1", "source": "task_1", "target": "val_1"}]))
    monkeypatch.setattr(executor, "_runtime_custom_entries", lambda *_a, **_k: {"val_1": revision_entry})

    _tools, _names, unavailable, _state = executor.get_all_agent_tools(
        flow, include_unavailable=True, db_user_id=7, user_id="u1")

    assert [(step["reason_code"], step["owner"]) for step in unavailable] == [("custom_validator_step", False)]
    # No custom-detail offer: only the owner could act on it.
    assert build_flow_step_unavailable_message(unavailable) == (
        "Flow cannot start. Step 1 (Owner's allele check) is a copy of a validator saved by the flow's owner, "
        "and a copy of a validator cannot run in a flow. Only the flow's owner can remove it in Flow Builder."
    )
    validator = refused_flow_step("gene_validation", AGENT_REGISTRY["gene_validation"], owner=False)
    assert build_flow_step_unavailable_message([validator.as_unavailable_step(2, "gene_validation")]) == (
        "Flow cannot start. Step 2 (Gene Validation Agent) is a validator, and validators cannot be flow "
        "steps. Only the flow's owner can remove it in Flow Builder. Disease Extraction Agent, Gene "
        "Expression Extractor and Gene Extraction Agent run the Gene Validation Agent check automatically."
    )


def test_studio_tells_a_project_member_viewing_a_shared_flow_that_only_the_owner_can_change_it(monkeypatch):
    from src.lib.agent_studio import flow_tools

    monkeypatch.setattr(flow_tools, "get_current_active_group_ids", lambda: ())
    validator = {"id": "val_1", "type": "agent", "data": {"agent_id": "gene_validation"}}
    expected_owner = refused_flow_step("gene_validation", AGENT_REGISTRY["gene_validation"])
    expected_member = refused_flow_step("gene_validation", AGENT_REGISTRY["gene_validation"], owner=False)

    monkeypatch.setattr(flow_tools, "_viewer_owns_current_flow", lambda: False)
    assert flow_tools._refused_step_finding(validator, {})["message"] == expected_member.explanation
    monkeypatch.setattr(flow_tools, "_viewer_owns_current_flow", lambda: True)
    assert flow_tools._refused_step_finding(validator, {})["message"] == expected_owner.explanation


@pytest.mark.parametrize(("owner_id", "owns"), [(7, True), (8, False)])
def test_studio_judges_ownership_by_the_saved_flow_open_in_flow_builder(monkeypatch, owner_id, owns):
    from contextlib import nullcontext
    from src.lib.agent_studio import flow_tools
    from src.lib.flows import access
    from src.models.sql import database

    flow_id = uuid4()
    seen = []

    def visible(db, requested_id, user_id):
        seen.append((requested_id, user_id))
        return SimpleNamespace(id=requested_id, user_id=owner_id)

    monkeypatch.setattr(access, "get_visible_flow", visible)
    monkeypatch.setattr(database, "SessionLocal", lambda: nullcontext(object()))
    monkeypatch.setattr(flow_tools, "get_current_user_id", lambda: 7)
    monkeypatch.setattr(flow_tools, "get_current_flow_context", lambda: {"flow_id": str(flow_id)})
    assert flow_tools._viewer_owns_current_flow() is owns
    assert seen == [(flow_id, 7)]
    # A draft that was never saved is the curator's own.
    monkeypatch.setattr(flow_tools, "get_current_flow_context", lambda: {"flow_id": None})
    assert flow_tools._viewer_owns_current_flow() is True


def test_the_run_records_a_curators_copy_by_its_own_code_and_text(monkeypatch):
    """A pinned revision with a validator-result schema runs as a copy, not as the packaged validator."""
    from src.lib.flows import executor
    from src.lib.flows.unavailable_steps import build_flow_step_unavailable_message

    copy_id = "ca_11111111-1111-4111-8111-111111111111"
    revision_entry = {"agent_id": copy_id, "name": "My allele check", "display_name": "My allele check",
                      "category": "Validation", "subcategory": "", "supervisor": {}, "is_active": True,
                      "visible": True, "output_schema_key": "AlleleResultEnvelope",
                      "template_source": "allele_validation", "requires_document": False,
                      "required_params": []}
    flow = SimpleNamespace(id=uuid4(), user_id=7, flow_definition=_flow(
        [_node("task_1", "task_input", "task_input"),
         {"id": "val_1", "type": "agent", "position": {"x": 0, "y": 0},
          "data": {"agent_id": copy_id, "agent_display_name": "My allele check", "output_key": "val_out"}}],
        [{"id": "e1", "source": "task_1", "target": "val_1"}]))
    monkeypatch.setattr(executor, "_runtime_custom_entries", lambda *_a, **_k: {"val_1": revision_entry})

    _tools, _names, unavailable, _state = executor.get_all_agent_tools(
        flow, include_unavailable=True, db_user_id=7, user_id="u1")

    assert [(step["reason_code"], step["custom_detail"]) for step in unavailable] == [("custom_validator_step", True)]
    assert build_flow_step_unavailable_message(unavailable) == (
        "Flow cannot start. Step 1 (My allele check) is your copy of a validator, and a copy of a validator "
        "cannot run in a flow. In Flow Builder, remove it. You can attach My allele check as the validator "
        "on a detail of your own Custom Output Structure, as long as it keeps the answer format of the "
        "validator it was copied from."
    )

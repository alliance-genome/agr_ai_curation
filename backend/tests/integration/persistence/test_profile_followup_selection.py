"""Follow-up proposals against a real saved output structure, through the real publish (ALL-1561).

The structure is persisted as a generic profile revision; the review source is
planned, admitted and published by the real ValidatorAdapter, so a selected field
passes the same envelope and attribute checks a curator's run does.
"""
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.lib.domain_packs.validator_dispatch import ValidatorRuntimeContext, prepare_validator_dispatch
from src.lib.validation_history import runtime, selections, store
from src.lib.validation_review import admission, extraction_results, extraction_scheduling, work
from src.lib.validation_review.decisions import ReviewContext
from src.lib.validation_review.source_selection import StructuralRefusal
from src.lib.validation_review.validator_adapter import ValidatorAdapter, save_plan
from tests.integration.persistence.test_validation_history import scope as scope, migrated as migrated


@pytest.fixture
def profiled(scope, monkeypatch):
    from unittest.mock import AsyncMock
    from tests.cancellation_test_support import install_cancel_reader
    from tests.integration.persistence.test_curator_selected_authority import _persist_test_profile
    from tests.unit.lib.domain_packs.test_profile_materialization import prepared
    from tests.unit.lib.domain_packs.test_profile_validation import example
    from src.lib.config.agent_loader import AgentDefinition

    install_cancel_reader(monkeypatch, AsyncMock(return_value=False))
    raw, cap, pack = example.__wrapped__()
    raw["fields"] += [
        {"key": "note", "value_schema": {"kind": "string"}},
        {"key": "source", "value_schema": {"kind": "string"}},
        {"key": "stage", "value_schema": {"kind": "enum", "values": ["embryo", "larva"]}},
        {"key": "details", "value_schema": {"kind": "object", "fields": [
            {"key": "lab", "value_schema": {"kind": "string"}},
            {"key": "room", "value_schema": {"kind": "string"}}]}},
    ]
    attributes = {"paper_name": "A", "note": "old note", "source": "a supplier",
                  "details": {"lab": "a lab", "room": "a room"}}
    source, _ = prepared((raw, cap, pack), attributes=attributes)
    source, context = _persist_test_profile(scope, raw, cap, pack, source, monkeypatch)
    monkeypatch.setattr("src.lib.config.agent_loader.get_agent_definition_for_package",
        lambda package, agent: AgentDefinition(folder_name=agent, agent_id=agent, name=agent, package_id=package))
    source = runtime.capture_extraction(scope=scope, producer_id=uuid4().hex,
        typed=source.model_dump(mode="json"), envelope=source, provenance={})
    groups = ()  # The fixture capability is not group-scoped.
    plan = prepare_validator_dispatch(source, pack, runtime_context=ValidatorRuntimeContext(
        user_id=scope.owner_id, document_id=scope.document_id, authenticated_groups=groups))
    plan_ref = save_plan(plan, context=ValidatorRuntimeContext(
        user_id=scope.owner_id, document_id=scope.document_id, authenticated_groups=groups))
    aid, ep = plan.history_binding.artifact_id, plan.envelope.metadata["review_episode_id"]
    sid = admission.create_scope(scope, operation_id=uuid4().hex, kind="discovered_document_run",
                                 targets=0, episodes=0)
    admission.attach_episode(scope, artifact_id=aid, episode_id=ep, scope_id=sid)
    adapter = ValidatorAdapter(scope, aid, ep, plan_ref, authenticated_groups=groups)
    return SimpleNamespace(plan=plan, adapter=adapter, aid=aid, ep=ep, scope=scope,
                           receipt=context.receipt, groups=groups)


def _retain(item, proposed_values):
    entry = item.plan.envelope.extracted_objects[0]
    target = entry.metadata["validation_history"]["target_id"]
    op = uuid4().hex
    with store.SessionLocal.begin() as db:
        parent = store._append(db, item.aid, operation_id="decision:" + op, kind="decision",
            body={"actor": {"type": "agent", "id": "supervisor"},
                  "entries": [{"target_id": target, "action": "defer", "reason": "Check the supplier"}]})
        store._append(db, item.aid, operation_id="episode-disposition:" + op,
            kind="episode_disposition", parent_id=item.ep, body={"selection_ids": []})
        parent_id = parent.id
    context = ReviewContext(item.scope, item.ep, "supervisor")
    wid = extraction_scheduling.request(context, operation_id=uuid4().hex, parent_decision_id=parent_id,
        entry_ids=[target], instructions="Check the supplier in Methods.",
        receipt={"agent_key": item.receipt.agent_key, "execution_receipt": item.receipt.model_dump(mode="json"),
                 "contract": {"instructions": "Keep reagents used in this study."}},
        current_entries={target: entry.model_dump(mode="json")})
    claim = work.claim(item.scope, wid)
    work.mark_submitted(item.scope, wid, claim["lease_token"])
    result = extraction_results.retain(item.scope, wid, claim["lease_token"], {"entries": [{
        "entry_id": target, "outcome": "proposed_correction", "assessment": "Methods clarify the record",
        "remaining_uncertainty": "", "evidence_record_ids": [],
        "proposed_values": [{"field_path": path, "value": value, "evidence_record_ids": []}
                            for path, value in proposed_values]}]}, evidence_records=[])
    work.finish(item.scope, wid, claim["lease_token"], outcome=result)
    return context, target, result


def _checkpoint(item):
    from src.lib.curation_workspace.models import DomainEnvelopeModel
    from src.lib.domain_envelopes.persistence import DomainEnvelopeCheckpointRequest
    from src.schemas.domain_envelope import DomainEnvelope

    source_id = uuid4()

    def load():
        with store.SessionLocal() as db:
            row = db.get(DomainEnvelopeModel, item.plan.envelope.envelope_id)
            return DomainEnvelopeCheckpointRequest(project_key="fixture",
                envelope=item.plan.envelope if row is None else DomainEnvelope.model_validate(row.envelope_json),
                expected_revision=0 if row is None else row.revision, execution_receipt=item.receipt,
                document_id=item.scope.document_id, adapter_key="fixture",
                source_extraction_result_id=source_id, authenticated_groups=item.groups)
    return load


async def _select(item, context, checkpoint, contribution, field_paths):
    with store.SessionLocal() as db:
        digest = selections.source_state_digest(db, item.aid)
    return await item.adapter.select_source(context, checkpoint, operation_id=uuid4().hex,
        contribution_ref=contribution, field_paths=field_paths, evidence_ids=[],
        expected_source_sha=digest, reason="Supported by the Methods")


@pytest.mark.asyncio
async def test_reference_case_with_a_nested_null_publishes_with_the_fields_left_out(profiled):
    item = profiled
    context, _, result = _retain(item, [("attributes.note", "new note"), ("attributes.source", None),
                                        ("attributes.details", {"lab": None, "room": "new room"})])
    assert "not_selectable" not in result
    checkpoint = _checkpoint(item)
    await _select(item, context, checkpoint, result["contributions"][0],
                  ["attributes.note", "attributes.source", "attributes.details"])
    published = checkpoint().envelope.extracted_objects[0].payload["attributes"]
    assert "source" not in published
    assert published["note"] == "new note" and published["details"] == {"room": "new room"}


@pytest.mark.asyncio
async def test_partly_bad_proposal_keeps_valid_fields_selectable_and_names_the_bad_one(profiled):
    item = profiled
    context, target, result = _retain(item, [("attributes.note", "new note"),
        ("attributes.details.room", "new room"), ("attributes.stage", "adult")])
    contribution = result["contributions"][0]
    refused, = result["not_selectable"]
    assert refused["structural_failure"] is None and list(refused["unselectable_fields"]) == ["attributes.stage"]
    issue, = refused["unselectable_fields"]["attributes.stage"]["issues"]
    assert (issue["candidate_id"], issue["field_path"], issue["reason"]) == (target, "attributes.stage", "invalid_enum")
    body = store.read_fact(item.scope, item.aid, contribution["fact_id"])["content"]
    assert body["selectable"] is True and body["unselectable_fields"] == refused["unselectable_fields"]
    checkpoint = _checkpoint(item)
    with pytest.raises(StructuralRefusal, match=r"attributes\.stage \(output_structure_conformance\)") as error:
        await _select(item, context, checkpoint, contribution, ["attributes.note", "attributes.stage"])
    assert error.value.issues == [issue]
    await _select(item, context, checkpoint, contribution, ["attributes.note", "attributes.details.room"])
    published = checkpoint().envelope.extracted_objects[0].payload["attributes"]
    assert published["note"] == "new note" and published["details"]["room"] == "new room"
    assert "stage" not in published


@pytest.mark.asyncio
async def test_a_wholly_bad_proposal_is_not_selectable(profiled):
    item = profiled
    _, _, result = _retain(item, [("attributes.paper_name", None)])
    refused, = result["not_selectable"]
    assert refused["structural_failure"] == "output_structure_conformance"
    assert [issue["field_path"] for issue in refused["structural_issues"]] == ["attributes.paper_name"]


@pytest.mark.asyncio
async def test_overlapping_proposals_are_reported_and_cannot_be_selected_together(profiled):
    item = profiled
    context, target, result = _retain(item, [("attributes.details", {"lab": "x", "room": "y"}),
                                             ("attributes.details.room", "z")])
    refused, = result["not_selectable"]
    assert refused["overlapping_fields"] == [["attributes.details", "attributes.details.room"]]
    assert refused["unselectable_fields"] == {} and refused["structural_failure"] is None
    checkpoint = _checkpoint(item)
    with pytest.raises(StructuralRefusal) as error:
        await _select(item, context, checkpoint, result["contributions"][0],
                      ["attributes.details", "attributes.details.room"])
    assert [(issue["field_path"], issue["reason"]) for issue in error.value.issues] == [
        ("attributes.details", "overlapping_fields"), ("attributes.details.room", "overlapping_fields")]
    await _select(item, context, checkpoint, result["contributions"][0], ["attributes.details.room"])
    assert checkpoint().envelope.extracted_objects[0].payload["attributes"]["details"]["room"] == "z"

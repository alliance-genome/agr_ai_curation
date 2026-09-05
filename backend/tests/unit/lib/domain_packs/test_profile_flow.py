"""Flow defaults and execution retain the exact selected profile mappings."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.lib.domain_packs.profile_validation import compile_profile_validation, profile_validation_attachment_options
from src.lib.domain_packs.validation_registry import DomainPackValidationRegistry
from src.lib.flows import executor, validation_attachments
from .test_profile_validation import example as example
from .test_profile_materialization import prepared, results
from ..flows.test_validation_attachments import _flow_definition, _validator_node


@pytest.mark.parametrize("per_element", [False, True])
def test_attachment_identity_survives_capability_revocation(example, per_element):
    source, context = prepared(example, per_element=per_element,
        attributes={"records": [{"paper_name": "A"}]} if per_element else None)
    available, = profile_validation_attachment_options(context)
    revoked = compile_profile_validation(context.receipt, context.profile, example[2],
        capabilities=[replace(example[1], available=False, unavailable_reason="Access revoked")])
    unavailable, = profile_validation_attachment_options(revoked)
    assert available.attachment_id == unavailable.attachment_id
    assert available.validator_binding_id == unavailable.validator_binding_id
    assert unavailable.default_enabled and not unavailable.allow_opt_out
    assert "revoked" in unavailable.reason
    assert not revoked.registry.bindings


def test_flow_defaults_resolve_node_receipt_not_global_generic_bindings(example, monkeypatch):
    source, context = prepared(example)
    monkeypatch.setattr(validation_attachments, "_domain_pack_validation_registries",
                        lambda: {"generic": DomainPackValidationRegistry.from_domain_pack(example[2])})
    monkeypatch.setattr("src.lib.curation_workspace.execution_contracts.load_receipt_profile",
                        lambda receipt: context.profile)
    groups = []
    monkeypatch.setattr("src.lib.domain_packs.profile_validation.capability_catalog",
                        lambda **kwargs: (groups.append(kwargs["active_group_ids"]), [example[1]])[1])
    definition = _flow_definition(context.receipt.agent_key)
    node = definition.nodes[1]
    node.data.agent_revision_id = context.receipt.agent_revision_id
    node.data.execution_receipt = context.receipt
    hydrated = validation_attachments.apply_flow_validation_attachment_defaults(definition, entries_by_node={node.id: {
        "curation": {"domain_pack_id": "generic"}, "execution_receipt": context.receipt.model_dump(mode="json"),
        "authenticated_group_ids": ["TEAM_A"],
    }})
    group, = hydrated.nodes[1].data.validation_groups
    assert group.binding_id == context.registry.bindings[0].binding_id
    assert group.state == "automatic"
    assert groups == [("TEAM_A",)]


@pytest.mark.asyncio
async def test_flow_dispatches_compiled_mapping_through_existing_jobs(example, monkeypatch):
    source, context = prepared(example)
    item, = results(source, context, [{"identifier": "EX:1"}])
    monkeypatch.setattr("src.lib.domain_packs.validator_dispatch.run_package_scoped_validator_agent",
                        lambda request, **kwargs: item.result.model_dump(mode="json"))
    inputs, findings, metadata = await executor._collect_flow_validator_materialization_inputs(
        source_envelope=source, source_envelope_revision=1, registry=context.registry,
        groups=[{"group_id": "profile", "state": "automatic", "binding_id": item.match.binding.binding_id}],
        flow=SimpleNamespace(flow_definition={"nodes": []}), agent_context={}, profile_context=context,
    )
    assert not findings
    assert len(inputs) == 1
    assert inputs[0].request == item.request
    assert inputs[0].result.resolved_values == {"identifier": "EX:1"}
    assert metadata[-1]["request_id"] == item.request.request_id


@pytest.mark.asyncio
async def test_flow_rechecks_revoked_mapping_before_result_reuse(example, monkeypatch):
    source, context = prepared(example)
    binding_id = context.registry.bindings[0].binding_id
    revoked = compile_profile_validation(context.receipt, context.profile, example[2],
        capabilities=[replace(example[1], available=False, unavailable_reason="Revoked")])
    monkeypatch.setattr(executor, "dispatch_validator_jobs", lambda *a, **kw: pytest.fail("Revoked validator ran"))
    inputs, findings, _ = await executor._collect_flow_validator_materialization_inputs(
        source_envelope=source, source_envelope_revision=1, registry=revoked.registry,
        groups=[{"state": "automatic", "binding_id": binding_id}],
        flow=SimpleNamespace(flow_definition={"nodes": []}), agent_context={}, profile_context=revoked,
    )
    assert not inputs
    assert findings[0].code == "generic_profile.validator_unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize("state,binding_id", [("skipped", None), ("replaced", None), ("supplemental", None), ("automatic", "other")])
async def test_flow_cannot_change_pinned_mapping_contract(example, state, binding_id):
    source, context = prepared(example)
    with pytest.raises(ValueError, match="exact profile"):
        await executor._collect_flow_validator_materialization_inputs(
            source_envelope=source, source_envelope_revision=1, registry=context.registry,
            groups=[{"state": state, "binding_id": binding_id or context.registry.bindings[0].binding_id}],
            flow=SimpleNamespace(flow_definition={"nodes": []}), agent_context={}, profile_context=context,
        )


def test_flow_authoring_rejects_profile_validator_sidecar(example, monkeypatch):
    source, context = prepared(example)
    option, = profile_validation_attachment_options(context)
    monkeypatch.setattr(validation_attachments, "_options_for_agent_entry", lambda entry: (option,))
    definition = _flow_definition(context.receipt.agent_key,
        extra_nodes=[_validator_node("replacement", "replacement_output")],
        edges=[{"id": "input", "source": "task_1", "target": "extract_1"},
               {"id": "sidecar", "source": "extract_1", "target": "replacement",
                "role": "validation_attachment", "replaces_attachment_id": option.attachment_id}])
    definition.nodes[1].data.agent_revision_id = context.receipt.agent_revision_id
    definition.nodes[1].data.execution_receipt = context.receipt
    with pytest.raises(validation_attachments.FlowValidationAttachmentError, match="pinned"):
        validation_attachments.apply_flow_validation_attachment_defaults(definition,
            entries_by_node={"extract_1": {}})


@pytest.mark.asyncio
@pytest.mark.parametrize("group_count", [0, 2])
async def test_flow_requires_each_saved_mapping_exactly_once(example, group_count):
    source, context = prepared(example)
    group = {"state": "automatic", "binding_id": context.registry.bindings[0].binding_id}
    with pytest.raises(ValueError, match="once each"):
        await executor._collect_flow_validator_materialization_inputs(
            source_envelope=source, source_envelope_revision=1, registry=context.registry,
            groups=[group] * group_count, flow=SimpleNamespace(flow_definition={}),
            agent_context={}, profile_context=context)


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_second,omit_groups", [(False, False), (True, False), (False, True)])
async def test_flow_step_commits_profile_record_atomically(example, monkeypatch, invalid_second, omit_groups):
    source, context = prepared(example, per_element=True,
        attributes={"records": [{"paper_name": "A"}, {"paper_name": "B"}]})
    items = results(source, context, [{"identifier": "EX:1"}, {"identifier": 2 if invalid_second else "EX:2"}])
    results_by_request = {item.request.request_id: item.result for item in items}
    monkeypatch.setattr("src.lib.domain_packs.validator_dispatch.run_package_scoped_validator_agent",
        lambda request, **kwargs: results_by_request[request.request_id].model_dump(mode="json"))
    monkeypatch.setattr(executor, "materialize_validator_results_into_envelope",
                        lambda *a, **kw: pytest.fail("Profile entered packaged writer"))
    monkeypatch.setattr(executor, "_persist_flow_extraction_candidates", lambda **kwargs: [object()])
    monkeypatch.setattr(executor, "ensure_domain_envelope_materialization",
                        lambda *args, **kwargs: SimpleNamespace(envelope_id=source.envelope_id))
    row = SimpleNamespace(envelope_json=source.model_dump(mode="json"), revision=1,
        project_key="fixture", document_id=None, session_id=None, flow_run_id=None,
        object_model_ref_json={}, model_field_ref_json={})
    db = SimpleNamespace(get=lambda *args: row, commit=Mock(), rollback=Mock(), close=Mock())
    monkeypatch.setattr(executor, "SessionLocal", lambda: db)
    monkeypatch.setattr(executor, "resolve_curation_domain_pack_by_id", lambda key: example[2])
    monkeypatch.setattr("src.lib.curation_workspace.execution_contracts.resolve_receipt_profile",
                        lambda session, receipt: context.profile)
    monkeypatch.setattr("src.lib.domain_packs.profile_validation.capability_catalog", lambda **kwargs: [example[1]])
    checkpoints = []
    monkeypatch.setattr(executor, "write_domain_envelope_checkpoint",
        lambda session, request: (checkpoints.append(request), SimpleNamespace(revision=2))[1])
    if omit_groups:
        from src.lib.curation_workspace.extraction_results import ExtractionEnvelopeCandidate
        with pytest.raises(ValueError, match="once each"):
            await executor._execute_validation_groups_for_step(
                flow=SimpleNamespace(id="flow", name="Profile", flow_definition={}),
                candidate=ExtractionEnvelopeCandidate(agent_key=context.receipt.agent_key,
                    payload_json=source.model_dump(mode="json"), execution_receipt=context.receipt),
                node_data={"validation_groups": []}, document_id="document", user_id="curator",
                session_id="session", flow_run_id="run", agent_context={}, flow_conversation_summary="")
        assert not checkpoints
        db.rollback.assert_called_once()
        return
    result = await executor._execute_validation_groups_for_step(
        flow=SimpleNamespace(id="flow", name="Profile", flow_definition={"nodes": []}), candidate=object(),
        node_data={"validation_groups": [{"group_id": "profile", "state": "automatic",
                    "binding_id": context.registry.bindings[0].binding_id}]},
        document_id="document", user_id="curator", session_id="session", flow_run_id="run",
        agent_context={}, flow_conversation_summary="Profile fixture",
    )
    assert result["validation_group_results"]["materialized_envelope_revision"] == 2
    saved = checkpoints[0].envelope
    records = saved.extracted_objects[0].payload["attributes"]["records"]
    if invalid_second:
        assert saved.extracted_objects == source.extracted_objects
    else:
        assert [record["resolved_id"] for record in records] == ["EX:1", "EX:2"]
    assert source.extracted_objects[0].payload["attributes"]["records"] == [{"paper_name": "A"}, {"paper_name": "B"}]
    db.commit.assert_called_once()
    db.rollback.assert_not_called()
    db.close.assert_called_once()

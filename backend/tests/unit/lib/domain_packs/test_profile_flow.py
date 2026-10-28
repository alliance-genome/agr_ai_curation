"""Flow defaults and execution retain the exact selected profile mappings."""

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.validation_history_unit_support import isolated_validation_history as isolated_validation_history


from src.lib.domain_packs.profile_validation import compile_profile_validation, profile_validation_attachment_options
from src.lib.domain_packs.validation_registry import DomainPackValidationRegistry
from src.lib.flows import executor, validation_attachments
from .test_profile_validation import example as example, profile_envelope, resolve
from .test_profile_materialization import prepared, results
from ..flows.test_validation_attachments import _flow_definition, _validator_node


pytestmark = pytest.mark.usefixtures("isolated_validation_history")


@pytest.mark.asyncio
async def test_same_label_profile_mappings_survive_flow_default_hydration(example, monkeypatch):
    raw, capability, pack = example
    raw["fields"][1]["required"] = True
    mapping = raw["validator_mappings"][0]
    mapping["outputs"] = {}
    second = deepcopy(mapping)
    second.update(mapping_id="lookup_id", inputs={"mention": {"field_path": "attributes.resolved_id"}})
    raw["validator_mappings"].append(second)
    receipt, profile = resolve(raw)
    context = compile_profile_validation(receipt, profile, pack, capabilities=[capability])
    assert not context.unavailable
    assert len(context.registry.bindings) == 2
    options = profile_validation_attachment_options(context)
    assert len(options) == 2
    assert options[0].label == options[1].label
    monkeypatch.setattr(validation_attachments, "_options_for_agent_entry", lambda entry, *, db=None: options)
    definition = _flow_definition(receipt.agent_key)
    node = definition.nodes[1]
    node.data.agent_revision_id = receipt.agent_revision_id
    node.data.execution_receipt = receipt
    # A saved flow may carry the old, collapsed derived selection. Defaults must
    # restore the missing automatic mapping without changing its pinned receipt.
    node.data.validation_attachments = [
        validation_attachments.FlowValidationAttachmentSelection(**options[0].to_dict(), enabled=True)
    ]
    hydrated = validation_attachments.apply_flow_validation_attachment_defaults(
        definition, entries_by_node={node.id: {}}
    )
    assert len(definition.nodes[1].data.validation_attachments) == 1
    assert hydrated.nodes[1].data.execution_receipt == receipt
    assert {group.binding_id for group in hydrated.nodes[1].data.validation_groups} == {
        binding.binding_id for binding in context.registry.bindings
    }
    assert all(group.state == "automatic" for group in hydrated.nodes[1].data.validation_groups)
    source = profile_envelope({"paper_name": "A", "resolved_id": "EX:1"}, receipt, profile)
    items = results(source, context, [{}, {}])
    results_by_request = {item.request.request_id: item.result for item in items}
    invoked = []

    def run_validator(request, **kwargs):
        invoked.append(request.request_id)
        return results_by_request[request.request_id].model_dump(mode="json")

    monkeypatch.setattr("src.lib.domain_packs.validator_dispatch.run_package_scoped_validator_agent", run_validator)
    plan = prepare_saved_profile(source, context, example[2],
        [group.model_dump(mode="json") for group in hydrated.nodes[1].data.validation_groups], monkeypatch)
    from src.lib.domain_packs.validator_dispatch import dispatch_validator_jobs
    dispatched = dispatch_validator_jobs(list(plan.initial_jobs), history_binding=plan.history_binding)
    assert not plan.findings
    assert len(dispatched.materialization_inputs) == 2
    assert sorted(invoked) == sorted(results_by_request)

    # The execute path must hydrate too: merely fixing the editor would leave
    # already-saved flows broken when run directly or from a batch.
    runtime_entry = {"name": "Fixture extractor", "requires_document": False,
                     "execution_receipt": receipt.model_dump(mode="json"),
                     "curation": {"domain_pack_id": "generic"}, "supervisor": {}}
    monkeypatch.setattr(executor, "_runtime_custom_entries", lambda *args, **kwargs: {node.id: runtime_entry})
    runtime_definitions = []
    monkeypatch.setattr(executor, "_runtime_output_sources_by_node_id",
                        lambda flow, **kwargs: (runtime_definitions.append(flow.flow_definition), {})[1])
    monkeypatch.setattr(executor, "get_agent_by_id", lambda *args, **kwargs: SimpleNamespace(name="Fixture"))

    async def unused_tool(query: str) -> str:
        pytest.fail("Hydration must not execute a tool")

    monkeypatch.setattr(executor, "_create_streaming_tool", lambda **kwargs: unused_tool)
    saved_definition = definition.model_dump(mode="json")
    saved_flow = SimpleNamespace(id="fixture", name="Fixture", flow_definition=deepcopy(saved_definition))
    tools, _ = executor.get_all_agent_tools(saved_flow, db_user_id=1)
    assert len(tools) == 1
    assert saved_flow.flow_definition == saved_definition
    runtime_node = runtime_definitions[0]["nodes"][1]
    assert runtime_node["data"]["execution_receipt"] == receipt.model_dump(mode="json")
    assert len(runtime_node["data"]["validation_groups"]) == 2


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


def test_flow_authoring_rejects_profile_validator_sidecar(example, monkeypatch):
    source, context = prepared(example)
    option, = profile_validation_attachment_options(context)
    monkeypatch.setattr(validation_attachments, "_options_for_agent_entry", lambda entry, *, db=None: (option,))
    definition = _flow_definition(context.receipt.agent_key,
        extra_nodes=[_validator_node("replacement", "replacement_output")],
        edges=[{"id": "input", "source": "task_1", "target": "extract_1"},
               {"id": "sidecar", "source": "extract_1", "target": "replacement",
                "role": "validation_attachment", "replaces_attachment_id": option.attachment_id}])
    definition.nodes[1].data.agent_revision_id = context.receipt.agent_revision_id
    definition.nodes[1].data.execution_receipt = context.receipt
    with pytest.raises(validation_attachments.FlowValidationAttachmentError, match="not supported"):
        validation_attachments.apply_flow_validation_attachment_defaults(definition,
            entries_by_node={"extract_1": {}})


def prepare_saved_profile(source, context, pack, groups, monkeypatch):
    from src.lib.validation_review.flow_plan import prepare
    from src.lib.domain_packs.validator_dispatch import ValidatorRuntimeContext
    monkeypatch.setattr('src.lib.domain_packs.profile_validation.resolve_envelope_profile_validation',
                        lambda *a, **kw: context)
    return prepare(source, pack, groups=groups, nodes={}, runtime_context=ValidatorRuntimeContext(
        user_id='unit-owner', document_id=None, authenticated_groups=()))


@pytest.mark.parametrize('groups', [[], ['duplicate', 'duplicate'], ['wrong'], ['skipped'], ['replaced'], ['supplemental']])
def test_shared_flow_plan_requires_exact_automatic_profile_mappings(example, monkeypatch, groups):
    from src.lib.validation_history import store
    source, context = prepared(example)
    binding_id = context.registry.bindings[0].binding_id
    selections = [{'binding_id': 'foreign' if state == 'wrong' else binding_id,
                   'state': state if state in {'skipped', 'replaced', 'supplemental'} else 'automatic'} for state in groups]
    with pytest.raises(store.HistoryConflict, match='exact saved profile'):
        prepare_saved_profile(source, context, example[2], selections, monkeypatch)


def test_shared_flow_plan_cannot_dispatch_revoked_profile_mapping(example, monkeypatch):
    from src.lib.validation_history import store
    source, context = prepared(example)
    binding_id = context.registry.bindings[0].binding_id
    revoked = compile_profile_validation(context.receipt, context.profile, example[2],
        capabilities=[replace(example[1], available=False, unavailable_reason='Revoked')])
    with pytest.raises(store.HistoryConflict, match='unavailable validator binding'):
        prepare_saved_profile(source, revoked, example[2], [{'binding_id': binding_id, 'state': 'automatic'}], monkeypatch)

"""Exact custom flow resolution and per-node authoring contracts."""

from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from src.lib.agent_studio.authoring_validation import (
    AuthoringValidationContext, validate_flow_authoring_draft,
)
from src.lib.flows import execution_revisions as module
from src.schemas.agent_execution_revision import AgentExecutionReceipt, AgentOutputContract
from src.schemas.flows import FlowDefinition


def flow(*receipts):
    nodes = [{
        "id": "task", "type": "task_input", "position": {"x": 0, "y": 0},
        "data": {"agent_id": "task_input", "agent_display_name": "Task",
                 "output_key": "task", "task_instructions": "Extract"},
    }]
    for index, receipt in enumerate(receipts):
        data = {"agent_id": "ca_fixture", "agent_display_name": "Fixture",
                "output_key": f"result_{index}"}
        if receipt:
            data.update(agent_revision_id=receipt.agent_revision_id,
                        execution_receipt=receipt)
        nodes.append({"id": f"node_{index}", "type": "agent",
                      "position": {"x": 100, "y": 100}, "data": data})
    return FlowDefinition.model_validate({
        "nodes": nodes, "entry_node_id": "task",
        "edges": [{"id": f"edge_{i}", "source": "task" if i == 0 else f"node_{i - 1}", "target": f"node_{i}"}
                  for i in range(len(receipts))],
    })


def receipt(mode):
    contract = {"output_state": "none"} if mode is None else {
        "output_state": "structured_extraction", "output_mode": mode,
    }
    if mode == "domain":
        contract["output_schema_key"] = "FixtureEnvelope"
    if mode == "profile_bound_generic":
        contract["generic_profile_ref"] = {
            "profile_id": uuid4(), "profile_revision_id": uuid4(),
            "revision": 3, "fingerprint": "sha256:" + "b" * 64,
        }
    return AgentExecutionReceipt(
        agent_id=uuid4(), agent_key="ca_fixture", agent_revision_id=uuid4(),
        revision=4, fingerprint="sha256:" + "a" * 64,
        output_contract=AgentOutputContract.model_validate(contract),
    )


def install_resolver(monkeypatch, receipts):
    by_id = {item.agent_revision_id: item for item in receipts}
    authorize = Mock(side_effect=lambda db, payload, user_id, **kw:
                     AgentExecutionReceipt.model_validate(payload))
    def read(db, agent_id, revision_id, user_id, **kwargs):
        item = by_id[revision_id]
        return SimpleNamespace(id=revision_id, revision=item.revision,
                               fingerprint=item.fingerprint), SimpleNamespace(
            output_contract=item.output_contract, tool_ids=[], curation=None,
            structured_finalization=None,
        )
    lookup = Mock(side_effect=read)
    monkeypatch.setattr(module, "authorize_execution_receipt", authorize)
    monkeypatch.setattr(module, "get_execution_revision", lookup)
    return authorize, lookup


def projection_flow(pin, ref="object.attribute.count", *, numeric_filter=False):
    data = flow(pin).model_dump(mode="json")
    plan = {"format": "tsv", "row_source": "object", "columns": [{"key": "value", "field_ref": ref}]}
    if numeric_filter:
        plan["filters"] = [{"field_ref": ref, "op": "gt", "value": 0}]
    data["nodes"].append({"id": "output", "type": "output", "position": {"x": 200, "y": 0},
                          "data": {"agent_id": "tsv_formatter", "agent_display_name": "TSV",
                                   "output_key": "export", "projection_plan": plan}})
    data["edges"].append({"id": "output-edge", "source": "node_0", "target": "output", "role": "output_attachment"})
    return FlowDefinition.model_validate(data)


def profile_receipt_and_db(kind="integer"):
    from src.schemas.generic_extraction_profile import GenericProfileContract
    contract = GenericProfileContract.model_validate({"name": "Records", "semantic_class": "record", "fields": [
        {"key": "count", "value_schema": {"kind": kind}},
        {"key": "sources", "value_schema": {"kind": "array", "items": {"kind": "object", "fields": [
            {"key": "name", "value_schema": {"kind": "string"}},
        ]}}},
    ]})
    pin = receipt("profile_bound_generic")
    profile_ref = pin.output_contract.generic_profile_ref.model_copy(update={"fingerprint": contract.fingerprint()})
    pin = pin.model_copy(update={"output_contract": pin.output_contract.model_copy(update={"generic_profile_ref": profile_ref})})
    db = Mock()
    db.get.return_value = SimpleNamespace(profile_id=profile_ref.profile_id, revision=profile_ref.revision,
                                         fingerprint=profile_ref.fingerprint, contract=contract.model_dump(mode="json"))
    return pin, db


def test_projection_discovery_uses_saved_profile_not_observed_values(monkeypatch):
    pin, db = profile_receipt_and_db()
    install_resolver(monkeypatch, [pin])
    definition = projection_flow(pin, "object.attribute.sources[].name")
    original = definition.model_dump(mode="json")
    resolved = module.resolve_flow_execution_revisions(db, definition, user_id=7, active_group_ids=[])
    assert not resolved.findings
    fields = resolved.entries_by_node["node_0"]["projection_fields"]
    child = next(field for field in fields if field["ref"] == "object.attribute.sources[].name")
    assert child["value_type"] == "list"
    assert child["array_depth"] == 1
    assert definition.model_dump(mode="json") == original
    assert "projection_fields" not in resolved.definition.model_dump_json()


def test_missing_or_renamed_projection_field_blocks_saved_profile_flow(monkeypatch):
    pin, db = profile_receipt_and_db()
    install_resolver(monkeypatch, [pin])
    result = module.resolve_flow_execution_revisions(db, projection_flow(pin, "object.attribute.old_name"), user_id=7, active_group_ids=[])
    assert [finding.code for finding in result.findings] == ["undeclared_projection_field"]
    assert result.findings[0].node_id == "output"
    assert result.findings[0].severity == "error"


@pytest.mark.parametrize("kind,valid", [("integer", True), ("string", False)])
def test_saved_projection_numeric_filter_checks_declared_type(monkeypatch, kind, valid):
    pin, db = profile_receipt_and_db(kind)
    install_resolver(monkeypatch, [pin])
    result = module.resolve_flow_execution_revisions(db, projection_flow(pin, numeric_filter=True), user_id=7, active_group_ids=[])
    assert (not result.findings) is valid
    if not valid:
        assert result.findings[0].code == "incompatible_projection_field_type"


@pytest.mark.parametrize("kind,ref,op,valid", [
    ("integer", "object.attribute.count", "gt", True),
    ("number", "object.attribute.count", "lte", True),
    ("string", "object.attribute.count", "gt", False),
    ("boolean", "object.attribute.count", "gte", False),
    ("integer", "object.attribute.sources[].name", "lt", False),
    ("string", "object.attribute.count", "eq", True),
])
def test_saved_profile_checks_conditional_numeric_predicate(monkeypatch, kind, ref, op, valid):
    pin, db = profile_receipt_and_db(kind)
    install_resolver(monkeypatch, [pin])
    definition = projection_flow(pin)
    definition.nodes[-1].data.projection_plan["columns"] = [{"key": "value", "transform": {
        "type": "conditional", "field_ref": ref, "condition_op": op, "value": 1,
        "when_true": {"type": "literal", "value": "Yes"},
        "when_false": {"type": "literal", "value": "No"},
    }}]
    original = definition.model_dump(mode="json")
    result = module.resolve_flow_execution_revisions(db, definition, user_id=7, active_group_ids=[])
    assert (not result.findings) is valid
    if not valid:
        assert [finding.code for finding in result.findings] == ["incompatible_projection_field_type"]
        assert ref in result.findings[0].message
    assert definition.model_dump(mode="json") == original


def test_explicit_retarget_reverifies_formatter_against_new_profile(monkeypatch):
    old, old_db = profile_receipt_and_db("integer")
    new, new_db = profile_receipt_and_db("string")
    new = new.model_copy(update={"agent_id": old.agent_id, "revision": old.revision + 1})
    install_resolver(monkeypatch, [old, new])
    rows = {old.output_contract.generic_profile_ref.profile_revision_id: old_db.get.return_value,
            new.output_contract.generic_profile_ref.profile_revision_id: new_db.get.return_value}
    db = Mock()
    db.get.side_effect = lambda model, key: rows[key]
    saved = projection_flow(old, numeric_filter=True)
    assert not module.resolve_flow_execution_revisions(db, saved, user_id=7, active_group_ids=[]).findings
    retargeted = saved.model_copy(deep=True)
    retargeted.nodes[1].data.agent_revision_id = new.agent_revision_id
    retargeted.nodes[1].data.execution_receipt = new
    resolved = module.resolve_flow_execution_revisions(db, retargeted, user_id=7, active_group_ids=[])
    assert resolved.findings[0].code == "incompatible_projection_field_type"
    assert saved.nodes[1].data.agent_revision_id == old.agent_revision_id
    assert resolved.definition.nodes[1].data.agent_revision_id == new.agent_revision_id


@pytest.mark.parametrize("selector", [None, "source_keys", "source_extraction_result_ids"])
def test_mixed_profile_types_require_runtime_source_check_only_for_selective_plans(monkeypatch, selector):
    numeric, numeric_db = profile_receipt_and_db("integer")
    text, text_db = profile_receipt_and_db("string")
    install_resolver(monkeypatch, [numeric, text])
    rows = {numeric.output_contract.generic_profile_ref.profile_revision_id: numeric_db.get.return_value,
            text.output_contract.generic_profile_ref.profile_revision_id: text_db.get.return_value}
    db = Mock()
    db.get.side_effect = lambda model, key: rows[key]
    definition = flow(numeric, text).model_dump(mode="json")
    output = projection_flow(numeric, numeric_filter=True).model_dump(mode="json")["nodes"][-1]
    output["data"]["projection_plan"]["row_strategy"] = "object_ledger"
    if selector:
        output["data"]["projection_plan"][selector] = ["runtime-source-identity"]
    definition["nodes"].append(output)
    definition["edges"].extend([
        {"id": f"output-{i}", "source": f"node_{i}", "target": "output", "role": "output_attachment"}
        for i in range(2)
    ])
    result = module.resolve_flow_execution_revisions(db, FlowDefinition.model_validate(definition), user_id=7, active_group_ids=[])
    assert len(result.findings) == 1
    assert result.findings[0].severity == ("warning" if selector else "error")
    assert result.findings[0].code == (
        "runtime_projection_source_type_check" if selector else "incompatible_projection_field_type"
    )


@pytest.mark.parametrize("system", [False, True])
@pytest.mark.parametrize("ref,expected_code", [
    ("object.attribute.packaged_only", None),
    ("object.attribute.count", "runtime_projection_source_type_check"),
])
def test_mixed_packaged_source_is_not_reinterpreted_as_a_custom_profile(monkeypatch, system, ref, expected_code):
    profile, db = profile_receipt_and_db("string")
    domain = receipt("domain")
    install_resolver(monkeypatch, [profile, domain])
    definition = flow(profile, domain).model_dump(mode="json")
    if system:
        definition["nodes"][2]["data"] = {
            "agent_id": "gene_extractor", "agent_display_name": "Gene", "output_key": "packaged",
        }
    output = projection_flow(profile, ref, numeric_filter=ref.endswith("count")).model_dump(mode="json")["nodes"][-1]
    output["data"]["projection_plan"]["source_keys"] = ["runtime-packaged-source"]
    definition["nodes"].append(output)
    definition["edges"].extend([
        {"id": f"output-{i}", "source": f"node_{i}", "target": "output", "role": "output_attachment"}
        for i in range(2)
    ])
    result = module.resolve_flow_execution_revisions(db, FlowDefinition.model_validate(definition), user_id=7, active_group_ids=[])
    assert [(finding.code, finding.severity) for finding in result.findings] == (
        [(expected_code, "warning")] if expected_code else []
    )
    assert "projection_fields" not in (result.entries_by_node.get("node_1") or {})


def test_authoring_response_exposes_exact_profile_field_catalog(monkeypatch):
    pin, db = profile_receipt_and_db()
    install_resolver(monkeypatch, [pin])
    resolved = module.resolve_flow_execution_revisions(db, projection_flow(pin), user_id=7, active_group_ids=[])
    result = validate_flow_authoring_draft(
        resolved.definition, context=AuthoringValidationContext.from_values(db_user_id=7),
        resolve_agent=lambda *args: {}, apply_attachment_defaults=lambda value: value,
        enforce_agent_references=False, enforce_agent_step_policy=False,
        entries_by_node=resolved.entries_by_node, contract_findings=resolved.findings,
    )
    catalog = result.to_dict()["projection_fields_by_node"]["node_0"]
    assert catalog["execution_receipt"] == pin.model_dump(mode="json")
    assert any(field["ref"] == "object.attribute.sources[].name" for field in catalog["fields"])


def test_nested_transform_references_are_checked_with_runtime_reference_rules(monkeypatch):
    pin, db = profile_receipt_and_db()
    install_resolver(monkeypatch, [pin])
    definition = projection_flow(pin)
    definition.nodes[-1].data.projection_plan["columns"] = [{"key": "value", "transform": {
        "type": "conditional", "field_ref": "object.attribute.count", "condition_op": "is_empty",
        "when_true": {"type": "literal", "value": "None"},
        "when_false": {"type": "concat", "values": ["object.attribute.renamed", {"field_ref": "object.attribute.sources[].name"}]},
    }}]
    result = module.resolve_flow_execution_revisions(db, definition, user_id=7, active_group_ids=[])
    assert len(result.findings) == 1
    assert "object.attribute.renamed" in result.findings[0].message


@pytest.mark.parametrize("mode", [None, "domain", "unprofiled_generic"])
def test_only_unprofiled_generic_warns_without_becoming_preflight_blocker(monkeypatch, mode):
    pin = receipt(mode)
    install_resolver(monkeypatch, [pin])
    definition = projection_flow(pin, "object.attribute.unknown")
    db = Mock()
    result = module.resolve_flow_execution_revisions(db, definition, user_id=7, active_group_ids=[])
    if mode == "unprofiled_generic":
        assert result.findings[0].code == "unprofiled_projection_field"
        assert result.findings[0].severity == "warning"
    else:
        assert not result.findings
    assert not module.flow_execution_revision_findings(db, definition.model_dump(mode="json"), user_id=7, active_group_ids=[])
    db.get.assert_not_called()


@pytest.mark.parametrize("mode", [None, "domain", "profile_bound_generic", "unprofiled_generic"])
def test_exact_resolution_preserves_every_output_state_without_reading_head(monkeypatch, mode):
    pin = receipt(mode)
    authorize, lookup = install_resolver(monkeypatch, [pin])
    db = Mock()
    original = flow(pin)
    result = module.resolve_flow_execution_revisions(db, original, user_id=7, active_group_ids=["TEAM_C"])
    assert not result.findings
    assert result.definition.nodes[1].data.execution_receipt == pin
    assert result.definition is not original
    assert result.entries_by_node["node_0"]["produces_flow_artifacts"] == (mode is not None)
    db.execute.assert_not_called()
    authorize.assert_called_once_with(db, pin.model_dump(mode="json"), 7, active_group_ids=["TEAM_C"])
    lookup.assert_called_once_with(db, pin.agent_id, pin.agent_revision_id, 7, active_group_ids=["TEAM_C"])


def test_same_agent_different_revisions_do_not_collapse(monkeypatch):
    first, second = receipt(None), receipt("profile_bound_generic")
    install_resolver(monkeypatch, [first, second])
    resolved = module.resolve_flow_execution_revisions(Mock(), flow(first, second), user_id=7, active_group_ids=[])
    assert not resolved.entries_by_node["node_0"]["produces_flow_artifacts"]
    assert resolved.entries_by_node["node_1"]["produces_flow_artifacts"]
    fallback = Mock(side_effect=AssertionError("Must not resolve mutable custom metadata"))
    result = validate_flow_authoring_draft(
        resolved.definition, context=AuthoringValidationContext.from_values(db_user_id=7),
        resolve_agent=fallback, apply_attachment_defaults=lambda value: value,
        entries_by_node=resolved.entries_by_node, contract_findings=resolved.findings,
    )
    assert result.valid
    fallback.assert_not_called()


def test_missing_pin_is_a_node_finding_without_head_lookup():
    db = Mock()
    result = module.resolve_flow_execution_revisions(db, flow(None), user_id=7, active_group_ids=[])
    assert result.findings[0].code == "missing_execution_revision"
    assert result.findings[0].node_id == "node_0"
    assert result.definition.nodes[1].data.execution_receipt is None
    db.execute.assert_not_called()


@pytest.mark.parametrize("error,code", [
    (module.ExecutionRevisionNotFoundError("private detail"), "unavailable_execution_revision"),
    (ValueError("private contract bytes"), "execution_contract_mismatch"),
])
def test_unavailable_or_mismatched_receipt_fails_closed(monkeypatch, error, code):
    pin = receipt("profile_bound_generic")
    authorize, lookup = install_resolver(monkeypatch, [pin])
    authorize.side_effect = error
    resolved = module.resolve_flow_execution_revisions(Mock(), flow(pin), user_id=7, active_group_ids=[])
    assert resolved.findings[0].code == code
    assert "private" not in resolved.findings[0].message
    assert resolved.entries_by_node == {"node_0": None}
    lookup.assert_not_called()


def test_explicit_revision_selection_derives_its_own_receipt(monkeypatch):
    pin = receipt("profile_bound_generic")
    authorize, lookup = install_resolver(monkeypatch, [pin])
    candidate = flow(pin)
    candidate.nodes[1].data.execution_receipt = None
    db = Mock()
    db.execute.return_value.scalar_one_or_none.return_value = pin.agent_id
    resolved = module.resolve_flow_execution_revisions(db, candidate, user_id=7, active_group_ids=[])
    assert not resolved.findings
    assert resolved.definition.nodes[1].data.execution_receipt == pin
    assert candidate.nodes[1].data.execution_receipt is None
    authorize.assert_not_called()
    lookup.assert_called_once()


def test_executor_builds_each_exact_pin_without_mutable_metadata(monkeypatch):
    from contextlib import nullcontext
    from unittest.mock import MagicMock
    from src.lib.flows import executor

    first, second = receipt(None), receipt("unprofiled_generic")
    second.agent_id = first.agent_id
    second.revision = 5
    install_resolver(monkeypatch, [first, second])
    monkeypatch.setattr(executor, "SessionLocal", lambda: nullcontext(Mock()))
    lookup = Mock(side_effect=AssertionError("Mutable metadata lookup"))
    monkeypatch.setattr(executor, "get_agent_metadata", lookup)
    build = Mock(return_value=SimpleNamespace(instructions="Saved instructions"))
    monkeypatch.setattr(executor, "get_agent_by_id", build)
    monkeypatch.setattr(executor, "_create_streaming_tool", Mock(return_value=Mock()))
    saved_flow = MagicMock(name="Saved flow")
    saved_flow.name = "Saved flow"
    saved_flow.flow_definition = flow(first, second).model_dump(mode="json")
    original = saved_flow.flow_definition.copy()
    _, names = executor.get_all_agent_tools(saved_flow, db_user_id=7, active_groups=["TEAM_C"])
    assert len(names) == 2
    assert [call.kwargs["execution_revision_id"] for call in build.call_args_list] == [
        str(first.agent_revision_id), str(second.agent_revision_id),
    ]
    assert [call.kwargs["execution_receipt"] for call in build.call_args_list] == [
        first.model_dump(mode="json"), second.model_dump(mode="json"),
    ]
    assert saved_flow.flow_definition == original
    lookup.assert_not_called()


def test_executor_rejects_missing_revision_before_building_any_specialist(monkeypatch):
    from contextlib import nullcontext
    from src.lib.flows import executor

    monkeypatch.setattr(executor, "SessionLocal", lambda: nullcontext(Mock()))
    build = Mock()
    monkeypatch.setattr(executor, "get_agent_by_id", build)
    saved_flow = SimpleNamespace(flow_definition=flow(None).model_dump(mode="json"))
    with pytest.raises(module.FlowExecutionRevisionError) as error:
        executor.get_all_agent_tools(saved_flow, db_user_id=7)
    assert error.value.findings[0]["code"] == "missing_execution_revision"
    build.assert_not_called()


def test_shared_batch_and_chat_access_guard_checks_each_saved_receipt(monkeypatch):
    from src.lib.agent_studio import agent_service, execution_revision_service

    first, second = receipt(None), receipt("profile_bound_generic")
    second.agent_id = first.agent_id
    current_lookup = Mock(side_effect=AssertionError("Mutable custom head policy"))
    monkeypatch.setattr(agent_service, "get_agent_by_key", current_lookup)
    authorize = Mock(side_effect=lambda db, payload, user_id, **kw:
                     AgentExecutionReceipt.model_validate(payload))
    monkeypatch.setattr(execution_revision_service, "authorize_execution_receipt", authorize)
    definition = flow(first, second).model_dump(mode="json")
    assert agent_service.inaccessible_flow_agent_keys(Mock(), definition, user_id=7,
                                                     active_group_ids=iter(["TEAM_C"])) == []
    assert authorize.call_count == 2
    assert all(call.kwargs["active_group_ids"] == ["TEAM_C"] for call in authorize.call_args_list)
    current_lookup.assert_not_called()
    authorize.side_effect = [first, ValueError("Unauthorized revision")]
    assert agent_service.inaccessible_flow_agent_keys(Mock(), definition, user_id=7,
                                                     active_group_ids=["TEAM_C"]) == ["ca_fixture"]


def test_http_preflight_returns_safe_node_findings_and_never_floats():
    db = Mock()
    findings = module.flow_execution_revision_findings(
        db, flow(None).model_dump(mode="json"), user_id=7, active_group_ids=[],
    )
    assert findings[0]["node_id"] == "node_0"
    assert findings[0]["code"] == "missing_execution_revision"
    db.execute.assert_not_called()


def test_http_preflight_preserves_system_only_behavior():
    assert module.flow_execution_revision_findings(Mock(), {"nodes": []}, user_id=7, active_group_ids=[]) == []


def test_ai_exact_flow_verification_uses_pinned_nodes_not_palette(monkeypatch):
    from contextlib import nullcontext
    from src.lib.agent_studio import flow_tools
    from src.models.sql import database

    pin = receipt("profile_bound_generic")
    install_resolver(monkeypatch, [pin])
    monkeypatch.setattr(database, "SessionLocal", lambda: nullcontext(Mock()))
    monkeypatch.setattr(flow_tools, "get_current_user_id", lambda: 7)
    monkeypatch.setattr(flow_tools, "get_current_active_group_ids", lambda: ["TEAM_C"])
    monkeypatch.setattr(flow_tools, "_accessible_flow_agents", lambda: {})
    mutable = Mock(side_effect=AssertionError("Current palette must not resolve saved custom nodes"))
    monkeypatch.setattr(flow_tools, "resolve_live_flow_agent", mutable)
    result = flow_tools._validate_exact_flow_for_current_user(flow(pin), phase="pre_apply")
    assert result.valid
    assert result.candidate.nodes[1].data.execution_receipt == pin
    mutable.assert_not_called()


def test_ai_initial_manifest_marks_missing_custom_pin_critical(monkeypatch):
    from contextlib import nullcontext
    from src.lib.agent_studio import flow_tools
    from src.models.sql import database

    monkeypatch.setattr(database, "SessionLocal", lambda: nullcontext(Mock()))
    monkeypatch.setattr(flow_tools, "get_current_user_id", lambda: 7)
    monkeypatch.setattr(flow_tools, "get_current_active_group_ids", lambda: [])
    monkeypatch.setattr(flow_tools, "get_current_flow_context", lambda: flow(None).model_dump(mode="json"))
    manifest = flow_tools._build_current_flow_manifest()
    assert manifest["has_critical_issues"] is True
    assert any(item["code"] == "missing_execution_revision" for item in manifest["findings"])


def test_ai_new_step_selects_catalog_revision_and_edits_preserve_it():
    from src.lib.agent_studio import flow_tools

    pin = receipt(None)
    candidate = flow().model_dump(mode="json")
    flow_tools._compile_flow_operations(candidate=candidate, metadata={}, semantic_refs={},
        accessible_agents={"ca_fixture": {"name": "Custom", "agent_revision_id": str(pin.agent_revision_id)}},
        operations=[{"operation": "add_agent_step", "agent_id": "ca_fixture"}])
    node = candidate["nodes"][-1]
    assert node["data"]["agent_revision_id"] == str(pin.agent_revision_id)
    flow_tools._compile_flow_operations(candidate=candidate, metadata={}, semantic_refs={},
        accessible_agents={"ca_fixture": {"agent_revision_id": str(uuid4())}},
        operations=[{"operation": "update_step", "node_id": node["id"], "step_goal": "Changed goal"}])
    assert node["data"]["agent_revision_id"] == str(pin.agent_revision_id)


def test_ai_retarget_discards_old_receipt_and_resolves_new_revision(monkeypatch):
    from src.lib.agent_studio import flow_tools

    old, new = receipt("profile_bound_generic"), receipt("unprofiled_generic")
    new.agent_id = old.agent_id
    candidate = flow(old).model_dump(mode="json")
    from copy import deepcopy
    other_use = deepcopy(candidate["nodes"][1])
    other_use["id"] = "other_use"
    other_use["data"]["output_key"] = "other_result"
    candidate["nodes"].append(other_use)
    original_other_use = deepcopy(other_use)
    flow_tools._compile_flow_operations(candidate=candidate, metadata={}, semantic_refs={}, accessible_agents={},
        operations=[{"operation": "retarget_agent_revision", "node_id": "node_0",
                     "agent_revision_id": str(new.agent_revision_id)}])
    data = candidate["nodes"][1]["data"]
    assert data["agent_revision_id"] == str(new.agent_revision_id)
    assert "execution_receipt" not in data
    assert candidate["nodes"][2] == original_other_use
    assert len(candidate["nodes"]) == 3
    candidate["nodes"].pop()  # Existing resolver assertions below concern the changed path.
    install_resolver(monkeypatch, [new])
    db = Mock()
    db.execute.return_value.scalar_one_or_none.return_value = new.agent_id
    resolved = module.resolve_flow_execution_revisions(db, FlowDefinition.model_validate(candidate),
                                                      user_id=7, active_group_ids=[])
    assert not resolved.findings
    assert resolved.definition.nodes[1].data.execution_receipt == new
    assert resolved.definition.nodes[1].data.execution_receipt.output_contract.generic_profile_ref is None
@pytest.mark.parametrize("mode,expected", [(None, []), ("unprofiled_generic", ["pdf_extraction"]),
                                          ("profile_bound_generic", ["pdf_extraction"])])
def test_batch_capability_derives_from_saved_output_and_tools(monkeypatch, mode, expected):
    from src.lib.agent_studio import catalog_service

    pin = receipt(mode)
    install_resolver(monkeypatch, [pin])
    monkeypatch.setattr(catalog_service, "_required_context_for_tool_ids", lambda _tools: ["document_id"])
    result = module.resolve_flow_execution_revisions(Mock(), flow(pin), user_id=7, active_group_ids=[])
    assert result.entries_by_node["node_0"]["batch_capabilities"] == expected


def test_ai_proposal_serializes_exact_custom_receipt_for_browser_review():
    import json
    from src.lib.agent_studio import flow_tools

    # Saved HTTP receipts include explicit null output-contract fields.
    pin = AgentExecutionReceipt.model_validate(
        receipt("profile_bound_generic").model_dump(mode="json"),
    )
    original = flow(pin)
    candidate = flow_tools._proposal_candidate_payload(original)
    fingerprint = flow_tools._flow_candidate_fingerprint(
        flow_context={}, name="Custom extraction", description="",
        definition=candidate,
    )
    transported = json.loads(json.dumps(candidate))
    restored = FlowDefinition.model_validate(transported)
    assert fingerprint.startswith("sha256:")
    assert transported["nodes"][1]["data"]["agent_revision_id"] == str(pin.agent_revision_id)
    assert restored.nodes[1].data.execution_receipt == pin
    assert transported["nodes"][1]["data"]["execution_receipt"] == pin.model_dump(mode="json")
    from src.lib.agent_studio.authoring_context import _fingerprint
    flat_nodes = [
        {"id": node["id"], "node_type": node["type"], "position": node["position"], **node["data"]}
        for node in transported["nodes"]
    ]
    # This is the browser's JSON value, without Pydantic coercion/serialization.
    expected = _fingerprint({
        "version": 1, "artifact_kind": "flow", "artifact_id": None,
        "baseline_updated_at": None,
        "draft": {"name": "Custom extraction", "description": "", "definition": {
            **transported, "version": transported.get("version", "1.1"),
            "nodes": sorted(flat_nodes, key=lambda node: node["id"]),
        }},
    })
    assert fingerprint == expected
    assert original.nodes[1].data.execution_receipt == pin
    assert fingerprint == flow_tools._flow_candidate_fingerprint(
        flow_context={}, name="Custom extraction", description="",
        definition=transported,
    )


def test_projection_shape_findings_identify_fields_for_ai_repair(monkeypatch):
    pin, db = profile_receipt_and_db()
    install_resolver(monkeypatch, [pin])
    definition = projection_flow(pin)
    definition.nodes[-1].data.projection_plan = {
        "format": "tsv", "row_source": "objects", "row_strategy": "one_per_object",
        "columns": [{"header": "Count", "field_ref": "object.attribute.count"}],
    }
    original = definition.model_dump(mode="json")
    result = module.resolve_flow_execution_revisions(db, definition, user_id=7, active_group_ids=[])
    paths = {finding.path for finding in result.findings}
    prefix = "flow_definition.nodes.output.data.projection_plan"
    assert paths == {f"{prefix}.row_source", f"{prefix}.row_strategy", f"{prefix}.columns.0.key"}
    assert all(finding.fix_hint is not None and "formatter_projection_plan" in finding.fix_hint for finding in result.findings)
    assert definition.model_dump(mode="json") == original
    definition.nodes[-1].data.projection_plan = {
        "format": "tsv", "row_source": "object", "row_strategy": "object",
        "columns": [{"key": "count", "header": "Count", "field_ref": "object.attribute.count"}],
    }
    repaired = module.resolve_flow_execution_revisions(db, definition, user_id=7, active_group_ids=[])
    assert not repaired.findings
    assert repaired.definition.nodes[1].data.execution_receipt == pin


@pytest.mark.parametrize("ref", ["attributes.count", "attributes.sources[].name", "attributes.typo"])
@pytest.mark.parametrize("transform", [False, True])
def test_structure_paths_are_not_executable_projection_references(monkeypatch, ref, transform):
    pin, db = profile_receipt_and_db()
    install_resolver(monkeypatch, [pin])
    definition = projection_flow(pin, ref)
    if transform:
        definition.nodes[-1].data.projection_plan["columns"] = [{"key": "supplier", "transform": {
            "type": "pair_join", "field_refs": [ref, "object.attribute.count"],
        }}]
    original = definition.model_dump(mode="json")
    result = module.resolve_flow_execution_revisions(db, definition, user_id=7, active_group_ids=[])
    assert [finding.code for finding in result.findings] == ["invalid_projection_field_reference"]
    assert "view=source_fields" in result.findings[0].message
    assert result.entries_by_node["node_0"]["projection_fields"]
    assert definition.model_dump(mode="json") == original

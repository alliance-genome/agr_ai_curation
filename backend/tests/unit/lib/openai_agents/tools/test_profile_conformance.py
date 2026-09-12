"""One profile drives provider schemas and every record mutation boundary."""

from copy import deepcopy
from uuid import uuid4

import pytest

from src.lib.agent_studio.profile_conformance import (
    ProfileConformanceError, ProfileIdentityError, ResolvedGenericProfile,
)
from src.schemas.agent_execution_revision import GenericProfilePin
from src.schemas.generic_extraction_profile import GenericProfileContract


@pytest.fixture
def profile():
    contract = GenericProfileContract.model_validate({
        "name": "Reagent inventory", "semantic_class": "reagent_inventory_item",
        "fields": [
            {"key": "paper_labels", "required": True, "source_labels": ["synonym"],
             "value_schema": {"kind": "array", "items": {"kind": "string"}}},
            {"key": "source_status", "required": True,
             "value_schema": {"kind": "enum", "values": ["new_in_paper", "external", "not_stated"]}},
            {"key": "sources", "nullable": True, "value_schema": {
                "kind": "array", "items": {"kind": "object", "fields": [
                    {"key": "name", "required": True, "value_schema": {"kind": "string"}},
                    {"key": "identifier", "nullable": True, "value_schema": {"kind": "string"}},
                ]}}},
            {"key": "count", "value_schema": {"kind": "integer"}},
            {"key": "score", "value_schema": {"kind": "number"}},
            {"key": "verified", "value_schema": {"kind": "boolean"}},
        ],
    })
    pin = GenericProfilePin(profile_id=uuid4(), profile_revision_id=uuid4(), revision=1,
                            fingerprint=contract.fingerprint())
    return ResolvedGenericProfile(pin, contract)


@pytest.fixture
def record():
    return {"paper_labels": ["UASz-FLAG-hMETTL1", "hMETTL1"], "source_status": "external",
            "sources": [{"name": "BDSC", "identifier": "41552"}],
            "count": 1, "score": 0.5, "verified": False}


def test_nested_gillian_shape_and_closed_provider_schema(profile, record):
    profile.require_attributes(record)
    schema = profile.attributes_schema()
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["paper_labels", "source_status"]
    assert "synonym" not in schema["properties"]
    assert "synonym" in schema["properties"]["paper_labels"]["description"]
    sources = schema["properties"]["sources"]["anyOf"][0]
    assert sources["items"]["additionalProperties"] is False
    assert sources["items"]["required"] == ["name"]
    assert "class_key" not in schema["properties"]


@pytest.mark.parametrize("key,value,path,reason", [
    ("paper_labels", "hMETTL1", "attributes.paper_labels", "wrong_type"),
    ("source_status", "new", "attributes.source_status", "invalid_enum"),
    ("paper_labels", None, "attributes.paper_labels", "wrong_type"),
    ("paper_labels", [None], "attributes.paper_labels[0]", "wrong_type"),
    ("sources", [{"name": "BDSC", "extra": True}], "attributes.sources[0].extra", "undeclared_field"),
    ("sources", [{"identifier": "41552"}], "attributes.sources[0].name", "missing_required"),
    ("count", True, "attributes.count", "wrong_type"),
    ("count", 1.0, "attributes.count", "wrong_type"),
    ("score", False, "attributes.score", "wrong_type"),
    ("verified", 0, "attributes.verified", "wrong_type"),
    ("synonym", ["hMETTL1"], "attributes.synonym", "undeclared_field"),
    ("paper_labels.extra", 2, "attributes.paper_labels.extra", "undeclared_field"),
])
def test_noncoercing_path_addressed_errors(profile, record, key, value, path, reason):
    record[key] = value
    original = deepcopy(record)
    issues = profile.validate_attributes(record, candidate_id="candidate-1")
    assert any(issue["field_path"] == path and issue["reason"] == reason for issue in issues)
    assert all(issue["candidate_id"] == "candidate-1" and "actual_kind" in issue for issue in issues)
    assert record == original


def test_required_and_nullable_are_distinct(profile, record):
    record["sources"] = None
    profile.require_attributes(record)
    del record["sources"]
    profile.require_attributes(record)
    del record["paper_labels"]
    assert profile.validate_attributes(record)[0]["reason"] == "missing_required"


def test_patches_replace_subtrees_and_indices_without_mutating_input(profile, record):
    before = deepcopy(record)
    result = profile.patch_attributes(record, [
        {"field_path": "attributes.sources[0].identifier", "value": None},
        {"field_path": "attributes.paper_labels", "value": []},
    ])
    assert result["sources"][0]["identifier"] is None
    assert result["paper_labels"] == []
    assert record == before
    assert profile.patch_attributes(record, [{"field_path": "attributes", "value": before}]) == before


@pytest.mark.parametrize("path,value", [
    ("class_key", "generic:generic_reagent_candidate"),
    ("attributes.synonym", "hMETTL1"),
    ("attributes.sources[1].name", "other"),
    ("attributes.sources[-1].name", "other"),
    ("attributes.sources[01].name", "other"),
    ("attributes.sources.name", "other"),
    ("attributes.sources[0].extra", "other"),
    ("attributes.count", True),
    ("attributes.sources", [{"name": "BDSC", "invented": 1}]),
])
def test_invalid_patch_is_atomic(profile, record, path, value):
    original = deepcopy(record)
    with pytest.raises(ProfileConformanceError):
        profile.patch_attributes(record, [{"field_path": path, "value": value}])
    assert record == original


def test_complete_draft_revalidated_and_absent_container_not_fabricated(profile, record):
    del record["source_status"]
    with pytest.raises(ProfileConformanceError):
        profile.patch_attributes(record, [{"field_path": "attributes.count", "value": 2}])
    record["source_status"] = "not_stated"
    del record["sources"]
    with pytest.raises(ProfileConformanceError):
        profile.patch_attributes(record, [{"field_path": "attributes.sources[0].name", "value": "BDSC"}])


def test_bound_contract_and_receipt_cannot_float(profile):
    profile.require_receipt(profile.receipt)
    receipt = profile.receipt
    receipt["revision"] = 2
    with pytest.raises(ProfileIdentityError):
        profile.require_receipt(receipt)
    contract = profile.contract
    contract.fields.clear()
    assert profile.attributes_schema()["required"] == ["paper_labels", "source_status"]
    with pytest.raises(ProfileIdentityError):
        ResolvedGenericProfile(GenericProfilePin.model_validate(profile.receipt), contract)


def test_configured_limits_and_non_json_values_fail_closed(profile, record, monkeypatch):
    monkeypatch.setenv("GENERIC_PROFILE_MAX_ISSUES", "1")
    assert len(profile.validate_attributes({"bad": 1, "also_bad": 2})) == 1
    monkeypatch.setenv("GENERIC_PROFILE_MAX_RECORD_VALUES", "2")
    assert profile.validate_attributes(record)[0]["reason"] == "record_value_limit"
    monkeypatch.setenv("GENERIC_PROFILE_MAX_RECORD_BYTES", "10")
    assert profile.validate_attributes(record)[0]["reason"] == "record_size_limit"
    assert profile.validate_attributes({"score": float("nan")})[0]["reason"] == "invalid_json"
    cyclic = {}
    cyclic["cycle"] = cyclic
    assert profile.validate_attributes(cyclic)[0]["reason"] == "invalid_json"


def test_profile_stage_patch_and_materialization_share_contract(profile, record, monkeypatch):
    from agr_ai_curation_alliance.tools import generic_builder_tools as tools
    from agr_ai_curation_alliance.domain_packs.generic import materialize_generic_builder_state
    from src.lib.openai_agents import extraction_builder_workspace as builder

    monkeypatch.setattr(tools, "write_extraction_trace_event", lambda **_: None)
    monkeypatch.setattr(builder, "write_extraction_trace_event", lambda **_: None)
    workspace = builder.ExtractionBuilderWorkspace(
        run_id="profile-test", agent_id="ca_test", generic_profile=profile,
        execution_receipt={"agent_key": "ca_test", "revision": 3},
    )
    token = builder.set_active_extraction_builder_workspace(workspace)
    try:
        stage = tools._stage_generic_object_impl(
            class_key="generic:generic_object", semantic_class=profile.contract.semantic_class,
            label="hMETTL1", attributes=record, evidence_record_ids=["evidence-1"],
            classification_notes=["Paper names this reagent."],
        )
        assert stage.status == "ok", stage
        candidate_id = stage.data["candidate_id"]
        # The intermediate replacement lacks required fields, but the entire
        # atomic edit restores them. Only its final value should be validated.
        atomic = tools._patch_generic_object_impl(candidate_id, [
            {"field_path": "attributes", "value": {}},
            *[{"field_path": "attributes." + key, "value": value} for key, value in record.items()],
        ])
        assert atomic.status == "ok", atomic
        patch = tools._patch_generic_object_impl(candidate_id, [
            {"field_path": "attributes.sources[0].identifier", "value": None},
        ])
        assert patch.status == "ok", patch
        candidate = workspace.get_candidate(candidate_id)
        assert candidate.staged_fields["attributes"]["sources"][0]["identifier"] is None
        before = deepcopy(candidate.staged_fields)
        denied = tools._patch_generic_object_impl(candidate_id, [
            {"field_path": "attributes.sources[0].extra", "value": "not declared"},
        ])
        assert denied.status == "error"
        assert candidate.staged_fields == before
        denied = tools._patch_generic_object_impl(candidate_id, [
            {"field_path": "class_key", "value": "generic:generic_reagent_candidate"},
        ])
        assert denied.status == "error"
        evidence = [{"evidence_record_id": "evidence-1", "verified_quote": "hMETTL1", "page": 1}]
        materialized = materialize_generic_builder_state(
            workspace=workspace, candidate_ids=[candidate_id], evidence_records=evidence,
            produced_by="wrong_display_name",
        )
        assert materialized.ok, materialized.issues
        result = materialized.payload
        assert result["metadata"]["provenance"]["produced_by"] == "ca_test"
        assert result["metadata"]["provenance"]["generic_profile_ref"] == profile.receipt
        assert result["metadata"]["provenance"]["execution_receipt"] == workspace.execution_receipt
        obj = result["curatable_objects"][0]
        assert obj["metadata"]["generic_profile_ref"] == profile.receipt
        assert obj["payload"]["attributes"] == candidate.staged_fields["attributes"]
        # A malformed internal writer cannot bypass final materialization checks.
        candidate.staged_fields["attributes"]["synonym"] = "not canonical"
        rejected = materialize_generic_builder_state(
            workspace=workspace, candidate_ids=[candidate_id], evidence_records=evidence,
        )
        assert not rejected.ok and rejected.payload is None
        assert rejected.issues[0]["field_path"] == "attributes.synonym"
        candidate.staged_fields = before
        monkeypatch.setattr(tools, "get_active_evidence_records_snapshot", lambda: evidence)
        finalized = tools._finalize_generic_extraction_impl([candidate_id])
        assert finalized.status == "ok", finalized
        profile.require_envelope(workspace.finalization.payload,
                                 execution_receipt=workspace.execution_receipt, agent_key="ca_test")
        assert workspace.finalization.payload["curatable_objects"][0]["payload"]["attributes"] == before["attributes"]
        tampered = deepcopy(workspace.finalization.payload)
        tampered["curatable_objects"][0]["payload"]["attributes"]["synonym"] = "not canonical"
        with pytest.raises(ProfileConformanceError):
            profile.require_envelope(tampered)
        tampered = deepcopy(workspace.finalization.payload)
        tampered["metadata"]["provenance"]["generic_profile_ref"]["revision"] += 1
        with pytest.raises(ProfileIdentityError):
            profile.require_envelope(tampered)
    finally:
        builder.reset_active_extraction_builder_workspace(token)


def test_empty_profile_extraction_can_finalize_with_receipts(profile, monkeypatch):
    from agr_ai_curation_alliance.tools import generic_builder_tools as tools
    from src.lib.openai_agents import extraction_builder_workspace as builder

    monkeypatch.setattr(tools, "write_extraction_trace_event", lambda **kwargs: None)
    monkeypatch.setattr(builder, "write_extraction_trace_event", lambda **kwargs: None)
    monkeypatch.setattr(tools, "get_active_evidence_records_snapshot", lambda: [])
    workspace = builder.ExtractionBuilderWorkspace(run_id="empty", agent_id="ca_test", generic_profile=profile)
    token = builder.set_active_extraction_builder_workspace(workspace)
    try:
        result = tools._finalize_generic_extraction_impl([])
        assert result.status == "ok", result
        assert workspace.finalization.payload["curatable_objects"] == []
        profile.require_envelope(workspace.finalization.payload, agent_key="ca_test")
    finally:
        builder.reset_active_extraction_builder_workspace(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("finalize,populated,dispatch_failure", [
    (True, True, False), (True, False, False), (False, False, False), (True, True, True),
])
async def test_direct_runner_consumes_backend_profile_finalization(
    profile, record, monkeypatch, finalize, populated, dispatch_failure,
):
    import json
    from types import SimpleNamespace
    from agr_ai_curation_alliance.tools import generic_builder_tools as tools
    from src.lib.openai_agents import runner, extraction_builder_workspace as builder, streaming_tools
    from src.lib.openai_agents.tools import evidence_workspace
    from src.lib.agent_studio.profile_tools import profile_bound_tool

    monkeypatch.setattr(runner, "_build_agents_run_config", lambda **kwargs: runner.RunConfig(tracing_disabled=True))
    monkeypatch.setattr(runner, "write_stream_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "write_extraction_trace_event", lambda **kwargs: None)
    monkeypatch.setattr(builder, "write_extraction_trace_event", lambda **kwargs: None)
    monkeypatch.setattr(tools, "write_extraction_trace_event", lambda **kwargs: None)
    captured = {}

    async def dispatch(payload, **kwargs):
        captured["dispatch"] = kwargs
        assert builder.get_active_extraction_builder_workspace() is captured["workspace"]
        original = json.loads(payload)
        profile.require_envelope(original, execution_receipt=captured["agent"].execution_receipt,
                                 agent_key="ca_canonical")
        if dispatch_failure:
            raise runner.SpecialistOutputError(
                specialist_name="Display only", output_type_name="profile", message="dispatch unavailable",
            )
        # The shared dispatcher returns DomainEnvelope shape, not extraction shape.
        result = {**original, "extracted_objects": original["curatable_objects"],
                  "validation_findings": [{"code": "validator_unavailable", "severity": "warning"}]}
        del result["curatable_objects"]
        captured["validated"] = result
        streaming_tools.add_specialist_event({"type": "TOOL_COMPLETE", "details": {
            "toolName": "dispatch_active_validator_bindings", "success": True,
        }})
        return json.dumps(result)

    monkeypatch.setattr(runner, "_dispatch_domain_envelope_validators_for_chat", dispatch)

    class Result:
        # A model-authored replacement must never supersede builder output.
        final_output = {"curatable_objects": [{"payload": {"injected": "claim"}}]}

        async def stream_events(self):
            workspace = builder.get_active_extraction_builder_workspace()
            captured["workspace"] = workspace
            if finalize:
                candidate_ids = []
                if populated:
                    evidence_workspace._workspace_records().append({
                        "evidence_record_id": "evidence-1", "verified_quote": "hMETTL1", "page": 1,
                        "entity": "hMETTL1", "section": "Results", "chunk_id": "chunk-1",
                    })
                    staged = await captured["agent"].tools[1].on_invoke_tool(
                        SimpleNamespace(tool_name="stage_generic_object"), json.dumps({
                            "label": "hMETTL1", "attributes": record, "evidence_record_ids": ["evidence-1"],
                            "classification_notes": ["Paper-backed reagent"],
                        }),
                    )
                    assert staged.status == "ok", staged
                    candidate_ids = [staged.data["candidate_id"]]
                result = await captured["agent"].tools[0].on_invoke_tool(
                    SimpleNamespace(tool_name="finalize_generic_extraction"), json.dumps({"candidate_ids": candidate_ids}),
                )
                assert result.status == "ok", result
            if False:
                yield None

    def run_streamed(agent, **kwargs):
        captured["agent"] = agent
        return Result()

    monkeypatch.setattr(runner.Runner, "run_streamed", run_streamed)
    agent = SimpleNamespace(name="Display only", agent_key="ca_canonical", model="test-model",
                            generic_profile=profile, tools=[tools.finalize_generic_extraction,
                                profile_bound_tool(tools._stage_generic_object_impl, tools.stage_generic_object, profile)],
                            output_type=None, execution_receipt={"agent_key": "ca_canonical", "revision": 1},
                            authenticated_groups=["curator-team"],
                            curation_metadata={"launchable": True, "adapter_key": "generic"})
    stream = runner._run_agent_with_owned_resources(
        SimpleNamespace(client=None, provider=None), agent=agent,
        input_items=[{"role": "user", "content": "Extract"}], user_id="user",
        document_id="document-1", document_name=None, user_message="Extract", trace_id="direct-test",
    )
    events = []
    if dispatch_failure:
        with pytest.raises(runner.SpecialistOutputError, match="dispatch unavailable"):
            async for event in stream:
                events.append(event)
        assert not any(event["type"] in {"STRUCTURED_RESULT", "INTERNAL_EXTRACTION_RESULT", "RUN_FINISHED"}
                       for event in events)
        with pytest.raises(RuntimeError, match="No active extraction builder workspace"):
            builder.get_active_extraction_builder_workspace()
        return
    events = [event async for event in stream]
    workspace = captured["workspace"]
    assert workspace.agent_id == "ca_canonical" and workspace.generic_profile is profile
    if finalize:
        result = next(event["data"]["result"] for event in events if event["type"] == "STRUCTURED_RESULT")
        assert result == captured["validated"]
        assert len(result["extracted_objects"]) == int(populated)
        if populated:
            assert result["extracted_objects"][0]["payload"]["attributes"] == record
        assert result["metadata"]["provenance"]["execution_receipt"] == agent.execution_receipt
        internal = [event for event in events if event["type"] == "INTERNAL_EXTRACTION_RESULT"]
        assert len(internal) == 1
        assert internal[0]["internal"]["canonical_payload"] == result
        assert internal[0]["internal"]["execution_receipt"] == agent.execution_receipt
        assert internal[0]["internal"]["adapter_key"] == "generic"
        assert captured["dispatch"]["execution_receipt"] == agent.execution_receipt
        assert captured["dispatch"]["source_agent_key"] == "ca_canonical"
        assert captured["dispatch"]["adapter_key"] == "generic"
        assert captured["dispatch"]["runtime_context"].document_id == "document-1"
        assert captured["dispatch"]["runtime_context"].authenticated_groups == ("curator-team",)
        assert any(event["type"] == "TOOL_COMPLETE" and
                   event["details"]["toolName"] == "dispatch_active_validator_bindings" for event in events)
        assert not any(event["type"] == "RUN_ERROR" for event in events)
    else:
        assert "dispatch" not in captured
        assert any(event["type"] == "RUN_ERROR" for event in events)
        assert not any(event["type"] == "STRUCTURED_RESULT" for event in events)


def test_profile_rejects_malformed_stage_and_direct_workspace_mutations(profile, record, monkeypatch):
    from agr_ai_curation_alliance.tools import generic_builder_tools as tools
    from src.lib.openai_agents import extraction_builder_workspace as builder

    monkeypatch.setattr(tools, "write_extraction_trace_event", lambda **_: None)
    workspace = builder.ExtractionBuilderWorkspace(run_id="profile-test", generic_profile=profile)
    token = builder.set_active_extraction_builder_workspace(workspace)
    try:
        invalid = tools._stage_generic_object_impl(
            class_key="generic:generic_reagent_candidate", label="reagent",
            semantic_class=profile.contract.semantic_class, attributes=record,
            evidence_record_ids=["evidence-1"], classification_notes=["Paper evidence"],
        )
        assert invalid.status == "error"
        assert invalid.data["validation_issues"][0]["reason"] == "profile_identity_violation"
        with pytest.raises(ProfileConformanceError):
            workspace.upsert_candidate(candidate_id="invalid", staged_fields={
                "class_key": "generic:generic_object", "object_type": "generic_object",
                "semantic_class": "wrong", "attributes": record,
            })
        assert not workspace.candidates
        candidate = {"class_key": "generic:generic_object", "object_type": "generic_object",
                     "semantic_class": profile.contract.semantic_class, "attributes": record}
        assert profile.validate_candidate({**candidate, "payload": {"attributes": {"extra": "claim"}}})[0]["reason"] == "profile_payload_forbidden"
        assert profile.validate_candidate({**candidate, "extra_fields": {"claim": 1}})[0]["reason"] == "undeclared_field"
    finally:
        builder.reset_active_extraction_builder_workspace(token)


@pytest.mark.asyncio
async def test_final_profile_tool_schema_and_callable_survive_run_state_rebinding(profile, record, monkeypatch):
    import json
    from types import SimpleNamespace
    from agr_ai_curation_alliance.tools import generic_builder_tools as tools
    from src.lib.agent_studio.profile_tools import profile_bound_tool, assert_profile_tool_contract
    from src.lib.openai_agents import extraction_builder_workspace as builder
    from src.lib.openai_agents import streaming_tools as streaming
    from src.lib.openai_agents.resolver_call_ledger import ResolverCallLedger

    monkeypatch.setattr(tools, "write_extraction_trace_event", lambda **_: None)
    monkeypatch.setattr(builder, "write_extraction_trace_event", lambda **_: None)
    stage = profile_bound_tool(tools._stage_generic_object_impl, tools.stage_generic_object, profile)
    patch = profile_bound_tool(tools._patch_generic_object_impl, tools.patch_generic_object, profile)
    workspace = builder.ExtractionBuilderWorkspace(run_id="bound", agent_id="ca_test", generic_profile=profile)
    agent = SimpleNamespace(tools=[stage, patch])
    monkeypatch.setattr(streaming, "_run_state_tool_impls", lambda: {
        "stage_generic_object": "must.not.import:static_stage",
        "patch_generic_object": "must.not.import:static_patch",
    })
    streaming._bind_run_state_into_tools(agent, evidence_records=[], builder_workspace=workspace,
                                        resolver_ledger=ResolverCallLedger(trace_id="bound"))
    final = agent.tools[0]
    assert_profile_tool_contract(final)
    assert final.params_json_schema == stage.params_json_schema
    assert final.params_json_schema["properties"]["attributes"]["additionalProperties"] is False
    assert "class_key" not in final.params_json_schema["properties"]
    args = {"label": "hMETTL1", "attributes": record, "evidence_record_ids": ["evidence-1"],
            "classification_notes": ["Evidence-backed reagent"]}
    context = SimpleNamespace(tool_name=final.name)
    output = await final.on_invoke_tool(context, json.dumps(args))
    assert output.status == "ok", output
    # The active context here is empty: only the rebuilt callable's closure owns
    # the workspace. Extra arguments cannot be silently ignored by SDK parsing.
    with pytest.raises(RuntimeError):
        builder.get_active_extraction_builder_workspace()
    denied = await final.on_invoke_tool(context, json.dumps({**args, "class_key": "wrong"}))
    assert json.loads(denied)["status"] == "error"
    assert len(workspace.candidates) == 1
    assert json.loads(await final.on_invoke_tool(context, "not json"))["status"] == "error"
    assert_profile_tool_contract(agent.tools[1])


def test_profile_configuration_narrows_installed_builder_without_adding_tools(profile, monkeypatch):
    from types import SimpleNamespace
    from agr_ai_curation_alliance.tools import generic_builder_tools as tools
    from src.lib.agent_studio import catalog_service
    from src.lib.agent_studio.profile_tools import configure_profile_tools

    monkeypatch.setattr(catalog_service, "_get_package_tool_binding", lambda name: SimpleNamespace(
        metadata={"builder_run_state": True},
        import_path=f"agr_ai_curation_alliance.tools.generic_builder_tools:{name}",
    ))
    finalizer = SimpleNamespace(name="finalize_generic_extraction")
    narrowed = configure_profile_tools([
        tools.stage_generic_object, tools.patch_generic_object,
        SimpleNamespace(name="list_generic_object_classes"), finalizer,
    ], profile)
    assert [tool.name for tool in narrowed] == [
        "stage_generic_object", "patch_generic_object", "finalize_generic_extraction",
    ]
    assert narrowed[-1] is finalizer
    assert narrowed[0].params_json_schema["properties"]["attributes"] == profile.attributes_schema()
    with pytest.raises(ValueError, match="requires saved generic"):
        configure_profile_tools([tools.stage_generic_object], profile)


def test_adapter_cannot_replace_profile_tool_with_open_signature(profile, monkeypatch):
    from agr_ai_curation_alliance.tools import generic_builder_tools as tools
    from src.lib.agent_studio.profile_tools import profile_bound_tool
    from src.lib.openai_agents import streaming_tools as streaming

    tool = profile_bound_tool(tools._stage_generic_object_impl, tools.stage_generic_object, profile)
    monkeypatch.setattr(streaming, "_tool_provider_adapter_factories", lambda _: {})
    assert streaming._adapt_tools_for_groq_schema_constraints([tool])[0] is tool
    monkeypatch.setattr(streaming, "_tool_provider_adapter_factories", lambda _: {
        tool.name: lambda: tools.stage_generic_object,
    })
    with pytest.raises(ValueError, match="cannot preserve"):
        streaming._adapt_tools_for_groq_schema_constraints([tool])


@pytest.mark.parametrize("provider", ["openai", "gemini", "groq", "openrouter"])
def test_configured_provider_serialization_preserves_final_profile_schema(profile, monkeypatch, provider):
    from types import SimpleNamespace
    from agents.models.openai_responses import Converter as ResponsesConverter
    from agents.models.openai_chatcompletions import Converter as ChatConverter, OpenAIChatCompletionsModel
    from agr_ai_curation_alliance.tools import generic_builder_tools as tools
    from src.lib.agent_studio.profile_tools import profile_bound_tool, assert_profile_tool_contract
    from src.lib.openai_agents import config, streaming_tools as streaming
    from src.lib.openai_agents.extraction_builder_workspace import ExtractionBuilderWorkspace
    from src.lib.openai_agents.resolver_call_ledger import ResolverCallLedger

    monkeypatch.setenv(provider.upper() + "_API_KEY", "test-only-not-a-credential")
    model = config.get_model_for_agent("test-profile-model", provider_override=provider)
    stage = profile_bound_tool(tools._stage_generic_object_impl, tools.stage_generic_object, profile)
    patch = profile_bound_tool(tools._patch_generic_object_impl, tools.patch_generic_object, profile)
    agent = SimpleNamespace(model=model, tools=[stage, patch], output_type=None)
    if provider == "groq":
        # Builder output_type=None intentionally avoids the response_format+tools
        # compatibility mode. Even an explicit installed Groq adapter pass must
        # preserve these signatures before run-state binding.
        assert not streaming._should_use_groq_tool_json_compat(agent)
        agent.tools = streaming._adapt_tools_for_groq_schema_constraints(agent.tools)
    streaming._bind_run_state_into_tools(agent, evidence_records=[],
        builder_workspace=ExtractionBuilderWorkspace(run_id="provider", generic_profile=profile),
        resolver_ledger=ResolverCallLedger(trace_id="provider"))
    for tool in agent.tools:
        assert_profile_tool_contract(tool)
    if provider == "openai":
        assert model == "test-profile-model"
        serialized = ResponsesConverter.convert_tools(agent.tools, []).tools
    else:
        assert isinstance(model, OpenAIChatCompletionsModel)
        serialized = [ChatConverter.tool_to_openai(tool)["function"] for tool in agent.tools]
    assert serialized[0]["parameters"] == stage.params_json_schema
    assert serialized[1]["parameters"] == patch.params_json_schema
    assert serialized[0]["strict"] is False
    attributes = serialized[0]["parameters"]["properties"]["attributes"]
    assert attributes["additionalProperties"] is False
    assert "sources" not in attributes["required"]
    assert "synonym" not in attributes["properties"]
    assert attributes["properties"]["sources"]["anyOf"][0]["items"]["additionalProperties"] is False

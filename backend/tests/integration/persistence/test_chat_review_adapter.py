"""Chat adapter uses real admission/history/checkpoints with fake validator answers."""

from tests.cancellation_test_support import install_cancel_reader
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select
from agents import Agent

from src.lib.validation_history import runtime, store
from src.lib.validation_review import admission, decisions, chat_dispatch
from src.lib.validation_review.validator_adapter import (
    ValidatorAdapter,
    save_plan,
    MATCH,
    serialize_match,
)
from src.lib.domain_packs.validator_dispatch import (
    prepare_validator_dispatch,
    ValidatorRuntimeContext,
)
from src.models.sql.validation_history import ValidationFact
from src.schemas.domain_envelope import DomainEnvelope, CuratableObjectEnvelope
from tests.integration.persistence.test_validation_history import (
    scope as scope,
    migrated as migrated,
)
from tests.unit.lib.domain_packs.test_validator_dispatch import (
    _multivalued_dispatch_pack,
    _result_payload,
)


@pytest.fixture
def planned(scope, tmp_path, monkeypatch, request):
    # These database tests use a new event loop per case. Cancellation behaviour
    # is tested separately; do not share Redis's loop-bound pool across cases.
    from unittest.mock import AsyncMock

    install_cancel_reader(monkeypatch, AsyncMock(return_value=False))
    fixture_options = getattr(request, "param", None)
    fixture_shape = fixture_options.get("shape") if isinstance(fixture_options, dict) else fixture_options
    admit_initial = fixture_options.get("admitted", True) if isinstance(fixture_options, dict) else True
    if isinstance(fixture_options, dict) and "packet_bytes" in fixture_options:
        monkeypatch.setenv("VALIDATION_REVIEW_PACKET_BYTES", str(fixture_options["packet_bytes"]))
    pack = _multivalued_dispatch_pack(tmp_path)
    if fixture_shape == "routed":
        from copy import deepcopy
        from src.schemas.domain_pack_metadata import DomainPackMetadata
        raw = pack.metadata.model_dump(mode="json", exclude_none=True)
        original = raw["metadata"]["validator_bindings"]["active"][0]
        route = {key: original.pop(key) for key in
                 ("validator_agent", "input_fields", "expected_result_fields")}
        for key in ("max_tool_calls", "custom_profile_reuse"):
            original.pop(key, None)
        original["route_by"] = {"source": "payload", "path": "kind"}
        original["routes"] = {
            name: {**deepcopy(route), "validator_agent": {
                **route["validator_agent"], "agent_id": name}}
            for name in ("one", "two")
        }
        raw["object_definitions"][0]["fields"].append({"field_path": "kind", "field_type": "string"})
        pack = replace(pack, metadata=DomainPackMetadata.model_validate(raw))
    if fixture_shape == "nested-selector":
        from copy import deepcopy
        raw = deepcopy(pack.metadata.metadata)
        raw["validator_bindings"]["active"][0]["custom_profile_reuse"] = {
            "enabled": True,
            "inputs": {"evidence": {
                "value_schema": {"kind": "string"},
                "context_selector": {"source": "envelope_metadata", "path": "evidence_note"},
            }},
            "outputs": {"identifier": {"value_schema": {"kind": "string"}, "result_path": "curie"}},
            "policy": {"unresolved_default": "informational", "unresolved_allowed": ["informational"],
                       "readiness_default": False, "readiness_allowed": [False]},
        }
        pack = replace(pack, metadata=pack.metadata.model_copy(update={"metadata": raw}))
    if fixture_shape in {"dependent", "unsupported-dependent"}:
        from copy import deepcopy

        raw = deepcopy(pack.metadata.metadata)
        first = raw["validator_bindings"]["active"][0]
        raw["validator_bindings"]["active"].append(
            {
                **deepcopy(first),
                "binding_id": "fixture.dependent",
                "runs_after": [first["binding_id"]],
            }
        )
        pack = replace(
            pack, metadata=pack.metadata.model_copy(update={"metadata": raw})
        )
    if fixture_shape == "nested":
        from src.lib.domain_packs.loader import load_domain_pack_metadata
        text = pack.metadata_path.read_text().replace("evidence_code_curies", "genes.aliases")
        text = text.replace("    fields:\n", "    fields:\n      - field_path: genes\n        field_type: string\n        metadata:\n          multivalued: true\n")
        pack.metadata_path.write_text(text)
        pack = replace(pack, metadata=load_domain_pack_metadata(pack.metadata_path))
    if fixture_shape in {"absent", "optional-absence", "optional-composite-absence", "absence-dependent-optional", "absence-dependent-required", "dependent-writeback", "absence-runnable-dependent", "unmatched-prerequisite", "unmatched-prerequisite-absence"}:
        from copy import deepcopy
        raw = deepcopy(pack.metadata.metadata)
        binding = raw["validator_bindings"]["active"][0]
        binding["applies_to"]["field_paths"] = ["optional.term"]
        binding["input_fields"] = {"query": {"source": "literal", "value": "fixture lookup"}}
        if fixture_shape not in {"absent", "dependent-writeback"}:
            binding["input_fields"] = {"query": {"source": "payload", "path": "optional.term", "required": False}}
        binding["expected_result_fields"] = {"curie": "optional.term"}
        if fixture_shape == "optional-composite-absence":
            binding["decision_units"] = [{"key": "identity", "fields": ["curie"]}]
        field = pack.metadata.object_definitions[0].fields[0].model_copy(
            update={"field_path": "optional.term", "metadata": {}})
        fields = [field]
        if fixture_shape in {"absence-dependent-optional", "absence-dependent-required", "dependent-writeback", "absence-runnable-dependent", "unmatched-prerequisite", "unmatched-prerequisite-absence"}:
            dependent = deepcopy(binding)
            dependent.update(binding_id="fixture.dependent", runs_after=[binding["binding_id"]])
            dependent["applies_to"]["field_paths"] = ["optional.other"]
            dependent["expected_result_fields"] = {"curie": "optional.other"}
            dependent["input_fields"] = {"query": {"source": "payload", "path": "optional.term",
                "required": fixture_shape == "absence-dependent-required"}}
            if fixture_shape in {"absence-runnable-dependent", "unmatched-prerequisite"}:
                dependent["input_fields"] = {"query": {"source": "literal", "value": "Supplied optional policy context"}}
            raw["validator_bindings"]["active"].append(dependent)
            fields.append(field.model_copy(update={"field_path": "optional.other"}))
        if fixture_shape in {"unmatched-prerequisite", "unmatched-prerequisite-absence"}:
            binding["applies_to"]["field_paths"] = ["optional.parents"]
            binding["expected_result_fields"] = {"curie": "optional.parents"}
            binding["input_fields"] = {"query": {"source": "payload", "path": "optional.parents"}}
            fields.append(field.model_copy(update={"field_path": "optional.parents", "metadata": {"multivalued": True}}))
        definition = pack.metadata.object_definitions[0].model_copy(update={"fields": fields})
        pack = replace(pack, metadata=pack.metadata.model_copy(update={
            "metadata": raw, "object_definitions": [definition]}))
    if fixture_shape in {"unsupported-preflight", "unsupported-dependent"}:
        from copy import deepcopy
        raw = deepcopy(pack.metadata.metadata)
        binding = raw["validator_bindings"]["active"][0]
        if fixture_shape == "unsupported-dependent":
            binding = raw["validator_bindings"]["active"][1]
        binding["preflight_policy"] = "provider_taxon_mapping_required"
        binding["input_fields"] = {}
        binding["input_fields"].update({
            key: {"source": "literal", "value": value}
            for key, value in {
                "label": "fixture term",
                "data_provider": "FIXTURE_UNSUPPORTED", "taxon_id": "NCBITaxon:1001",
                "provider_taxon_ontology_mappings": [
                    {"data_provider": "FIXTURE_SUPPORTED", "taxon_id": "NCBITaxon:1002", "ontology_term_type": "FixtureTerm"}
                ],
            }.items()
        })
        binding["input_fields"]["label"] = {"source": "payload", "path": "evidence_code_curies"}
        pack = replace(pack, metadata=pack.metadata.model_copy(update={"metadata": raw}))
    groups = ()
    if fixture_shape == "authorized":
        from copy import deepcopy

        raw = deepcopy(pack.metadata.metadata)
        from src.lib.config.groups_loader import get_valid_group_ids
        group_id = get_valid_group_ids()[0]
        raw["validator_bindings"]["active"][0]["group_scope"] = {
            "required_any_active_group": [group_id]
        }
        pack = replace(
            pack, metadata=pack.metadata.model_copy(update={"metadata": raw})
        )
        groups = (group_id,)
    env = DomainEnvelope(
        envelope_id=uuid4().hex,
        domain_pack_id=pack.pack_id,
        extracted_objects=[
            CuratableObjectEnvelope(
                object_type="Annotation",
                pending_ref_id="one",
                payload=({"genes": [{"aliases": ["eco:1", "eco:2"]}]}
                         if fixture_shape == "nested"
                         else {"evidence_code_curies": ["eco:1", "eco:2"]}),
            )
        ],
    )
    if fixture_shape == "routed":
        env.extracted_objects = [CuratableObjectEnvelope(
            object_type="Annotation", pending_ref_id=name,
            payload={"kind": name, "evidence_code_curies": ["eco:" + name]})
            for name in ("one", "two")]
    if fixture_shape in {"absent", "optional-absence", "optional-composite-absence", "absence-dependent-optional", "absence-dependent-required", "dependent-writeback", "absence-runnable-dependent", "unmatched-prerequisite", "unmatched-prerequisite-absence"}:
        env.extracted_objects[0].payload = {"optional": {}}
        if fixture_shape in {"unmatched-prerequisite", "unmatched-prerequisite-absence"}:
            env.extracted_objects[0].payload["optional"]["parents"] = []
    if fixture_shape == "three":
        env.extracted_objects = [CuratableObjectEnvelope(object_type="Annotation", pending_ref_id=name,
            payload={"evidence_code_curies": ["eco:" + name]}) for name in "ABC"]
    if fixture_shape == "evidence":
        env.extracted_objects[0].evidence_record_ids = ["original-evidence"]
        env.metadata["evidence_records"] = [{"evidence_record_id": "original-evidence",
            "document_id": scope.document_id, "verified_quote": "Original quoted evidence"}]
    if isinstance(fixture_options, dict) and fixture_options.get("count"):
        template = env.extracted_objects[0]
        env.extracted_objects = [template.model_copy(deep=True, update={"pending_ref_id": f"record-{index}"})
                                 for index in range(fixture_options["count"])]
    if isinstance(fixture_options, dict) and fixture_options.get("mixed_prerequisites"):
        env.extracted_objects[0].payload["optional"]["parents"] = ["ECO:parent"]
    env = runtime.capture_extraction(
        scope=scope,
        producer_id=uuid4().hex,
        typed=env.model_dump(mode="json"),
        envelope=env,
        provenance={},
    )
    context = ValidatorRuntimeContext(
        user_id=scope.owner_id,
        document_id=scope.document_id,
        authenticated_groups=groups,
    )
    from src.lib.config.agent_loader import AgentDefinition

    monkeypatch.setattr(
        "src.lib.config.agent_loader.get_agent_definition_for_package",
        lambda package, agent: AgentDefinition(
            folder_name=agent, agent_id=agent, name=agent, package_id=package
        ),
    )
    monkeypatch.setattr(
        "src.lib.curation_workspace.adapter_registry.resolve_curation_domain_pack_by_id",
        lambda _: pack,
    )
    plan = prepare_validator_dispatch(env, pack, runtime_context=context)
    plan_ref = save_plan(plan, context=context)
    aid = plan.history_binding.artifact_id
    ep = plan.envelope.metadata["review_episode_id"]
    if admit_initial:
        sid = admission.create_scope(
            scope,
            operation_id=uuid4().hex,
            kind="discovered_document_run",
            targets=0,
            episodes=0,
        )
        admission.attach_episode(scope, artifact_id=aid, episode_id=ep, scope_id=sid)
    adapter = ValidatorAdapter(scope, aid, ep, plan_ref, authenticated_groups=groups)
    return SimpleNamespace(
        plan=plan, adapter=adapter, aid=aid, ep=ep, scope=scope, pack=pack, source_envelope=env
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("planned", [None, "nested-selector"], indirect=True)
@pytest.mark.parametrize("specialized", [False, True], ids=["base", "gene-result"])
async def test_real_adapter_retains_candidates_and_replay_never_calls_provider_again(
    planned, monkeypatch, specialized
):
    item = planned
    calls = []

    def run(request, binding, **kwargs):
        calls.append(request.request_id)
        payload = {
            **_result_payload(request, resolved_values={"curie": "ECO:correct"}),
            "resolved_objects": [],
        }
        if specialized:
            from src.lib.config.schema_discovery import resolve_output_schema
            from src.lib.domain_packs.validator_dispatch import _ValidatorAgentRunOutput
            payload["gene_candidates"] = [{"gene_id": "GENE:example-1", "symbol": "geneA"}]
            result = resolve_output_schema("GeneResultEnvelope").model_validate(payload)
            return _ValidatorAgentRunOutput(raw_output=payload, accepted_result=result)
        return payload

    monkeypatch.setattr(
        "src.lib.validation_review.validator_adapter._default_package_scoped_validator_runner",
        lambda: run,
    )
    monkeypatch.setattr(
        "src.lib.validation_review.validator_adapter._default_package_scoped_validator_batch_runner",
        lambda: lambda *args, **kwargs: pytest.fail("Unexpected batch"),
    )
    await item.adapter.initial()
    await item.adapter.initial()
    assert len(calls) == 2
    with store.SessionLocal() as db:
        completed = list(
            db.scalars(
                select(ValidationFact).where(
                    ValidationFact.artifact_id == item.aid,
                    ValidationFact.kind == "completion",
                )
            )
        )
        candidates = list(
            db.scalars(
                select(ValidationFact).where(
                    ValidationFact.artifact_id == item.aid,
                    ValidationFact.kind == "candidate",
                )
            )
        )
        assert len(completed) == len(candidates) == 2
        assert all(store._content(db, row)["dispatch_context"] is None for row in candidates)
        assert all(store._content(db, row)["state"] == "completed" for row in completed)
        schema_key = "GeneResultEnvelope" if specialized else "DomainValidatorResultBase"
        assert all(store._content(db, row)["result_schema"]["key"] == schema_key for row in candidates)
        if specialized:
            assert all(store._content(db, row)["result"]["gene_candidates"][0]["symbol"] == "geneA"
                       for row in candidates)
    assert not admission.status(item.scope, item.ep)["ready"]
    assert item.plan.envelope.extracted_objects[0].payload["evidence_code_curies"] == [
        "eco:1",
        "eco:2",
    ]
    from src.lib.validation_review import decisions
    result = decisions.record_decisions(
        decisions.ReviewContext(item.scope, item.ep, "supervisor"),
        checkpoint_request=checkpoint_provider(item)(), operation_id="accept-typed-result",
        entries=entries_for(item, "accept"),
    )
    assert result["ready"] is True
    assert len(result["applied"]) == 2
    assert all(entry["action"] == "accept" for entry in result["applied"])
    assert len(calls) == 2


def test_saved_match_round_trip_and_agent_substitution_refused(planned):
    job = planned.plan.initial_jobs[0]
    assert MATCH.validate_python(serialize_match(job.match)) == job.match
    frozen = runtime.frozen_job_requests(planned.plan.history_binding, [job])[0]
    frozen["request"]["validator_binding_id"] = "unconfigured"
    with pytest.raises(store.HistoryAccessError):
        planned.adapter.job(frozen)


@pytest.mark.parametrize("planned", [None, "authorized"], indirect=True)
def test_job_audits_actual_execution_groups_without_changing_frozen_request(planned):
    from copy import deepcopy
    from src.lib.domain_packs.validator_dispatch import group_dispatch_context

    item = planned
    frozen = runtime.frozen_job_requests(item.plan.history_binding, [item.plan.initial_jobs[0]])[0]
    original = deepcopy(frozen)
    original_groups = tuple(item.adapter.context.authenticated_groups)
    execution_groups = tuple(sorted((*original_groups, "another-authorized-group")))
    resumed = ValidatorAdapter(
        item.scope, item.aid, item.ep, item.adapter.plan_ref,
        authenticated_groups=execution_groups,
    )
    job = resumed.job(frozen)
    if job.match.binding.required_any_active_group:
        assert job.dispatch_context == group_dispatch_context(execution_groups)
        assert job.dispatch_context != group_dispatch_context(original_groups)
    else:
        assert job.dispatch_context is None
    assert frozen == original


def test_dispatch_survives_resume_and_unknown_submission_is_not_replayed(scope):
    context = SimpleNamespace(
        owner_id=scope.owner_id,
        document_id=scope.document_id,
        session_id=scope.session_id,
        turn_id=uuid4().hex,
    )
    first = chat_dispatch.claim(context, "extract", "query")
    second = chat_dispatch.claim(context, "extract", "query")
    assert first["claimed"] and not second["claimed"] and second["state"] == "started"
    chat_dispatch.complete(context, first["id"], {"output": "result-ref"})
    third = chat_dispatch.claim(context, "extract", "query")
    assert not third["claimed"] and third["result"] == {"output": "result-ref"}


@pytest.mark.asyncio
@pytest.mark.parametrize("review_kind", ["extraction_concern", "operational_refusal"])
async def test_chat_source_checkpoint_and_prep_share_identity_before_and_after_review(
    scope, tmp_path, monkeypatch, review_kind
):
    from src.lib.validation_review.chat import ChatReview
    from src.lib.observability.cost_context import attach_agent_cost_identity
    from src.lib.curation_workspace import curation_prep_service as prep
    from src.lib.curation_workspace.models import DomainEnvelopeModel

    from unittest.mock import AsyncMock

    install_cancel_reader(monkeypatch, AsyncMock(return_value=False))

    pack = _multivalued_dispatch_pack(tmp_path)
    env = DomainEnvelope(
        envelope_id=uuid4().hex,
        domain_pack_id=pack.pack_id,
        extracted_objects=[
            CuratableObjectEnvelope(
                object_type="Annotation",
                pending_ref_id="one",
                payload={"evidence_code_curies": ["eco:1"]},
            )
        ],
    )
    env = runtime.capture_extraction(
        scope=scope,
        producer_id=uuid4().hex,
        typed=env.model_dump(mode="json"),
        envelope=env,
        provenance={},
    )
    from src.lib.config.agent_loader import AgentDefinition

    monkeypatch.setattr(
        "src.lib.config.agent_loader.get_agent_definition_for_package",
        lambda package, agent: AgentDefinition(
            folder_name=agent, agent_id=agent, name=agent, package_id=package
        ),
    )
    monkeypatch.setattr(
        "src.lib.curation_workspace.adapter_registry.resolve_curation_domain_pack_by_id",
        lambda _: pack,
    )

    def run(request, binding, **kwargs):
        return {
            **_result_payload(request, resolved_values={"curie": "ECO:selected"}),
            "resolved_objects": [],
        }

    monkeypatch.setattr(
        "src.lib.validation_review.validator_adapter._default_package_scoped_validator_runner",
        lambda: run,
    )
    monkeypatch.setattr(
        "src.lib.validation_review.validator_adapter._default_package_scoped_validator_batch_runner",
        lambda: lambda *a, **kw: pytest.fail("Unexpected batch"),
    )
    supervisor = Agent(name="Supervisor", model="gpt-6-astra", instructions="Policy")
    attach_agent_cost_identity(
        supervisor, {"agent_id": "supervisor", "agent_role": "supervisor"}
    )
    chat = ChatReview(
        scope.owner_id,
        scope.document_id,
        scope.session_id,
        uuid4().hex,
        supervisor,
        (),
        "Extract",
    )
    context = ValidatorRuntimeContext(
        user_id=scope.owner_id, document_id=scope.document_id, authenticated_groups=()
    )
    from datetime import datetime, timezone
    from src.schemas.execution_provenance import ExtractionExecutionContext

    provenance = ExtractionExecutionContext(
        captured_at=datetime.now(timezone.utc),
        source_kind="chat",
        agent_key="fixture",
        executed_query="Extract",
        document={"document_id": scope.document_id},
    )
    await chat.receive(
        env,
        pack,
        agent_key="fixture",
        adapter_key="fixture",
        tool_name="ask_fixture",
        execution_receipt=None,
        context=context,
        trace_id="trace-source",
        source_metadata={
            "execution_context": provenance.model_dump(mode="json"),
            "specialist_explanation": "Extracted from the supplied paper.",
        },
    )
    records = chat.records()
    assert len(records) == 1 and chat.status()["pending_count"] == 1
    assert records[0].trace_id == "trace-source"
    assert (
        records[0].metadata["specialist_explanation"]
        == "Extracted from the supplied paper."
    )
    assert chat.checkpoint(records[0]).execution_context == provenance
    from src.lib.validation_review.chat import refresh_saved_response
    monkeypatch.setattr(
        "src.lib.agent_studio.catalog_service.resolve_tools", lambda *args: []
    )
    monkeypatch.setattr("src.lib.prompts.assembly.get_all_active_prompts", lambda: {})
    reviewer_calls = []
    monkeypatch.setenv("VALIDATION_REVIEW_INITIAL_CORRECTIVE_CONTINUATIONS", "1")
    reports = []
    monkeypatch.setattr("src.lib.validation_review.audit.report_operational_failure", reports.append)
    concern = "The extraction omitted an observation. Ask the curator before starting fresh extraction."

    async def fake_reviewer(agent, input, **kwargs):
        reviewer_calls.append(agent.name)
        if review_kind == "operational_refusal":
            raise admission.ReviewStopped("configured_validator_changed")
        return SimpleNamespace(final_output=concern, to_input_list=lambda: [
            *(input if isinstance(input, list) else [{"role": "user", "content": input}]),
            {"role": "assistant", "content": concern}])

    monkeypatch.setattr(
        "src.lib.openai_agents.runner.run_agent_with_owned_openai_resources",
        fake_reviewer,
    )
    await chat.resume()
    expected_note = (
        concern
        if review_kind == "extraction_concern"
        else "Review paused: configured validator changed"
    )
    rendered, delivery_status = chat.direct_delivery(current_turn_only=True)
    assert expected_note in rendered
    replay = refresh_saved_response(
        owner_id=scope.owner_id,
        session_id=chat.session_id,
        status=delivery_status,
        original="old extractor prose",
    )
    expected_calls = 2 if review_kind == "extraction_concern" else 1
    assert expected_note in replay and len(reviewer_calls) == expected_calls
    assert len(reports) == (1 if review_kind == "extraction_concern" else 0)
    assert chat.supervisor_handoff()["review_outcomes"][0]["outcome"][
        "output" if review_kind == "extraction_concern" else "reason"
    ] == (
        concern
        if review_kind == "extraction_concern"
        else "configured_validator_changed"
    )
    reconnected = ChatReview(
        scope.owner_id, scope.document_id, scope.session_id,
        chat.turn_id, supervisor, (), "Extract",
    )
    await reconnected.resume()
    assert len(reviewer_calls) == expected_calls
    assert reconnected.review_notes() == chat.review_notes()
    with store.SessionLocal() as db:
        summaries = db.scalars(
            select(ValidationFact).where(
                ValidationFact.artifact_id == records[0].metadata["chat_review"]["artifact_id"],
                ValidationFact.kind == "review_supervisor_summary",
            )
        ).all()
        assert len(summaries) == 1
    later = ChatReview(
        scope.owner_id, scope.document_id, scope.session_id,
        uuid4().hex, supervisor, (), "Unrelated question",
    )
    assert later.review_notes() == chat.review_notes()
    assert later.review_notes(current_turn_only=True) == []
    assert later.render_review_notes(current_turn_only=True) == ""
    assert chat.review_notes(current_turn_only=True) == chat.review_notes()
    assert len(chat.records()) == 1
    record = records[0]
    assert record.payload_json["extracted_objects"][0]["payload"][
        "evidence_code_curies"
    ] == ["eco:1"]
    with pytest.raises(store.SelectionNotReady):
        prep.ensure_domain_envelope_materialization(record, persist=True)
    request = chat.checkpoint(record)
    ref = record.metadata["chat_review"]
    with store.SessionLocal() as db:
        attempt = db.scalar(
            select(ValidationFact).where(
                ValidationFact.artifact_id == ref["artifact_id"],
                ValidationFact.kind == "attempt",
            )
        )
        entry = {
            "target_id": attempt.target_id,
            "decision_unit_id": attempt.target_id,
            "expected_version": 0,
            "logical_attempt_id": attempt.id,
            "action": "accept",
            "reason": "Solid answer",
        }
    result = decisions.record_decisions(
        chat.context(record),
        checkpoint_request=request,
        operation_id="review",
        entries=[entry],
    )
    assert result["ready"] and chat.status()["pending_count"] == 0
    presentation = chat.selected_presentation()
    assert "ECO:selected" in presentation and '"eco:1"' not in presentation
    from src.lib.validation_review.chat import refresh_saved_status

    assert (
        refresh_saved_status(
            owner_id=scope.owner_id,
            session_id=chat.session_id,
            saved_status=chat.status(),
        )
        == chat.status()
    )
    updated = chat.checkpoint(record)
    assert (
        updated.expected_revision == 1
        and updated.source_payload_hash == request.source_payload_hash
    )
    assert updated.envelope.extracted_objects[0].payload["evidence_code_curies"] == [
        "ECO:selected"
    ]
    with store.SessionLocal() as db:
        row = db.get(DomainEnvelopeModel, env.envelope_id)
        # The real prep path checks the exact same hash and source/receipt scope.
        prep._validate_existing_materialization_scope(
            row,
            extraction_result=record,
            envelope=updated.envelope,
            project_key=request.project_key,
            adapter_key=record.adapter_key,
            source_payload_hash=request.source_payload_hash,
        )
    assert chat.records()[0].payload_json == record.payload_json


def checkpoint_provider(item):
    from src.lib.domain_envelopes.persistence import DomainEnvelopeCheckpointRequest
    from src.lib.curation_workspace.models import DomainEnvelopeModel

    source_id = uuid4()

    def load():
        with store.SessionLocal() as db:
            row = db.get(DomainEnvelopeModel, item.plan.envelope.envelope_id)
            return DomainEnvelopeCheckpointRequest(
                project_key="fixture",
                envelope=item.plan.envelope
                if row is None
                else DomainEnvelope.model_validate(row.envelope_json),
                expected_revision=0 if row is None else row.revision,
                document_id=item.scope.document_id,
                adapter_key="fixture",
                source_extraction_result_id=source_id,
            )

    return load


def entries_for(item, action, *, binding_id=None):
    with store.SessionLocal() as db:
        rows = db.scalars(
            select(ValidationFact)
            .where(
                ValidationFact.artifact_id == item.aid, ValidationFact.kind == "attempt"
            )
            .order_by(ValidationFact.sequence)
        ).all()
        return [
            {
                "target_id": row.target_id,
                "decision_unit_id": row.target_id,
                "expected_version": store._current(db, item.aid, row.target_id)[
                    "version"
                ],
                "logical_attempt_id": row.id,
                "action": action,
                "reason": "Reviewer assessment",
            }
            for row in rows
            if binding_id is None
            or store._content(db, row)["input"]["binding_id"] == binding_id
        ]


@pytest.mark.asyncio
@pytest.mark.parametrize("planned", ["dependent"], indirect=True)
async def test_dependent_initial_work_reads_selected_prerequisite_only_after_review(
    planned, monkeypatch
):
    item = planned
    calls = []

    def run(request, binding, **kwargs):
        calls.append((binding.binding_id, dict(request.selected_inputs)))
        return {
            **_result_payload(request, resolved_values={"curie": "ECO:SELECTED"}),
            "resolved_objects": [],
        }

    monkeypatch.setattr(
        "src.lib.validation_review.validator_adapter._default_package_scoped_validator_runner",
        lambda: run,
    )
    monkeypatch.setattr(
        "src.lib.validation_review.validator_adapter._default_package_scoped_validator_batch_runner",
        lambda: lambda *a, **kw: pytest.fail("Unexpected batch"),
    )
    checkpoint = checkpoint_provider(item)
    await item.adapter.initial()
    assert len(calls) == 2
    before = await item.adapter.advance(checkpoint)
    assert all(result["state"] == "awaiting_review" for result in before)
    assert len(calls) == 2
    context = decisions.ReviewContext(item.scope, item.ep, "supervisor")
    decision = decisions.record_decisions(
        context,
        checkpoint_request=checkpoint(),
        operation_id="first-review",
        entries=entries_for(item, "accept", binding_id="fixture.evidence_lookup"),
    )
    assert not decision["ready"]
    after = await item.adapter.advance(checkpoint)
    assert all(result["state"] == "completed" for result in after)
    assert [
        values["curie"] for name, values in calls if name == "fixture.dependent"
    ] == ["ECO:SELECTED", "ECO:SELECTED"]
    assert await item.adapter.advance(checkpoint) == []
    assert len(calls) == 4


@pytest.mark.asyncio
async def test_reconsideration_uses_saved_binding_and_durable_queued_work(
    planned, monkeypatch
):
    from src.lib.validation_review import scheduling

    item = planned
    calls = []

    def run(request, binding, **kwargs):
        from src.lib.validation_review.validator_adapter import RECONSIDERATION_INPUT

        calls.append((request.validator_binding_id, RECONSIDERATION_INPUT.get()))
        return {
            **_result_payload(request, resolved_values={"curie": "ECO:VALUE"}),
            "resolved_objects": [],
        }

    monkeypatch.setattr(
        "src.lib.validation_review.validator_adapter._default_package_scoped_validator_runner",
        lambda: run,
    )
    monkeypatch.setattr(
        "src.lib.validation_review.validator_adapter._default_package_scoped_validator_batch_runner",
        lambda: lambda *a, **kw: pytest.fail("Unexpected batch"),
    )
    await item.adapter.initial()
    checkpoint = checkpoint_provider(item)
    context = decisions.ReviewContext(item.scope, item.ep, "supervisor")
    entries = entries_for(item, "defer")[:1]
    decision = decisions.record_decisions(
        context, checkpoint_request=checkpoint(), operation_id="defer", entries=entries
    )
    scheduling.reconsider(
        context,
        operation_id="retry",
        parent_decision_id=decision["decision_id"],
        target_ids=[entries[0]["target_id"]],
        instructions="Check the supplied identifier carefully",
        evidence_refs=[],
    )
    # Simulate a disconnect after queueing but before executing the retry.
    resumed = ValidatorAdapter(
        item.scope, item.aid, item.ep, item.adapter.plan_ref, authenticated_groups=()
    )
    await resumed.initial()
    await resumed.initial()
    assert len(calls) == 3
    assert calls[-1][0] == "fixture.evidence_lookup"
    assert (
        calls[-1][1]["context"]["instructions"]
        == "Check the supplied identifier carefully"
    )
    supplied = calls[-1][1]["context"]["specialist_context"]
    assert supplied["previous_validation"][0]["assessed_input"]["request"]
    assert supplied["original_entries"]
    assert supplied["current_selected_entries"]
    assert supplied["attempt"]["targets"][entries[0]["target_id"]] == {
        "ordinal": 2, "ceiling": 2, "remaining_after": 0}
    assert calls[-1][1]["history"]["reader"] == "read_supplied_history"
    assert (
        admission.status(item.scope, item.ep)["budgets"][0]["remaining"][
            "extra_attempts"
        ]
        == 15
    )
    assert not admission.status(item.scope, item.ep)["ready"]


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["prompt", "retry_budget", "retry_policy"])
async def test_changed_executable_validator_contract_is_refused_before_provider(
    planned, monkeypatch, drift
):
    from src.lib.validation_review import scheduling
    from src.lib.validation_review.validator_adapter import pin_validator_agent

    from agents import ModelSettings
    from src.lib.openai_agents.config import build_default_model_retry
    from src.lib.openai_agents.model_settings_contract import register_retry_policy

    monkeypatch.setenv("OPENAI_MODEL_MAX_RETRIES", "3")
    item = planned
    validator = Agent(
        name="Configured validator", model="gpt-test", instructions="Saved policy",
        model_settings=ModelSettings(retry=build_default_model_retry()),
    )
    calls = []

    def run(request, binding, **kwargs):
        pin_validator_agent(validator, request.validator_binding_id)
        calls.append(request.request_id)
        return {
            **_result_payload(request, resolved_values={"curie": "ECO:VALUE"}),
            "resolved_objects": [],
        }

    monkeypatch.setattr(
        "src.lib.validation_review.validator_adapter._default_package_scoped_validator_runner",
        lambda: run,
    )
    monkeypatch.setattr(
        "src.lib.validation_review.validator_adapter._default_package_scoped_validator_batch_runner",
        lambda: lambda *a, **kw: pytest.fail("Unexpected batch"),
    )
    await item.adapter.initial()
    checkpoint = checkpoint_provider(item)
    context = decisions.ReviewContext(item.scope, item.ep, "supervisor")
    entries = entries_for(item, "defer")[:1]
    decision = decisions.record_decisions(
        context,
        checkpoint_request=checkpoint(),
        operation_id="defer-config",
        entries=entries,
    )
    wid = scheduling.reconsider(
        context,
        operation_id="retry-config",
        parent_decision_id=decision["decision_id"],
        target_ids=[entries[0]["target_id"]],
        instructions="Check again",
        evidence_refs=[],
    )
    if drift == "prompt":
        validator.instructions = "A different unapproved policy"
    elif drift == "retry_budget":
        validator.model_settings.retry.max_retries = 8
    else:
        validator.model_settings.retry.policy = register_retry_policy(
            lambda ctx: False, {"id": "test.declared-no-retries", "revision": 1}
        )
    with pytest.raises(admission.ReviewStopped, match="configured_validator_changed"):
        await item.adapter.execute(wid)
    assert len(calls) == 2
    assert not admission.status(item.scope, item.ep)["ready"]


@pytest.mark.asyncio
async def test_stop_keeps_actual_late_answer_without_repeating_provider(
    planned, monkeypatch
):
    import asyncio
    import threading

    item = planned
    started, finish = threading.Event(), threading.Event()
    calls = []

    def run(request, binding, **kwargs):
        calls.append(request.request_id)
        started.set()
        assert finish.wait(10)  # Test synchronization only, not an operational limit.
        return {
            **_result_payload(request, resolved_values={"curie": "ECO:LATE"}),
            "resolved_objects": [],
        }

    monkeypatch.setattr(
        "src.lib.validation_review.validator_adapter._default_package_scoped_validator_runner",
        lambda: run,
    )
    monkeypatch.setattr(
        "src.lib.validation_review.validator_adapter._default_package_scoped_validator_batch_runner",
        lambda: lambda *a, **kw: pytest.fail("Unexpected batch"),
    )
    task = asyncio.create_task(item.adapter.initial())
    assert await asyncio.to_thread(started.wait, 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    finish.set()
    # Actual synchronous provider threads finish after cancellation. Wait only
    # for their durable completions, not for any new dispatch or retry.
    async with asyncio.timeout(10):
        while True:
            with store.SessionLocal() as db:
                count = len(
                    list(
                        db.scalars(
                            select(ValidationFact.id).where(
                                ValidationFact.artifact_id == item.aid,
                                ValidationFact.kind == "completion",
                            )
                        )
                    )
                )
            if count == len(calls):
                break
            await asyncio.sleep(0.01)
    previous = len(calls)
    await item.adapter.initial()
    assert len(calls) == previous
    assert admission.status(item.scope, item.ep)["state"] == "pending"


@pytest.mark.asyncio
@pytest.mark.parametrize("chosen_action", ["continue_review", "new_extraction"])
async def test_direct_intent_is_supervisor_decision_and_durable_replay(
    planned, monkeypatch, chosen_action
):
    from src.lib.validation_review.chat import ChatReview, DirectChatAction
    from src.lib.observability.cost_context import attach_agent_cost_identity
    from src.lib.openai_agents import runner

    calls = []
    supervisor = Agent(
        name="Resolved supervisor", model="gpt-test", instructions="policy"
    )
    attach_agent_cost_identity(
        supervisor, {"agent_id": "supervisor", "agent_role": "supervisor"}
    )
    extractor = Agent(name="Only chosen extractor")
    attach_agent_cost_identity(extractor, {"agent_id": "chosen-extractor"})
    context = ChatReview(
        planned.scope.owner_id,
        planned.scope.document_id,
        planned.scope.session_id,
        "followup-turn",
        supervisor,
        (),
        "continue",
    )
    monkeypatch.setattr(context, "records", lambda: [object()])
    monkeypatch.setattr(context, "status", lambda: {"pending_count": 1, "results": []})

    async def run(agent, input, **kwargs):
        import json

        calls.append(json.loads(input))
        assert agent.tools == [] and agent.handoffs == []
        assert (
            agent.model == supervisor.model
            and agent.model_settings == supervisor.model_settings
        )
        return SimpleNamespace(
            final_output=DirectChatAction(
                action=chosen_action, explanation="Supervisor intent decision."
            )
        )

    monkeypatch.setattr(runner, "run_agent_with_owned_openai_resources", run)
    first = await context.direct_action(extractor, [])
    second = await context.direct_action(extractor, [])
    assert first == second and first.action == chosen_action and len(calls) == 1
    assert calls[0]["chosen_extractor"] == "chosen-extractor"


@pytest.mark.asyncio
async def test_multitarget_retry_retains_first_answer_when_second_is_not_admitted(
    planned, monkeypatch
):
    from src.lib.validation_review import scheduling
    from src.lib.validation_review.validator_adapter import RECONSIDERATION_INPUT

    item = planned
    retry_calls = []

    def run(request, binding, **kwargs):
        if RECONSIDERATION_INPUT.get():
            from src.lib.validation_review.provider import ACTIVE_BUDGET

            ACTIVE_BUDGET.get().execution.provider_admitted = True
            retry_calls.append(request.request_id)
            if len(retry_calls) == 2:
                raise admission.ReviewStopped(
                    "budget_exhausted", resource="reconsideration_tokens"
                )
        return {
            **_result_payload(request, resolved_values={"curie": "ECO:ANSWER"}),
            "resolved_objects": [],
        }

    monkeypatch.setattr(
        "src.lib.validation_review.validator_adapter._default_package_scoped_validator_runner",
        lambda: run,
    )
    await item.adapter.initial()
    context = decisions.ReviewContext(item.scope, item.ep, "supervisor")
    entries = entries_for(item, "defer")
    decision = decisions.record_decisions(
        context,
        checkpoint_request=checkpoint_provider(item)(),
        operation_id="defer-both",
        entries=entries,
    )
    wid = scheduling.reconsider(
        context,
        operation_id="retry-both",
        parent_decision_id=decision["decision_id"],
        target_ids=[entry["target_id"] for entry in entries],
        instructions="Check both",
        evidence_refs=[],
    )
    with pytest.raises(admission.ReviewStopped):
        await item.adapter.execute(wid)
    assert len(retry_calls) == 2
    with store.SessionLocal() as db:
        completed = [
            store._content(db, row)
            for row in db.scalars(
                select(ValidationFact).where(
                    ValidationFact.artifact_id == item.aid,
                    ValidationFact.kind == "completion",
                )
            )
        ]
    assert sum(body["state"] == "completed" for body in completed) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("planned", ["authorized"], indirect=True)
async def test_changed_groups_refuse_queued_work_without_provider_or_uncertain_state(
    planned, monkeypatch
):
    from src.lib.validation_review import scheduling
    from src.models.sql.validation_review import ValidationReviewWork

    item = planned
    calls = []

    def run(request, binding, **kwargs):
        calls.append(request.request_id)
        return {
            **_result_payload(request, resolved_values={"curie": "ECO:VALUE"}),
            "resolved_objects": [],
        }

    monkeypatch.setattr(
        "src.lib.validation_review.validator_adapter._default_package_scoped_validator_runner",
        lambda: run,
    )
    await item.adapter.initial()
    assert len(calls) == 2
    entries = entries_for(item, "defer")[:1]
    context = decisions.ReviewContext(item.scope, item.ep, "supervisor")
    decision = decisions.record_decisions(
        context,
        checkpoint_request=checkpoint_provider(item)(),
        operation_id="defer-auth",
        entries=entries,
    )
    wid = scheduling.reconsider(
        context,
        operation_id="retry-auth",
        parent_decision_id=decision["decision_id"],
        target_ids=[entries[0]["target_id"]],
        instructions="Check",
        evidence_refs=[],
    )
    resumed = ValidatorAdapter(
        item.scope, item.aid, item.ep, item.adapter.plan_ref, authenticated_groups=()
    )
    with pytest.raises(admission.ReviewStopped, match="validator_not_authorized"):
        await resumed.initial()
    assert len(calls) == 2
    with store.SessionLocal() as db:
        row = db.get(ValidationReviewWork, wid)
        assert row.state == "completed" and row.outcome["state"] == "not_admitted"
    await (
        resumed.initial()
    )  # Completed work is not rebuilt/revalidated after access changes.
    assert len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("planned", ["three"], indirect=True)
async def test_workspace_b_round_calls_only_b_and_retains_a_c(planned, monkeypatch):
    from datetime import datetime, timezone
    from src.lib.curation_workspace import review_operations as ops
    from src.lib.curation_workspace.models import CurationCandidate, CurationReviewSession, DomainEnvelopeModel
    from src.models.sql.validation_review import WorkspaceReviewOperation
    from src.schemas.curation_workspace import CurationCandidateSource
    from src.lib.validation_history import selections
    from src.lib.validation_review.validator_adapter import RECONSIDERATION_INPUT
    item = planned
    calls = []
    contexts = []
    def run(request, binding, **kwargs):
        calls.append(request.target.model_dump(mode="json"))
        contexts.append(RECONSIDERATION_INPUT.get())
        return {**_result_payload(request, resolved_values={"curie": "ECO:correct"}), "resolved_objects": []}
    monkeypatch.setattr("src.lib.validation_review.validator_adapter._default_package_scoped_validator_runner", lambda: run)
    monkeypatch.setattr("src.lib.validation_review.validator_adapter._default_package_scoped_validator_batch_runner",
                        lambda: lambda *args, **kwargs: pytest.fail("Unexpected batch"))
    await item.adapter.initial()
    checkpoint = checkpoint_provider(item)
    decisions.record_decisions(decisions.ReviewContext(item.scope, item.ep, "supervisor"),
        checkpoint_request=checkpoint(), operation_id="accept-original", entries=entries_for(item, "accept"))
    before = selections.read_manifest(item.scope, item.aid, episode_id=item.ep)
    assert before['ready'] and len(calls) == 3
    with store.SessionLocal.begin() as db:
        session = CurationReviewSession(adapter_key='fixture', document_id=item.scope.document_id,
            created_by_id=item.scope.owner_id, prepared_at=datetime.now(timezone.utc))
        db.add(session)
        db.flush()
        db.get(DomainEnvelopeModel, item.plan.envelope.envelope_id).session_id = session.id
        candidate = CurationCandidate(session=session, adapter_key='fixture', source=CurationCandidateSource.MANUAL,
            envelope_id=item.plan.envelope.envelope_id, object_id='B', envelope_revision=1)
        db.add(candidate)
        db.flush()
        saved = ops.submit(db, owner_id=item.scope.owner_id, session_id=str(session.id), request_id=str(uuid4()),
                           candidate_ids=[str(candidate.id)], guidance='Please reconsider B', groups=[])
        row = db.get(WorkspaceReviewOperation, saved['operation_id'])
        row.state, row.lease_token = 'preparing', 'test-lease'
    from auth_runtime.base import AuthPrincipal
    monkeypatch.setattr("src.lib.curation_workspace.review_authorization.get_current_subject_resolver",
        lambda: lambda subject: AuthPrincipal(subject=subject, provider="fixture", groups=[]))
    owner, groups, artifacts = ops.prepare(saved['operation_id'], 'test-lease')
    current = artifacts[0]
    adapter = ValidatorAdapter(item.scope, item.aid, current['episode_id'], current['plan_ref'], authenticated_groups=groups)
    assert len(adapter.plan['initial_requests']) == 1
    await adapter.initial()
    assert len(calls) == 4
    assert calls[-1] == adapter.plan['initial_requests'][0]['request']['target']
    assert contexts[-1] is not None
    assert 'Please reconsider B' in str(contexts[-1])
    # Exact history reaches the specialist, but untouched records are not dispatched.
    await adapter.initial()
    assert len(calls) == 4
    with store.SessionLocal() as db:
        attempts = list(db.scalars(select(ValidationFact).where(ValidationFact.artifact_id == item.aid, ValidationFact.kind == 'attempt')))
        new_attempt = next(f for f in attempts if store._content(db, f)['input']['review_episode_id'] == current['episode_id'])
        version = store._current(db, item.aid, new_attempt.target_id)['version']
    decisions.record_decisions(decisions.ReviewContext(item.scope, current['episode_id'], 'supervisor'),
        checkpoint_request=ops.checkpoint(owner, current, groups), operation_id='accept-B', entries=[{
            'target_id': new_attempt.target_id, 'decision_unit_id': new_attempt.target_id, 'expected_version': version,
            'logical_attempt_id': new_attempt.id, 'action': 'accept', 'reason': 'Reviewed B again'}])
    after = selections.read_manifest(item.scope, item.aid, episode_id=current['episode_id'])
    assert after['review_ready']
    for old in before['units']:
        if old['target_id'] == new_attempt.target_id:
            continue
        retained = next(unit for unit in after['units'] if unit['target_id'] == old['target_id'])
        for key in ('selected', 'selection_id', 'version'):
            assert retained[key] == old[key]


@pytest.mark.asyncio
async def test_curator_round_reuses_accepted_dependency_until_supervisor_explicitly_reconsiders(planned, monkeypatch):
    from src.lib.validation_history import selections
    from src.lib.validation_review import scheduling
    item = planned
    calls = []
    def run(request, binding, **kwargs):
        calls.append(request.request_id)
        return {**_result_payload(request, resolved_values={'curie': 'ECO:correct'}), 'resolved_objects': []}
    monkeypatch.setattr('src.lib.validation_review.validator_adapter._default_package_scoped_validator_runner', lambda: run)
    monkeypatch.setattr('src.lib.validation_review.validator_adapter._default_package_scoped_validator_batch_runner', lambda: lambda *a, **k: pytest.fail('Unexpected batch'))
    await item.adapter.initial()
    checkpoint = checkpoint_provider(item)
    old_entries = entries_for(item, 'accept')
    decisions.record_decisions(decisions.ReviewContext(item.scope, item.ep, 'supervisor'),
        checkpoint_request=checkpoint(), operation_id='accept-old', entries=old_entries)
    dependency, requested = [entry['target_id'] for entry in old_entries]
    with store.SessionLocal() as db:
        original = store._content(db, store._fact(db, item.aid, item.ep))
    episode = selections.start_episode(item.scope, item.aid, episode={
        'episode_id': str(uuid4()), 'reason': 'Record plus previously reviewed dependency',
        'obligations': original['obligations'], 'review_target_ids': [dependency, requested],
        'retained_episode_ids': {dependency: item.ep}})
    root = admission.create_scope(item.scope, operation_id=str(uuid4()), kind='workspace_operation', targets=2, episodes=1)
    child = admission.create_scope(item.scope, operation_id=str(uuid4()), kind='document_run', targets=2, episodes=1, parent_id=root)
    admission.attach_episode(item.scope, artifact_id=item.aid, episode_id=episode, scope_id=child,
        predecessor_id=item.ep, renewal_actor_id=item.scope.owner_id, renewal_reason='Explicit curator round', curator_round=True)
    plan = replace(item.plan, envelope=item.plan.envelope.model_copy(deep=True),
                   initial_jobs=tuple(replace(job, history_episode_id=episode) for job in item.plan.initial_jobs))
    plan.envelope.metadata['review_episode_id'] = episode
    plan_ref = save_plan(plan, context=item.adapter.context)
    adapter = ValidatorAdapter(item.scope, item.aid, episode, plan_ref, authenticated_groups=())
    await adapter.initial()
    assert len(calls) == 3  # Two original calls, only the requested target in the fresh round.
    ctx = decisions.ReviewContext(item.scope, episode, 'supervisor')
    entry = next(entry for entry in entries_for(item, 'defer') if entry['target_id'] == dependency)
    parent = decisions.record_decisions(ctx, checkpoint_request=checkpoint(), operation_id='question-dependency', entries=[entry])
    adapter.prepare_inputs(checkpoint)
    wid = scheduling.reconsider(ctx, operation_id='reconsider-dependency', parent_decision_id=parent['decision_id'],
        target_ids=[dependency], instructions='Reconsider the dependency needed by this record', evidence_refs=[],
        refresh_inputs=adapter.refresh_inputs, rebind_receipt=adapter.rebind_receipt)
    await adapter.execute(wid)
    assert len(calls) == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("planned", ["three"], indirect=True)
@pytest.mark.parametrize("fatal", [False, True])
async def test_initial_drains_started_threads_and_leaves_queued_work(planned, monkeypatch, fatal):
    import asyncio
    import threading
    from src.lib.validation_review.admission import ReviewStopped

    monkeypatch.setattr("src.lib.openai_agents.config.get_max_parallel_validators", lambda: 2)
    started, release = threading.Event(), threading.Event()
    refused = asyncio.Event()
    calls = []

    def slow_worker():
        started.set()
        assert release.wait(10)
        if fatal:
            raise RuntimeError("fatal sibling")
        return {"state": "completed"}

    async def execute(wid):
        calls.append(wid)
        if len(calls) == 1:
            assert await asyncio.to_thread(started.wait, 5)
            refused.set()
            raise ReviewStopped("budget_exhausted")
        return await asyncio.to_thread(slow_worker)

    monkeypatch.setattr(planned.adapter, "execute", execute)
    task = asyncio.create_task(planned.adapter.initial())
    try:
        await asyncio.wait_for(refused.wait(), 5)
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(RuntimeError if fatal else ReviewStopped, match="fatal sibling" if fatal else "budget_exhausted"):
            await task
        assert len(calls) == 2
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("planned", ["routed"], indirect=True)
async def test_routed_plan_replays_both_specialists_with_distinct_execution_pins(planned, monkeypatch):
    from copy import deepcopy
    from src.lib.validation_review.validator_adapter import pin_validator_agent
    item = planned
    calls = []
    def run(request, binding, **kwargs):
        pin_validator_agent(Agent(name=binding.route_value, model="gpt-test",
            instructions="Policy for " + binding.route_value),
            binding.binding_id, route_value=binding.route_value)
        calls.append(binding.route_value)
        return {**_result_payload(request, resolved_values={"curie": "ECO:" + binding.route_value}), "resolved_objects": []}
    monkeypatch.setattr("src.lib.validation_review.validator_adapter._default_package_scoped_validator_runner", lambda: run)
    monkeypatch.setattr("src.lib.validation_review.validator_adapter._default_package_scoped_validator_batch_runner", lambda: lambda *a, **kw: pytest.fail("Unexpected batch"))
    assert item.adapter.plan["contracts"]
    frozen = runtime.frozen_job_requests(item.plan.history_binding, item.plan.initial_jobs)
    assert {request["route_value"] for request in frozen} == {"one", "two"}
    changed = deepcopy(frozen[0])
    changed["route_value"] = "two" if changed["route_value"] == "one" else "one"
    with pytest.raises(store.HistoryAccessError):
        item.adapter.job(changed)
    await item.adapter.initial()
    await item.adapter.initial()
    assert sorted(calls) == ["one", "two"]
    # Unselected healthy answers need no extra freshness probes merely to
    # determine whether an operational-unavailability blocker exists.
    from src.lib.validation_history import selections
    with monkeypatch.context() as patch:
        patch.setattr(store, "_attempt_is_current", lambda *a, **kw: pytest.fail("Unnecessary outage freshness probe"))
        manifest = selections.read_manifest(item.scope, item.aid, episode_id=item.ep)
        assert not any(row["code"] == "configured_validator_unavailable" for row in manifest["blockers"])
    with store.SessionLocal() as db:
        pins = list(db.scalars(select(ValidationFact).where(
            ValidationFact.artifact_id == item.aid, ValidationFact.kind == "review_validator_config")))
        assert len(pins) == 2
    result = decisions.record_decisions(decisions.ReviewContext(item.scope, item.ep, "supervisor"),
        checkpoint_request=checkpoint_provider(item)(), operation_id="accept-both-routes", entries=entries_for(item, "accept"))
    assert result["ready"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("planned", ["routed"], indirect=True)
async def test_runtime_unavailable_route_does_not_stop_independent_route(planned, monkeypatch):
    from unittest.mock import Mock
    from src.lib.agent_studio.catalog_service import AgentRuntimeUnavailable
    from src.lib.domain_packs.validator_dispatch import _build_available_validator_agent
    item = planned
    monkeypatch.setenv("MAX_PARALLEL_VALIDATORS", "1")
    unavailable = item.plan.initial_jobs[0].match.binding.route_value
    calls = []
    offline = {unavailable}
    capture = Mock(return_value=True)
    monkeypatch.setattr("src.lib.observability.runtime.report_runtime_exception", capture)
    def build(agent_key, **kwargs):
        if agent_key in offline:
            raise AgentRuntimeUnavailable("private curator text and database password must not escape")
        return Agent(name=agent_key, model="gpt-test", instructions="Configured policy")
    monkeypatch.setattr("src.lib.agent_studio.catalog_service.get_agent_by_id", build)
    def run(request, binding, **kwargs):
        _build_available_validator_agent(binding.route_value)
        calls.append(binding.route_value)
        return {**_result_payload(request, resolved_values={"curie": "ECO:accepted"}), "resolved_objects": []}
    monkeypatch.setattr("src.lib.validation_review.validator_adapter._default_package_scoped_validator_runner", lambda: run)
    result = await item.adapter.initial()
    assert any(row.get("reason") == "configured_validator_unavailable" for row in result)
    assert calls == [route for route in ("one", "two") if route != unavailable]
    assert capture.call_count == 1
    assert "password" not in str(capture.call_args.args[0])
    assert capture.call_args.args[0].__cause__ is None
    assert capture.call_args.args[0].__context__ is None
    from src.lib.validation_history import selections
    manifest = selections.read_manifest(item.scope, item.aid, episode_id=item.ep)
    blocked = [row for row in manifest["blockers"] if row["code"] == "configured_validator_unavailable"]
    assert len(blocked) == 1
    assert blocked[0]["route_value"] == unavailable
    assert not manifest["ready"]
    with store.SessionLocal() as db:
        completion = store._fact(db, item.aid, blocked[0]["completion_ref"]["fact_id"], kind="completion")
        body = store._content(db, completion)
        assert body["state"] == "failed"
        assert body["result"] is None
        assert body["diagnostics"]["operational_state"] == "not_admitted"
    # Reconnect must not resubmit a completed operational refusal or duplicate capture.
    await item.adapter.initial()
    assert len(calls) == 1
    assert capture.call_count == 1

    checkpoint = checkpoint_provider(item)
    context = decisions.ReviewContext(item.scope, item.ep, "supervisor")
    healthy = [entry for entry in entries_for(item, "accept") if entry["target_id"] != blocked[0]["target_id"]]
    accepted = decisions.record_decisions(context, checkpoint_request=checkpoint(),
        operation_id="accept-healthy-route", entries=healthy)
    assert not accepted["ready"]
    assert len(accepted["applied"]) == 1
    deferred = decisions.record_decisions(context, checkpoint_request=checkpoint(),
        operation_id="defer-unavailable-route", entries=[entry for entry in entries_for(item, "defer")
            if entry["target_id"] == blocked[0]["target_id"]])
    from src.lib.validation_review import scheduling
    offline.clear()
    wid = scheduling.reconsider(context, operation_id="retry-now-available-route",
        parent_decision_id=deferred["decision_id"], target_ids=[blocked[0]["target_id"]],
        instructions="Retry the configured specialist after its runtime recovered", evidence_refs=[])
    await item.adapter.execute(wid)
    current = selections.read_manifest(item.scope, item.aid, episode_id=item.ep)
    assert not any(row["code"] == "configured_validator_unavailable" for row in current["blockers"])
    assert not current["ready"]  # The new answer still needs an explicit decision.
    assert capture.call_count == 1


@pytest.mark.parametrize("planned", ["optional-absence"], indirect=True)
def test_optional_absence_is_saved_once_without_a_validator_attempt(planned):
    from src.lib.validation_review import source_inputs
    from src.lib.validation_history import selections
    from src.lib.domain_packs.input_selectors import build_domain_validation_request
    item = planned
    assert not item.plan.initial_jobs
    match = item.plan.matches[0]
    result = build_domain_validation_request(match)
    assert result.absent_inputs == ("query",)
    first = runtime.record_absent_source_input(item.plan.history_binding, match, result)
    again = runtime.record_absent_source_input(item.plan.history_binding, match, result)
    assert again == first
    with store.SessionLocal() as db:
        facts = list(db.scalars(select(ValidationFact).where(
            ValidationFact.artifact_id == item.aid, ValidationFact.kind == "validation_source_input")))
        assert len(facts) == 1
        body = store._content(db, facts[0])
        assert source_inputs.is_applicability(body)
        assert body["request"] is None
        assert body["input"]["input_selectors"]["query"]["path"] == "optional.term"
        assert not source_inputs.is_applicability({"input": None, "request": None})
        assert not list(db.scalars(select(ValidationFact).where(
            ValidationFact.artifact_id == item.aid, ValidationFact.kind == "attempt")))
    manifest = selections.read_manifest(item.scope, item.aid, episode_id=item.ep)
    assert manifest["units"]
    assert not manifest["ready"]  # A declared optional absence is not scientific acceptance.


def absence_entry(item, action="accept", assessment="omission_supported", target_id=None):
    from src.lib.validation_review import source_inputs
    from src.lib.validation_history import selections
    manifest = selections.read_manifest(item.scope, item.aid, episode_id=item.ep)
    unit = next(unit for unit in manifest["units"] if target_id is None or unit["target_id"] == target_id)
    with store.SessionLocal() as db:
        fact = source_inputs.latest(db, item.aid, unit["target_id"])
        return {"target_id": fact.target_id, "decision_unit_id": unit["target_id"],
                "expected_version": unit["version"], "action": action,
                "reason": "Reviewer assessed the original source and supplied evidence",
                "applicability_ref": source_inputs.reference(fact),
                **({"absence_assessment": assessment} if action == "accept" else {})}


@pytest.mark.parametrize("planned", ["optional-absence"], indirect=True)
@pytest.mark.parametrize("action", ["accept", "reject", "defer"])
def test_optional_absence_has_an_explicit_scientific_disposition(planned, action):
    from src.lib.validation_history import selections
    item = planned
    entry = absence_entry(item, action)
    checkpoint = checkpoint_provider(item)
    response = decisions.record_decisions(decisions.ReviewContext(item.scope, item.ep, "supervisor"),
        checkpoint_request=checkpoint(), operation_id="assess-absence", entries=[entry])
    assert response["ready"] == (action == "accept")
    assert checkpoint().envelope.extracted_objects[0].payload == {"optional": {}}
    manifest = selections.read_manifest(item.scope, item.aid, episode_id=item.ep)
    assert manifest["units"][0]["eligible"] == (action == "accept")
    if action == "accept":
        assert manifest["units"][0]["applicability_ref"] == entry["applicability_ref"]


@pytest.mark.parametrize("planned", ["optional-absence"], indirect=True)
@pytest.mark.parametrize("invalid", ["hash", "no_assessment", "ambiguous", "baseline"])
def test_optional_absence_decisions_reject_inexact_or_ambiguous_subjects(planned, invalid):
    item = planned
    entry = absence_entry(item)
    if invalid == "hash":
        entry["applicability_ref"]["sha256"] = "wrong"
    elif invalid == "no_assessment":
        entry.pop("absence_assessment")
    elif invalid == "ambiguous":
        entry["extraction_ref"] = "also-baseline"
    else:
        entry.pop("applicability_ref")
        entry.pop("absence_assessment")
        with store.SessionLocal() as db:
            entry["extraction_ref"] = store._artifact(db, item.aid, item.scope).baseline_sha
    with pytest.raises((ValueError, store.HistoryConflict)):
        decisions.record_decisions(decisions.ReviewContext(item.scope, item.ep, "supervisor"),
            checkpoint_request=checkpoint_provider(item)(), operation_id="invalid-absence", entries=[entry])
    with store.SessionLocal() as db:
        assert store._current(db, item.aid, entry["target_id"])["version"] == 0


@pytest.mark.parametrize("planned", [{"shape": "optional-absence", "count": 2}], indirect=True)
def test_absence_assessment_stales_on_changed_supplied_evidence(planned):
    from dataclasses import replace
    from src.lib.domain_packs.input_selectors import build_domain_validation_request
    from src.lib.validation_history import selections
    item = planned
    checkpoint = checkpoint_provider(item)
    entry = absence_entry(item)
    decisions.record_decisions(decisions.ReviewContext(item.scope, item.ep, "supervisor"),
        checkpoint_request=checkpoint(), operation_id="accept-absence", entries=[entry])
    match = item.plan.matches[0]
    result = build_domain_validation_request(match)
    assert runtime.record_absent_source_input(item.plan.history_binding, match, result) == entry["applicability_ref"]
    assert selections.read_manifest(item.scope, item.aid, episode_id=item.ep)["units"][0]["eligible"]
    changed = replace(result, evidence=[{"evidence_record_id": "new-evidence", "quote": "New supplied evidence"}])
    assert runtime.record_absent_source_input(item.plan.history_binding, match, changed) != entry["applicability_ref"]
    manifest = selections.read_manifest(item.scope, item.aid, episode_id=item.ep)
    assert not manifest["ready"]
    assert manifest["units"][0]["reason"] == "stale"
    from src.lib.validation_review import continuation
    context = decisions.ReviewContext(item.scope, item.ep, "supervisor")
    # No validator/extractor repair tool is offered: this fresh scientific
    # subject can be assessed without spending another provider attempt.
    view = continuation.inspect(context, {"record_validation_review"})
    assert entry["decision_unit_id"] in view["eligible"]
    decisions.record_decisions(context, checkpoint_request=checkpoint(),
        operation_id="assess-fresh-absence", entries=[absence_entry(item)])
    assert selections.read_manifest(item.scope, item.aid, episode_id=item.ep)["units"][0]["eligible"]


@pytest.mark.parametrize("planned", ["optional-absence"], indirect=True)
def test_unresolved_absence_is_reviewed_but_projects_honest_uncertainty(planned):
    from src.lib.validation_history.assessments import FINDING_CODE
    item = planned
    checkpoint = checkpoint_provider(item)
    entry = absence_entry(item, assessment="unresolved")
    response = decisions.record_decisions(decisions.ReviewContext(item.scope, item.ep, "supervisor"),
        checkpoint_request=checkpoint(), operation_id="unresolved-absence", entries=[entry])
    assert response["ready"]  # Assessment is complete; submission rules remain separate.
    envelope = checkpoint().envelope
    assert envelope.extracted_objects[0].payload == {"optional": {}}
    findings = [finding for finding in envelope.validation_findings if finding.code == FINDING_CODE]
    assert len(findings) == 1
    assert findings[0].message == entry["reason"]
    assert findings[0].field_ref.field_path == "optional.term"
    assert findings[0].details["review_assessment"]["applicability_ref"] == entry["applicability_ref"]


@pytest.mark.asyncio
@pytest.mark.parametrize("planned", ["absence-dependent-optional", "absence-dependent-required"], indirect=True)
async def test_dependent_absence_requires_reviewed_prerequisite_and_respects_selectors(planned, monkeypatch):
    from src.lib.validation_review import source_inputs
    from src.lib.validation_history import selections
    item = planned
    monkeypatch.setattr("src.lib.validation_review.validator_adapter._default_package_scoped_validator_runner",
        lambda: lambda *a, **kw: pytest.fail("Absent inputs must not call a provider"))
    checkpoint = checkpoint_provider(item)
    matches = {match.binding.binding_id: match for match in item.plan.matches}
    parent = runtime.binding_target(item.plan.history_binding, matches["fixture.evidence_lookup"])
    child = runtime.binding_target(item.plan.history_binding, matches["fixture.dependent"])
    before = await item.adapter.advance(checkpoint)
    assert before[0]["state"] == "awaiting_review"
    with store.SessionLocal() as db:
        assert source_inputs.latest(db, item.aid, child) is None
    context = decisions.ReviewContext(item.scope, item.ep, "supervisor")
    decisions.record_decisions(context, checkpoint_request=checkpoint(), operation_id="accept-parent-absence",
        entries=[absence_entry(item, target_id=parent)])
    after = await item.adapter.advance(checkpoint)
    required = matches["fixture.dependent"].binding.input_fields["query"].required
    if required:
        assert after[0]["state"] == "not_runnable"
        assert after[0]["reason"] == "required_inputs_unavailable"
        with store.SessionLocal() as db:
            assert source_inputs.latest(db, item.aid, child) is None
    else:
        assert after[0]["reason"] == "applicability_review_required"
        with store.SessionLocal() as db:
            fact = source_inputs.latest(db, item.aid, child)
            body = store._content(db, fact)
            assert body["input"]["prerequisites"][0]["target_id"] == parent
            original_ref = source_inputs.reference(fact)
        repeated = await item.adapter.advance(checkpoint)
        assert repeated[0]["applicability_refs"][child] == original_ref
        # Refresh the prerequisite while the dependent unit is still pending,
        # so this remains the same active review episode.
        with store.SessionLocal.begin() as db:
            store.apply_decisions_in_transaction(db, item.scope, item.aid, operation_id="revoke-prerequisite",
                actor={"type": "agent", "id": "supervisor"}, entries=[{
                    "target_id": parent, "expected_version": store._current(db, item.aid, parent)["version"],
                    "action": "revoke", "reason": "Reconsider source"}])
        with store.SessionLocal() as db:
            with pytest.raises(store.HistoryConflict, match="Prerequisite"):
                source_inputs.require_applicability(db, item.aid, child, original_ref["fact_id"], scope=item.scope)
        decisions.record_decisions(context, checkpoint_request=checkpoint(), operation_id="reaccept-parent-absence",
            entries=[absence_entry(item, target_id=parent)])
        refreshed = await item.adapter.advance(checkpoint)
        fresh_ref = refreshed[0]["applicability_refs"][child]
        assert fresh_ref != original_ref
        assert (await item.adapter.advance(checkpoint))[0]["applicability_refs"][child] == fresh_ref
        decisions.record_decisions(context, checkpoint_request=checkpoint(), operation_id="accept-dependent-absence",
            entries=[absence_entry(item, target_id=child)])
        assert selections.read_manifest(item.scope, item.aid, episode_id=item.ep)["ready"]


@pytest.mark.parametrize("planned", ["optional-absence"], indirect=True)
def test_retirement_and_restoration_do_not_reactivate_old_absence_acceptance(planned):
    from src.lib.validation_history import selections
    item = planned
    entry = absence_entry(item)
    decisions.record_decisions(decisions.ReviewContext(item.scope, item.ep, "supervisor"),
        checkpoint_request=checkpoint_provider(item)(), operation_id="accept-before-retirement", entries=[entry])
    root = item.plan.envelope.extracted_objects[0].metadata["validation_history"]["target_id"]
    with store.SessionLocal.begin() as db:
        for action in ("retire", "restore"):
            store.apply_decisions_in_transaction(db, item.scope, item.aid, operation_id=action + "-source",
                actor={"type": "agent", "id": "supervisor"}, entries=[{
                    "target_id": root, "expected_version": store._current(db, item.aid, root)["version"],
                    "action": action, "reason": "Explicit source lifecycle decision"}])
    manifest = selections.read_manifest(item.scope, item.aid, episode_id=item.ep)
    assert not manifest["ready"]
    assert manifest["units"][0]["reason"] == "stale"


@pytest.mark.parametrize("planned", [{"shape": "optional-absence", "count": 80, "packet_bytes": 3500}], indirect=True)
def test_live_outstanding_scan_covers_eighty_records_while_earlier_pages_complete(planned):
    from src.lib.validation_review import reads
    item = planned
    context = decisions.ReviewContext(item.scope, item.ep, "supervisor")
    checkpoint = checkpoint_provider(item)
    seen = set()
    cursor = None
    pages = 0
    while True:
        page = reads.outstanding(item.scope, item.ep, cursor=cursor)
        assert reads._wire_size(page) <= 3500
        assert page["decision_entries"] == 100
        assert not page["ready"]
        assert page["rows"]
        entries = []
        for row in page["rows"]:
            assert row["decision_unit_id"] not in seen
            seen.add(row["decision_unit_id"])
            assert row["mode"] == "validation" and row["agent_may_decide"]
            assert row["record_target_id"]
            assert row["unit_ref"]["fact_id"] == row["decision_unit_id"]
            entries.append({key: row[key] for key in ("target_id", "decision_unit_id", "expected_version", "applicability_ref")}
                           | {"action": "accept", "absence_assessment": "omission_supported", "reason": "Reviewed source supports omission"})
        pages += 1
        decisions.record_decisions(context, checkpoint_request=checkpoint(), operation_id=f"page-{pages}", entries=entries)
        cursor = page["next_cursor"]
        if cursor is None:
            assert page["scan_complete"]
            break
    assert pages > 1 and len(seen) == 80
    complete = reads.outstanding(item.scope, item.ep)
    assert complete["ready"] and complete["artifact_ready"]
    assert complete["rows"] == [] and complete["outstanding_units"] == 0
    assert complete["scan_complete"]


@pytest.mark.parametrize("planned", [{"shape": "optional-absence", "count": 3, "packet_bytes": 1800}], indirect=True)
def test_outstanding_cursor_is_bound_to_authenticated_episode(planned):
    import base64
    import json
    from src.lib.validation_review import reads
    item = planned
    page = reads.outstanding(item.scope, item.ep)
    assert page["next_cursor"]
    cursor = json.loads(base64.urlsafe_b64decode(page["next_cursor"]))
    cursor["context"] = "different-episode-or-owner"
    forged = base64.urlsafe_b64encode(json.dumps(cursor).encode()).decode()
    with pytest.raises(ValueError, match="bound episode"):
        reads.outstanding(item.scope, item.ep, cursor=forged)
    with pytest.raises(ValueError, match="bound episode"):
        reads.outstanding(item.scope, item.ep, cursor="not a cursor")


@pytest.mark.parametrize("planned", ["optional-absence"], indirect=True)
def test_continuation_view_uses_absence_subject_and_ignores_status_and_version_churn(planned):
    from src.lib.validation_review import continuation, reads
    item = planned
    context = decisions.ReviewContext(item.scope, item.ep, "supervisor")
    checkpoint = checkpoint_provider(item)
    before = continuation.inspect(context, {"record_validation_review"})
    assert before["eligible"] and not before["causes"]
    reads.status_reference(item.scope, item.ep)
    reads.snapshot(item.scope, item.ep, operation_id="status-churn")
    assert continuation.inspect(context, {"record_validation_review"})["signature"] == before["signature"]
    for operation in ("defer-first", "defer-again"):
        decisions.record_decisions(context, checkpoint_request=checkpoint(),
            operation_id=operation, entries=[absence_entry(item, action="defer")])
        current = continuation.inspect(context, {"record_validation_review"})
        if operation == "defer-first":
            deferred = current["signature"]
        else:
            assert current["signature"] == deferred
    decisions.record_decisions(context, checkpoint_request=checkpoint(),
        operation_id="accept-source", entries=[absence_entry(item)])
    complete = continuation.inspect(context, {"record_validation_review"})
    assert complete["review"]["ready"] and not complete["eligible"]
    assert complete["signature"] != deferred


@pytest.mark.parametrize("planned", [{"shape": "optional-absence", "count": 2}], indirect=True)
def test_continuation_accept_revoke_accept_returns_to_seen_scientific_state(planned):
    from src.lib.validation_review import continuation
    item = planned
    context = decisions.ReviewContext(item.scope, item.ep, "supervisor")
    checkpoint = checkpoint_provider(item)
    decisions.record_decisions(context, checkpoint_request=checkpoint(),
        operation_id="first-accept", entries=[absence_entry(item)])
    accepted = continuation.inspect(context, {"record_validation_review"})["signature"]
    revoke = absence_entry(item, action="revoke")
    revoke.pop("applicability_ref")
    decisions.record_decisions(context, checkpoint_request=checkpoint(),
        operation_id="revoke-selection", entries=[revoke])
    decisions.record_decisions(context, checkpoint_request=checkpoint(),
        operation_id="accept-again", entries=[absence_entry(item)])
    assert continuation.inspect(context, {"record_validation_review"})["signature"] == accepted


@pytest.mark.parametrize("planned", [{"shape": "optional-absence", "count": 3}], indirect=True)
def test_focused_outstanding_and_progress_do_not_expand_record_scope(planned):
    from src.lib.validation_history import selections
    from src.lib.validation_review import continuation, reads
    item = planned
    with store.SessionLocal() as db:
        original = store._content(db, store._fact(db, item.aid, item.ep))
    obligations = original["obligations"]
    chosen = obligations[1]["target_id"]
    neighbor = obligations[0]["target_id"]
    ep = selections.start_episode(item.scope, item.aid, episode={
        "episode_id": uuid4().hex, "reason": "Explicit curator request for one record",
        "obligations": obligations, "review_target_ids": [chosen],
        "retained_episode_ids": {o["target_id"]: item.ep for o in obligations if o["target_id"] != chosen},
    })
    sid = admission.create_scope(item.scope, operation_id=uuid4().hex, kind="document_run", targets=1, episodes=1)
    admission.attach_episode(item.scope, artifact_id=item.aid, episode_id=ep, scope_id=sid)
    context = decisions.ReviewContext(item.scope, ep, "supervisor")
    first = continuation.inspect(context, {"record_validation_review"})
    page = reads.outstanding(item.scope, ep)
    assert {row["decision_unit_id"] for row in page["rows"]} == {chosen}
    assert first["eligible"] == {chosen}
    checkpoint = checkpoint_provider(item)
    with store.SessionLocal.begin() as db:
        store.apply_decisions_in_transaction(db, item.scope, item.aid, operation_id="outside-scoped-review",
            actor={"type": "curator", "id": item.scope.owner_id}, entries=[{
                "target_id": neighbor, "expected_version": store._current(db, item.aid, neighbor)["version"],
                "action": "defer", "reason": "Curator independently revisited the neighboring record"}])
    assert continuation.inspect(context, {"record_validation_review"})["signature"] == first["signature"]
    with pytest.raises(store.HistoryAccessError, match="outside"):
        decisions.record_decisions(context, checkpoint_request=checkpoint(), operation_id="cannot-review-neighbor",
                                   entries=[absence_entry(item, target_id=neighbor)])
    decisions.record_decisions(context, checkpoint_request=checkpoint(), operation_id="review-requested-record",
                               entries=[absence_entry(item, target_id=chosen)])
    complete = reads.outstanding(item.scope, ep)
    assert complete["ready"] and not complete["artifact_ready"]
    assert complete["rows"] == []  # The remaining unrequested record is still pending.


@pytest.mark.parametrize("planned", ["optional-composite-absence"], indirect=True)
def test_composite_absence_uses_parent_source_and_exact_child_decision_unit(planned):
    from src.lib.validation_review import continuation, reads
    from src.lib.validation_history.assessments import FINDING_CODE
    item = planned
    context = decisions.ReviewContext(item.scope, item.ep, "supervisor")
    checkpoint = checkpoint_provider(item)
    page = reads.outstanding(item.scope, item.ep)
    assert len(page["rows"]) == 1
    row = page["rows"][0]
    assert row["target_id"] != row["decision_unit_id"]
    assert continuation.inspect(context, {"record_validation_review"})["eligible"] == {row["decision_unit_id"]}
    entry = {key: row[key] for key in ("target_id", "decision_unit_id", "expected_version", "applicability_ref")}
    entry.update(action="accept", absence_assessment="unresolved", reason="Supplied source cannot resolve the optional identity")
    result = decisions.record_decisions(context, checkpoint_request=checkpoint(), operation_id="assess-component", entries=[entry])
    assert result["ready"]
    assert reads.outstanding(item.scope, item.ep)["rows"] == []
    envelope = checkpoint().envelope
    assert envelope.extracted_objects[0].payload == {"optional": {}}
    findings = [f for f in envelope.validation_findings if f.code == FINDING_CODE]
    assert len(findings) == 1
    assert findings[0].field_ref.field_path == "optional.term"
    assert findings[0].details["review_assessment"]["applicability_ref"] == entry["applicability_ref"]


@pytest.mark.parametrize("planned", ["optional-absence"], indirect=True)
def test_absence_acceptance_never_releases_curator_value_even_with_revalidation_intent(planned):
    from src.lib.validation_history import curator_authority
    item = planned
    envelope = item.plan.envelope
    obj = envelope.extracted_objects[0]
    root = obj.metadata["validation_history"]["target_id"]
    with store.SessionLocal.begin() as db:
        overlay = curator_authority.record_patch(db, item.scope, item.aid, event_id=uuid4().hex,
            actor_id=item.scope.owner_id, body=curator_authority.bind_patch_anchors(envelope, {
                "target_id": root, "object_id": "one", "value_path": "optional.term", "action": "override",
                "changes": [{"target_id": root, "path": "optional.term", "present": True,
                             "value": "Curator supplied term"}]}))
    curator_authority.record_revalidation_intent(item.plan.history_binding, envelope=envelope,
        episode_id=item.ep, object_id="one", field_paths=["optional.term"], actor_id=item.scope.owner_id)
    checkpoint = checkpoint_provider(item)
    decisions.record_decisions(decisions.ReviewContext(item.scope, item.ep, "supervisor"),
        checkpoint_request=checkpoint(), operation_id="assess-with-curator-overlay", entries=[absence_entry(item)])
    assert checkpoint().envelope.extracted_objects[0].payload["optional"]["term"] == "Curator supplied term"
    with store.SessionLocal() as db:
        assert [fact.id for fact, _ in curator_authority.active_overlays(db, item.aid)] == [overlay]


@pytest.mark.asyncio
@pytest.mark.parametrize("planned", [{"shape": "absence-dependent-optional", "count": 2}], indirect=True)
async def test_dependent_absence_reference_refresh_is_not_new_scientific_progress(planned):
    from src.lib.validation_review import continuation, reads
    from src.lib.validation_review.observations import Observations, observation_scope
    from src.lib.validation_history import selections
    item = planned
    checkpoint = checkpoint_provider(item)
    context = decisions.ReviewContext(item.scope, item.ep, "supervisor")
    first_object = item.plan.envelope.extracted_objects[0].metadata["validation_history"]["target_id"]
    matches = [m for m in item.plan.matches if m.object_envelope.metadata["validation_history"]["target_id"] == first_object]
    parent = runtime.binding_target(item.plan.history_binding, next(m for m in matches if not m.binding.runs_after))
    child = runtime.binding_target(item.plan.history_binding, next(m for m in matches if m.binding.runs_after))
    signatures, subjects = [], []
    observations = Observations()
    progress = []
    for round_index in range(2):
        if round_index:
            entry = absence_entry(item, action="revoke", target_id=parent)
            entry.pop("applicability_ref")
            decisions.record_decisions(context, checkpoint_request=checkpoint(), operation_id="revoke-parent", entries=[entry])
        decisions.record_decisions(context, checkpoint_request=checkpoint(), operation_id=f"parent-{round_index}",
            entries=[absence_entry(item, target_id=parent)])
        await item.adapter.advance(checkpoint)
        child_entry = absence_entry(item, target_id=child)
        subjects.append(child_entry["applicability_ref"])
        decisions.record_decisions(context, checkpoint_request=checkpoint(), operation_id=f"child-{round_index}",
            entries=[child_entry])
        assert not selections.read_manifest(item.scope, item.aid, episode_id=item.ep)["ready"]
        signatures.append(continuation.inspect(context, {"record_validation_review"})["signature"])
        with observation_scope(observations):
            offset = 0
            while True:
                page = reads.read(item.scope, item.ep, **child_entry["applicability_ref"], offset=offset)
                assert page["sha256"] == child_entry["applicability_ref"]["sha256"]
                offset = page["next_offset"]
                if offset is None:
                    break
        progress.append(observations.progress(signatures[-1]))
    assert subjects[0] != subjects[1]  # Exact prerequisite histories remain distinct.
    assert signatures[0] == signatures[1]  # No new source, science or provider attempt.
    assert progress == [True, False]


@pytest.mark.asyncio
@pytest.mark.parametrize("planned", ["absence-runnable-dependent"], indirect=True)
async def test_reconsideration_refreshes_an_accepted_absence_prerequisite(planned, monkeypatch):
    from src.lib.validation_review import scheduling
    from src.lib.validation_review.attempt_lineage import counts
    item = planned
    checkpoint = checkpoint_provider(item)
    context = decisions.ReviewContext(item.scope, item.ep, "supervisor")
    parent_match = next(m for m in item.plan.matches if not m.binding.runs_after)
    child_match = next(m for m in item.plan.matches if m.binding.runs_after)
    parent = runtime.binding_target(item.plan.history_binding, parent_match)
    child = runtime.binding_target(item.plan.history_binding, child_match)
    monkeypatch.setattr("src.lib.validation_review.validator_adapter._default_package_scoped_validator_runner",
        lambda: lambda request, **kwargs: {**_result_payload(request, resolved_values={"curie": "ECO:CHILD"}),
                                          "resolved_objects": []})
    from src.lib.domain_packs.input_selectors import build_domain_validation_request
    from src.lib.domain_packs.validator_dispatch import ValidatorDispatchJob
    pending = runtime.frozen_job_requests(item.plan.history_binding, [ValidatorDispatchJob(
        child_match, build_domain_validation_request(child_match).request, history_episode_id=item.ep)])
    assert pending[0]["requires_prerequisites"] and not pending[0]["prerequisites"]
    with pytest.raises(store.HistoryConflict, match="reviewed prerequisites"):
        store.begin_execution(item.scope, item.aid, operation_id="reject-unpinned-child", receipt={}, requests=pending)
    decisions.record_decisions(context, checkpoint_request=checkpoint(), operation_id="accept-absent-parent",
        entries=[absence_entry(item, target_id=parent)])
    assert (await item.adapter.advance(checkpoint))[0]["state"] == "completed"
    deferred = decisions.record_decisions(context, checkpoint_request=checkpoint(), operation_id="review-first-child",
        entries=entries_for(item, "defer", binding_id="fixture.dependent"))
    revoke = absence_entry(item, action="revoke", target_id=parent)
    revoke.pop("applicability_ref")
    decisions.record_decisions(context, checkpoint_request=checkpoint(), operation_id="reconsider-absent-parent", entries=[revoke])
    decisions.record_decisions(context, checkpoint_request=checkpoint(), operation_id="reaccept-absent-parent",
        entries=[absence_entry(item, target_id=parent)])
    item.adapter.prepare_inputs(checkpoint)
    wid = scheduling.reconsider(context, operation_id="retry-child", parent_decision_id=deferred["decision_id"],
        target_ids=[child], instructions="Use the newly reviewed omission", evidence_refs=[],
        refresh_inputs=item.adapter.refresh_inputs, rebind_receipt=item.adapter.rebind_receipt)
    assert (await item.adapter.execute(wid))["state"] == "completed"
    decisions.record_decisions(context, checkpoint_request=checkpoint(), operation_id="accept-second-child",
        entries=entries_for(item, "accept", binding_id="fixture.dependent")[-1:])
    with store.SessionLocal.begin() as db:
        ep, _ = admission.lock_episode(db, item.scope, item.ep, require_open=False)
        assert counts(db, ep, [child])[child] == 2


@pytest.mark.parametrize("planned", [{"packet_bytes": 2400}], indirect=True)
def test_outstanding_pages_keep_multiple_answers_and_global_blockers(planned):
    from uuid import UUID
    from src.lib.validation_review import reads
    item = planned
    request = item.adapter.plan["initial_requests"][0]
    expected = set()
    for ordinal in range(2):
        invocation = store.begin_execution(item.scope, item.aid, operation_id=f"original-answer-{ordinal}",
            receipt={}, requests=[request])
        expected.add(store.complete_attempt(item.scope, item.aid, invocation["attempt_ids"][0],
            state="completed", result={"answer": ordinal}, diagnostics={}))
    cursor, seen, pages = None, set(), 0
    while True:
        page = reads.outstanding(item.scope, item.ep, cursor=cursor)
        pages += 1
        assert not page["ready"] and reads._wire_size(page) <= 2400
        for row in page["rows"]:
            if row["code"] == "answer_review_pending":
                assert row["decision_unit_id"] == request["target_id"]
                seen.add(row["completion_ref"]["fact_id"])
        cursor = page["next_cursor"]
        if cursor is None:
            assert page["scan_complete"] and page["remaining_rows"] == 0
            break
    assert pages > 1 and seen == expected
    with store.SessionLocal.begin() as db:
        assert store.retire_document_history(db, UUID(item.scope.document_id)) == 1
    retired = reads.outstanding(item.scope, item.ep)
    assert not retired["ready"]
    assert retired["global_blockers"]["document_retired"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("planned", ["unmatched-prerequisite", "unmatched-prerequisite-absence"], indirect=True)
async def test_unmatched_prerequisite_orders_no_initial_work_or_absence_review(planned, monkeypatch):
    item = planned
    assert len(item.plan.matches) == 1
    assert item.plan.matches[0].binding.runs_after
    checkpoint = checkpoint_provider(item)
    context = decisions.ReviewContext(item.scope, item.ep, "supervisor")
    if item.plan.initial_jobs:
        assert len(item.plan.initial_jobs) == 1 and item.plan.initial_jobs[0].history_wave == 0
        assert not item.adapter.plan["initial_requests"][0]["requires_prerequisites"]
        monkeypatch.setattr("src.lib.validation_review.validator_adapter._default_package_scoped_validator_runner",
            lambda: lambda request, **kwargs: {**_result_payload(request, resolved_values={"curie": "ECO:VALUE"}),
                                              "resolved_objects": []})
        await item.adapter.initial()
        entries = entries_for(item, "accept")
    else:
        entries = [absence_entry(item)]
    result = decisions.record_decisions(context, checkpoint_request=checkpoint(), operation_id="review-first-wave", entries=entries)
    assert result["ready"]


@pytest.mark.asyncio
@pytest.mark.parametrize("planned", [
    {"shape": "unmatched-prerequisite", "count": 2, "mixed_prerequisites": True},
    {"shape": "unmatched-prerequisite-absence", "count": 2, "mixed_prerequisites": True},
], indirect=True)
async def test_unmatched_prerequisite_on_one_record_does_not_wait_for_another(planned, monkeypatch):
    item = planned
    from src.lib.validation_history import selections
    monkeypatch.setattr("src.lib.validation_review.validator_adapter._default_package_scoped_validator_runner",
        lambda: lambda request, **kwargs: {**_result_payload(request, resolved_values={"curie": "ECO:VALUE"}),
                                          "resolved_objects": []})
    await item.adapter.initial()
    # Deliberately leave the first record's parent unreviewed.
    outcomes = await item.adapter.advance(checkpoint_provider(item))
    independent = next(result for result in outcomes if result["state"] != "awaiting_review"
                       or result.get("reason") == "applicability_review_required")
    target = independent["target"]
    if independent.get("reason") == "applicability_review_required":
        entries = [absence_entry(item, target_id=target)]
    else:
        assert independent["state"] == "completed"
        entries = [entry for entry in entries_for(item, "accept") if entry["target_id"] == target]
    assert len(entries) == 1
    decisions.record_decisions(decisions.ReviewContext(item.scope, item.ep, "supervisor"),
        checkpoint_request=checkpoint_provider(item)(), operation_id="review-independent", entries=entries)
    manifest = selections.read_manifest(item.scope, item.aid, episode_id=item.ep)
    assert target not in {blocker["target_id"] for blocker in manifest["blockers"]}
    assert not manifest["ready"]  # The other record still needs prerequisite review.


@pytest.mark.asyncio
@pytest.mark.parametrize("planned", ["unsupported-preflight"], indirect=True)
async def test_unsupported_lookup_is_reviewable_unresolved_without_execution(planned):
    from src.lib.validation_review import source_inputs
    from src.lib.validation_history import selections
    item = planned
    await item.adapter.initial()
    await item.adapter.initial()
    with store.SessionLocal() as db:
        facts = list(db.scalars(select(ValidationFact).where(
            ValidationFact.artifact_id == item.aid,
            ValidationFact.kind == "validation_source_input")))
        assert len(facts) == len(item.plan.initial_jobs)
        for fact in facts:
            body = store._content(db, fact)
            assert source_inputs.is_applicability(body)
            assert "absent_inputs" not in body["input"]
            assert "FIXTURE_UNSUPPORTED" in body["input"]["unsupported_preflight"]["explanation"]
        assert not list(db.scalars(select(ValidationFact).where(
            ValidationFact.artifact_id == item.aid, ValidationFact.kind.in_(["attempt", "completion"]))))
    checkpoint = checkpoint_provider(item)
    context = decisions.ReviewContext(item.scope, item.ep, "supervisor")
    units = selections.read_manifest(item.scope, item.aid, episode_id=item.ep)["units"]
    response = None
    for unit in units:
        invalid = absence_entry(item, assessment="omission_supported", target_id=unit["target_id"])
        with pytest.raises(store.HistoryConflict, match="unresolved assessment"):
            decisions.record_decisions(context, checkpoint_request=checkpoint(),
                operation_id=uuid4().hex, entries=[invalid])
        entry = absence_entry(item, assessment="unresolved", target_id=unit["target_id"])
        invalid_hash = {**entry, "applicability_ref": {**entry["applicability_ref"], "sha256": "wrong"}}
        with pytest.raises(store.HistoryConflict):
            decisions.record_decisions(context, checkpoint_request=checkpoint(),
                operation_id=uuid4().hex, entries=[invalid_hash])
        response = decisions.record_decisions(context, checkpoint_request=checkpoint(),
            operation_id=uuid4().hex, entries=[entry])
    assert response is not None and response["ready"]
    assert any(finding.code == "validation_review.assessment_unresolved" and finding.field_ref is not None
               for finding in checkpoint().envelope.validation_findings)
    assert all(unit["eligible"] for unit in selections.read_manifest(item.scope, item.aid, episode_id=item.ep)["units"])


@pytest.mark.asyncio
@pytest.mark.parametrize("planned", ["unsupported-dependent"], indirect=True)
async def test_dependent_unsupported_lookup_never_calls_provider(planned, monkeypatch):
    item = planned
    calls = []
    def run(request, binding, **kwargs):
        assert binding.binding_id != "fixture.dependent"
        calls.append(request.request_id)
        return {**_result_payload(request, resolved_values={"curie": "ECO:SELECTED"}), "resolved_objects": []}
    monkeypatch.setattr("src.lib.validation_review.validator_adapter._default_package_scoped_validator_runner", lambda: run)
    checkpoint = checkpoint_provider(item)
    await item.adapter.initial()
    assert len(calls) == 2
    context = decisions.ReviewContext(item.scope, item.ep, "supervisor")
    decisions.record_decisions(context, checkpoint_request=checkpoint(), operation_id="parent-review",
        entries=entries_for(item, "accept", binding_id="fixture.evidence_lookup"))
    after = await item.adapter.advance(checkpoint)
    assert after and all(outcome["state"] == "awaiting_review" for outcome in after)
    assert all(outcome["reason"] == "applicability_review_required" for outcome in after)
    response = None
    for outcome in after:
        entry = absence_entry(item, assessment="unresolved", target_id=outcome["target"])
        response = decisions.record_decisions(context, checkpoint_request=checkpoint(),
            operation_id=uuid4().hex, entries=[entry])
    assert response is not None and response["ready"]
    assert len(calls) == 2

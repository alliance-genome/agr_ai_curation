"""Database-enforced flow replay fences and honest review completion."""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import delete, update
from sqlalchemy.exc import DBAPIError

from src.lib.validation_review import flow_steps, admission
from src.lib.validation_history import store
from src.models.sql.validation_review import FlowReviewStep
from tests.integration.persistence.test_chat_review_adapter import planned as planned, scope as scope, migrated as migrated


def test_concurrent_claim_never_resubmits_and_contract_is_immutable(scope):
    args = dict(run_id=uuid4().hex, node_id="extract", contract={"configured_agent": "saved", "query": "extract"})
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: flow_steps.claim(scope, **args), range(2)))
    assert sum(item["claimed"] for item in claims) == 1
    assert claims[0]["step_id"] == claims[1]["step_id"]
    with pytest.raises(store.HistoryConflict, match="configuration changed"):
        flow_steps.claim(scope, **{**args, "contract": {"configured_agent": "other"}})
    flow_steps.stop(scope, claims[0]["step_id"], interrupted=True, reason="cancelled")
    repeated = flow_steps.claim(scope, **args)
    assert not repeated["claimed"] and repeated["state"] == "interrupted"
    with pytest.raises(DBAPIError, match="cannot be replayed"):
        with store.SessionLocal.begin() as db:
            db.execute(update(FlowReviewStep).where(FlowReviewStep.id == repeated["step_id"]).values(state="started"))
    with pytest.raises(DBAPIError, match="retained"):
        with store.SessionLocal.begin() as db:
            db.execute(delete(FlowReviewStep).where(FlowReviewStep.id == repeated["step_id"]))


def test_saved_extraction_remains_pending_until_actual_decisions(planned):
    item = planned
    step = flow_steps.claim(item.scope, run_id=uuid4().hex, node_id="extract", contract={"extractor": "saved"})
    bound = flow_steps.bind(item.scope, step["step_id"], artifact_id=item.aid, episode_id=item.ep)
    assert bound["state"] == "review_pending"
    assert flow_steps.bind(item.scope, step["step_id"], artifact_id=item.aid, episode_id=item.ep) == bound
    with pytest.raises(admission.ReviewStopped, match="flow_review_pending"):
        flow_steps.finish(item.scope, step["step_id"], outcome={"message": "All good"})
    stopped = flow_steps.stop(item.scope, step["step_id"], reason="review_pending")
    assert stopped["artifact_id"] == item.aid and stopped["episode_id"] == item.ep
    with pytest.raises(store.HistoryConflict, match="cannot complete"):
        flow_steps.finish(item.scope, step["step_id"], outcome={})


@pytest.mark.asyncio
async def test_flow_capture_has_one_validator_pipeline_then_real_review_gate(planned, monkeypatch):
    from datetime import datetime, timezone
    from agents import Agent
    from src.lib.validation_review.flow import FlowReview
    from src.lib.validation_review import decisions
    from src.lib.validation_history import runtime
    from src.schemas.domain_envelope import DomainEnvelope, CuratableObjectEnvelope
    from src.lib.domain_packs.validator_dispatch import ValidatorRuntimeContext
    from src.lib.observability.cost_context import attach_agent_cost_identity
    from tests.unit.lib.domain_packs.test_validator_dispatch import _result_payload
    from tests.integration.persistence.test_chat_review_adapter import entries_for
    from types import SimpleNamespace

    item = planned
    run_id = uuid4().hex
    supervisor = Agent(name="Configured Flow Supervisor", model="test", instructions="Policy")
    attach_agent_cost_identity(supervisor, {"agent_id": "supervisor", "agent_role": "supervisor"})
    step = flow_steps.claim(item.scope, run_id=run_id, node_id="extract", contract={"extractor": "saved"})
    groups = [{"state": "automatic", "binding_id": match.binding.binding_id} for match in item.plan.matches[:1]]
    parent_scope = admission.create_scope(item.scope, operation_id="batch:" + run_id,
        kind="discovered_batch", targets=0, episodes=0)
    flow = FlowReview(owner_id=item.scope.owner_id, document_id=item.scope.document_id,
        session_id=item.scope.session_id, turn_id=run_id, supervisor=supervisor,
        authenticated_groups=(), user_request="Extract evidence", flow_run_id=run_id,
        step_id=step["step_id"], node_id="extract", validation_groups=groups, parent_scope_id=parent_scope,
        execution_context={"captured_at": datetime.now(timezone.utc).isoformat(), "source_kind": "flow",
            "flow_id": "fixture", "step_id": "extract", "agent_key": "fixture", "executed_query": "Extract evidence", "document": None})
    source_scope = store.HistoryScope(item.scope.owner_id, item.scope.document_id, uuid4().hex)
    env = DomainEnvelope(envelope_id=uuid4().hex, domain_pack_id=item.pack.pack_id,
        extracted_objects=[CuratableObjectEnvelope(object_type="Annotation", pending_ref_id="one",
                                                   payload={"evidence_code_curies": ["eco:1", "eco:2"]})])
    env = runtime.capture_extraction(scope=source_scope, producer_id=uuid4().hex,
        envelope=env, typed=env.model_dump(mode="json"), provenance={})
    calls = []
    def run(request, binding, **kwargs):
        calls.append(request.request_id)
        return {**_result_payload(request, resolved_values={"curie": "ECO:chosen"}), "resolved_objects": []}
    monkeypatch.setattr("src.lib.validation_review.validator_adapter._default_package_scoped_validator_runner", lambda: run)
    monkeypatch.setattr("src.lib.validation_review.validator_adapter._default_package_scoped_validator_batch_runner",
                        lambda: lambda *args, **kwargs: pytest.fail("Unexpected batch"))
    # The fixture domain pack has no production adapter; preserve its exact source
    # when constructing the checkpoint, using the same persisted-row loader.
    from tests.integration.persistence.test_chat_review_adapter import checkpoint_provider
    await flow.receive(env, item.pack, agent_key="fixture", adapter_key="fixture", tool_name="extract",
        execution_receipt=None, context=ValidatorRuntimeContext(user_id=item.scope.owner_id,
            document_id=item.scope.document_id, authenticated_groups=()))
    assert len(calls) == 2
    from src.models.sql.validation_review import ValidationReviewScope
    with store.SessionLocal() as db:
        parent = db.get(ValidationReviewScope, parent_scope)
        credited = admission._allowances(db, parent)
        assert credited["initial_tokens"] == 2 * parent.contract["policy"]["tokens_per_target"]
        assert credited["extra_attempts"] == 64
    ref = flow.reference(flow.record)
    assert flow.record.source_kind.value == "flow"
    envelope = DomainEnvelope.model_validate(flow.record.payload_json)
    fixture = SimpleNamespace(scope=source_scope, aid=ref["artifact_id"], ep=ref["episode_id"],
        plan=SimpleNamespace(envelope=envelope))
    checkpoint = checkpoint_provider(fixture)
    monkeypatch.setattr(flow, "checkpoint", lambda record: checkpoint())
    async def review(record):
        decisions.record_decisions(flow.context(record), checkpoint_request=checkpoint(), operation_id="accept-flow",
            entries=entries_for(fixture, "accept"))
        return {"state": "completed", "output": "Reviewed saved answers"}
    monkeypatch.setattr(flow, "review_result", review)
    result = await flow.finish_review()
    assert result.extracted_objects[0].payload["evidence_code_curies"] == ["ECO:chosen", "ECO:chosen"]
    assert len(calls) == 2
    with store.SessionLocal() as db:
        row = db.get(FlowReviewStep, step["step_id"])
        assert row.state == "completed" and row.artifact_id == ref["artifact_id"]


def test_discovered_batch_credits_are_shared_and_never_refill(planned):
    from src.models.sql.validation_review import ValidationReviewScope
    item = planned
    parent = admission.create_scope(item.scope, operation_id=uuid4().hex,
        kind="discovered_batch", targets=0, episodes=0)
    child = admission.create_scope(item.scope, operation_id=uuid4().hex,
        kind="discovered_document_run", targets=0, episodes=0, parent_id=parent)
    # The fixture is already admitted elsewhere. A separate episode/immutable
    # source is required; attempting to move its existing coverage must fail.
    with pytest.raises(store.HistoryConflict, match="scope identity conflict"):
        admission.attach_episode(item.scope, artifact_id=item.aid, episode_id=item.ep, scope_id=child)
    with store.SessionLocal() as db:
        parent_row = db.get(ValidationReviewScope, parent)
        child_row = db.get(ValidationReviewScope, child)
        assert admission._allowances(db, parent_row)["extra_attempts"] == 64
        assert admission._allowances(db, child_row)["extra_attempts"] == 16
        assert admission._allowances(db, parent_row)["initial_tokens"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('accept', [True, False, 'commit_failure', 'budget_exhausted', 'extractor_turns', 'extractor_timeout'])
async def test_ordered_flow_wrapper_gates_next_step_and_never_reextracts(planned, monkeypatch, accept):
    """Real wrapper, source persistence, validators and decisions; only providers are fake."""
    import json
    from types import SimpleNamespace
    from agents import Agent, function_tool
    from src.lib.flows import executor
    from src.lib.validation_review.flow import FlowReview
    from src.lib.validation_review.chat import ACTIVE_CHAT_REVIEW
    from src.lib.validation_review import decisions
    from src.lib.validation_history import runtime
    from src.schemas.domain_envelope import DomainEnvelope, CuratableObjectEnvelope
    from src.lib.domain_packs.validator_dispatch import ValidatorRuntimeContext
    from src.lib.observability.cost_context import attach_agent_cost_identity
    from tests.unit.lib.domain_packs.test_validator_dispatch import _result_payload
    from tests.integration.persistence.test_chat_review_adapter import checkpoint_provider, entries_for
    from tests.unit.lib.flows.test_executor import _make_flow, _agent_node, _metadata_from_registry

    item = planned
    run_id = uuid4().hex
    source_scope = store.HistoryScope(item.scope.owner_id, item.scope.document_id, uuid4().hex)
    supervisor = Agent(name='Configured supervisor', model='test', instructions='Review')
    attach_agent_cost_identity(supervisor, {'agent_id': 'supervisor', 'agent_role': 'supervisor'})
    env = DomainEnvelope(envelope_id=uuid4().hex, domain_pack_id=item.pack.pack_id,
        extracted_objects=[CuratableObjectEnvelope(object_type='Annotation', pending_ref_id='one',
            payload={'evidence_code_curies': ['eco:1', 'eco:2']})])
    env = runtime.capture_extraction(scope=source_scope, producer_id=uuid4().hex,
        envelope=env, typed=env.model_dump(mode='json'), provenance={})
    calls = []
    if accept == 'budget_exhausted':
        async def stop_initial(self):
            raise admission.ReviewStopped('budget_exhausted', resource='reviewer_tokens')
        monkeypatch.setattr('src.lib.validation_review.validator_adapter.ValidatorAdapter.initial', stop_initial)
        monkeypatch.setattr('src.lib.curation_workspace.domain_envelope_normalization.domain_envelope_from_extraction_result',
                            lambda *a, **kw: env)
        monkeypatch.setattr(runtime, 'capture_extraction', lambda **kw: env)
    def validate(request, binding, **kwargs):
        calls.append('validate')
        return {**_result_payload(request, resolved_values={'curie': 'ECO:chosen'}), 'resolved_objects': []}
    monkeypatch.setattr('src.lib.validation_review.validator_adapter._default_package_scoped_validator_runner', lambda: validate)
    monkeypatch.setattr('src.lib.validation_review.validator_adapter._default_package_scoped_validator_batch_runner',
        lambda: lambda *a, **kw: pytest.fail('Unexpected batch dispatch'))
    monkeypatch.setattr(executor, 'get_agent_metadata', lambda key, **kw: _metadata_from_registry(key) if key == 'gene' else {'agent_id': key, 'display_name': 'Configured analysis', 'description': 'Analyze the reviewed result', 'requires_document': False, 'required_params': []})
    monkeypatch.setattr(executor, 'capture_source_document', lambda *_: None)
    monkeypatch.setattr(executor, 'get_agent_by_id', lambda key, **kw: Agent(name=key, instructions='Saved', model='test'))
    def fixture(review):
        ref = review.reference(review.record)
        return SimpleNamespace(scope=source_scope, aid=ref['artifact_id'], ep=ref['episode_id'],
            plan=SimpleNamespace(envelope=DomainEnvelope.model_validate(review.record.payload_json)))
    monkeypatch.setattr(FlowReview, 'checkpoint', lambda self, record: checkpoint_provider(fixture(self))())
    async def review(self, record):
        calls.append('review')
        if accept:
            from sqlalchemy import event
            from sqlalchemy.orm import Session
            def reject_commit(session):
                raise RuntimeError('fixture review checkpoint commit rejected')
            if accept == 'commit_failure':
                event.listen(Session, 'before_commit', reject_commit)
            try:
                decisions.record_decisions(self.context(record), checkpoint_request=self.checkpoint(record),
                    operation_id='accept', entries=entries_for(fixture(self), 'accept'))
            finally:
                if accept == 'commit_failure':
                    event.remove(Session, 'before_commit', reject_commit)
        return {'state': 'completed' if accept else 'pending', 'output': 'Reviewed'}
    monkeypatch.setattr(FlowReview, 'review_result', review)
    def streaming(agent, tool_name, tool_description, specialist_name, **kwargs):
        @function_tool(name_override=tool_name, failure_error_function=None)
        async def invoke(query: str) -> str:
            if tool_name == 'ask_gene_specialist':
                calls.append('extract')
                if accept == 'extractor_turns':
                    from agents import MaxTurnsExceeded
                    raise MaxTurnsExceeded('extractor turns')
                if accept == 'extractor_timeout':
                    raise TimeoutError('extractor timed out')
                active = ACTIVE_CHAT_REVIEW.get()
                assert active is not None
                if accept == 'budget_exhausted':
                    from src.lib.openai_agents.streaming_tools import _dispatch_domain_envelope_validators_for_chat
                    payload = await _dispatch_domain_envelope_validators_for_chat(env.model_dump_json(),
                        history_scope_id=source_scope.session_id, producer_id='streamed-source', expected_output_type=None,
                        specialist_name='gene', tool_name=tool_name, source_agent_key='gene_extractor',
                        adapter_key='gene', is_builder_envelope=True, source_agent=agent, source_max_turns=4,
                        runtime_context=ValidatorRuntimeContext(user_id=item.scope.owner_id,
                            document_id=item.scope.document_id, authenticated_groups=()))
                else:
                    payload = await active.receive(env, item.pack, agent_key='gene', adapter_key='fixture',
                    tool_name=tool_name, execution_receipt=None, context=ValidatorRuntimeContext(
                        user_id=item.scope.owner_id, document_id=item.scope.document_id, authenticated_groups=()),
                    source_agent=agent)
                from src.lib.openai_agents.streaming_tools import add_specialist_event
                add_specialist_event({'type': executor.INTERNAL_EXTRACTION_RESULT_EVENT_TYPE,
                    'details': {'toolName': tool_name, 'success': True},
                    'internal': {'tool_output': payload, 'output_length': len(payload)}})
                return 'Saved extraction; review its reference'
            calls.append('next')
            return 'Next configured step completed'
        return invoke
    monkeypatch.setattr(executor, '_create_streaming_tool', streaming)
    extract = _agent_node('extract', 'gene')
    extract['data']['validation_groups'] = [{'state': 'automatic', 'binding_id': item.plan.matches[0].binding.binding_id}]
    flow = _make_flow([extract, _agent_node('next', 'configured_analysis')])
    from src.lib.document_context import DocumentContext
    tools, _, _, state = executor.get_all_agent_tools(flow, include_unavailable=True,
        doc_context=DocumentContext(document_id=item.scope.document_id, user_id=item.scope.owner_id),
        document_id=item.scope.document_id, user_id=item.scope.owner_id, session_id=item.scope.session_id,
        flow_run_id=run_id, enable_result_inspection=False)
    state['supervisor'] = supervisor
    context = SimpleNamespace(tool_name='step', run_config=None)
    if accept in ('extractor_turns', 'extractor_timeout'):
        from agents import MaxTurnsExceeded
        with pytest.raises(MaxTurnsExceeded if accept == 'extractor_turns' else TimeoutError):
            await tools[0].on_invoke_tool(context, json.dumps({'query': 'extract'}))
        assert state['next_tool_index'] == 0 and not state['completed_steps']
        assert calls == ['extract']
        pending = state['pending_review']
        assert pending.record is None
        with store.SessionLocal() as db:
            assert db.get(FlowReviewStep, pending.step_id).state == 'failed'
    elif accept == 'budget_exhausted':
        with pytest.raises(admission.ReviewStopped, match='budget_exhausted') as stopped:
            await tools[0].on_invoke_tool(context, json.dumps({'query': 'extract'}))
        assert state['next_tool_index'] == 0 and not state['completed_steps']
        assert calls == ['extract']
        pending = state['pending_review']
        assert pending.record is not None
        with store.SessionLocal() as db:
            assert db.get(FlowReviewStep, pending.step_id).state == 'review_pending'
        from src.lib.validation_review.flow_outcome import status_for_reason
        assert status_for_reason(stopped.value.reason) == 'review_exhausted'
    elif accept == 'commit_failure':
        with pytest.raises(RuntimeError, match='fixture review checkpoint commit rejected'):
            await tools[0].on_invoke_tool(context, json.dumps({'query': 'extract'}))
        assert state['next_tool_index'] == 0 and not state['completed_steps']
        assert 'step order is strict' in (await tools[1].on_invoke_tool(context, json.dumps({'query': 'next'}))).lower()
        assert calls == ['extract', 'validate', 'validate', 'review']
        from sqlalchemy import select
        from src.lib.curation_workspace.models import CurationExtractionResultRecord, DomainEnvelopeModel
        with store.SessionLocal() as db:
            row = db.scalar(select(CurationExtractionResultRecord).where(CurationExtractionResultRecord.flow_run_id == run_id))
            assert row.payload_json['extracted_objects'][0]['payload']['evidence_code_curies'] == ['eco:1', 'eco:2']
            assert 'inspection_checkpoint' not in row.extraction_metadata
            checkpoint_row = db.get(DomainEnvelopeModel, env.envelope_id)
            assert checkpoint_row is None or checkpoint_row.revision == 1
            saved_step = db.scalar(select(FlowReviewStep).where(FlowReviewStep.run_id == run_id))
            assert saved_step.state != 'completed'
    elif accept:
        await tools[0].on_invoke_tool(context, json.dumps({'query': 'extract'}))
        assert state['next_tool_index'] == 1
        await tools[1].on_invoke_tool(context, json.dumps({'query': 'next'}))
        assert calls == ['extract', 'validate', 'validate', 'review', 'next']
        assert state['completed_steps'][0]['validated_candidate'].payload_json['extracted_objects'][0]['payload']['evidence_code_curies'] == ['ECO:chosen', 'ECO:chosen']
    else:
        with pytest.raises(admission.ReviewStopped, match='flow_review_pending'):
            await tools[0].on_invoke_tool(context, json.dumps({'query': 'extract'}))
        assert state['next_tool_index'] == 0 and not state['completed_steps']
        assert 'step order is strict' in (await tools[1].on_invoke_tool(context, json.dumps({'query': 'next'}))).lower()
        with pytest.raises(admission.ReviewStopped, match='flow_step_already_submitted'):
            await tools[0].on_invoke_tool(context, json.dumps({'query': 'extract'}))
        assert calls == ['extract', 'validate', 'validate', 'review']
        pending = state['pending_review']
        with store.SessionLocal() as db:
            saved = db.get(FlowReviewStep, pending.step_id)
            assert saved.state == 'review_pending' and saved.artifact_id

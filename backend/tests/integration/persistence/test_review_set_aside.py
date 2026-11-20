"""ALL-1561: a paper finishes when its review sets a record aside.

Real flow capture, validators (fake answers), decisions, continuation view,
flow step fence, publication and export finalization. A record the reviewer
deferred or did not accept no longer stops the run; a check that could not run
still does, and readiness relaxes nowhere except a finished run's exports.
"""
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import csv
import io
import json

import pytest
from sqlalchemy import select

from src.lib.validation_history import store
from src.lib.validation_review import admission, flow_steps
from src.models.sql.validation_review import FlowReviewStep
from tests.integration.persistence.test_chat_review_adapter import (
    entries_for, migrated as migrated, planned as planned, scope as scope,
)
from tests.sdk_tool_context_support import tool_context

STATUS = "object.review_status"
REASON = "object.review_reason"
ASIDE_REASON = "The figure shows two codes and the text names neither."
ACCEPTED_REASON = "The legend names this code."


async def _finished(item, monkeypatch, action, *, outcome=None, aside=("aside",),
                    codes=(("kept", "eco:1"), ("aside", "eco:2")), accepted_first=False):
    """Run one configured flow step whose review accepts the other records and decides ``aside`` with ``action``.

    The review outcome is the real continuation view of the saved decisions
    (as reviewer.run returns it), unless ``outcome`` replaces it. With
    ``accepted_first`` the reviewer accepts every answer, then decides the
    ``aside`` answers with ``action``, so their accepted value still stands.
    """
    from agents import Agent
    from src.lib.validation_review.flow import FlowReview
    from src.lib.validation_review import continuation, decisions
    from src.lib.validation_history import runtime
    from src.schemas.domain_envelope import DomainEnvelope, CuratableObjectEnvelope
    from src.lib.domain_packs.validator_dispatch import ValidatorRuntimeContext
    from src.lib.observability.cost_context import attach_agent_cost_identity
    from tests.unit.lib.domain_packs.test_validator_dispatch import _result_payload

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
        configured_nodes={},
        step_provenance={"flow_id": "fixture", "flow_name": "Fixture flow", "step": 1,
                         "agent_name": "Fixture extractor", "tool_name": "extract"},
        execution_context={"captured_at": datetime.now(timezone.utc).isoformat(), "source_kind": "flow",
            "flow_id": "fixture", "step_id": "extract", "agent_key": "fixture",
            "executed_query": "Extract evidence", "document": None})
    source_scope = store.HistoryScope(item.scope.owner_id, item.scope.document_id, uuid4().hex)
    env = DomainEnvelope(envelope_id=uuid4().hex, domain_pack_id=item.pack.pack_id, extracted_objects=[
        CuratableObjectEnvelope(object_type="Annotation", pending_ref_id=key, payload={"evidence_code_curies": [code]})
        for key, code in codes])
    env = runtime.capture_extraction(scope=source_scope, producer_id=uuid4().hex,
        envelope=env, typed=env.model_dump(mode="json"), provenance={})
    monkeypatch.setattr("src.lib.validation_review.validator_adapter._default_package_scoped_validator_runner",
        lambda: lambda request, binding, **kwargs: {
            **_result_payload(request, resolved_values={"curie": request.selected_inputs["curie"].upper()}),
            "resolved_objects": []})
    monkeypatch.setattr("src.lib.validation_review.validator_adapter._default_package_scoped_validator_batch_runner",
                        lambda: lambda *args, **kwargs: pytest.fail("Unexpected batch"))
    await flow.receive(env, item.pack, agent_key="fixture", adapter_key="fixture", tool_name="specialist-label",
        source_metadata={}, execution_receipt=None, context=ValidatorRuntimeContext(
            user_id=item.scope.owner_id, document_id=item.scope.document_id, authenticated_groups=()))
    ref = flow.reference(flow.record)
    records = {obj.pending_ref_id: obj.metadata["validation_history"]["target_id"]
               for obj in DomainEnvelope.model_validate(flow.record.payload_json).extracted_objects}
    fixture = SimpleNamespace(aid=ref["artifact_id"])

    async def review(record):
        context = flow.context(record)

        def entries(aside_action):
            found = entries_for(fixture, "accept")
            with store.SessionLocal() as db:
                for entry in found:
                    target = store._content(db, store._fact(db, ref["artifact_id"], entry["target_id"], kind="target"))
                    entry["aside"] = target["object_target"] in {records[key] for key in aside}
            for entry in found:
                if entry.pop("aside"):
                    entry.update(action=aside_action, reason=ASIDE_REASON if aside_action != "accept"
                                 else ACCEPTED_REASON)
                else:
                    entry["reason"] = "Table 2 gives this evidence code."
            return found

        if accepted_first:
            # Accept the set-aside answers, decide them again with ``action`` while
            # the round is still open, then accept the rest.
            accepted = [entry for entry in entries("accept") if entry["reason"] == ACCEPTED_REASON]
            decisions.record_decisions(context, checkpoint_request=flow.checkpoint(record),
                                       operation_id="accept-first", entries=accepted)
            later = [entry for entry in entries(action) if entry["action"] == action]
            decisions.record_decisions(context, checkpoint_request=flow.checkpoint(record),
                                       operation_id="decide-later", entries=later)
            rest = [entry for entry in entries(action) if entry["action"] == "accept"]
            decisions.record_decisions(context, checkpoint_request=flow.checkpoint(record),
                                       operation_id="accept-rest", entries=rest)
        else:
            decisions.record_decisions(context, checkpoint_request=flow.checkpoint(record),
                                       operation_id="decide", entries=entries(action))
        if outcome is not None:
            return outcome
        view = continuation.inspect(context, {"record_validation_review"})
        return continuation.terminal(view, [], "Reviewed saved answers")

    monkeypatch.setattr(flow, "review_result", review)
    return flow, step, records


def _step_row(step):
    with store.SessionLocal() as db:
        return db.get(FlowReviewStep, step["step_id"])


def _bundle(flow, item, monkeypatch):
    from src.lib.flows.output_projection import build_flow_output_artifact_bundle
    monkeypatch.setattr("src.lib.flows.export_fields.packaged_domain_pack", lambda *args, **kwargs: item.pack)
    envelope = flow.checkpoint(flow.record).envelope
    return build_flow_output_artifact_bundle(
        user_id=item.scope.owner_id, document_id=item.scope.document_id,
        completed_steps=[{"step": 1, "node_id": "extract", "agent_id": "fixture", "agent_name": "Fixture",
            "validated_candidate": SimpleNamespace(payload_json=envelope.model_dump(mode="json"), agent_key="fixture",
                adapter_key="fixture", candidate_count=2, conversation_summary="x")}],
        flow_name="Set aside", output_format="tsv")


def _plan(output_format, refs):
    from src.lib.flows.output_projection import FlowOutputProjectionPlan
    return FlowOutputProjectionPlan.model_validate({"format": output_format, "row_source": "object",
        "missing_value": "", "columns": [{"key": key, "field_ref": ref} for key, ref in refs]})


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["defer", "reject"])
async def test_a_record_the_review_deferred_or_did_not_accept_no_longer_stops_the_paper(planned, monkeypatch, action):
    from src.lib.validation_history import selections
    from src.models.sql.validation_history import ValidationFact

    item = planned
    flow, step, records = await _finished(item, monkeypatch, action)
    envelope = await flow.finish_review()

    row = _step_row(step)
    assert row.state == "completed"
    assert row.outcome["set_aside_record_target_ids"] == [records["aside"]]
    assert flow.set_aside_record_target_ids == [records["aside"]]
    ref = flow.reference(flow.record)
    with store.SessionLocal() as db:
        facts = list(db.scalars(select(ValidationFact).where(
            ValidationFact.artifact_id == ref["artifact_id"], ValidationFact.kind == selections.SET_ASIDE_KIND)))
        assert len(facts) == 1 and facts[0].parent_id == ref["episode_id"]
        body = store._content(db, facts[0])
        assert body["record_target_ids"] == [records["aside"]]
    # Nothing was decided for the record: it is still pending and the
    # manifest still blocks it.
    status = admission.status(flow.context(flow.record).history_scope, ref["episode_id"])
    assert not status["ready"] and status["state"] == "pending"
    # The published projection keeps the set-aside record with its extracted value.
    assert {obj.pending_ref_id for obj in envelope.extracted_objects} == {"kept", "aside"}


@pytest.mark.asyncio
async def test_a_check_that_could_not_run_still_stops_the_paper(planned, monkeypatch):
    """Chris, decision 1: an outage, exhaustion or interruption is not a review decision."""
    item = planned
    stopped = {"state": "needs_curator_review", "reason": "configured_validator_unavailable",
               "resource": None, "review": {}, "causes": [], "pending": [], "output": None, "set_aside": None}
    flow, step, _ = await _finished(item, monkeypatch, "defer", outcome=stopped)
    with pytest.raises(admission.ReviewStopped, match="configured_validator_unavailable"):
        await flow.finish_review()
    assert _step_row(step).state == "review_pending"
    # Without a recorded set-aside fact the step fence refuses as before.
    with pytest.raises(admission.ReviewStopped, match="flow_review_pending"):
        flow_steps.finish(store.HistoryScope(item.scope.owner_id, item.scope.document_id, item.scope.session_id),
                          step["step_id"], outcome={})


@pytest.mark.asyncio
async def test_the_set_aside_fact_is_written_with_the_step_and_pins_the_state_it_covers(planned, monkeypatch):
    from src.lib.flows.output_projection import finalize_output_projection
    from src.lib.validation_history import selections
    from src.lib.validation_review import decisions
    from src.models.sql.validation_history import ValidationFact

    item = planned
    flow, step, records = await _finished(item, monkeypatch, "defer")
    outcome = await flow.review_result(flow.record)
    scope = store.HistoryScope(item.scope.owner_id, item.scope.document_id, item.scope.session_id)
    artifact_id = flow.reference(flow.record)["artifact_id"]

    def facts():
        with store.SessionLocal() as db:
            return list(db.scalars(select(ValidationFact).where(
                ValidationFact.artifact_id == artifact_id, ValidationFact.kind == selections.SET_ASIDE_KIND)))

    # A flag read from another state stops the step and writes no fact.
    stale = {**outcome["set_aside"], "manifest_sha256": "0" * 64}
    with pytest.raises(admission.ReviewStopped, match="flow_review_pending"):
        flow_steps.finish(scope, step["step_id"], outcome={"done": True}, set_aside=stale)
    assert facts() == [] and _step_row(step).state == "review_pending"
    flow_steps.finish(scope, step["step_id"], outcome={"done": True}, set_aside=outcome["set_aside"])
    assert _step_row(step).state == "completed" and len(facts()) == 1
    finalize_output_projection(_bundle(flow, item, monkeypatch), _plan("tsv", [("record", "object.pending_ref_id")]))

    # The reviewer deciding the set-aside unit again leaves the fact behind: strict again.
    context = flow.context(flow.record)
    with store.SessionLocal() as db:
        later = [{**entry, "action": "defer", "reason": "Still unclear after a second look."}
                 for entry in entries_for(SimpleNamespace(aid=artifact_id), "defer")
                 if store._content(db, store._fact(db, artifact_id, entry["target_id"], kind="target"))[
                     "object_target"] == records["aside"]]
    assert len(later) == 1
    decisions.record_decisions(context, checkpoint_request=flow.checkpoint(flow.record),
                               operation_id="defer-again-later", entries=later)
    with pytest.raises(store.SelectionNotReady):
        finalize_output_projection(_bundle(flow, item, monkeypatch),
                                   _plan("tsv", [("record", "object.pending_ref_id")]))


@pytest.mark.asyncio
async def test_a_curator_deciding_one_set_aside_record_keeps_the_other_set_aside(planned, monkeypatch):
    """S5: the viewer lists what is still set aside, and re-export works under the same rule."""
    from dataclasses import replace
    from src.lib.flows.output_projection import finalize_output_projection
    from src.lib.flows.set_aside_records import list_set_aside_records
    from src.lib.validation_review import decisions

    item = planned
    flow, _, records = await _finished(item, monkeypatch, "defer", aside=("aside", "later"),
                                       codes=(("kept", "eco:1"), ("aside", "eco:2"), ("later", "eco:3")))
    await flow.finish_review()
    artifact_id = flow.reference(flow.record)["artifact_id"]
    with store.SessionLocal() as db:
        decided = [{**entry, "action": "accept", "reason": "Figure 3 shows this code."}
                   for entry in entries_for(SimpleNamespace(aid=artifact_id), "accept")
                   if store._content(db, store._fact(db, artifact_id, entry["target_id"], kind="target"))[
                       "object_target"] == records["later"]]
    assert len(decided) == 1
    curator = replace(flow.context(flow.record), actor_id=item.scope.owner_id, actor_type="curator")
    decisions.record_decisions(curator, checkpoint_request=flow.checkpoint(flow.record),
                               operation_id="curator-accepts-later", entries=decided)

    listed = list_set_aside_records(user_id=item.scope.owner_id, flow_run_id=flow.flow_run_id,
                                    document_id=item.scope.document_id)
    assert listed.set_aside_count == 1
    assert [record.review_reason for record in listed.results[0].records] == [
        f"evidence_code_curies[0]: {ASIDE_REASON}"]
    bundle = _bundle(flow, item, monkeypatch)
    plain = finalize_output_projection(bundle, _plan("tsv", [("record", "object.pending_ref_id")]))
    assert plain.rows == [{"record": "kept"}, {"record": "later"}] and plain.set_aside_left_out == 1
    marked = finalize_output_projection(bundle, _plan("tsv", [("record", "object.pending_ref_id"),
                                                             ("status", STATUS)]))
    assert marked.rows == [{"record": "kept", "status": "accepted"}, {"record": "aside", "status": "set aside"},
                           {"record": "later", "status": "accepted"}]
    assert (marked.set_aside_included, marked.set_aside_left_out) == (1, 0)


@pytest.mark.asyncio
async def test_exports_leave_set_aside_records_out_unless_they_select_review_status(planned, monkeypatch):
    from src.lib.flows.output_projection import finalize_output_projection
    from src.lib.openai_agents.tools.file_output_tools import _projection_content_for_file_type
    from src.lib.validation_history.projection import latest_publication_reference, read_published_projection

    item = planned
    flow, _, records = await _finished(item, monkeypatch, "defer")
    await flow.finish_review()
    bundle = _bundle(flow, item, monkeypatch)
    [artifact] = bundle.artifacts
    assert STATUS in {field.ref for field in artifact.declared_fields}

    for output_format in ("csv", "tsv"):
        plain = finalize_output_projection(bundle, _plan(output_format, [("record", "object.pending_ref_id")]))
        content = _projection_content_for_file_type(output_format=output_format, projection=plain)
        assert list(csv.reader(io.StringIO(content), delimiter="\t" if output_format == "tsv" else ",")) == [
            ["record"], ["kept"]]
        assert (plain.set_aside_left_out, plain.set_aside_included) == (1, 0)
        marked = finalize_output_projection(bundle, _plan(output_format, [
            ("record", "object.pending_ref_id"), ("Review status", STATUS), ("Review reason", REASON)]))
        rows = list(csv.reader(io.StringIO(_projection_content_for_file_type(
            output_format=output_format, projection=marked)), delimiter="\t" if output_format == "tsv" else ","))
        assert rows[0] == ["record", "Review status", "Review reason"]
        assert rows[1] == ["kept", "accepted", "evidence_code_curies[0]: Table 2 gives this evidence code."]
        # Chris, decision 7: Review reason carries the reason that set the record aside.
        assert rows[2] == ["aside", "set aside", f"evidence_code_curies[0]: {ASIDE_REASON}"]
        assert (marked.set_aside_left_out, marked.set_aside_included) == (0, 1)
    plain_json = finalize_output_projection(bundle, _plan("json", [("record", "object.pending_ref_id")]))
    assert json.loads(_projection_content_for_file_type(output_format="json", projection=plain_json)) == [
        {"record": "kept"}]
    marked_json = finalize_output_projection(bundle, _plan("json", [("record", "object.pending_ref_id"),
                                                                    ("status", STATUS)]))
    assert json.loads(_projection_content_for_file_type(output_format="json", projection=marked_json)) == [
        {"record": "kept", "status": "accepted"}, {"record": "aside", "status": "set aside"}]
    chat = finalize_output_projection(bundle, _plan("chat", [("record", "object.pending_ref_id")]))
    assert "kept" in chat.chat_output and "aside" not in chat.chat_output

    # Only exports relax readiness: submission, curator restore and curation
    # preparation read the same publication strictly.
    scope = store.HistoryScope(item.scope.owner_id, item.scope.document_id,
                               flow.reference(flow.record)["history_scope_id"])
    reference = latest_publication_reference(scope, flow.reference(flow.record)["artifact_id"])
    with pytest.raises(store.SelectionNotReady):
        read_published_projection(scope, reference, finalized=True)
    read_published_projection(scope, reference, finalized=True, set_aside_ok=True)


@pytest.mark.asyncio
async def test_a_stopped_review_is_not_exported_even_with_review_status(planned, monkeypatch):
    from src.lib.flows.output_projection import finalize_output_projection

    item = planned
    stopped = {"state": "needs_curator_review", "reason": "configured_validator_unavailable",
               "resource": None, "review": {}, "causes": [], "pending": [], "output": None, "set_aside": None}
    flow, _, _ = await _finished(item, monkeypatch, "defer", outcome=stopped)
    with pytest.raises(admission.ReviewStopped):
        await flow.finish_review()
    bundle = _bundle(flow, item, monkeypatch)
    with pytest.raises(store.SelectionNotReady):
        finalize_output_projection(bundle, _plan("tsv", [("record", "object.pending_ref_id"), ("status", STATUS)]))


@pytest.mark.asyncio
async def test_the_viewer_lists_the_owners_set_aside_records_only(planned, monkeypatch):
    from src.lib.flows.set_aside_records import FlowRunResultsNotFound, list_set_aside_records

    item = planned
    flow, _, _ = await _finished(item, monkeypatch, "defer")
    await flow.finish_review()
    monkeypatch.setattr("src.lib.flows.export_fields.packaged_domain_pack", lambda *args, **kwargs: item.pack)
    listed = list_set_aside_records(user_id=item.scope.owner_id, flow_run_id=flow.flow_run_id,
                                    document_id=item.scope.document_id)
    assert listed.set_aside_count == 1
    [result] = listed.results
    assert result.extraction_result_id == str(flow.record.extraction_result_id)
    assert result.node_id == "extract" and result.review_fields_available
    [record] = result.records
    assert record.review_status == "set aside"
    assert record.review_reason == f"evidence_code_curies[0]: {ASIDE_REASON}"
    assert record.fields and all(field.field_ref != STATUS for field in record.fields)
    with pytest.raises(FlowRunResultsNotFound):
        list_set_aside_records(user_id="someone-else", flow_run_id=flow.flow_run_id,
                               document_id=item.scope.document_id)


@pytest.mark.asyncio
async def test_the_viewer_lists_nothing_for_a_run_that_stopped(planned, monkeypatch):
    """Forward-only: a stopped run has no set-aside fact; nothing is repaired or listed."""
    from src.lib.flows.set_aside_records import list_set_aside_records

    item = planned
    stopped = {"state": "needs_curator_review", "reason": "review_pending", "resource": None, "review": {},
               "causes": [], "pending": [], "output": None, "set_aside": None}
    flow, _, _ = await _finished(item, monkeypatch, "defer", outcome=stopped)
    with pytest.raises(admission.ReviewStopped):
        await flow.finish_review()
    listed = list_set_aside_records(user_id=item.scope.owner_id, flow_run_id=flow.flow_run_id,
                                    document_id=item.scope.document_id)
    assert listed.set_aside_count == 0 and listed.results == []


@pytest.mark.asyncio
async def test_review_and_curate_shows_set_aside_records_undecided_and_blocks_submission(planned, monkeypatch):
    """Chris, decision 3: the record is an undecided candidate; sending stays blocked until a curator decides."""
    from src.lib.curation_workspace.session_queries import get_session_workspace
    from src.lib.curation_workspace.session_submission_service import _build_domain_envelope_submission_context
    from src.lib.curation_workspace.review_history_service import history_page
    from uuid import UUID
    from src.lib.curation_workspace.models import CurationReviewSession
    from src.schemas.curation_workspace import CurationCandidateStatus

    item = planned
    definition = item.pack.metadata.object_definitions[0]
    definition.metadata.update({"object_role": "curatable_unit"})
    flow, _, _ = await _finished(item, monkeypatch, "defer")
    await flow.finish_review()
    session_id = await _bootstrap(flow, item, monkeypatch)
    with store.SessionLocal() as db:
        workspace = get_session_workspace(db, session_id, owner_sub=item.scope.owner_id)
        assert workspace.workspace.candidates
        assert all(candidate.status == CurationCandidateStatus.PENDING
                   for candidate in workspace.workspace.candidates)
        session_row = db.get(CurationReviewSession, UUID(str(session_id)))
        candidates = {str(candidate.id): candidate for candidate in session_row.candidates}
        context = _build_domain_envelope_submission_context(
            db=db, candidates=candidates, target_candidate_ids=list(candidates), user_id=item.scope.owner_id)
        for candidate_id in candidates:
            assert any(blocker.code == "domain_envelope.machine_review_pending"
                       for blocker in context.blockers_for(candidate_id))
        # The candidate's review history tells the set-aside record from the accepted one.
        reviews = {}
        for candidate in session_row.candidates:
            page = history_page(db, candidate.id, owner_id=item.scope.owner_id)
            reviews[candidate.object_id] = {unit["review"] for unit in page["record"]["selections"]}
        # The accepted record's checked value reads "accept"; the set-aside one has none.
        assert "accept" in reviews["kept"]
        assert "accept" not in reviews["aside"]


async def _bootstrap(flow, item, monkeypatch):
    """The public bootstrap of Review & Curate for this run, as test_flow_review_steps runs it."""
    from src.lib.curation_workspace import adapter_registry, curation_prep_service, pipeline
    from src.lib.curation_workspace.bootstrap_service import bootstrap_document_session
    from src.lib.domain_packs.materialization import DomainPackMetadataReviewRowMaterializer
    from src.schemas.curation_workspace import CurationDocumentBootstrapRequest

    registry = adapter_registry.load_curation_adapter_registry()
    materializer = DomainPackMetadataReviewRowMaterializer(item.pack.metadata)

    class FixtureRegistry:
        def get_review_row_materializer(self, key):
            return materializer if key == "fixture" else registry.get_review_row_materializer(key)

        def get_review_row_materializer_for_domain_pack(self, key):
            return materializer if key == item.pack.pack_id else registry.get_review_row_materializer_for_domain_pack(key)

        def __getattr__(self, key):
            return getattr(registry, key)

    monkeypatch.setattr(adapter_registry, "load_curation_adapter_registry", lambda: FixtureRegistry())
    monkeypatch.setattr(curation_prep_service, "load_curation_adapter_registry", lambda: FixtureRegistry())
    monkeypatch.setattr(pipeline, "resolve_curation_domain_pack_by_id", lambda _: item.pack)
    with store.SessionLocal() as db:
        created = await bootstrap_document_session(item.scope.document_id, CurationDocumentBootstrapRequest(
            adapter_key="fixture", flow_run_id=flow.flow_run_id, origin_session_id=flow.session_id),
            current_user_id=item.scope.owner_id, active_groups=(), db=db)
        return created.session.session_id


@pytest.mark.asyncio
async def test_a_set_aside_record_whose_accepted_value_stands_shows_both_reasons(planned, monkeypatch):
    """Chris (19 Nov): the accepted reason of the value shown, then the reason that set it aside."""
    from src.lib.flows.output_projection import finalize_output_projection
    from src.lib.flows.set_aside_records import list_set_aside_records

    item = planned
    flow, step, records = await _finished(item, monkeypatch, "reject", accepted_first=True)
    await flow.finish_review()
    assert _step_row(step).outcome["set_aside_record_target_ids"] == [records["aside"]]
    both = (f"evidence_code_curies[0]: Accepted earlier: {ACCEPTED_REASON} | "
            f"evidence_code_curies[0]: Set aside later: {ASIDE_REASON}")
    projection = finalize_output_projection(_bundle(flow, item, monkeypatch), _plan("csv", [
        ("record", "object.pending_ref_id"), ("status", STATUS), ("reason", REASON)]))
    assert projection.rows == [
        {"record": "kept", "status": "accepted", "reason": "evidence_code_curies[0]: Table 2 gives this evidence code."},
        {"record": "aside", "status": "set aside", "reason": both},
    ]
    listed = list_set_aside_records(user_id=item.scope.owner_id, flow_run_id=flow.flow_run_id,
                                    document_id=item.scope.document_id)
    assert [record.review_reason for record in listed.results[0].records] == [both]


THREE = (("kept", "eco:1"), ("aside", "eco:2"), ("later", "eco:3"))


@pytest.mark.asyncio
async def test_every_accepted_view_listing_marks_the_set_aside_records(planned, monkeypatch):
    """R2: the mark is part of the accepted view, so later manifest pages and inspect_results carry it."""
    import json
    from src.lib.context import _current_session_id, _current_user_id
    from src.lib.openai_agents.extraction_manifest import build_extraction_manifest_page
    from src.lib.openai_agents.result_inspection_tool import make_result_inspection_tool
    from src.lib.openai_agents.result_inspection_views import accepted_view

    item = planned
    definition = item.pack.metadata.object_definitions[0]
    definition.metadata.update({"object_role": "curatable_unit",
                                "supervisor_manifest": {"summary_fields": ["evidence_code_curies[0]"]}})
    definition.fields.append(definition.fields[0].model_copy(
        update={"field_path": "evidence_code_curies[0]", "metadata": {}}))
    flow, _, _ = await _finished(item, monkeypatch, "defer", aside=("aside", "later"), codes=THREE)
    await flow.finish_review()
    accepted = accepted_view(flow.record, user_id=item.scope.owner_id)

    # One object per page: the set-aside records sit on pages 2 and 3.
    marks = {}
    for cursor in ("0", "1", "2"):
        page = build_extraction_manifest_page(accepted["payload_json"], limit=1, cursor=cursor)
        [listed] = page["objects"]
        marks[listed["object_ref"]] = listed.get("review_status")
    assert marks == {"kept": None, "aside": "set aside", "later": "set aside"}

    result_ref = "extraction-result:" + str(flow.record.extraction_result_id)
    scope = {"user_id": item.scope.owner_id, "origin_session_id": flow.session_id,
             "document_id": item.scope.document_id, "flow_run_id": flow.flow_run_id,
             "metadata": {key: flow.record.metadata[key] for key in ("idempotency_key", "flow_step_key")},
             "post_validation_checkpoint": flow.record.metadata["inspection_checkpoint"]}
    inspector = make_result_inspection_tool(scope_provider=lambda: {result_ref: scope})
    session_token = _current_session_id.set(flow.session_id)
    user_token = _current_user_id.set(item.scope.owner_id)
    try:
        arguments = json.dumps({"action": "objects", "result_ref": result_ref, "result_view": "accepted"})
        response = json.loads(await inspector.on_invoke_tool(
            tool_context("inspect_results", arguments), arguments))
    finally:
        _current_user_id.reset(user_token)
        _current_session_id.reset(session_token)
    assert response["status"] == "ok", response
    assert {row["object_ref"]: row.get("review_status") for row in response["objects"]} == marks


@pytest.mark.asyncio
@pytest.mark.parametrize("curator_action", ["defer", "reject"])
async def test_a_curator_holding_a_set_aside_record_is_shown_not_dropped(planned, monkeypatch, curator_action):
    """R3: the result is returned as held by the curator, and re-export gives the specific reason.

    The curator decides through Review & Curate's own decide() wrapper.
    """
    from uuid import uuid4 as new_id
    from src.lib.curation_workspace.models import CurationReviewSession
    from src.lib.curation_workspace.review_history_service import decide
    from src.lib.flows.output_projection import finalize_output_projection
    from src.lib.flows.set_aside_records import list_set_aside_records
    from src.lib.validation_history.selections import CURATOR_HOLDS_SET_ASIDE
    from src.schemas.workspace_review import WorkspaceReviewDecisionRequest
    from uuid import UUID

    item = planned
    item.pack.metadata.object_definitions[0].metadata.update({"object_role": "curatable_unit"})
    flow, _, records = await _finished(item, monkeypatch, "defer", aside=("aside", "later"), codes=THREE)
    await flow.finish_review()
    session_id = await _bootstrap(flow, item, monkeypatch)
    artifact_id = flow.reference(flow.record)["artifact_id"]
    with store.SessionLocal() as db:
        candidate = next(candidate for candidate in db.get(CurationReviewSession, UUID(str(session_id))).candidates
                         if candidate.object_id == "later")
        entries = [{**entry, "action": curator_action, "reason": "I will check the supplement myself."}
                   for entry in entries_for(SimpleNamespace(aid=artifact_id), "accept")
                   if store._content(db, store._fact(db, artifact_id, entry["target_id"], kind="target"))[
                       "object_target"] == records["later"]]
    assert len(entries) == 1
    decide(candidate.id, owner_id=item.scope.owner_id, groups=(), request=WorkspaceReviewDecisionRequest(
        request_id=new_id(), episode_id=UUID(flow.reference(flow.record)["episode_id"]), entries=entries))

    listed = list_set_aside_records(user_id=item.scope.owner_id, flow_run_id=flow.flow_run_id,
                                    document_id=item.scope.document_id)
    [result] = listed.results
    assert result.held_by_curator and result.records == [] and listed.set_aside_count == 0
    with pytest.raises(store.SelectionNotReady, match="A curator is deciding records"):
        finalize_output_projection(_bundle(flow, item, monkeypatch),
                                   _plan("tsv", [("record", "object.pending_ref_id")]))
    assert CURATOR_HOLDS_SET_ASIDE.startswith("A curator is deciding records")

"""Source-changing saves after an accepted validated reference (v0.11.6, trace 1b937f7b).

In production, review accepted the crb validator answers, which created
validated-reference Allele objects with findings anchored on them. Every later
save that changed the source (reviewer retire, extractor-recommendation approval,
curator restore, follow-up contribution selection) then failed: the selection
source dropped the validator-contributed objects but kept their findings. These
tests reproduce that state with a pack whose validator returns resolved objects of
a validated-reference type, and pin the three fixes: the source leaves out rebuilt
findings (A), retiring or restoring a record another record refers to is refused
with a named reason (B), and an invalid source stops review as a system error (C).
"""
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from agents.tool_context import ToolContext
from pydantic import ValidationError
from sqlalchemy import select

from src.lib.domain_packs.validator_dispatch import ValidatorRuntimeContext, prepare_validator_dispatch
from src.lib.validation_history import projection, runtime, selections, store
from src.lib.validation_review import admission, extraction_results, extraction_scheduling, reads, work
from src.lib.validation_review.decisions import ReviewContext
from src.lib.validation_review.tools import create_tools
from src.lib.validation_review.validator_adapter import MATCH, ValidatorAdapter, save_plan
from src.models.sql.validation_history import ValidationFact
from src.schemas.domain_envelope import (
    CuratableObjectEnvelope, DomainEnvelope, FieldRef, ObjectRef, ValidationFinding,
)
from tests.cancellation_test_support import install_cancel_reader
from tests.integration.persistence.test_chat_review_adapter import checkpoint_provider
from tests.integration.persistence.test_review_packet import _answers, _handles
from tests.integration.persistence.test_validation_history import migrated as migrated, scope as scope
from tests.unit.lib.domain_packs.test_validator_dispatch import _multivalued_dispatch_pack, _result_payload


def _pack(tmp_path):
    """The multivalued fixture pack, plus a validated-reference type and an extraction-only link.

    The validated reference declares the answer's own field, so the answer's
    findings anchor on it as well as on the record, as the allele pack's anchor on
    validated Allele objects.
    """
    from src.schemas.domain_pack_metadata import DomainPackObjectDefinition

    pack = _multivalued_dispatch_pack(tmp_path)
    supporting = DomainPackObjectDefinition.model_validate({
        "object_type": "SupportingTerm", "display_name": "Supporting term",
        "metadata": {"object_role": "validated_reference"},
        "fields": [{"field_path": "primary_external_id", "field_type": "string"},
                   {"field_path": "name", "field_type": "string"},
                   {"field_path": "evidence_code_curies", "field_type": "string",
                    "metadata": {"multivalued": True}}],
    })
    # Extraction-only types: a link record, and production's quote, association and reference records.
    extraction_only = [DomainPackObjectDefinition.model_validate({
        "object_type": object_type, "display_name": object_type,
        "fields": [{"field_path": field, "field_type": "string"}],
    }) for object_type, field in (("Link", "note"), ("EvidenceQuote", "quote"), ("Association", "note"),
                                  ("Reference", "title"))]
    return replace(pack, metadata=pack.metadata.model_copy(update={
        "object_definitions": [*pack.metadata.object_definitions, supporting, *extraction_only]}))


def _answer(request):
    """A resolved answer that contributes a validated reference object."""
    curie = request.selected_inputs["curie"].upper()
    return {**_result_payload(request, resolved_values={"curie": curie}),
            "resolved_objects": [{"object_type": "SupportingTerm", "canonical_id": curie,
                                  "payload": {"primary_external_id": curie, "name": "term " + curie,
                                              "evidence_code_curies": [curie]}}]}


def _review(scope, tmp_path, monkeypatch, *, referrer=False):
    """Records A and B (validated), optionally a link record that refers to B, planned as chat plans them."""
    from src.lib.config.agent_loader import AgentDefinition

    install_cancel_reader(monkeypatch, AsyncMock(return_value=False))
    pack = _pack(tmp_path)
    objects = [CuratableObjectEnvelope(object_type="Annotation", pending_ref_id=name,
                                       payload={"evidence_code_curies": ["eco:" + name.lower()]})
               for name in ("A", "B")]
    if referrer:
        objects.append(CuratableObjectEnvelope(object_type="Link", pending_ref_id="link-B",
            payload={"note": "B is shown in Figure 2"},
            object_refs=[ObjectRef(pending_ref_id="B", object_type="Annotation")]))
    envelope = DomainEnvelope(envelope_id=uuid4().hex, domain_pack_id=pack.pack_id, extracted_objects=objects)
    envelope = runtime.capture_extraction(scope=scope, producer_id=uuid4().hex,
                                          typed=envelope.model_dump(mode="json"), envelope=envelope, provenance={})
    monkeypatch.setattr("src.lib.config.agent_loader.get_agent_definition_for_package",
        lambda package, agent: AgentDefinition(folder_name=agent, agent_id=agent, name=agent, package_id=package))
    monkeypatch.setattr("src.lib.curation_workspace.adapter_registry.resolve_curation_domain_pack_by_id",
                        lambda _: pack)
    context = ValidatorRuntimeContext(user_id=scope.owner_id, document_id=scope.document_id,
                                      authenticated_groups=())
    plan = prepare_validator_dispatch(envelope, pack, runtime_context=context)
    plan_ref = save_plan(plan, context=context)
    aid = plan.history_binding.artifact_id
    ep = plan.envelope.metadata["review_episode_id"]
    sid = admission.create_scope(scope, operation_id=uuid4().hex, kind="discovered_document_run",
                                 targets=0, episodes=0)
    admission.attach_episode(scope, artifact_id=aid, episode_id=ep, scope_id=sid)
    item = SimpleNamespace(plan=plan, adapter=ValidatorAdapter(scope, aid, ep, plan_ref, authenticated_groups=()),
                           aid=aid, ep=ep, scope=scope, pack=pack,
                           context=ReviewContext(scope, ep, "supervisor.validation_review"),
                           roots={obj.pending_ref_id: obj.metadata["validation_history"]["target_id"]
                                  for obj in plan.envelope.extracted_objects})
    item.checkpoint = checkpoint_provider(item)
    _answers(monkeypatch, _answer)
    return item


def _tool(item, name):
    """The reviewer's real bound tool, with source changes routed through the real adapter."""
    tools = create_tools(item.context, checkpoint_provider=item.checkpoint,
        request_extraction=AsyncMock(), assess_source=AsyncMock(),
        select_source=lambda **arguments: item.adapter.select_source(item.context, item.checkpoint, **arguments))
    return next(tool for tool in tools if tool.name == name)


async def _call(item, name, arguments):
    tool = _tool(item, name)
    encoded = json.dumps(arguments)
    return await tool.on_invoke_tool(ToolContext(context=None, tool_name=tool.name,
        tool_call_id=arguments["operation_id"], tool_arguments=encoded), encoded)


def _record(packet, target_id):
    return next(record for record in packet["records"] if record["target_id"] == target_id)


async def _decide(item, decided, operation_id):
    """One packet save: {record name: action}; retire uses any of the record's handles."""
    packet = reads.review_packet(item.scope, item.ep)
    decisions = [{"handle": handle, "action": action, "reason": f"Record {name}: what the paper shows.",
                  "absence_assessment": None, "unresolved_fields": None}
                 for name, action in decided.items()
                 for handle in _handles(_record(packet, item.roots[name]))]
    return await _call(item, "record_validation_review", {"operation_id": operation_id,
        "packet_id": packet["packet_id"], "packet_decisions": decisions, "entries": None})


def _digest(item):
    with store.SessionLocal() as db:
        return selections.source_state_digest(db, item.aid)


def _contribution(item, name, entry_result):
    """The extraction agent's targeted re-check of one record, run through real scheduling and retention."""
    entry = item.roots[name]
    obj = next(obj for obj in item.plan.envelope.extracted_objects if obj.pending_ref_id == name)
    match = next(match for match in item.plan.matches if match.object_envelope.pending_ref_id == name)
    binding = runtime.binding_target(item.plan.history_binding, match)
    operation = uuid4().hex
    with store.SessionLocal.begin() as db:
        parent = store._append(db, item.aid, operation_id="decision:" + operation, kind="decision",
            body={"actor": {"type": "agent", "id": item.context.actor_id},
                  "entries": [{"target_id": binding, "action": "defer", "reason": "Check this record in Methods"}]})
        store._append(db, item.aid, operation_id="episode-disposition:" + operation,
                      kind="episode_disposition", parent_id=item.ep, body={"selection_ids": []})
        parent_id = parent.id
    wid = extraction_scheduling.request(item.context, operation_id=uuid4().hex, parent_decision_id=parent_id,
        entry_ids=[entry], instructions="Check whether this record belongs to the question.",
        receipt={"agent_key": "saved", "execution_receipt": None,
                 "contract": {"instructions": "Keep records used in this study."}},
        current_entries={entry: obj.model_dump(mode="json")})
    claim = work.claim(item.scope, wid)
    work.mark_submitted(item.scope, wid, claim["lease_token"])
    retained = extraction_results.retain(item.scope, wid, claim["lease_token"],
        {"entries": [{"entry_id": entry, **entry_result}]}, evidence_records=[])
    work.finish(item.scope, wid, claim["lease_token"], outcome=retained)
    return retained["contributions"][0]


RETIREMENT = {"outcome": "propose_retirement", "assessment": "The paper uses this record only as a control.",
              "remaining_uncertainty": "Absence alone is not evidence of non-use.",
              "proposed_values": [], "evidence_record_ids": []}


def _correction(item, name):
    refs = next(obj for obj in item.plan.envelope.extracted_objects
                if obj.pending_ref_id == name).metadata["validation_history"]
    return {"outcome": "proposed_correction", "assessment": "Methods name the exact term.",
            "remaining_uncertainty": "", "evidence_record_ids": [],
            "proposed_values": [{"field_path": "evidence_code_curies", "value": ["eco:b2"], "evidence_record_ids": [],
                "collection_mappings": [{"collection_path": "evidence_code_curies",
                    "source_collection_id": refs["fields"]["evidence_code_curies"],
                    "items": [{"item_key": "revised", "predecessor_ids": refs["elements"]["evidence_code_curies"],
                               "relationship": "revise", "reason": "Methods name the exact term"}],
                    "retired_item_ids": [], "retirement_reason": ""}]}]}


def _validated_references(envelope):
    """Reference keys of validator-contributed objects, read from their structural role."""
    return {key for obj in envelope.extracted_objects if obj.object_type == "SupportingTerm" for key in obj.ref_keys()}


def _anchored(envelope, keys):
    return [finding for finding in envelope.validation_findings
            if any(ref is not None and ref.ref_key() in keys
                   for ref in (finding.object_ref, finding.field_ref.object_ref if finding.field_ref else None))]


async def _accept_a(item):
    """Review accepts A's answer; the published result now holds its validated reference."""
    saved = await _decide(item, {"A": "accept"}, "accept-A")
    assert saved.get("state") != "conflict", saved
    current = item.checkpoint().envelope
    references = _validated_references(current)
    assert references and _anchored(current, references), (
        "the production state: findings anchored on a validated reference the answer contributed")
    return current


def _assert_published_source_is_valid(item):
    """The checkpoint, the latest source-plan envelope and every saved match validate."""
    DomainEnvelope.model_validate(item.checkpoint().envelope.model_dump(mode="json"))
    with store.SessionLocal() as db:
        fact = db.scalar(select(ValidationFact).where(ValidationFact.artifact_id == item.aid,
            ValidationFact.kind == "review_source_plan").order_by(ValidationFact.sequence.desc()).limit(1))
        body = store._content(db, fact)
    DomainEnvelope.model_validate(body["envelope"])
    assert body["matches"], "the accepted record's binding is still published"
    for match in body["matches"].values():
        MATCH.validate_python(match)


@pytest.mark.asyncio
async def test_the_selection_source_is_a_valid_envelope_after_an_accepted_validated_reference(
        scope, tmp_path, monkeypatch):
    item = _review(scope, tmp_path, monkeypatch)
    await item.adapter.initial()
    current = await _accept_a(item)
    with store.SessionLocal() as db:
        source = projection.source_envelope_in_transaction(db, item.scope, item.aid, current)
    DomainEnvelope.model_validate(source.model_dump(mode="json"))
    assert not _validated_references(source), "validator contributions are rebuilt, never source"
    assert not [finding for finding in source.validation_findings if projection.rebuilt_finding(finding)]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["reviewer_retire", "approve_recommendation", "curator_restore",
                                  "select_contribution"])
async def test_a_source_change_after_an_accepted_validated_reference_is_saved(scope, tmp_path, monkeypatch, path):
    """Production failed every one of these with dangling validation_findings[].field_ref.object_ref."""
    item = _review(scope, tmp_path, monkeypatch)
    await item.adapter.initial()
    b = item.roots["B"]
    if path == "curator_restore":
        retired = await _decide(item, {"B": "retire"}, "retire-B-first")
        assert retired.get("state") != "conflict", retired
    await _accept_a(item)
    if path == "reviewer_retire":
        result = await _decide(item, {"B": "retire"}, "retire-B")
        assert result.get("state") != "conflict", result
        assert [entry["action"] for entry in result["applied"]] == ["retire"]
    elif path == "approve_recommendation":
        contribution = _contribution(item, "B", RETIREMENT)
        result = await _call(item, "decide_record_retirement", {"operation_id": "approve-B",
            "contribution": contribution, "decision": "approve", "expected_source_sha": _digest(item),
            "reason": "A control, not a record the question asks for."})
        assert result.get("state") != "conflict", result
        assert result["retired"] is True
    elif path == "curator_restore":
        with store.SessionLocal() as db:
            version = store._current(db, item.aid, b)["version"]
        curator = ReviewContext(item.scope, item.ep, item.scope.owner_id, actor_type="curator")
        result = await item.adapter.select_source(curator, item.checkpoint, operation_id="restore-B",
            restore_target_id=b, expected_version=version, reason="The curator wants B reviewed.")
        assert result["publication"]["ready"] is False
    else:
        contribution = _contribution(item, "B", _correction(item, "B"))
        result = await _call(item, "select_extraction_contribution", {"operation_id": "select-B",
            "contribution": contribution, "field_paths": ["evidence_code_curies"], "evidence_ids": [],
            "expected_source_sha": _digest(item), "reason": "Methods name the exact term."})
        assert result.get("state") != "conflict", result
        assert result["affected_bindings"], "B's validator input changed"
    current = item.checkpoint().envelope
    with store.SessionLocal() as db:
        assert store._current(db, item.aid, b)["active"] is (path in {"curator_restore", "select_contribution"})
    if path == "select_contribution":
        assert next(obj for obj in current.extracted_objects
                    if obj.metadata["validation_history"]["target_id"] == b).payload["evidence_code_curies"] == ["eco:b2"]
    assert _validated_references(current), "A's accepted validated reference is projected again"
    _assert_published_source_is_valid(item)


@pytest.mark.asyncio
async def test_retiring_a_referenced_record_alone_is_refused_and_names_the_referrer(scope, tmp_path, monkeypatch):
    item = _review(scope, tmp_path, monkeypatch, referrer=True)
    await item.adapter.initial()
    await _accept_a(item)
    digest = _digest(item)
    refused = await _decide(item, {"B": "retire"}, "retire-B-alone")
    assert refused["state"] == "conflict" and refused["code"] == "HistoryConflict", refused
    link = item.roots["link-B"]
    assert f'record "link-B" ({link}) still refers to this record' in refused["message"]
    assert "retire them together in this save" in refused["message"]
    assert refused["message"].startswith(tuple(refused["handles"])), "the retired record is named by its handle"
    assert _digest(item) == digest, "nothing applied"
    together = await _decide(item, {"B": "retire", "link-B": "retire"}, "retire-B-with-link")
    assert together.get("state") != "conflict", together
    with store.SessionLocal() as db:
        assert not store._current(db, item.aid, item.roots["B"])["active"]
        assert not store._current(db, item.aid, link)["active"]
    _assert_published_source_is_valid(item)


@pytest.mark.asyncio
async def test_approving_retirement_of_a_referenced_record_is_refused_until_the_referrer_is_retired(
        scope, tmp_path, monkeypatch):
    item = _review(scope, tmp_path, monkeypatch, referrer=True)
    await item.adapter.initial()
    await _accept_a(item)
    contribution = _contribution(item, "B", RETIREMENT)
    refused = await _call(item, "decide_record_retirement", {"operation_id": "approve-B-referenced",
        "contribution": contribution, "decision": "approve", "expected_source_sha": _digest(item),
        "reason": "A control, not a record the question asks for."})
    assert refused["state"] == "conflict", refused
    link = item.roots["link-B"]
    assert f'record "link-B" ({link}) still refers to the record this recommendation retires' in refused["message"]
    with store.SessionLocal() as db:
        assert store._current(db, item.aid, item.roots["B"])["active"]
    retired = await _decide(item, {"link-B": "retire"}, "retire-link-first")
    assert retired.get("state") != "conflict", retired
    approved = await _call(item, "decide_record_retirement", {"operation_id": "approve-B-after-link",
        "contribution": contribution, "decision": "approve", "expected_source_sha": _digest(item),
        "reason": "A control, not a record the question asks for."})
    assert approved.get("retired") is True, approved
    _assert_published_source_is_valid(item)


@pytest.mark.asyncio
async def test_restoring_a_record_that_refers_to_a_retired_record_waits_for_that_restore(
        scope, tmp_path, monkeypatch):
    item = _review(scope, tmp_path, monkeypatch, referrer=True)
    await item.adapter.initial()
    await _accept_a(item)
    together = await _decide(item, {"B": "retire", "link-B": "retire"}, "retire-B-with-link")
    assert together.get("state") != "conflict", together
    curator = ReviewContext(item.scope, item.ep, item.scope.owner_id, actor_type="curator")

    async def restore(name, operation_id):
        with store.SessionLocal() as db:
            version = store._current(db, item.aid, item.roots[name])["version"]
        return await item.adapter.select_source(curator, item.checkpoint, operation_id=operation_id,
            restore_target_id=item.roots[name], expected_version=version, reason="The curator wants it reviewed.")

    with pytest.raises(store.HistoryConflict) as refused:
        await restore("link-B", "restore-link-first")
    assert str(refused.value) == ('This record refers to "B", which is still removed from the results; '
                                  "restore that record first")
    with store.SessionLocal() as db:
        assert not store._current(db, item.aid, item.roots["link-B"])["active"], "nothing applied"
    assert (await restore("B", "restore-B"))["publication"]["ready"] is False
    assert (await restore("link-B", "restore-link"))["publication"]["ready"] is False
    with store.SessionLocal() as db:
        assert all(store._current(db, item.aid, item.roots[name])["active"] for name in ("B", "link-B"))
    _assert_published_source_is_valid(item)


def test_a_retired_record_leaves_with_the_findings_that_name_it():
    """Ownership is structural: findings naming the retired record by reference leave with it."""
    def obj(name, target):
        return CuratableObjectEnvelope(object_type="Annotation", pending_ref_id=name,
                                       metadata={"validation_history": {"target_id": target}})

    def finding(name, **refs):
        return ValidationFinding(severity="warning", message="A required field is missing.", code="fixture." + name,
                                 **refs)

    envelope = DomainEnvelope(envelope_id="ownership", domain_pack_id="fixture",
        extracted_objects=[obj("kept", "t-kept"), obj("gone", "t-gone")],
        validation_findings=[
            finding("object", object_ref=ObjectRef(pending_ref_id="gone")),
            finding("field", field_ref=FieldRef(object_ref=ObjectRef(pending_ref_id="gone"), field_path="name")),
            finding("other", object_ref=ObjectRef(pending_ref_id="kept")),
            finding("envelope")])
    result = projection.without_records(envelope, {"t-gone"})
    assert [item.pending_ref_id for item in result.extracted_objects] == ["kept"]
    assert [item.code for item in result.validation_findings] == ["fixture.other", "fixture.envelope"]
    DomainEnvelope.model_validate(result.model_dump(mode="json"))
    assert len(envelope.extracted_objects) == 2, "the input envelope is unchanged"


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["the selection source", "the published source"])
async def test_an_invalid_source_stops_review_once_with_an_alert(scope, monkeypatch, caplog, stage,
                                                                   source_envelope_guard):
    """A source integrity failure is a system error: no conflict to retry, no second run, one report."""
    from tests.integration.persistence.test_review_end_summary import QUOTE, _chat_review, _outcome, _reviewer, _target
    from src.lib.validation_review import record_retirement

    reports = []
    monkeypatch.setattr("src.lib.validation_review.audit.report_operational_failure", reports.append)
    chat = await _chat_review(scope, monkeypatch, count=3)
    dangling = ValidationFinding(severity="info", message="Fixture finding on an object the source dropped.",
        code="fixture.dangling", field_ref=FieldRef(object_ref=ObjectRef(object_id="fixture-dropped-object"),
                                                    field_path="name"))
    if stage == "the selection source":
        build = projection.source_envelope_in_transaction

        def broken_source(*args, **kwargs):
            source = build(*args, **kwargs)
            return source.model_copy(update={"validation_findings": [*source.validation_findings, dangling]})

        monkeypatch.setattr(projection, "source_envelope_in_transaction", broken_source)
    else:
        remove = record_retirement.without_records

        def broken_removal(envelope, target_ids):
            removed = remove(envelope, target_ids)
            return removed.model_copy(update={"validation_findings": [*removed.validation_findings, dangling]})

        monkeypatch.setattr(record_retirement, "without_records", broken_removal)
    calls = _reviewer(chat, monkeypatch, {"VT0": "accept", "VT1": "retire", "VT2": "accept"})
    caplog.set_level("ERROR", logger="src.lib.validation_review.validator_adapter")
    await chat.resume()
    outcome = _outcome(chat)
    assert outcome["reason"] == "source_projection_invalid", outcome
    assert len(calls) == 1, "the run stops at the first failure; no reminder starts another"
    assert len(reports) == 1 and isinstance(reports[0], ValidationError), reports
    record, = chat.records()
    with store.SessionLocal() as db:
        aid = chat.reference(record)["artifact_id"]
        assert store._current(db, aid, _target(chat, "VT1"))["active"], "the retire did not apply"
        assert store._current(db, aid, _target(chat, "VT0"))["version"] == 0, "nor did the accept"
    notes = chat.render_review_notes(current_turn_only=True)
    assert ("Review paused: a system error stopped a save before it changed anything, and it was reported. "
            "Saved results and history are retained.") in notes
    assert "source_projection" not in notes and "source projection" not in notes
    logged = [entry for entry in caplog.records if entry.name == "src.lib.validation_review.validator_adapter"]
    assert len(logged) == 1 and logged[0].levelname == "ERROR"
    message = logged[0].getMessage()
    assert stage in message and aid in message and "fixture-dropped-object" in message
    assert QUOTE not in message and "VT1" not in message, "structural identifiers only, never record content"
    if stage == "the published source":
        assert [issue for issue, _ in source_envelope_guard] == ["edited envelope before publish"]
        source_envelope_guard.clear()


# Production trace 1b937f7b in small: a paper reference, the crb family and the w family.
# Each family is a mention (validated), an evidence quote and an association that refers
# to the reference, the mention and the quote.
CRB = ("paper-reference-1", "allele-mention-1", "evidence-quote-1-1", "allele-paper-evidence-association-1")
W_MENTION, W_QUOTE, W_ASSOCIATION = "allele-mention-4", "evidence-quote-4-1", "allele-paper-evidence-association-4"
W = (W_MENTION, W_QUOTE, W_ASSOCIATION)
REVIEW_OUTPUT = "Reviewed every record."


def _family(number, curie, quote, note):
    mention = f"allele-mention-{number}"
    evidence = f"evidence-quote-{number}-1"
    return [
        CuratableObjectEnvelope(object_type="Annotation", pending_ref_id=mention,
                                payload={"evidence_code_curies": [curie]}),
        CuratableObjectEnvelope(object_type="EvidenceQuote", pending_ref_id=evidence, payload={"quote": quote}),
        CuratableObjectEnvelope(object_type="Association", pending_ref_id=f"allele-paper-evidence-association-{number}",
            payload={"note": note}, object_refs=[ObjectRef(pending_ref_id=ref) for ref in
                                                 ("paper-reference-1", mention, evidence)]),
    ]


async def _production_chat(scope, tmp_path, monkeypatch):
    """A real chat review of the production record shape, with validators that contribute references."""
    from src.lib.config.agent_loader import AgentDefinition
    from src.lib.observability.cost_context import attach_agent_cost_identity
    from src.lib.validation_review.chat import ChatReview
    from agents import Agent

    install_cancel_reader(monkeypatch, AsyncMock(return_value=False))
    monkeypatch.setattr("src.lib.agent_studio.catalog_service.resolve_tools", lambda *args: [])
    monkeypatch.setattr("src.lib.prompts.assembly.get_all_active_prompts", lambda: {})
    pack = _pack(tmp_path)
    monkeypatch.setattr("src.lib.config.agent_loader.get_agent_definition_for_package",
        lambda package, agent: AgentDefinition(folder_name=agent, agent_id=agent, name=agent, package_id=package))
    monkeypatch.setattr("src.lib.curation_workspace.adapter_registry.resolve_curation_domain_pack_by_id",
                        lambda _: pack)
    _answers(monkeypatch, _answer)
    objects = [CuratableObjectEnvelope(object_type="Reference", pending_ref_id="paper-reference-1",
                                       payload={"title": "The crumbs paper"}),
               *_family(1, "eco:crb", "crb mutant clones were examined.", "crb"),
               *_family(4, "eco:w", "w flies served as the control stock.", "w (paper wording)")]
    envelope = DomainEnvelope(envelope_id=uuid4().hex, domain_pack_id=pack.pack_id, extracted_objects=objects)
    envelope = runtime.capture_extraction(scope=scope, producer_id=uuid4().hex,
                                          typed=envelope.model_dump(mode="json"), envelope=envelope, provenance={})
    supervisor = Agent(name="Supervisor", model="gpt-6-astra", instructions="Policy")
    attach_agent_cost_identity(supervisor, {"agent_id": "supervisor", "agent_role": "supervisor"})
    chat = ChatReview(scope.owner_id, scope.document_id, scope.session_id, uuid4().hex, supervisor, (),
                      "What alleles were examined in the paper?")
    await chat.receive(envelope, pack, agent_key="fixture", adapter_key="fixture", tool_name="ask_fixture",
        execution_receipt=None, context=ValidatorRuntimeContext(
            user_id=scope.owner_id, document_id=scope.document_id, authenticated_groups=()))
    return chat


def _recheck(chat, record, decision_id, names, roots):
    """The extraction agent's targeted re-check of the w records: each proposes its retirement.

    Admitted, run and retained through the real scheduling and result paths, with
    the current source entries as the extractor adapter supplies them.
    """
    context = chat.context(record)
    aid = chat.reference(record)["artifact_id"]
    entries = [roots[name] for name in names]
    with store.SessionLocal.begin() as db:
        source = projection.source_envelope_in_transaction(db, context.history_scope, aid,
                                                           chat.checkpoint(record).envelope)
    current = {obj.metadata["validation_history"]["target_id"]: obj.model_dump(mode="json")
               for obj in source.extracted_objects if obj.metadata["validation_history"]["target_id"] in entries}
    wid = extraction_scheduling.request(context, operation_id=uuid4().hex, parent_decision_id=decision_id,
        entry_ids=entries, instructions="Check whether the w records belong to the question.",
        receipt={"agent_key": "saved", "execution_receipt": None,
                 "contract": {"instructions": "Keep alleles examined in this study."}},
        current_entries=current)
    claim = work.claim(context.history_scope, wid)
    work.mark_submitted(context.history_scope, wid, claim["lease_token"])
    retained = extraction_results.retain(context.history_scope, wid, claim["lease_token"],
        {"entries": [{"entry_id": entry, **RETIREMENT} for entry in entries]}, evidence_records=[])
    work.finish(context.history_scope, wid, claim["lease_token"], outcome=retained)
    with store.SessionLocal() as db:
        return {store._fact(db, aid, ref["fact_id"]).target_id: ref for ref in retained["contributions"]}


def _production_reviewer(chat, monkeypatch, seen):
    """The reviewer model's first run, through its real bound tools, as production's run 1 went.

    It saves the crb accepts and the w defers, has the w records re-checked, then
    retires the w records. Every tool result is kept in seen; later runs do nothing.
    """
    calls = []

    async def run(agent, input, **kwargs):
        calls.append(agent.name)
        if len(calls) == 1:
            surface = getattr(agent, "tool_surface", None)
            tools = {tool.name: tool for tool in (surface.source_tools if surface is not None else agent.tools)}
            record, = chat.records()
            context = chat.context(record)
            roots = {obj.pending_ref_id: obj.metadata["validation_history"]["target_id"]
                     for obj in chat.checkpoint(record).envelope.extracted_objects}
            seen["roots"] = roots

            async def call(name, arguments):
                encoded = json.dumps(arguments)
                return await tools[name].on_invoke_tool(ToolContext(context=None, tool_name=name,
                    tool_call_id=arguments["operation_id"], tool_arguments=encoded), encoded)

            async def save(operation_id, decided):
                packet = reads.review_packet(context.history_scope, context.episode_id)
                decisions = [{"handle": handle, "action": action, "reason": f"{name}: what the paper shows.",
                              "absence_assessment": None, "unresolved_fields": None}
                             for name, action in decided.items()
                             for handle in _handles(_record(packet, roots[name]))]
                return await call("record_validation_review", {"operation_id": operation_id,
                    "packet_id": packet["packet_id"], "packet_decisions": decisions, "entries": None})

            seen["initial"] = await save("review-initial-1", {**{name: "accept" for name in CRB},
                                                              **{name: "defer" for name in W}})
            seen["published"] = chat.checkpoint(record).envelope
            contributions = _recheck(chat, record, seen["initial"]["decision_id"], W, roots)
            packet = reads.review_packet(context.history_scope, context.episode_id)
            seen["mention_items"] = [entry["review"] for entry in _record(packet, roots[W_MENTION])["review"]]
            if _handles(_record(packet, roots[W_MENTION])):
                seen["alone"] = await save("retire-white-mention-alone", {W_MENTION: "retire"})
                seen["approve_alone"] = await call("decide_record_retirement", {
                    "operation_id": "retire-white-mention-1", "contribution": contributions[roots[W_MENTION]],
                    "decision": "approve", "expected_source_sha": packet["source_state_sha256"],
                    "reason": "w is the control stock, not an allele examined."})
                seen["family"] = await save("retire-white-family", {name: "retire" for name in W})
            else:
                # v0.11.5: the mention has no handle, so only its quote and association can be retired.
                seen["family"] = await save("retire-white-evidence-association-1",
                                            {W_QUOTE: "retire", W_ASSOCIATION: "retire"})
        return SimpleNamespace(final_output=REVIEW_OUTPUT, to_input_list=lambda: [
            *(input if isinstance(input, list) else [{"role": "user", "content": input}]),
            {"role": "assistant", "content": REVIEW_OUTPUT}])

    monkeypatch.setattr("src.lib.openai_agents.runner.run_agent_with_owned_openai_resources", run)
    return calls


@pytest.mark.asyncio
async def test_production_trace_1b937f7b_retires_the_white_family_after_accepted_validated_references(
        scope, tmp_path, monkeypatch):
    """End to end: accepted references (A), a re-offered deferred answer (D), a named refusal (B), one run."""
    reports = []
    monkeypatch.setattr("src.lib.validation_review.audit.report_operational_failure",
                        lambda error, **details: reports.append(error))
    chat = await _production_chat(scope, tmp_path, monkeypatch)
    seen = {}
    calls = _production_reviewer(chat, monkeypatch, seen)
    await chat.resume()
    assert seen["initial"].get("state") != "conflict", seen["initial"]
    references = _validated_references(seen["published"])
    assert references and _anchored(seen["published"], references), "the crb accepts published validated references"
    roots = seen["roots"]
    # D: after the re-check, the deferred w answer is offered again with a handle.
    assert seen["mention_items"] == ["validator_answer"], (
        f"packet items for the w mention: {seen['mention_items']}; "
        f"retiring the w records it could: {seen['family'].get('message', seen['family'])}")
    # B: retiring the mention alone, either way, names the association that still refers to it.
    referrer = f'record "{W_ASSOCIATION}" ({roots[W_ASSOCIATION]})'
    assert seen["alone"]["state"] == "conflict" and seen["alone"]["code"] == "HistoryConflict", seen["alone"]
    assert f"{referrer} still refers to this record; retire them together" in seen["alone"]["message"]
    assert seen["approve_alone"]["state"] == "conflict", seen["approve_alone"]
    assert f"{referrer} still refers to the record this recommendation retires" in seen["approve_alone"]["message"]
    # A: retiring the whole w family in one save applies, and review is ready.
    assert seen["family"].get("state") != "conflict", seen["family"]
    assert seen["family"]["ready"] is True
    assert len(calls) == 1, "review ended ready, so continuation started no further run"
    outcome = chat.supervisor_handoff()["review_outcomes"][0]["outcome"]
    assert outcome["review"]["ready"] is True
    assert {item["label"] for item in outcome["retired"]} == set(W)
    assert outcome["review_counts"] == {"accepted": 4, "pending": 0, "retired_in_this_review": 3}
    assert reports == [], "no system error"
    record, = chat.records()
    current = chat.checkpoint(record).envelope
    DomainEnvelope.model_validate(current.model_dump(mode="json"))
    assert _validated_references(current) and {roots[name] for name in CRB} <= {
        obj.metadata["validation_history"]["target_id"] for obj in current.extracted_objects}

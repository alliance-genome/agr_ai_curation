"""Compact review packet: every outstanding record read once, decisions saved by handle."""
import json
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from agents import Agent, ModelResponse, RunConfig
from openai.types.responses import ResponseFunctionToolCall

from src.lib.domain_envelopes.persistence import DomainEnvelopeCheckpointRequest
from src.lib.validation_history import selections, store
from src.lib.validation_review import admission, decisions, reads, reviewer
from src.lib.validation_review.decisions import ReviewContext
from src.lib.validation_review.tools import create_tools
from tests.integration.persistence.test_chat_review_adapter import (
    checkpoint_provider, migrated as migrated, planned as planned, scope as scope,
)
from tests.integration.persistence.test_curator_selected_authority import scenario as scenario
from tests.integration.persistence.test_review_grouped_sources import (
    QUOTE, _build, _evidence, _object, _targets, cancel_reader as cancel_reader,
)
from tests.unit.lib.domain_packs.test_validator_dispatch import _result_payload
from tests.unit.lib.openai_agents.test_model_request_measurement import FakeResponsesHTTPModel, _final, _usage


def _answers(monkeypatch, answer):
    monkeypatch.setattr("src.lib.validation_review.validator_adapter._default_package_scoped_validator_runner",
                        lambda: lambda request, binding, **kwargs: answer(request))
    monkeypatch.setattr("src.lib.validation_review.validator_adapter._default_package_scoped_validator_batch_runner",
                        lambda: lambda *args, **kwargs: pytest.fail("Unexpected batch"))


def _doug_answer(request):
    """A resolved first element and an ambiguous second element per record."""
    curie = request.selected_inputs["curie"]
    payload = {**_result_payload(request, resolved_values={"curie": curie.upper()}),
               "resolved_objects": [{"curie": curie.upper(), "name": "evidence term", "obsolete": False,
                                     "synonyms": ["fixture alias"], "context": {"lookup": "nested detail"}}],
               "explanation": f"Exact database match for {curie}.",
               "curator_message": "Confirmed term."}
    if curie == "eco:2":
        payload.update(
            status="unresolved", resolved_values={}, resolved_objects=[], missing_expected_fields=["curie"],
            explanation="Two terms fit the supplied mention.", unresolved_questions=["Which assay was used?"],
            curator_message=None,
            candidates=[{"value": f"ECO:2{suffix}", "label": f"candidate {suffix}", "object_type": "Term",
                         "score": None, "matched_fields": {},
                         "details": {"source_record": {"large": "x" * 400},
                                     "scientific_assessment": {"disposition": "rejected",
                                                               "explanation": f"Candidate {suffix} is weaker.",
                                                               "evidence_record_ids": ["original-evidence"]}}}
                        for suffix in "AB"])
    return payload


def _handles(record):
    found = []
    for item in record["review"]:
        if "handle" in item:
            found.append(item["handle"])
        found.extend(item.get("handles", {}).values())
    return found


def _decide(handles, action="accept", **extra):
    return [{"handle": handle, "action": action, "reason": f"Evidence specific to {handle} supports it.", **extra}
            for handle in handles]


def test_gillian_shaped_packet_reads_every_record_once_and_quotes_once(scope):
    objects = [_object(index, ["e1", "e2"] if index % 2 else ["e1"]) for index in range(6)]
    envelope, artifact, episode, baseline, checkpoint = _build(
        scope, objects, [_evidence("e1"), _evidence("e2", QUOTE + " second")])
    targets = _targets(envelope)
    page = reads.review_packet(scope, episode)
    assert (page["page"], page["pages"], page["next_page"]) == (1, 1, None)
    assert (page["record_count"], page["answer_count"], page["decision_count"]) == (6, 0, 6)
    assert [record["target_id"] for record in page["records"]] == targets, "extractor order"
    assert [record["record"] for record in page["records"]] == [f"R{n}" for n in range(1, 7)]
    text = json.dumps(page)
    assert text.count(QUOTE + " second") == 1 and text.count(QUOTE) == 2, "each quote once"
    assert page["evidence"] == {"E1": {"verified_quote": QUOTE, "section": "Appendix 1", "page": 1},
                                "E2": {"verified_quote": QUOTE + " second", "section": "Appendix 1", "page": 1}}
    for record, obj in zip(page["records"], objects, strict=True):
        assert record["values"] == obj.payload, "scientific values are never compacted"
        assert record["values"]["attributes"]["absent_detail"] is None
        assert record["evidence"] == (["E1", "E2"] if obj.evidence_record_ids == ["e1", "e2"] else ["E1"])
        assert not {"metadata", "schema_ref", "model_ref", "status", "evidence_record_ids", "pending_ref_id"} & set(record)
        assert [item["review"] for item in record["review"]] == ["record"]
    assert reads.review_packet(scope, episode)["packet_id"] == page["packet_id"], "same state, same packet"
    handles = [handle for record in page["records"] for handle in _handles(record)]
    context = ReviewContext(scope, episode, "fixture-reviewer")
    result = decisions.record_packet_decisions(context, checkpoint_request=checkpoint(), operation_id="all",
        packet_id=page["packet_id"], packet_decisions=_decide(handles))
    assert result["ready"]
    assert [(item["handle"], item["decision_unit_id"]) for item in result["applied"]] == list(zip(handles, targets))
    with store.SessionLocal() as db:
        saved = store._content(db, store._fact(db, artifact, result["decision_id"], kind="decision"))
    assert [(entry["target_id"], entry["subject"], entry["expected_version"]) for entry in saved["entries"]] == [
        (target, "baseline", 0) for target in targets], "the decision fact keeps exact targets"
    after = reads.review_packet(scope, episode)
    assert after["ready"] and after["records"] == [] and after["packet_id"] != page["packet_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("planned", [{"shape": "evidence", "count": 3}], indirect=True)
async def test_doug_shaped_packet_inlines_every_answer_and_saves_in_one_call(planned, monkeypatch):
    item = planned
    _answers(monkeypatch, _doug_answer)
    await item.adapter.initial()
    page = reads.review_packet(item.scope, item.ep)
    assert (page["record_count"], page["answer_count"], page["decision_count"]) == (3, 6, 6)
    text = json.dumps(page)
    assert text.count("Original quoted evidence") == 1
    assert "x" * 400 not in text and "fixture alias" not in text and "nested detail" not in text
    assert "lookup_attempts" not in text and "request_id" not in text
    for record in page["records"]:
        assert record["values"] == {"evidence_code_curies": ["eco:1", "eco:2"]}
        assert record["evidence"] == ["E1"]
        answers = sorted((entry for entry in record["review"] if entry["review"] == "validator_answer"),
                         key=lambda entry: entry["sent"]["curie"])
        resolved, ambiguous = answers
        assert resolved["validator"] == "fixture.evidence_lookup"
        assert resolved["status"] == "resolved" and resolved["values"] == {"curie": "ECO:1"}
        assert resolved["explanation"] == "Exact database match for eco:1."
        assert resolved["resolved"] == [{"curie": "ECO:1", "name": "evidence term", "obsolete": False}]
        assert "candidates" not in resolved, "a resolved answer keeps its identity, not alternatives"
        assert ambiguous["status"] == "unresolved" and ambiguous["missing"] == ["curie"]
        assert ambiguous["questions"] == ["Which assay was used?"]
        assert [candidate["value"] for candidate in ambiguous["candidates"]] == ["ECO:2A", "ECO:2B"]
        assert ambiguous["candidates"][0]["assessment"] == {
            "disposition": "rejected", "explanation": "Candidate A is weaker.", "evidence": ["E1"]}
    handles = [handle for record in page["records"] for handle in _handles(record)]
    assert handles == [f"D{n}" for n in range(1, 7)]
    context = ReviewContext(item.scope, item.ep, "supervisor")
    result = decisions.record_packet_decisions(context, checkpoint_request=checkpoint_provider(item)(),
        operation_id="one-save", packet_id=page["packet_id"], packet_decisions=_decide(handles))
    assert result["ready"] and len(result["applied"]) == 6
    with store.SessionLocal() as db:
        saved = store._content(db, store._fact(db, item.aid, result["decision_id"], kind="decision"))
        kinds = {store._fact(db, item.aid, entry["subject"]).kind for entry in saved["entries"]}
    assert kinds == {"completion"}, "handles resolve to the exact read answers"


@pytest.mark.asyncio
@pytest.mark.parametrize("planned", [{"shape": "evidence", "count": 2}], indirect=True)
async def test_a_rejected_answer_waits_with_its_target_for_reconsideration(planned, monkeypatch):
    item = planned
    _answers(monkeypatch, _doug_answer)
    await item.adapter.initial()
    page = reads.review_packet(item.scope, item.ep)
    ambiguous = next(entry for entry in page["records"][0]["review"] if entry.get("status") == "unresolved")
    others = [handle for record in page["records"] for handle in _handles(record) if handle != ambiguous["handle"]]
    context = ReviewContext(item.scope, item.ep, "supervisor")
    result = decisions.record_packet_decisions(context, checkpoint_request=checkpoint_provider(item)(),
        operation_id="mixed", packet_id=page["packet_id"],
        packet_decisions=[*_decide([ambiguous["handle"]], "reject"), *_decide(others)])
    assert not result["ready"]
    rejected = next(applied for applied in result["applied"] if applied["handle"] == ambiguous["handle"])
    fresh = reads.review_packet(item.scope, item.ep)
    assert [record["target_id"] for record in fresh["records"]] == [page["records"][0]["target_id"]]
    waiting, = fresh["records"][0]["review"]
    assert waiting["review"] == "waiting" and waiting["earlier_decision"] == "reject"
    assert waiting["target_id"] == rejected["target_id"] and fresh["decision_count"] == 0


def test_references_between_records_and_evidence_shaped_values_use_handles(scope):
    from src.schemas.domain_envelope import ObjectRef

    quote_record = {"evidence_record_id": "e1", "verified_quote": QUOTE, "section": "Appendix 1", "page": 1,
                    "chunk_id": "chunk"}
    supporting = _object(0, ["e1"]).model_copy(update={"payload": dict(quote_record)})
    annotation = _object(1, ["e1"]).model_copy(update={"object_refs": [
        ObjectRef(pending_ref_id="generic-object-0", object_type="generic_object"),
        ObjectRef(pending_ref_id="generic-object-2", object_type="generic_object")]})
    envelope, _, episode, baseline, checkpoint = _build(
        scope, [supporting, annotation, _object(2, ["e1"])], [_evidence("e1")])
    decided = _targets(envelope)[2]
    decisions.record_decisions(ReviewContext(scope, episode, "fixture-reviewer"), checkpoint_request=checkpoint(),
        operation_id="decided-earlier", entries=[{"target_id": decided, "decision_unit_id": decided,
            "expected_version": 0, "action": "accept", "reason": "Supported.", "extraction_ref": baseline}])
    page = reads.review_packet(scope, episode)
    first, second = page["records"]
    assert first["values"] == "E1" and json.dumps(page).count(QUOTE) == 1
    assert second["object_refs"] == ["R1", {"object_id": None, "pending_ref_id": "generic-object-2",
                                            "object_type": "generic_object"}], "only records in this packet get handles"


def test_an_answer_not_yet_received_waits_without_a_handle(planned):
    from dataclasses import replace
    from src.lib.domain_packs.materialization import ValidatorResultMaterializationInput
    from src.lib.validation_history import runtime
    from src.lib.validation_history.candidates import retain_candidates
    from src.schemas.domain_validator import DomainValidatorResultBase

    item = planned
    jobs = [replace(job, history_episode_id=item.ep) for job in item.plan.initial_jobs]
    invocation = runtime.begin_jobs(item.plan.history_binding, jobs, operation_id="in-flight", receipt={})
    page = reads.review_packet(item.scope, item.ep)
    assert page["decision_count"] == 0 and page["answer_count"] == 0
    entries = [entry for record in page["records"] for entry in record["review"]]
    assert entries and all(entry["review"] == "waiting" and "execution_pending" in entry["codes"]
                           and "handle" not in entry for entry in entries)
    results = [DomainValidatorResultBase.model_validate(
        {**_result_payload(job.request, resolved_values={"curie": "ECO:LATE"}), "resolved_objects": []})
        for job in jobs]
    runtime.complete_jobs(item.plan.history_binding, invocation, results)
    retain_candidates(item.plan.envelope, [ValidatorResultMaterializationInput(job.match, job.request, result)
                                           for job, result in zip(jobs, results, strict=True)],
                      scope=item.scope, source_envelope_revision=1)
    fresh = reads.review_packet(item.scope, item.ep)
    assert fresh["packet_id"] != page["packet_id"] and fresh["answer_count"] == len(jobs)
    handles = [handle for record in fresh["records"] for handle in _handles(record)]
    assert decisions.record_packet_decisions(ReviewContext(item.scope, item.ep, "supervisor"),
        checkpoint_request=checkpoint_provider(item)(), operation_id="late-answers",
        packet_id=fresh["packet_id"], packet_decisions=_decide(handles))["ready"]


@pytest.mark.asyncio
@pytest.mark.parametrize("planned", ["dependent"], indirect=True)
async def test_a_dependent_answer_names_the_selected_prerequisites_it_used(planned, monkeypatch):
    item = planned
    _answers(monkeypatch, lambda request: {**_result_payload(request, resolved_values={"curie": "ECO:SELECTED"}),
                                           "resolved_objects": []})
    checkpoint = checkpoint_provider(item)
    await item.adapter.initial()
    first = reads.review_packet(item.scope, item.ep)
    assert not any("prerequisites" in entry for record in first["records"] for entry in record["review"])
    handles = [entry["handle"] for record in first["records"] for entry in record["review"]
               if entry.get("validator") == "fixture.evidence_lookup"]
    context = ReviewContext(item.scope, item.ep, "supervisor")
    decisions.record_packet_decisions(context, checkpoint_request=checkpoint(), operation_id="prerequisites",
        packet_id=first["packet_id"], packet_decisions=_decide(handles))
    await item.adapter.advance(checkpoint)
    second = reads.review_packet(item.scope, item.ep)
    dependent = [entry for record in second["records"] for entry in record["review"]
                 if entry.get("validator") == "fixture.dependent"]
    assert dependent and all(entry["prerequisites"] for entry in dependent)
    for reference in dependent[0]["prerequisites"]:
        selection = json.loads(reads.read(item.scope, item.ep, **reference["selection"])["content"])
        assert selection["action"] == "accept" and selection["selected"] == reference["selected"]["fact_id"]
        answer = json.loads(reads.read(item.scope, item.ep, **reference["selected"])["content"])
        assert answer["result"]["resolved_values"] == {"curie": "ECO:SELECTED"}


def _second_attempt(item, job, *, complete):
    from dataclasses import replace
    from src.lib.domain_packs.materialization import ValidatorResultMaterializationInput
    from src.lib.validation_history import runtime
    from src.lib.validation_history.candidates import retain_candidates
    from src.schemas.domain_validator import DomainValidatorResultBase

    job = replace(job, history_episode_id=item.ep)
    invocation = runtime.begin_jobs(item.plan.history_binding, [job], operation_id=uuid4().hex, receipt={})
    if complete:
        result = DomainValidatorResultBase.model_validate(
            {**_result_payload(job.request, resolved_values={"curie": "ECO:NEWER"}), "resolved_objects": []})
        runtime.complete_jobs(item.plan.history_binding, invocation, [result])
        retain_candidates(item.plan.envelope, [ValidatorResultMaterializationInput(job.match, job.request, result)],
                          scope=item.scope, source_envelope_revision=1)


def test_a_crashed_validation_is_acknowledged_by_its_handle_and_nothing_else(planned):
    from src.lib.validation_history import runtime
    from src.lib.validation_review import scheduling, work

    item = planned
    frozen = runtime.frozen_job_requests(item.plan.history_binding, item.plan.initial_jobs)
    [wid] = scheduling.initial(item.scope, item.ep, [{"operation_id": "crashed", "requests": frozen,
                                                      "receipt": {"revision": "fixture"}}])
    claim = work.claim(item.scope, wid)
    work.mark_submitted(item.scope, wid, claim["lease_token"])
    work.mark_uncertain(item.scope, wid, claim["lease_token"], reason="worker_disconnected")
    page = reads.review_packet(item.scope, item.ep)
    crashed = [entry for record in page["records"] for entry in record["review"]]
    assert crashed and all(entry["review"] == "uncertain_outcome" and "handle" in entry for entry in crashed)
    handles = [entry["handle"] for entry in crashed]
    context = ReviewContext(item.scope, item.ep, "supervisor")
    checkpoint = checkpoint_provider(item)
    with pytest.raises(ValueError, match=f"{handles[0]}: .*only acknowledge_failure"):
        decisions.record_packet_decisions(context, checkpoint_request=checkpoint(), operation_id="accept-crash",
            packet_id=page["packet_id"], packet_decisions=_decide(handles))
    decisions.record_packet_decisions(context, checkpoint_request=checkpoint(), operation_id="acknowledge-crash",
        packet_id=page["packet_id"], packet_decisions=_decide(handles, "acknowledge_failure"))
    manifest = selections.read_manifest(item.scope, item.aid, episode_id=item.ep)
    assert not any(blocker["code"] == "execution_pending" for blocker in manifest["blockers"])
    fresh = reads.review_packet(item.scope, item.ep)
    assert not any(entry["review"] == "uncertain_outcome"
                   for record in fresh["records"] for entry in record["review"])


def _same_field(page, field):
    return [entry for record in page["records"] for entry in record["review"] if entry.get("field") == field]


@pytest.mark.asyncio
async def test_a_newer_validation_still_running_holds_the_older_answer(planned, monkeypatch):
    item = planned
    _answers(monkeypatch, lambda request: {**_result_payload(request, resolved_values={"curie": "ECO:OLDER"}),
                                           "resolved_objects": []})
    await item.adapter.initial()
    job = item.plan.initial_jobs[0]
    _second_attempt(item, job, complete=False)
    page = reads.review_packet(item.scope, item.ep)
    held, = _same_field(page, job.request.target.field_path) or [
        entry for record in page["records"] for entry in record["review"] if entry["review"] == "waiting"]
    assert held["review"] == "waiting" and "execution_pending" in held["codes"] and "handle" not in held
    assert page["decision_count"] == len(item.plan.initial_jobs) - 1


@pytest.mark.asyncio
async def test_two_answers_for_one_field_have_two_handles_and_one_save_refuses_both(planned, monkeypatch):
    item = planned
    _answers(monkeypatch, lambda request: {**_result_payload(request, resolved_values={"curie": "ECO:OLDER"}),
                                           "resolved_objects": []})
    await item.adapter.initial()
    job = item.plan.initial_jobs[0]
    _second_attempt(item, job, complete=True)
    page = reads.review_packet(item.scope, item.ep)
    older, newer = _same_field(page, job.request.target.field_path)
    assert {older["values"]["curie"], newer["values"]["curie"]} == {"ECO:OLDER", "ECO:NEWER"}
    pattern = f"{older['handle']}, {newer['handle']}: these handles decide the same record field"
    with pytest.raises(ValueError, match=pattern):
        decisions.record_packet_decisions(ReviewContext(item.scope, item.ep, "supervisor"),
            checkpoint_request=checkpoint_provider(item)(), operation_id="both-answers",
            packet_id=page["packet_id"], packet_decisions=_decide([older["handle"], newer["handle"]]))


def test_two_decisions_for_one_record_field_are_refused_naming_the_handles(scope):
    envelope, artifact, episode, _, checkpoint = _build(scope, [_object(0, ["e1"])], [_evidence("e1")])
    page = reads.review_packet(scope, episode)
    handle, = _handles(page["records"][0])
    with pytest.raises(ValueError, match=f"{handle}, {handle}: "):
        decisions.record_packet_decisions(ReviewContext(scope, episode, "fixture-reviewer"),
            checkpoint_request=checkpoint(), operation_id="twice", packet_id=page["packet_id"],
            packet_decisions=[*_decide([handle]), *_decide([handle], "defer")])
    with store.SessionLocal() as db:
        assert store._current(db, artifact, _targets(envelope)[0])["version"] == 0


def test_a_changed_record_conflicts_by_handle_and_nothing_in_the_call_applies(scope):
    objects = [_object(index, ["e1"]) for index in range(3)]
    envelope, artifact, episode, baseline, checkpoint = _build(scope, objects, [_evidence("e1")])
    targets = _targets(envelope)
    context = ReviewContext(scope, episode, "fixture-reviewer")
    page = reads.review_packet(scope, episode)
    first, second, third = (_handles(record)[0] for record in page["records"])
    decisions.record_packet_decisions(context, checkpoint_request=checkpoint(), operation_id="defer-one",
        packet_id=page["packet_id"], packet_decisions=_decide([first], "defer"))
    with pytest.raises(store.HistoryConflict, match=first) as caught:
        decisions.record_packet_decisions(context, checkpoint_request=checkpoint(), operation_id="all",
            packet_id=page["packet_id"], packet_decisions=_decide([first, second, third]))
    assert not any(target in str(caught.value) for target in targets), "conflicts name handles"
    manifest = selections.read_manifest(scope, artifact, episode_id=episode)
    assert {unit["target_id"]: unit["version"] for unit in manifest["units"]} == {
        targets[0]: 1, targets[1]: 0, targets[2]: 0}
    fresh = reads.review_packet(scope, episode)
    assert fresh["packet_id"] != page["packet_id"]
    deferred = next(record for record in fresh["records"] if record["target_id"] == targets[0])
    assert [item["review"] for item in deferred["review"]] == ["record"], "a deferred record is decided again"
    handles = [handle for record in fresh["records"] for handle in _handles(record)]
    assert decisions.record_packet_decisions(context, checkpoint_request=checkpoint(), operation_id="all-fresh",
        packet_id=fresh["packet_id"], packet_decisions=_decide(handles))["ready"]


def test_every_unknown_uncertain_field_is_named_with_its_handle_and_the_valid_fields(scope):
    objects = [_object(0, ["e1"]), _object(1, ["e1"])]
    envelope, artifact, episode, _, checkpoint = _build(scope, objects, [_evidence("e1")])
    targets = _targets(envelope)
    page = reads.review_packet(scope, episode)
    first, second = (_handles(record)[0] for record in page["records"])
    decided = [
        {"handle": first, "action": "accept", "reason": "Name supported; source unclear.",
         "unresolved_fields": [{"field_path": "attributes.source", "explanation": "Stock centre not named."},
                               {"field_path": "attributes.reagent_name", "explanation": "Typo in the path."}]},
        {"handle": second, "action": "accept", "reason": "Supported except its detail.",
         "unresolved_fields": [{"field_path": "detail", "explanation": "Wrong path."}]},
    ]
    with pytest.raises(ValueError) as caught:
        decisions.record_packet_decisions(ReviewContext(scope, episode, "fixture-reviewer"),
            checkpoint_request=checkpoint(), operation_id="bad-paths", packet_id=page["packet_id"],
            packet_decisions=decided)
    message = str(caught.value)
    assert f'{first}: unresolved field "attributes.reagent_name" does not exist' in message
    assert f'{second}: unresolved field "detail" does not exist' in message
    assert '"attributes.source" does not exist' not in message
    for valid in ("label", "rationale", "attributes.reagent_names", "attributes.source",
                  "attributes.absent_detail", "semantic_class"):
        assert valid in message
    assert not any(target in message for target in targets)
    with store.SessionLocal() as db:
        assert {store._current(db, artifact, target)["version"] for target in targets} == {0}


def _identity_record(index):
    """A standalone extracted identity: its whole value carries a resolution state, as for a ZFIN term."""
    obj = _object(index, ["e1"])
    return obj.model_copy(update={"payload": {
        "curie": None, "label": None, "mention": "bifurcation", "resolution_state": "unresolved",
        "lookup_outcome": "not_validated", "validator_explanation": "Not validated yet."}})


def test_an_identity_record_is_marked_and_accepting_it_keeps_its_own_resolution_state(scope):
    """S06/S07: the reviewer listed an identity record's curie as uncertain; the packet now says up front."""
    from src.lib.validation_history.projection import read_published_projection

    objects = [_identity_record(0), _object(1, ["e1"])]
    _, _, episode, _, checkpoint = _build(scope, objects, [_evidence("e1")])
    page = reads.review_packet(scope, episode)
    identity, plain = page["records"]
    first, second = _handles(identity)[0], _handles(plain)[0]
    assert identity["review"] == [{"handle": first, "review": "record", "identity_record": (
        "Accepting keeps this record's own resolution_state; send it without unresolved_fields.")}]
    assert plain["review"] == [{"handle": second, "review": "record"}], "a plain record is not marked"
    context = ReviewContext(scope, episode, "fixture-reviewer")
    decided = [
        {"handle": first, "action": "accept", "reason": "E1 names bifurcation; no ontology mapping ran.",
         "unresolved_fields": [{"field_path": "curie", "explanation": "Ontology identity is unresolved."}]},
        {"handle": second, "action": "accept", "reason": "E1 supports the reagent."},
    ]
    with pytest.raises(ValueError) as caught:
        decisions.record_packet_decisions(context, checkpoint_request=checkpoint(), operation_id="identity-uncertain",
            packet_id=page["packet_id"], packet_decisions=decided)
    message = str(caught.value)
    assert message.startswith(
        f'Nothing in this call was saved. {first}: unresolved field "curie" is part of an identity that keeps '
        "its own resolution_state, which accepting does not resolve, so leave it out of unresolved_fields.")
    assert message.endswith(f"Fields that can be assessed for {first}: none, so send {first} without unresolved_fields.")
    assert f"{second}:" not in message
    result = decisions.record_packet_decisions(context, checkpoint_request=checkpoint(),
        operation_id="identity-accepted", packet_id=page["packet_id"],
        packet_decisions=[{key: value for key, value in decided[0].items() if key != "unresolved_fields"}, decided[1]])
    assert result["ready"]
    projected, _ = read_published_projection(scope, result["publication"], finalized=True)
    assert projected.extracted_objects[0].payload == objects[0].payload, "the identity stays unresolved"
    assert not any(finding.code == "validation_review.assessment_unresolved"
                   for finding in projected.validation_findings)


def _list_row(index, *, source=True):
    attributes = {"reagent_names": f"UAS-gene{index}-RNAi", "detail_reagent_type": "Transgene or insertion",
                  "detail_associated_gene": f"gene{index}"}
    if source:
        attributes["source"] = f"BDSC:{index}"
    obj = _object(index, ["e1"])
    return obj.model_copy(update={"payload": {**obj.payload, "attributes": attributes}})


def test_r44_shape_valid_paths_bind_after_a_selected_followup_and_only_a_missing_field_is_named(scope):
    """r44: valid uncertain fields on every record, plus one record genuinely lacking source."""
    from src.lib.validation_history.projection import read_published_projection
    from src.lib.validation_review import extraction_results, extraction_scheduling, source_selection, work

    objects = [_list_row(0), _list_row(1, source=False)]
    envelope, artifact, episode, _, checkpoint = _build(scope, objects, [_evidence("e1")])
    with_source, without_source = _targets(envelope)
    context = ReviewContext(scope, episode, "fixture-reviewer")
    first = reads.review_packet(scope, episode)
    deferred = decisions.record_packet_decisions(context, checkpoint_request=checkpoint(), operation_id="defer",
        packet_id=first["packet_id"], packet_decisions=_decide(
            [handle for record in first["records"] for handle in _handles(record)], "defer"))
    wid = extraction_scheduling.request(context, operation_id="followup", parent_decision_id=deferred["decision_id"],
        entry_ids=[with_source, without_source], instructions="Check the reagent table.",
        receipt={"agent_key": "saved", "execution_receipt": None,
                 "contract": {"instructions": "Keep reagents used in this study."}},
        current_entries={obj.metadata["validation_history"]["target_id"]: obj.model_dump(mode="json")
                         for obj in envelope.extracted_objects})
    claim = work.claim(scope, wid)
    work.mark_submitted(scope, wid, claim["lease_token"])
    retained = extraction_results.retain(scope, wid, claim["lease_token"], {"entries": [
        {"entry_id": with_source, "outcome": "proposed_correction", "assessment": "Table names an RNAi line.",
         "remaining_uncertainty": "", "evidence_record_ids": [],
         "proposed_values": [{"field_path": "attributes.detail_reagent_type", "value": "RNAi line",
                              "evidence_record_ids": []}]},
        {"entry_id": without_source, "outcome": "no_new_information", "assessment": "No supplier is given.",
         "remaining_uncertainty": "Supplier unknown.", "proposed_values": [], "evidence_record_ids": []}]},
        evidence_records=[])
    work.finish(scope, wid, claim["lease_token"], outcome=retained)
    correction = next(ref for ref in retained["contributions"]
                      if store.read_fact(scope, artifact, ref["fact_id"])["content"]["proposal"]["entry_id"] == with_source)
    with store.SessionLocal.begin() as db:
        source_selection.select_in_transaction(db, context, operation_id="select", contribution_ref=correction,
            field_paths=["attributes.detail_reagent_type"], evidence_ids=[],
            expected_source_sha=selections.source_state_digest(db, artifact), reason="Table supports it.",
            source_envelope=envelope, rebuild_inputs=lambda db, env: {},
            publish=lambda db, env, requests, changed, selection_id: {})
    second = reads.review_packet(scope, episode)
    handles = {record["target_id"]: _handles(record)[0] for record in second["records"]}
    assert set(handles) == {with_source, without_source}

    def save(operation_id, fields_without_source):
        return decisions.record_packet_decisions(context, checkpoint_request=checkpoint(),
            operation_id=operation_id, packet_id=second["packet_id"], packet_decisions=[
                {"handle": handles[with_source], "action": "accept", "reason": "Pathway tool use is defensible.",
                 "unresolved_fields": [
                     {"field_path": "attributes.detail_associated_gene", "explanation": "Tool or gene biology."},
                     {"field_path": "attributes.reagent_names", "explanation": "Two lines share a name."}]},
                {"handle": handles[without_source], "action": "accept", "reason": "Measurement tool.",
                 "unresolved_fields": [{"field_path": path, "explanation": "Not established."}
                                       for path in fields_without_source]}])

    with pytest.raises(store.HistoryConflict) as refused:
        save("first-try", ["attributes.reagent_names", "attributes.source"])
    message = str(refused.value)
    assert f'{handles[without_source]}: unresolved field "attributes.source" does not exist' in message
    assert handles[with_source] + ":" not in message, "valid paths on the corrected record are not refused"
    assert "attributes.detail_reagent_type" in message and with_source not in message
    result = save("second-try", ["attributes.reagent_names"])
    assert result["ready"]
    projected, _ = read_published_projection(scope, result["publication"], finalized=True)
    assert sorted(finding.field_ref.field_path for finding in projected.validation_findings
                  if finding.code == "validation_review.assessment_unresolved") == [
        "attributes.detail_associated_gene", "attributes.reagent_names", "attributes.reagent_names"]


def test_a_malformed_path_is_named_and_the_other_entries_are_still_checked(scope):
    odd = _object(0, ["e1"])
    odd = odd.model_copy(update={"payload": {**odd.payload, "attributes": {**odd.payload["attributes"],
                                                                           "odd key": "unaddressable"}}})
    _, _, episode, _, checkpoint = _build(scope, [odd, _object(1, ["e1"])], [_evidence("e1")])
    page = reads.review_packet(scope, episode)
    first, second = (_handles(record)[0] for record in page["records"])
    decided = [
        {"handle": first, "action": "accept", "reason": "Supported.",
         "unresolved_fields": [{"field_path": "attributes source", "explanation": "Space in the path."}]},
        {"handle": second, "action": "accept", "reason": "Supported.",
         "unresolved_fields": [{"field_path": "missing", "explanation": "Wrong path."}]},
    ]
    with pytest.raises(ValueError) as caught:
        decisions.record_packet_decisions(ReviewContext(scope, episode, "fixture-reviewer"),
            checkpoint_request=checkpoint(), operation_id="malformed", packet_id=page["packet_id"],
            packet_decisions=decided)
    message = str(caught.value)
    assert f'{first}: unresolved field "attributes source" is not a valid field path:' in message
    assert f'{second}: unresolved field "missing" does not exist' in message
    assert "odd key" not in message, "fields no path can name are not offered"
    assert message.count("attributes.reagent_names") == 2, "each record's fields are listed once"


def test_explicit_entries_sharing_a_record_list_its_fields_once_and_long_lists_are_cut(scope, monkeypatch):
    envelope, _, episode, baseline, checkpoint = _build(scope, [_object(0, ["e1"])], [_evidence("e1")])
    target = _targets(envelope)[0]
    entry = {"target_id": target, "decision_unit_id": target, "expected_version": 0, "action": "accept",
             "reason": "Supported.", "extraction_ref": baseline,
             "unresolved_fields": [{"field_path": "nope", "explanation": "Wrong path."}]}
    context = ReviewContext(scope, episode, "fixture-reviewer")
    with pytest.raises(ValueError) as caught:
        decisions.record_decisions(context, checkpoint_request=checkpoint(), operation_id="shared",
                                   entries=[entry, {**entry, "action": "accept"}])
    message = str(caught.value)
    assert 'entry 1: unresolved field "nope"' in message and 'entry 2: unresolved field "nope"' in message
    assert "Fields that can be assessed for entry 1 and entry 2:" in message
    assert message.count("attributes.reagent_names") == 1 and target not in message
    monkeypatch.setenv("VALIDATION_REVIEW_PACKET_BYTES", "260")
    with pytest.raises(ValueError, match="more not listed; this message was cut") as cut:
        decisions.record_decisions(context, checkpoint_request=checkpoint(), operation_id="cut", entries=[entry])
    assert len(str(cut.value)) <= 260


def test_the_curator_api_keeps_a_conflict_for_a_missing_field_and_names_entries_by_position(scope):
    from fastapi import HTTPException
    from src.api.curation_workspace import _workspace_review_error

    envelope, _, episode, baseline, checkpoint = _build(scope, [_object(0, ["e1"])], [_evidence("e1")])
    target = _targets(envelope)[0]
    entry = {"target_id": target, "decision_unit_id": target, "expected_version": 0, "action": "accept",
             "reason": "Supported.", "extraction_ref": baseline,
             "unresolved_fields": [{"field_path": "missing", "explanation": "Wrong path."}]}
    with pytest.raises(store.HistoryConflict) as refused:
        decisions.record_decisions(ReviewContext(scope, episode, "fixture-reviewer"),
                                   checkpoint_request=checkpoint(), operation_id="curator", entries=[entry])
    with pytest.raises(HTTPException) as response:
        _workspace_review_error(refused.value)
    assert response.value.status_code == 409
    assert 'entry 1: unresolved field "missing" does not exist' in response.value.detail
    assert target not in response.value.detail


def test_handles_are_valid_only_with_their_own_packet_and_round(scope):
    envelope, _, episode, _, checkpoint = _build(scope, [_object(0, ["e1"])], [_evidence("e1")])
    other_envelope, _, other_episode, _, _ = _build(scope, [_object(1, ["e1"])], [_evidence("e1")])
    page = reads.review_packet(scope, episode)
    context = ReviewContext(scope, episode, "fixture-reviewer")
    with pytest.raises(ValueError, match="D9: Unknown handle"):
        decisions.record_packet_decisions(context, checkpoint_request=checkpoint(), operation_id="unknown",
            packet_id=page["packet_id"], packet_decisions=_decide(["D9"]))
    other = reads.review_packet(scope, other_episode)
    with pytest.raises(store.HistoryConflict):
        decisions.record_packet_decisions(context, checkpoint_request=checkpoint(), operation_id="foreign",
            packet_id=other["packet_id"], packet_decisions=_decide(["D1"]))
    with pytest.raises(ValueError, match="packet_id it belongs to"):
        reads.review_packet(scope, episode, page=2)
    with pytest.raises(ValueError, match="1 page"):
        reads.review_packet(scope, episode, packet_id=page["packet_id"], page=2)
    with store.SessionLocal() as db:
        target = _targets(envelope)[0]
        assert store._current(db, envelope.metadata["validation_history"]["artifact_id"], target)["version"] == 0


@pytest.mark.parametrize("scenario", ["component"], indirect=True)
def test_component_answer_has_one_handle_per_covered_component(scope, scenario):
    x = scenario
    sid = admission.create_scope(scope, operation_id=uuid4().hex, kind="document_run", targets=1, episodes=1)
    admission.attach_episode(scope, artifact_id=x.aid, episode_id=x.episode, scope_id=sid)
    completion = x.propose("ECO:VALIDATED")
    attempt = store.read_fact(scope, x.aid, store.read_fact(scope, x.aid, completion)["parent_id"])
    parent = attempt["content"]["input"]["target_id"]
    child, = attempt["content"]["coverage"]
    page = reads.review_packet(scope, x.episode)
    answer, = (entry for record in page["records"] for entry in record["review"])
    assert answer["review"] == "validator_answer" and answer["values"] == {"curie": "ECO:VALIDATED"}
    assert set(answer["handles"]) == {"identity"} and "handle" not in answer
    checkpoint = DomainEnvelopeCheckpointRequest(project_key="fixture", envelope=x.env, expected_revision=x.revision,
        document_id=scope.document_id, adapter_key="fixture", source_extraction_result_id=x.source)
    result = decisions.record_packet_decisions(ReviewContext(scope, x.episode, "supervisor"),
        checkpoint_request=checkpoint, operation_id="component", packet_id=page["packet_id"],
        packet_decisions=_decide([answer["handles"]["identity"]]))
    assert result["applied"] == [{"decision_unit_id": child, "action": "accept",
                                  "handle": answer["handles"]["identity"], "target_id": parent}]
    assert result["ready"]


@pytest.mark.parametrize("scenario", ["component"], indirect=True)
def test_a_component_answer_shows_the_earlier_decision_for_its_component(scope, scenario):
    x = scenario
    sid = admission.create_scope(scope, operation_id=uuid4().hex, kind="document_run", targets=1, episodes=1)
    admission.attach_episode(scope, artifact_id=x.aid, episode_id=x.episode, scope_id=sid)
    x.propose("ECO:FIRST")
    first = reads.review_packet(scope, x.episode)
    answer, = (entry for record in first["records"] for entry in record["review"])
    assert "earlier_decisions" not in answer
    checkpoint = DomainEnvelopeCheckpointRequest(project_key="fixture", envelope=x.env, expected_revision=x.revision,
        document_id=scope.document_id, adapter_key="fixture", source_extraction_result_id=x.source)
    decisions.record_packet_decisions(ReviewContext(scope, x.episode, "supervisor"), checkpoint_request=checkpoint,
        operation_id="reject-first", packet_id=first["packet_id"],
        packet_decisions=_decide([answer["handles"]["identity"]], "reject"))
    x.propose("ECO:SECOND")
    second = reads.review_packet(scope, x.episode)
    newer, = (entry for record in second["records"] for entry in record["review"])
    assert newer["values"] == {"curie": "ECO:SECOND"} and set(newer["handles"]) == {"identity"}
    assert newer["earlier_decisions"] == {"identity": "reject"}


@pytest.mark.parametrize("planned", ["optional-absence", "optional-composite-absence"], indirect=True)
def test_optional_absence_is_decided_by_handle_with_an_explicit_assessment(planned):
    item = planned
    page = reads.review_packet(item.scope, item.ep)
    entry, = (entry for record in page["records"] for entry in record["review"])
    assert entry["review"] == "optional_input_absent" and entry["absent_inputs"] == ["query"]
    handle, = _handles(page["records"][0])
    context = ReviewContext(item.scope, item.ep, "supervisor")
    checkpoint = checkpoint_provider(item)
    with pytest.raises(ValueError, match=handle):
        decisions.record_packet_decisions(context, checkpoint_request=checkpoint(), operation_id="no-assessment",
            packet_id=page["packet_id"], packet_decisions=_decide([handle]))
    result = decisions.record_packet_decisions(context, checkpoint_request=checkpoint(), operation_id="assessed",
        packet_id=page["packet_id"], packet_decisions=_decide([handle], absence_assessment="omission_supported"))
    assert result["ready"]
    assert checkpoint().envelope.extracted_objects[0].payload == {"optional": {}}


@pytest.mark.asyncio
@pytest.mark.parametrize("planned", ["unsupported-preflight"], indirect=True)
async def test_unsupported_lookup_by_handle_requires_an_unresolved_assessment(planned):
    item = planned
    await item.adapter.initial()
    page = reads.review_packet(item.scope, item.ep)
    entries = [entry for record in page["records"] for entry in record["review"]]
    assert entries and all(entry["review"] == "lookup_not_supported" for entry in entries)
    assert all("FIXTURE_UNSUPPORTED" in entry["explanation"] for entry in entries)
    handles = [handle for record in page["records"] for handle in _handles(record)]
    context = ReviewContext(item.scope, item.ep, "supervisor")
    checkpoint = checkpoint_provider(item)
    with pytest.raises(store.HistoryConflict, match="unresolved assessment") as caught:
        decisions.record_packet_decisions(context, checkpoint_request=checkpoint(), operation_id="claims-omission",
            packet_id=page["packet_id"], packet_decisions=_decide(handles, absence_assessment="omission_supported"))
    assert handles[0] in str(caught.value)
    result = decisions.record_packet_decisions(context, checkpoint_request=checkpoint(), operation_id="unresolved",
        packet_id=page["packet_id"], packet_decisions=_decide(handles, absence_assessment="unresolved"))
    assert result["ready"]


@pytest.mark.asyncio
@pytest.mark.parametrize("planned", ["three"], indirect=True)
async def test_a_retired_record_leaves_the_packet_and_its_old_handles_conflict(planned, monkeypatch):
    from tests.integration.persistence.test_record_retirement import proposal

    item = planned
    _answers(monkeypatch, lambda request: {**_result_payload(request, resolved_values={"curie": "ECO:X"}),
                                           "resolved_objects": []})
    await item.adapter.initial()
    before = reads.review_packet(item.scope, item.ep)
    context, ref, retired = proposal(item)
    checkpoint = checkpoint_provider(item)
    with store.SessionLocal() as db:
        digest = selections.source_state_digest(db, item.aid)
    outcome = await item.adapter.select_source(context, checkpoint, operation_id="retire", contribution_ref=ref,
        retirement_decision="approve", expected_source_sha=digest, reason="Outside the requested task")
    assert outcome["retired"] is True
    after = reads.review_packet(item.scope, item.ep)
    assert retired in {record["target_id"] for record in before["records"]}
    assert retired not in {record["target_id"] for record in after["records"]}
    old = [handle for record in before["records"] if record["target_id"] == retired for handle in _handles(record)]
    with pytest.raises(store.HistoryConflict, match="outside episode coverage"):
        decisions.record_packet_decisions(context, checkpoint_request=checkpoint(), operation_id="stale-retired",
            packet_id=before["packet_id"], packet_decisions=_decide(old))
    survivors = [handle for record in after["records"] for handle in _handles(record)]
    assert decisions.record_packet_decisions(context, checkpoint_request=checkpoint(), operation_id="survivors",
        packet_id=after["packet_id"], packet_decisions=_decide(survivors))["ready"]


def test_pages_hold_whole_records_and_carry_each_quote_once(scope, monkeypatch):
    monkeypatch.setenv("VALIDATION_REVIEW_PACKET_BYTES", "6000")
    quotes = [_evidence(f"e{index}", f"{index} " + "q" * 900) for index in range(6)]
    objects = [_object(index, [f"e{index // 2}"]) for index in range(12)]
    envelope, _, episode, _, _ = _build(scope, objects, quotes)
    targets = _targets(envelope)
    first = reads.review_packet(scope, episode)
    assert first["pages"] > 1
    seen, quoted, number = [], [], 1
    while True:
        page = reads.review_packet(scope, episode, packet_id=first["packet_id"], page=number)
        assert len(json.dumps(page, ensure_ascii=True, separators=(",", ":")).encode()) <= 6000
        seen += [record["target_id"] for record in page["records"]]
        quoted += list(page["evidence"])
        for record in page["records"]:
            assert set(record["evidence"]) <= set(quoted), "evidence arrives no later than its first record"
        if page["next_page"] is None:
            break
        number = page["next_page"]
    assert seen == targets
    assert len(quoted) == len(set(quoted)) == 6


def test_a_record_too_large_for_any_page_is_named_with_its_handles(scope, monkeypatch):
    monkeypatch.setenv("VALIDATION_REVIEW_PACKET_BYTES", "4096")
    objects = [_object(0, ["big"]), _object(1, ["small"])]
    envelope, _, episode, _, _ = _build(scope, objects, [_evidence("big", "b" * 6000), _evidence("small")])
    targets = _targets(envelope)
    page = reads.review_packet(scope, episode)
    # The stub keeps the record's follow-up flag; this fixture's saved extractor has none.
    assert page["too_large"] == [{"record": "R1", "target_id": targets[0], "follow_up_available": False,
                                  "handles": ["D1"]}]
    assert [record["target_id"] for record in page["records"]] == [targets[1]]


@pytest.mark.asyncio
async def test_real_sdk_loop_reads_the_packet_once_and_saves_once_with_progress(scope, monkeypatch):
    from src.lib.openai_agents import streaming_tools

    for key in ("REVIEWER_TURNS", "REVIEWER_TOOLS", "DEADLINE_SECONDS"):
        monkeypatch.delenv("VALIDATION_REVIEW_" + key, raising=False)
    monkeypatch.setenv("AGENT_MODEL_CAPACITY_OVERRIDES_JSON", '{"gpt-test":{"context_window_tokens":1050000,"max_input_tokens":922000,"max_output_tokens":128000}}')
    monkeypatch.setattr("src.lib.openai_agents.context_capacity.NativeCapacityRequest.count", AsyncMock(return_value=1000))
    monkeypatch.setattr(streaming_tools, "write_stream_event", lambda *args, **kwargs: None)
    objects = [_object(index, ["e1"]) for index in range(20)]
    envelope, _, episode, _, checkpoint = _build(scope, objects, [_evidence("e1")])
    context = ReviewContext(scope, episode, "fixture-reviewer")
    pinned = reads.review_packet(scope, episode)

    def response(index, name, arguments):
        return ModelResponse(output=[ResponseFunctionToolCall(id=f"fc-{index}", call_id=f"call-{index}", name=name,
            arguments=json.dumps(arguments), type="function_call")], usage=_usage(), response_id=f"response-{index}")

    handles = [handle for record in pinned["records"] for handle in _handles(record)]
    model = FakeResponsesHTTPModel([
        response(0, "read_review_packet", {"packet_id": None, "page": None}),
        response(1, "record_validation_review", {"operation_id": "packet-save", "packet_id": pinned["packet_id"],
                                                 "packet_decisions": _decide(handles), "entries": None}),
        _final("Review decisions recorded."),
    ])
    model._client = model._client.copy(max_retries=0)
    agent = Agent(name="Fixture Reviewer", model=model, instructions="Review saved records.",
                  tools=create_tools(context, checkpoint_provider=checkpoint))
    from src.lib.observability.cost_context import attach_agent_cost_identity
    attach_agent_cost_identity(agent, {"agent_id": "fixture-reviewer", "agent_role": "supervisor"})
    events = []
    streaming_tools.set_live_event_list(events)
    try:
        result = await reviewer.run(context, agent, input="Review every saved record.",
                                    run_config=RunConfig(tracing_disabled=True))
    finally:
        streaming_tools.set_live_event_list(None)
    assert result["review"]["ready"], result
    assert len(model.calls) == 2, "one packet read and one save; the run ends once review is ready"
    lines = [event["details"]["friendlyName"] for event in events
             if event.get("details", {}).get("reviewPhase") == "supervisor_review"]
    assert lines == ["Supervisor review: reviewing 20 records", "Supervisor review: saving 20 decisions"]

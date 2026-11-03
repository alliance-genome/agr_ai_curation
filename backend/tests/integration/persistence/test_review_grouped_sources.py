"""Grouped review reads keep exact history while sending shared evidence only once."""
import json
from dataclasses import replace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from agents import Agent, ModelResponse, RunConfig
from openai.types.responses import ResponseFunctionToolCall

from src.lib.curation_workspace.models import DomainEnvelopeModel
from src.lib.domain_envelopes.persistence import DomainEnvelopeCheckpointRequest, load_domain_envelope
from src.lib.validation_history import runtime, selections, store
from src.lib.validation_review import admission, decisions, reads, record_source, reviewer
from src.lib.validation_review.decisions import ReviewContext
from src.lib.validation_review.tools import create_tools
from src.schemas.domain_envelope import CuratableObjectEnvelope, DomainEnvelope
from tests.cancellation_test_support import install_cancel_reader
from tests.integration.persistence.test_validation_history import migrated as migrated, scope as scope
from tests.unit.lib.openai_agents.test_model_request_measurement import FakeResponsesHTTPModel, _final, _usage

QUOTE = "Appendix row: SS63295 (VT043662-p65ADZp in attP40; VT047418-ZpGdbd in attP2) | RRID:BDSC_88290"


def _evidence(identity, quote=QUOTE):
    return {"evidence_record_id": identity, "verified_quote": quote, "section": "Appendix 1", "page": 1,
            "chunk_id": "chunk", "agent_note": None,
            "source_fragments": [{"text": quote, "chunk_id": "chunk", "page": 1, "bbox": None}]}


def _object(index, evidence_ids, *, source="RRID:BDSC_1"):
    return CuratableObjectEnvelope(
        object_type="generic_object", pending_ref_id=f"generic-object-{index}", object_role="generic_object",
        model_ref="GenericExtractedObjectPayload", evidence_record_ids=list(evidence_ids),
        payload={"attributes": {"reagent_names": f"VT{index}", "source": source, "absent_detail": None},
                 "class_key": "generic:generic_object", "label": f"VT{index}", "rationale": "Labels a neuron.",
                 "classification_notes": [], "semantic_class": "genetic_reagent_inventory_item"})


def _build(scope, objects, evidence):
    envelope = DomainEnvelope(envelope_id=uuid4().hex, domain_pack_id="generic", extracted_objects=objects,
                              metadata={"evidence_records": evidence})
    envelope = runtime.capture_extraction(scope=scope, producer_id=uuid4().hex, envelope=envelope,
                                          typed=envelope.model_dump(mode="json"), provenance={})
    artifact_id = envelope.metadata["validation_history"]["artifact_id"]
    envelope = selections.declare_envelope_episode(runtime.CapturedArtifact(scope, artifact_id), envelope, [])
    episode_id = envelope.metadata["review_episode_id"]
    scope_id = admission.create_scope(scope, operation_id=uuid4().hex, kind="document_run",
                                      targets=len(objects), episodes=1)
    admission.attach_episode(scope, artifact_id=artifact_id, episode_id=episode_id, scope_id=scope_id)
    with store.SessionLocal() as db:
        baseline = store._artifact(db, artifact_id, scope).baseline_sha
    request = DomainEnvelopeCheckpointRequest(project_key="fixture", envelope=envelope, expected_revision=0,
                                              document_id=scope.document_id, adapter_key="fixture",
                                              source_extraction_result_id=uuid4())

    def checkpoint():
        with store.SessionLocal() as db:
            row = db.get(DomainEnvelopeModel, envelope.envelope_id)
            return request if row is None else replace(
                request, envelope=load_domain_envelope(db, envelope.envelope_id), expected_revision=row.revision)

    return envelope, artifact_id, episode_id, baseline, checkpoint


def _targets(envelope):
    return [obj.metadata["validation_history"]["target_id"] for obj in envelope.extracted_objects]


@pytest.fixture(autouse=True)
def cancel_reader(monkeypatch):
    install_cancel_reader(monkeypatch, AsyncMock(return_value=False))


def test_group_read_is_inline_sends_shared_evidence_once_and_matches_exact_history(scope):
    objects = [_object(0, ["e1", "e2"]), _object(1, ["e1"]), _object(2, ["e2"])]
    envelope, _, episode, _, _ = _build(scope, objects, [_evidence("e1"), _evidence("e2", QUOTE + " second")])
    targets = _targets(envelope)
    page = record_source.group_snapshot(scope, episode, target_ids=targets)
    assert [item["target_id"] for item in page["records"]] == targets
    assert page["not_returned"] == [] and page["unavailable"] == []
    assert set(page["evidence"]) == {"e1", "e2"}
    assert json.dumps(page).count(QUOTE + " second") == 1, "shared evidence and its fragments appear once"
    assert "agent_note" not in page["evidence"]["e1"]
    assert page["evidence"]["e1"]["source_fragments"][0]["chunk_id"] == "chunk"
    for item, obj in zip(page["records"], objects, strict=True):
        assert item["record"]["payload"] == obj.payload, "scientific values are never compacted"
        assert item["record"]["payload"]["attributes"]["absent_detail"] is None
        assert "schema_ref" not in item["record"] and "model_ref" not in item["record"]
        exact = json.loads(reads.read(scope, episode, **item["ref"])["content"])
        assert exact["record"]["model_ref"] == "GenericExtractedObjectPayload"
        assert [e["evidence_record_id"] for e in exact["evidence_records"]] == item["record"]["evidence_record_ids"]
    # The same immutable reference as the single-record tool.
    assert page["records"][0]["ref"] == record_source.snapshot(scope, episode, target_id=targets[0])


def test_conflicting_evidence_with_one_identity_stays_with_its_own_record(scope):
    objects = [_object(0, ["e1"]), _object(1, ["e1"])]
    payload_evidence = {**_evidence("e1", "object level text"), "section": "Results"}
    objects[1] = objects[1].model_copy(update={"metadata": {"evidence_records": [payload_evidence]}})
    envelope, _, episode, _, _ = _build(scope, objects, [_evidence("e1")])
    targets = _targets(envelope)
    page = record_source.group_snapshot(scope, episode, target_ids=targets)
    accounted = [item["target_id"] for item in page["records"]] + [item["target_id"] for item in page["unavailable"]]
    assert sorted(accounted) == sorted(targets)
    assert page["records"][0]["target_id"] == targets[0]
    assert page["evidence"]["e1"]["verified_quote"] == QUOTE


def test_group_limit_and_packet_size_return_the_rest_without_truncating_a_record(scope, monkeypatch):
    monkeypatch.setenv("VALIDATION_REVIEW_SOURCE_GROUP_RECORDS", "2")
    objects = [_object(index, ["e1"]) for index in range(3)]
    envelope, _, episode, _, _ = _build(scope, objects, [_evidence("e1")])
    targets = _targets(envelope)
    page = record_source.group_snapshot(scope, episode, target_ids=[*targets, targets[0]])
    assert [item["target_id"] for item in page["records"]] == targets[:2]
    assert page["not_returned"] == targets[2:]
    assert page["group_limit"] == 2


def test_packet_limit_keeps_every_response_within_bounds(scope, monkeypatch):
    monkeypatch.setenv("VALIDATION_REVIEW_PACKET_BYTES", "6000")
    long_quote = "q" * 1500
    objects = [_object(index, [f"e{index}"]) for index in range(4)]
    envelope, _, episode, _, _ = _build(scope, objects, [_evidence(f"e{index}", long_quote) for index in range(4)])
    targets, seen, remaining = _targets(envelope), [], None
    remaining = targets
    for _ in range(6):
        page = record_source.group_snapshot(scope, episode, target_ids=remaining)
        assert len(json.dumps(page, ensure_ascii=True, separators=(",", ":")).encode()) <= 6000
        seen += [item["target_id"] for item in page["records"]]
        remaining = page["not_returned"]
        if not remaining:
            break
    assert seen == targets


def test_one_record_too_large_for_a_packet_returns_its_exact_reference(scope, monkeypatch):
    monkeypatch.setenv("VALIDATION_REVIEW_PACKET_BYTES", "4096")
    objects = [_object(0, ["big"]), _object(1, ["small"])]
    envelope, _, episode, _, _ = _build(scope, objects, [_evidence("big", "β" * 6000), _evidence("small")])
    targets = _targets(envelope)
    page = record_source.group_snapshot(scope, episode, target_ids=targets)
    assert [item["target_id"] for item in page["too_large"]] == [targets[0]]
    assert page["too_large"][0]["ref"] == record_source.snapshot(scope, episode, target_id=targets[0])
    assert [item["target_id"] for item in page["records"]] == [targets[1]]


def test_one_unreadable_target_does_not_block_the_others(scope):
    objects = [_object(0, ["e1"]), _object(1, ["e1"])]
    envelope, _, episode, _, _ = _build(scope, objects, [_evidence("e1")])
    targets = _targets(envelope)
    page = record_source.group_snapshot(scope, episode, target_ids=["not-a-target", targets[1]])
    assert [item["target_id"] for item in page["records"]] == [targets[1]]
    assert page["unavailable"][0]["target_id"] == "not-a-target"


def test_outstanding_rows_are_ordered_by_shared_evidence_and_pages_cover_every_row(scope, monkeypatch):
    objects = [_object(0, ["a"]), _object(1, ["b"]), _object(2, ["a"]), _object(3, ["b"]), _object(4, ["a"])]
    envelope, _, episode, _, _ = _build(scope, objects, [_evidence("a"), _evidence("b", QUOTE + " b")])
    targets = _targets(envelope)
    groups = {targets[0]: "a", targets[1]: "b", targets[2]: "a", targets[3]: "b", targets[4]: "a"}
    rows = reads.outstanding(scope, episode)["rows"]
    keys = [row["evidence_group"] for row in rows]
    assert len(set(keys)) == 2
    assert keys == sorted(keys)
    assert all(len({groups[r["target_id"]] for r in rows if r["evidence_group"] == key}) == 1 for key in keys)
    monkeypatch.setenv("VALIDATION_REVIEW_PACKET_BYTES", "1800")
    envelope, _, episode, _, _ = _build(scope, objects, [_evidence("a"), _evidence("b", QUOTE + " b")])
    seen, cursor = [], None
    while True:
        page = reads.outstanding(scope, episode, cursor=cursor)
        seen += [row["target_id"] for row in page["rows"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert sorted(seen) == sorted(_targets(envelope))


def test_a_stale_entry_rejects_the_whole_group_and_names_itself(scope):
    objects = [_object(index, ["e1"]) for index in range(3)]
    envelope, artifact, episode, baseline, checkpoint = _build(scope, objects, [_evidence("e1")])
    targets = _targets(envelope)
    context = ReviewContext(scope, episode, "fixture-reviewer")

    def entry(target, version=0):
        return {"target_id": target, "decision_unit_id": target, "expected_version": version, "action": "accept",
                "reason": f"Evidence supports {target}.", "extraction_ref": baseline}

    decisions.record_decisions(context, checkpoint_request=checkpoint(), operation_id="first", entries=[entry(targets[0])])
    with pytest.raises(store.HistoryConflict, match=targets[0]):
        decisions.record_decisions(context, checkpoint_request=checkpoint(), operation_id="group",
                                   entries=[entry(target) for target in targets])
    manifest = selections.read_manifest(scope, artifact, episode_id=episode)
    assert {unit["target_id"]: unit["version"] for unit in manifest["units"]} == {
        targets[0]: 1, targets[1]: 0, targets[2]: 0}


@pytest.mark.asyncio
async def test_real_sdk_loop_reviews_a_group_in_one_read_and_one_save(scope, monkeypatch):
    for key in ("REVIEWER_TURNS", "REVIEWER_TOOLS", "DEADLINE_SECONDS"):
        monkeypatch.delenv("VALIDATION_REVIEW_" + key, raising=False)
    monkeypatch.setenv("AGENT_MODEL_CAPACITY_OVERRIDES_JSON", '{"gpt-test":{"context_window_tokens":1050000,"max_input_tokens":922000,"max_output_tokens":128000}}')
    monkeypatch.setattr("src.lib.openai_agents.context_capacity.NativeCapacityRequest.count", AsyncMock(return_value=1000))
    objects = [_object(index, ["e1"]) for index in range(20)]
    envelope, _, episode, baseline, checkpoint = _build(scope, objects, [_evidence("e1")])
    targets = _targets(envelope)
    context = ReviewContext(scope, episode, "fixture-reviewer")

    def response(index, name, arguments):
        return ModelResponse(output=[ResponseFunctionToolCall(id=f"fc-{index}", call_id=f"call-{index}", name=name,
            arguments=json.dumps(arguments), type="function_call")], usage=_usage(), response_id=f"response-{index}")

    entries = [{"target_id": target, "decision_unit_id": target, "expected_version": 0, "action": "accept",
                "reason": f"Row {position} is supported by the Appendix.", "extraction_ref": baseline}
               for position, target in enumerate(targets)]
    model = FakeResponsesHTTPModel([
        response(0, "get_validation_record_sources", {"target_ids": targets}),
        response(1, "record_validation_review", {"operation_id": "group-1", "entries": entries}),
        _final("Review decisions recorded."),
    ])
    model._client = model._client.copy(max_retries=0)
    agent = Agent(name="Fixture Reviewer", model=model, instructions="Review saved records.",
                  tools=create_tools(context, checkpoint_provider=checkpoint))
    from src.lib.observability.cost_context import attach_agent_cost_identity
    attach_agent_cost_identity(agent, {"agent_id": "fixture-reviewer", "agent_role": "supervisor"})
    result = await reviewer.run(context, agent, input="Review every saved record.",
                                run_config=RunConfig(tracing_disabled=True))
    assert result["review"]["ready"], result
    assert len(model.calls) <= 3, "one read and one save cover the whole group"


def test_inline_record_keeps_draft_definitions_and_non_evidence_references(scope):
    objects = [_object(0, ["e1"]), _object(1, ["e1"])]
    objects[0] = objects[0].model_copy(update={
        "definition_state": "stable", "definition_notes": ["Repeated note."],
        "metadata_refs": [{"metadata_path": "evidence_records[0]", "role": "verified_evidence"}]})
    objects[1] = objects[1].model_copy(update={
        "definition_state": "draft", "definition_notes": ["Definition still under review."],
        "metadata_refs": [{"metadata_path": "evidence_records[0]", "role": "verified_evidence"},
                          {"metadata_path": "raw_mentions[0]", "role": "source_mention", "description": "Ambiguous mention"}]})
    envelope, _, episode, _, _ = _build(scope, objects, [_evidence("e1")])
    stable, draft = record_source.group_snapshot(scope, episode, target_ids=_targets(envelope))["records"]
    assert not {"definition_state", "definition_notes", "metadata_refs"} & set(stable["record"])
    assert draft["record"]["definition_notes"] == ["Definition still under review."]
    assert [ref["role"] for ref in draft["record"]["metadata_refs"]] == ["source_mention"]


def test_listed_problems_never_push_a_page_over_the_packet_limit(scope, monkeypatch):
    objects = [_object(0, ["e1"])]
    monkeypatch.setenv("VALIDATION_REVIEW_PACKET_BYTES", "4096")
    envelope, _, episode, _, _ = _build(scope, objects, [_evidence("e1")])
    target = _targets(envelope)[0]
    missing = [f"missing-target-{index}" for index in range(60)]
    seen, remaining = [], [target, *missing]
    for _ in range(40):
        page = record_source.group_snapshot(scope, episode, target_ids=remaining)
        assert len(json.dumps(page, ensure_ascii=True, separators=(",", ":")).encode()) <= 4096
        seen += [item["target_id"] for item in page["records"]] + [item["target_id"] for item in page["unavailable"]]
        remaining = page["not_returned"]
        if not remaining:
            break
    assert remaining == [] and sorted(seen) == sorted([target, *missing])


def test_an_oversized_id_list_is_refused_rather_than_returned_unchanged(scope, monkeypatch):
    monkeypatch.setenv("VALIDATION_REVIEW_PACKET_BYTES", "2000")
    envelope, _, episode, _, _ = _build(scope, [_object(0, ["e1"])], [_evidence("e1")])
    target = _targets(envelope)[0]
    with pytest.raises(ValueError, match="request fewer records"):
        record_source.group_snapshot(scope, episode, target_ids=[target, *[f"missing-{n}" for n in range(200)]])
    assert [item["target_id"] for item in record_source.group_snapshot(scope, episode, target_ids=[target])["records"]] == [target]


def test_an_earlier_unreadable_id_does_not_discard_a_later_record_that_fits_alone(scope, monkeypatch):
    envelope, _, episode, _, _ = _build(scope, [_object(0, ["e1"])], [_evidence("e1", "q" * 2000)])
    target = _targets(envelope)[0]
    alone = len(json.dumps(record_source.group_snapshot(scope, episode, target_ids=[target]),
                           ensure_ascii=True, separators=(",", ":")).encode())
    monkeypatch.setenv("VALIDATION_REVIEW_PACKET_BYTES", str(alone + 20))
    envelope, _, episode, _, _ = _build(scope, [_object(0, ["e1"])], [_evidence("e1", "q" * 2000)])
    target = _targets(envelope)[0]
    first = record_source.group_snapshot(scope, episode, target_ids=["missing-record-identifier", target])
    assert first["unavailable"][0]["target_id"] == "missing-record-identifier"
    assert first["not_returned"] == [target]
    assert [item["target_id"] for item in record_source.group_snapshot(scope, episode, target_ids=first["not_returned"])["records"]] == [target]


def test_outstanding_page_reports_the_group_limit(scope, monkeypatch):
    monkeypatch.setenv("VALIDATION_REVIEW_SOURCE_GROUP_RECORDS", "7")
    envelope, _, episode, _, _ = _build(scope, [_object(0, ["e1"])], [_evidence("e1")])
    assert reads.outstanding(scope, episode)["group_limit"] == 7


def test_authorization_failures_keep_their_type_and_are_not_rewritten(scope, monkeypatch):
    from src.lib.validation_review import source_inputs

    envelope, _, episode, baseline, checkpoint = _build(scope, [_object(0, ["e1"])], [_evidence("e1")])
    target = _targets(envelope)[0]
    denied = store.HistoryAccessError("Validation history artifact is not accessible")

    def refuse(*args, **kwargs):
        raise denied

    monkeypatch.setattr(source_inputs, "require_applicability", refuse)
    with pytest.raises(store.HistoryAccessError) as caught:
        decisions.record_decisions(ReviewContext(scope, episode, "fixture-reviewer"), checkpoint_request=checkpoint(),
            operation_id="denied", entries=[{"target_id": target, "decision_unit_id": target, "expected_version": 0,
                "action": "accept", "reason": "Supported.", "absence_assessment": "unresolved",
                "applicability_ref": {"fact_id": "fact", "sha256": "digest"}}])
    assert caught.value is denied


def test_decision_conflicts_name_the_record_at_fault(scope):
    objects = [_object(index, ["e1"]) for index in range(2)]
    envelope, artifact, episode, baseline, checkpoint = _build(scope, objects, [_evidence("e1")])
    targets = _targets(envelope)
    context = ReviewContext(scope, episode, "fixture-reviewer")
    good = {"target_id": targets[0], "decision_unit_id": targets[0], "expected_version": 0, "action": "accept",
            "reason": "Supported.", "extraction_ref": baseline}
    wrong_baseline = {**good, "target_id": targets[1], "decision_unit_id": targets[1], "extraction_ref": "not-the-baseline"}
    with pytest.raises(store.HistoryConflict, match=targets[1]):
        decisions.record_decisions(context, checkpoint_request=checkpoint(), operation_id="bad-ref", entries=[good, wrong_baseline])
    missing_reason = {**good, "reason": " "}
    with pytest.raises(ValueError, match=targets[0]):
        decisions.record_decisions(context, checkpoint_request=checkpoint(), operation_id="bad-reason", entries=[missing_reason])
    assert {unit["version"] for unit in selections.read_manifest(scope, artifact, episode_id=episode)["units"]} == {0}

"""Scoped source access preserves full immutable originals and exact evidence."""

import json
from uuid import uuid4

import pytest

from src.lib.validation_history import store as h
from src.lib.validation_review import admission, reads, record_source
from tests.integration.persistence.test_validation_history import scope, migrated, decision
from tests.integration.persistence.test_selected_value_authority import episode


def setup_record(scope, *, historical=False, evidence=None, origin=None):
    record = {"object_id": "one", "object_type": "fixture", "payload": {"term": "α"},
              "evidence_record_ids": ["e1"], "object_refs": []}
    evidence = evidence if evidence is not None else [
        {"evidence_record_id": "e1", "quote": "β source", "page_number": 2}]
    normalized = {"extracted_objects": [record, {
        "object_id": "unrelated", "payload": {"term": "other"}, "evidence_record_ids": []}],
        "metadata": {"evidence_records": evidence},
        "validation_findings": [{"historical": "large unrelated history" * 2000}]}
    aid = h.capture_baseline(scope, producer_id=uuid4().hex,
        subject_kind="historical" if historical else "extracted",
        typed=None if historical else {"original": "α"}, normalized=normalized,
        provenance={"test": "source"})
    obj = h.register_target(scope, aid, identity="object:one", original={
        "object_ref": "one", "origin": origin if origin is not None else {"baseline_target": True}})
    field = h.register_target(scope, aid, identity="field:one", original={"initial_path": "term", "value": "α"}, parent_target_id=obj)
    binding = h.register_target(scope, aid, identity="binding:one", original={"binding_id": "fixture", "object_target": obj}, parent_target_id=field)
    ep = episode(scope, aid, binding)
    sid = admission.create_scope(scope, operation_id=uuid4().hex, kind="document_run", targets=1, episodes=1)
    admission.attach_episode(scope, artifact_id=aid, episode_id=ep, scope_id=sid)
    return aid, ep, obj, field, binding, record, normalized


@pytest.mark.parametrize("historical", [False, True])
def test_exact_scoped_source_and_full_baseline_both_remain_available(scope, historical):
    aid, ep, obj, field, binding, record, normalized = setup_record(scope, historical=historical)
    before = h.read_baseline(scope, aid)
    refs = [record_source.snapshot(scope, ep, target_id=tid) for tid in (obj, field, binding, obj)]
    assert all(ref == refs[0] for ref in refs)
    page = reads.read(scope, ep, **refs[0])
    body = json.loads(page["content"])
    assert page["next_offset"] is None
    assert body["record"] == record
    assert body["evidence_records"] == normalized["metadata"]["evidence_records"]
    assert body["subject_kind"] == ("historical" if historical else "extracted")
    assert body["original_available"] is (not historical)
    assert "validation_findings" not in body and "unrelated" not in page["content"]
    assert h.read_baseline(scope, aid) == before
    # The exact full historical findings remain accessible; no data was stripped.
    full, offset = "", 0
    while True:
        part = reads.read(scope, ep, **body["baseline"], offset=offset)
        full += part["content"]
        offset = part["next_offset"]
        if offset is None:
            break
    assert json.loads(full)["normalized"] == normalized


def test_large_unicode_evidence_is_paged_without_loss(scope, monkeypatch):
    monkeypatch.setenv("VALIDATION_REVIEW_PACKET_BYTES", "4096")
    evidence = [{"evidence_record_id": "e1", "quote": "β🧬" * 4000, "page_number": 2}]
    _, ep, obj, _, _, _, _ = setup_record(scope, evidence=evidence)
    ref = record_source.snapshot(scope, ep, target_id=obj)
    content, offset, pages = "", 0, 0
    while True:
        page = reads.read(scope, ep, **ref, offset=offset)
        assert reads._wire_size(page) <= 4096
        content += page["content"]
        pages += 1
        offset = page["next_offset"]
        if offset is None:
            break
    assert pages > 1
    assert json.loads(content)["evidence_records"] == evidence
    assert h.canonical(json.loads(content))[0] == ref["sha256"]


def test_source_read_does_not_cross_owner_or_artifact(scope):
    aid, ep, obj, _, _, _, _ = setup_record(scope)
    _, other_ep, other_obj, _, _, _, _ = setup_record(scope)
    wrong_owner = h.HistoryScope("someone-else", scope.document_id, scope.session_id)
    with pytest.raises(h.HistoryAccessError):
        record_source.snapshot(wrong_owner, ep, target_id=obj)
    with pytest.raises(h.HistoryConflict):
        record_source.snapshot(scope, ep, target_id=other_obj)
    ref = record_source.snapshot(scope, ep, target_id=obj)
    with pytest.raises(h.HistoryConflict):
        reads.read(scope, other_ep, **ref)
    with pytest.raises(h.HistoryConflict):
        reads.read(scope, ep, fact_id=ref["fact_id"], sha256="0" * 64)


@pytest.mark.parametrize("evidence,error", [
    ([], "missing"),
    ([{"evidence_record_id": "e1", "quote": "a"}, {"evidence_record_id": "e1", "quote": "b"}], "ambiguous"),
])
def test_missing_or_ambiguous_evidence_is_not_invented(scope, evidence, error):
    _, ep, obj, _, _, _, _ = setup_record(scope, evidence=evidence)
    with pytest.raises(h.HistoryConflict, match=error):
        record_source.snapshot(scope, ep, target_id=obj)


def test_contribution_target_cannot_masquerade_as_baseline_original(scope):
    _, ep, obj, _, _, _, _ = setup_record(scope, origin={"kind": "extraction_contribution", "baseline_target": False})
    with pytest.raises(h.HistoryConflict, match="no baseline original"):
        record_source.snapshot(scope, ep, target_id=obj)


def test_added_nested_field_cannot_return_its_parents_original_as_its_own(scope):
    aid, ep, obj, _, _, _, _ = setup_record(scope)
    field = h.register_target(scope, aid, identity="new-field", parent_target_id=obj,
        original={"initial_path": "new_field", "value": "later", "object_target": obj,
                  "origin": {"kind": "extraction_contribution", "contribution_id": "new", "predecessor_ids": []}})
    with pytest.raises(h.HistoryConflict, match="no baseline original"):
        record_source.snapshot(scope, ep, target_id=field)
    # Explicitly asking for the parent's original remains valid.
    assert record_source.snapshot(scope, ep, target_id=obj)["sha256"]


def test_repeated_source_reference_does_not_repeat_committed_audit_event(scope, monkeypatch):
    from src.lib.openai_agents import extraction_trace_events

    events = []
    monkeypatch.setattr(extraction_trace_events, "write_extraction_trace_event",
                        lambda **kwargs: events.append(kwargs))
    _, ep, obj, field, binding, _, _ = setup_record(scope)
    events.clear()
    refs = [record_source.snapshot(scope, ep, target_id=t) for t in (obj, field, binding, obj)]
    assert all(r == refs[0] for r in refs)
    assert [e["event_type"] for e in events] == ["validation_review.review_record_source"]


def test_retired_original_remains_readable_without_reactivation(scope):
    aid, ep, obj, _, _, record, _ = setup_record(scope)
    decision(scope, aid, obj, 0, "retire")
    ref = record_source.snapshot(scope, ep, target_id=obj)
    assert json.loads(reads.read(scope, ep, **ref)["content"])["record"] == record
    with h.SessionLocal() as db:
        with pytest.raises(h.HistoryConflict):
            h._require_active_target(db, aid, obj)

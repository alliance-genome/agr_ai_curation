"""One review save must not re-read an artifact's entire immutable history per field."""
import re
from collections import Counter
from contextlib import contextmanager
from dataclasses import replace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.engine import Engine

from src.lib.curation_workspace.models import DomainEnvelopeModel
from src.lib.domain_envelopes.persistence import DomainEnvelopeCheckpointRequest, load_domain_envelope
from src.lib.validation_history import runtime, selections, store
from src.lib.validation_review import admission, decisions
from src.models.sql.validation_history import ValidationHead
from src.schemas.domain_envelope import CuratableObjectEnvelope, DomainEnvelope
from tests.cancellation_test_support import install_cancel_reader
from tests.integration.persistence.test_validation_history import migrated as migrated, scope as scope

RECORDS = 30
# Writes grow with the record count; re-reading history per field would be far above this.
MAX_STATEMENTS_PER_SAVE = 600


@contextmanager
def statements():
    seen = []

    def record(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(Engine, "before_cursor_execute", record)
    try:
        yield seen
    finally:
        event.remove(Engine, "before_cursor_execute", record)


def _build(scope):
    objects = [CuratableObjectEnvelope(
        object_type="generic_object", pending_ref_id=f"generic-object-{index}", object_role="generic_object",
        model_ref="GenericExtractedObjectPayload",
        payload={"attributes": {"reagent_names": f"VT{index}-ZpGdbd in attP2", "source": f"RRID:BDSC_{index}",
                                "detail_reagent_type": "Transgene or insertion", "detail_associated_gene": "TOOL"},
                 "class_key": "generic:generic_object", "classification_notes": ["Constituent of a split driver."],
                 "label": f"VT{index}-ZpGdbd in attP2", "rationale": "Labels a descending neuron.",
                 "semantic_class": "genetic_reagent_inventory_item"})
        for index in range(RECORDS)]
    envelope = DomainEnvelope(envelope_id=uuid4().hex, domain_pack_id="generic", extracted_objects=objects)
    envelope = runtime.capture_extraction(scope=scope, producer_id=uuid4().hex, envelope=envelope,
                                          typed=envelope.model_dump(mode="json"), provenance={})
    artifact_id = envelope.metadata["validation_history"]["artifact_id"]
    envelope = selections.declare_envelope_episode(runtime.CapturedArtifact(scope, artifact_id), envelope, [])
    episode_id = envelope.metadata["review_episode_id"]
    scope_id = admission.create_scope(scope, operation_id=uuid4().hex, kind="document_run", targets=RECORDS, episodes=1)
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


def _entry(obj, baseline):
    target = obj.metadata["validation_history"]["target_id"]
    return {"target_id": target, "decision_unit_id": target, "expected_version": 0, "action": "accept",
            "reason": "Insertion identity is supported by its evidence.", "extraction_ref": baseline}


@pytest.fixture
def review(scope, monkeypatch):
    install_cancel_reader(monkeypatch, AsyncMock(return_value=False))
    envelope, artifact_id, episode_id, baseline, checkpoint = _build(scope)
    return scope, envelope, artifact_id, episode_id, baseline, checkpoint


def test_single_record_save_stays_within_query_budget_and_completes(review):
    scope, envelope, artifact_id, episode_id, baseline, checkpoint = review
    context = decisions.ReviewContext(scope, episode_id, "fixture-reviewer")
    counts = []
    last = None
    for index, obj in enumerate(envelope.extracted_objects):
        request = checkpoint()
        with statements() as seen:
            result = decisions.record_decisions(context, checkpoint_request=request,
                                                operation_id=f"accept-{index}", entries=[_entry(obj, baseline)])
        counts.append(len(seen))
        last = seen
    shapes = Counter(re.sub(r"\s+", " ", text)[:110] for text in last)
    print("statements first/last:", counts[0], counts[-1])
    for shape, count in shapes.most_common(14):
        print("SHAPE", count, shape)
    assert result["ready"] is True
    assert max(counts) <= MAX_STATEMENTS_PER_SAVE, counts


def test_current_state_within_one_transaction_reflects_a_new_decision(review):
    scope, envelope, artifact_id, episode_id, baseline, checkpoint = review
    target = envelope.extracted_objects[0].metadata["validation_history"]["target_id"]
    with store.SessionLocal.begin() as db:
        before = store._current(db, artifact_id, target)
        assert (before["version"], before["selected"]) == (0, None)
        store.apply_decisions_in_transaction(db, scope, artifact_id, operation_id="same-transaction",
            actor={"type": "agent", "id": "fixture-reviewer"},
            entries=[{"target_id": target, "expected_version": 0, "action": "accept",
                      "reason": "Supported.", "subject": "baseline"}])
        after = store._current(db, artifact_id, target)
        assert (after["version"], after["selected"]) == (1, "baseline")
        head = db.scalar(select(ValidationHead).where(ValidationHead.artifact_id == artifact_id,
                                                      ValidationHead.target_id == target))
        assert head.version == after["version"]


def test_a_rolled_back_decision_is_not_remembered(review):
    scope, envelope, artifact_id, episode_id, baseline, checkpoint = review
    target = envelope.extracted_objects[0].metadata["validation_history"]["target_id"]
    with store.SessionLocal() as db:
        store._current(db, artifact_id, target)
        store.apply_decisions_in_transaction(db, scope, artifact_id, operation_id="rolled-back",
            actor={"type": "agent", "id": "fixture-reviewer"},
            entries=[{"target_id": target, "expected_version": 0, "action": "accept",
                      "reason": "Supported.", "subject": "baseline"}])
        assert store._current(db, artifact_id, target)["version"] == 1
        db.rollback()
        assert store._current(db, artifact_id, target)["version"] == 0


@pytest.mark.parametrize("read_before_lock", ["current", "primed"])
def test_a_stale_read_before_the_write_lock_cannot_pass_the_version_check(review, read_before_lock):
    scope, envelope, artifact_id, episode_id, baseline, checkpoint = review
    target = envelope.extracted_objects[0].metadata["validation_history"]["target_id"]
    entry = {"target_id": target, "expected_version": 0, "action": "accept",
             "reason": "Supported.", "subject": "baseline"}
    actor = {"type": "agent", "id": "fixture-reviewer"}
    with store.SessionLocal() as stale:
        if read_before_lock == "primed":
            store.prime_artifact(stale, artifact_id)
        assert store._current(stale, artifact_id, target)["version"] == 0
        with store.SessionLocal.begin() as other:
            store.apply_decisions_in_transaction(other, scope, artifact_id, operation_id="other-writer",
                                                 actor=actor, entries=[entry])
        with pytest.raises(store.HistoryConflict):
            store.apply_decisions_in_transaction(stale, scope, artifact_id, operation_id="stale-writer",
                                                 actor=actor, entries=[entry])


def test_retiring_a_document_is_seen_later_in_the_same_transaction(review):
    scope, envelope, artifact_id, episode_id, baseline, checkpoint = review
    with store.SessionLocal() as db:
        store._require_live_artifact(db, artifact_id)
        store._append(db, artifact_id, operation_id="document-retired", kind="document_retirement", body={"reason": "fixture"})
        with pytest.raises(store.HistoryConflict):
            store._require_live_artifact(db, artifact_id)
        db.rollback()

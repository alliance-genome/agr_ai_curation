"""Real PostgreSQL history: immutable facts, scoped reads and retirement CAS."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, update, delete
from sqlalchemy.exc import DBAPIError

from src.lib.validation_history import store as h
from src.models.sql.database import SessionLocal
from src.models.sql.pdf_document import PDFDocument
from src.models.sql.validation_history import (
    ValidationArtifact,
    ValidationFact,
)
from tests.pdf_document_test_support import ensure_test_pdf_owner


@pytest.fixture(scope="module", autouse=True)
def migrated():
    command.upgrade(
        Config(str(Path(__file__).resolve().parents[3] / "alembic.ini")), "head"
    )


@pytest.fixture
def scope():
    doc = uuid4()
    owner = "validation-history-" + uuid4().hex
    with SessionLocal.begin() as db:
        uid = ensure_test_pdf_owner(db, auth_sub=owner)
        db.add(
            PDFDocument(
                id=doc,
                user_id=uid,
                filename="history.pdf",
                file_path=f"{doc}/history.pdf",
                file_hash=doc.hex * 2,
                file_size=1024,
                page_count=1,
                status="processed",
            )
        )
    # Immutable fixtures are retained in the disposable test database; no bypass of triggers.
    return h.HistoryScope(owner, str(doc), uuid4().hex)


def artifact(scope):
    return h.capture_baseline(
        scope,
        producer_id="producer-1",
        subject_kind="extracted",
        typed={"mention": "α", "value": None},
        normalized={"items": [{"label": "α"}]},
        provenance={"schema": "fixture/v1"},
    )


def target(scope, aid, key="one"):
    return h.register_target(
        scope, aid, identity=key, original={"object_id": key, "field": "term"}
    )


def attempt(scope, aid, tid, key="run", request=None):
    return h.begin_execution(
        scope,
        aid,
        operation_id=key,
        receipt={"model": "fake"},
        requests=[
            {
                "target_id": tid,
                "binding_id": "fixture",
                "request": request
                if request is not None
                else {"evidence": [{"quote": "original"}]},
            }
        ],
    )["attempt_ids"][0]


def decision(scope, aid, tid, version, action, *, subject=None, operation=None):
    entry = {
        "target_id": tid,
        "expected_version": version,
        "action": action,
        "reason": "reviewer reason",
    }
    if subject is not None:
        entry["subject"] = subject
    return h.apply_decisions(
        scope,
        aid,
        operation_id=operation or str(uuid4()),
        actor={"type": "curator", "id": scope.owner_id},
        entries=[entry],
    )


def test_baseline_and_inputs_are_frozen_before_call_and_survive_outer_rollback(scope):
    aid = artifact(scope)
    tid = target(scope, aid)
    request = {"evidence": [{"quote": "original"}], "nested": {"null": None}}
    with SessionLocal() as outer:
        outer.execute(select(ValidationArtifact))
        att = attempt(scope, aid, tid, request=request)
        assert h.read_fact(scope, aid, att)["content"]["input"]["request"] == request
        request["evidence"][0]["quote"] = "changed by fake validator"
        outer.rollback()
    assert (
        h.read_fact(scope, aid, att)["content"]["input"]["request"]["evidence"][0][
            "quote"
        ]
        == "original"
    )
    assert h.read_baseline(scope, aid)["typed"] == {"mention": "α", "value": None}
    assert artifact(scope) == aid
    with pytest.raises(h.HistoryConflict):
        h.capture_baseline(
            scope,
            producer_id="producer-1",
            subject_kind="extracted",
            typed={},
            normalized={},
            provenance={},
        )


def test_identical_answers_distinct_attempts_duplicate_completion_and_conflict(scope):
    aid = artifact(scope)
    tid = target(scope, aid)
    a, b = attempt(scope, aid, tid, "a"), attempt(scope, aid, tid, "b")
    result = {"status": "unresolved", "unresolved_questions": ["Optional caveat"]}

    def complete():
        return h.complete_attempt(
            scope, aid, a, state="completed", result=result, diagnostics={}
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: complete(), range(2)))
    assert ids[0] == ids[1]
    other = h.complete_attempt(
        scope, aid, b, state="completed", result=result, diagnostics={}
    )
    assert other != ids[0]
    assert (
        h.read_fact(scope, aid, other)["sha256"]
        == h.read_fact(scope, aid, ids[0])["sha256"]
    )
    with pytest.raises(h.HistoryConflict):
        h.complete_attempt(scope, aid, a, state="completed", result={}, diagnostics={})
    with SessionLocal.begin() as db:
        with pytest.raises(DBAPIError), db.begin_nested():
            db.execute(
                update(ValidationFact)
                .where(ValidationFact.id == a)
                .values(kind="changed")
            )
        with pytest.raises(DBAPIError), db.begin_nested():
            db.execute(delete(ValidationArtifact).where(ValidationArtifact.id == aid))


def test_retirement_preserves_history_late_result_cannot_reactivate_and_restore_needs_review(
    scope,
):
    aid = artifact(scope)
    tid = target(scope, aid)
    att = attempt(scope, aid, tid)
    retired = decision(scope, aid, tid, 0, "retire")[0]
    result = h.complete_attempt(
        scope,
        aid,
        att,
        state="completed",
        result={"status": "resolved"},
        diagnostics={},
    )
    with pytest.raises(h.HistoryConflict):
        decision(scope, aid, tid, 1, "accept", subject=result)
    with pytest.raises(h.HistoryConflict):
        attempt(scope, aid, tid, "late")
    restored = decision(scope, aid, tid, 1, "restore")[0]
    assert h.read_fact(scope, aid, restored)["content"]["review"] == "pending"
    with pytest.raises(h.HistoryConflict):
        decision(scope, aid, tid, 2, "accept", subject=result)
    assert h.read_fact(scope, aid, retired)["content"]["active"] is False
    assert h.read_fact(scope, aid, att)["content"]["input"]["request"]["evidence"]


def test_shared_execution_and_grouped_cas_is_atomic(scope):
    aid = artifact(scope)
    a, b = target(scope, aid, "a"), target(scope, aid, "b")
    execution = h.begin_execution(
        scope,
        aid,
        operation_id="shared",
        receipt={"deduplicated": True},
        requests=[
            {"target_id": t, "binding_id": "fixture", "request": {"same": True}}
            for t in (a, b)
        ],
    )
    assert len(execution["attempt_ids"]) == 2
    assert {
        h.read_fact(scope, aid, t)["parent_id"] for t in execution["attempt_ids"]
    } == {execution["execution_id"]}
    decision(scope, aid, b, 0, "retire")
    with pytest.raises(h.HistoryConflict):
        h.apply_decisions(
            scope,
            aid,
            operation_id="group",
            actor={"type": "curator", "id": scope.owner_id},
            entries=[
                {
                    "target_id": t,
                    "expected_version": 0,
                    "action": "retire",
                    "reason": "no longer current",
                }
                for t in (a, b)
            ],
        )
    # First update rolled back with the second stale entry.
    decision(scope, aid, a, 0, "retire", operation="after-rollback")


def test_scoped_exact_reads_and_foreign_targets(scope):
    aid = artifact(scope)
    tid = target(scope, aid)
    with pytest.raises(h.HistoryAccessError):
        h.read_baseline(
            h.HistoryScope(scope.owner_id, scope.document_id, "other-session"), aid
        )
    with pytest.raises(h.HistoryAccessError):
        h.read_baseline(
            h.HistoryScope("other-owner", scope.document_id, scope.session_id), aid
        )
    other = h.capture_baseline(
        scope,
        producer_id="other",
        subject_kind="extracted",
        typed={},
        normalized={},
        provenance={},
    )
    with pytest.raises(h.HistoryConflict):
        attempt(scope, other, tid)
    assert h.list_history(scope, aid)[0]["id"] == tid


def test_execution_redelivery_keeps_frozen_versions_after_retirement(scope):
    aid = artifact(scope)
    tid = target(scope, aid)
    first = attempt(scope, aid, tid)
    decision(scope, aid, tid, 0, "retire")
    assert attempt(scope, aid, tid) == first
    with pytest.raises(h.HistoryConflict):
        attempt(scope, aid, tid, request={"changed": True})
    assert h.read_fact(scope, aid, first)["content"]["target_version"] == 0


def test_retired_ancestor_blocks_descendant_and_restore_does_not_refresh_old_answer(
    scope,
):
    aid = artifact(scope)
    parent = target(scope, aid)
    child = h.register_target(
        scope,
        aid,
        identity="child",
        original={"value": "original"},
        parent_target_id=parent,
    )
    att = attempt(scope, aid, child)
    decision(scope, aid, parent, 0, "retire")
    result = h.complete_attempt(
        scope,
        aid,
        att,
        state="completed",
        result={"status": "resolved"},
        diagnostics={},
    )
    with pytest.raises(h.HistoryConflict):
        decision(scope, aid, child, 0, "accept", subject=result)
    with pytest.raises(h.HistoryConflict):
        attempt(scope, aid, child, "new")
    decision(scope, aid, parent, 1, "restore")
    with pytest.raises(h.HistoryConflict):
        decision(scope, aid, child, 0, "accept", subject=result)
    fresh = attempt(scope, aid, child, "fresh")
    fresh_result = h.complete_attempt(
        scope,
        aid,
        fresh,
        state="completed",
        result={"status": "resolved"},
        diagnostics={},
    )
    selected = decision(scope, aid, child, 0, "accept", subject=fresh_result)[0]
    assert h.read_fact(scope, aid, selected)["content"]["selected"] == fresh_result


def test_replacement_has_new_identity_and_preserves_old_content(scope):
    aid = artifact(scope)
    old = target(scope, aid)
    entries = [
        {
            "target_id": old,
            "expected_version": 0,
            "action": "replace",
            "reason": "corrected extraction",
            "replacement_original": {"field": "new original"},
        }
    ]
    args = dict(
        operation_id="replace",
        actor={"type": "curator", "id": scope.owner_id},
        entries=entries,
    )
    versions = h.apply_decisions(scope, aid, **args)
    assert h.apply_decisions(scope, aid, **args) == versions
    state = h.read_fact(scope, aid, versions[0])["content"]
    new = state["replacement_target_id"]
    assert new != old and state["active"] is False
    assert h.read_fact(scope, aid, old)["content"]["field"] == "term"
    assert h.read_fact(scope, aid, new)["content"]["field"] == "new original"
    attempt(scope, aid, new)
    with pytest.raises(h.HistoryConflict):
        attempt(scope, aid, old)


def test_runtime_captures_baseline_and_request_before_runner_mutates_them(
    scope, monkeypatch
):
    from types import SimpleNamespace
    from src.schemas.domain_envelope import DomainEnvelope, CuratableObjectEnvelope
    from src.schemas.domain_validator import DomainValidationRequest
    from src.lib.validation_history import runtime
    from src.lib.domain_packs import validator_dispatch as dispatch

    envelope = DomainEnvelope(
        envelope_id="runtime-" + uuid4().hex,
        domain_pack_id="fixture",
        extracted_objects=[
            CuratableObjectEnvelope(
                object_id="one",
                object_type="gene",
                payload={"terms": [{"label": "same"}, {"label": "same"}]},
            )
        ],
    )
    typed = envelope.model_dump(mode="json")
    captured = runtime.capture_extraction(
        scope=scope,
        producer_id="runtime",
        typed=typed,
        envelope=envelope,
        provenance={"agent": "fixture"},
    )
    binding = runtime.bind_artifact(
        captured, SimpleNamespace(user_id=scope.owner_id, document_id=scope.document_id)
    )
    obj = captured.extracted_objects[0]
    ids = obj.metadata["validation_history"]["elements"]["terms"]
    assert len(set(ids)) == 2  # Identical content is not identical element identity.
    request = DomainValidationRequest(
        request_id="request-1",
        validator_binding_id="fixture",
        validator_agent={"package_id": "fixture", "agent_id": "fake"},
        target={
            "domain_pack_id": "fixture",
            "object_id": "one",
            "field_path": "terms[0].label",
        },
        evidence=[{"quote": "original"}],
    )
    job = dispatch.ValidatorDispatchJob(
        match=SimpleNamespace(
            object_envelope=obj,
            envelope=captured,
            field_path=request.target.field_path,
            binding=SimpleNamespace(decision_units=(), binding_id=request.validator_binding_id),
        ),
        request=request,
    )
    seen = []
    fake_result = []

    def all_facts():
        rows = []
        after = 0
        while page := h.list_history(scope, binding.artifact_id, after=after):
            rows.extend(page)
            after = page[-1]["sequence"]
        return rows

    def fake_run(*args, **kwargs):
        facts = all_facts()
        attempt_row = next(f for f in facts if f["kind"] == "attempt")
        frozen = h.read_fact(scope, binding.artifact_id, attempt_row["id"])
        assert frozen["content"]["input"]["request"]["evidence"] == [
            {"quote": "original"}
        ]
        seen.append(attempt_row["id"])
        request.evidence[0]["quote"] = "mutated by validator"
        obj.payload["terms"][0]["label"] = "mutated"
        result = dispatch.unresolved_validator_result_for_dispatch_problem(
            request, reason="fixture", explanation="fixture"
        )
        # Model a real unresolved scientific answer, not an operational failure.
        result._execution_failure = None
        result._raw_output = None
        result.output_issues = []
        fake_result.append(result)
        return dispatch._ValidatorRunGroupResult(
            dedupe_group_results={0: [result]},
            validator_agent_run_count=1,
            batch_validator_run_count=0,
        )

    monkeypatch.setattr(dispatch, "_execute_validator_run_group_uncaptured", fake_run)
    dispatch._execute_validator_run_group(
        dispatch._ValidatorRunGroup((0,)),
        grouped_jobs=[[job]],
        agent_runner=None,
        batch_runner=None,
        event_emitter=None,
        history_binding=binding,
    )
    assert len(seen) == 1
    assert h.read_fact(scope, binding.artifact_id, seen[0])["content"]["input"][
        "request"
    ]["evidence"] == [{"quote": "original"}]
    assert h.read_baseline(scope, binding.artifact_id)["typed"] == typed
    completion = next(f for f in all_facts() if f["kind"] == "completion")
    assert (
        h.read_fact(scope, binding.artifact_id, completion["id"])["content"]["state"]
        == "completed"
    )
    from src.lib.domain_packs.validator_result_records import (
        retain_validator_original,
        read_validator_original,
    )

    result = fake_result[0]
    summarized, ref = retain_validator_original(
        captured, result=result, request=request, source_envelope_revision=1
    )
    descriptor = summarized.validator_results[ref]
    assert "request" not in descriptor and "explanation" not in descriptor["result"]
    exact = read_validator_original(
        descriptor, user_id=scope.owner_id, document_id=scope.document_id
    )
    assert exact["request"]["evidence"] == [{"quote": "original"}]
    assert exact["result"]["explanation"] == "fixture"
    with pytest.raises(h.HistoryAccessError):
        read_validator_original(
            descriptor, user_id="other", document_id=scope.document_id
        )


def test_existing_curator_remove_retires_atomically_and_keeps_exact_revision(scope):
    from src.lib.validation_history.runtime import capture_extraction
    from src.lib.domain_envelopes.patches import (
        EnvelopeFieldPatch,
        EnvelopeFieldPatchOperation,
        apply_curator_field_patch,
    )
    from src.lib.domain_envelopes.persistence import (
        DomainEnvelopeCheckpointRequest,
        write_domain_envelope_checkpoint,
        load_domain_envelope,
    )
    from tests.unit.lib.domain_packs.test_curator_override import (
        _envelope_with,
        _list_pack,
        unresolved_value,
        KEYS,
    )

    value = unresolved_value("same", identity_keys=KEYS)
    envelope = _envelope_with({"sites": [value.copy(), value.copy()]})
    envelope.envelope_id = "retire-" + uuid4().hex
    original = envelope.model_dump(mode="json")
    envelope = capture_extraction(
        scope=scope,
        producer_id="remove",
        typed=original,
        envelope=envelope,
        provenance={"test": "remove"},
    )
    aid = envelope.metadata["validation_history"]["artifact_id"]
    first, second = envelope.extracted_objects[0].metadata["validation_history"][
        "elements"
    ]["sites"]
    refs = envelope.extracted_objects[0].metadata["validation_history"]
    list_target, object_target = refs["fields"]["sites"], refs["target_id"]
    list_attempt = attempt(scope, aid, list_target, "list-before-remove")
    object_attempt = attempt(scope, aid, object_target, "object-before-remove")
    list_answer = h.complete_attempt(
        scope, aid, list_attempt, state="completed", result={}, diagnostics={}
    )
    selection = decision(scope, aid, list_target, 0, "accept", subject=list_answer)[0]
    independent = target(scope, aid, "independent")
    dependent_request = {
        "target_id": independent,
        "binding_id": "dependent",
        "request": {},
        "prerequisites": [
            {
                "artifact_id": aid,
                "target_id": list_target,
                "selection_id": selection,
                "selection_generation": 1,
            }
        ],
    }
    dependent = h.begin_execution(
        scope,
        aid,
        operation_id="dependent-before-remove",
        receipt={},
        requests=[dependent_request],
    )["attempt_ids"][0]
    source_id = str(uuid4())

    def checkpoint(db, current, version):
        return write_domain_envelope_checkpoint(
            db,
            DomainEnvelopeCheckpointRequest(
                project_key="fixture",
                envelope=current,
                expected_revision=version,
                document_id=scope.document_id,
                adapter_key="fixture",
                source_extraction_result_id=source_id,
            ),
        )

    with SessionLocal.begin() as db:
        checkpoint(db, envelope, 0)
    patch = EnvelopeFieldPatch(
        envelope_id=envelope.envelope_id,
        expected_revision=1,
        object_id="obs-1",
        field_path="sites[0]",
        before=value,
        value=None,
        operation=EnvelopeFieldPatchOperation.REMOVE,
        reason="No longer needed",
    )
    result = apply_curator_field_patch(
        envelope,
        _list_pack(),
        patch,
        current_revision=1,
        actor_id=scope.owner_id,
        actor_display_name="Test curator",
    )
    assert result.accepted, result.errors
    assert result.envelope.extracted_objects[0].metadata["validation_history"][
        "elements"
    ]["sites"] == [second]
    with SessionLocal() as db:
        checkpoint(db, result.envelope, 1)
        assert h._current(db, aid, first)["active"] is False
        db.rollback()
    with SessionLocal() as db:
        assert h._current(db, aid, first)["active"] is True
        assert (
            len(
                load_domain_envelope(db, envelope.envelope_id)
                .extracted_objects[0]
                .payload["sites"]
            )
            == 2
        )
    # Rollback keeps the selected enclosing answer usable as a prerequisite.
    h.begin_execution(
        scope,
        aid,
        operation_id="dependent-after-rollback",
        receipt={},
        requests=[dependent_request],
    )
    with SessionLocal.begin() as db:
        checkpoint(db, result.envelope, 1)
    object_answer = h.complete_attempt(
        scope, aid, object_attempt, state="completed", result={}, diagnostics={}
    )
    dependent_answer = h.complete_attempt(
        scope, aid, dependent, state="completed", result={}, diagnostics={}
    )
    for tid, answer in [
        (list_target, list_answer),
        (object_target, object_answer),
        (independent, dependent_answer),
    ]:
        with SessionLocal() as db:
            version = h._current(db, aid, tid)["version"]
        with pytest.raises(h.HistoryConflict):
            decision(scope, aid, tid, version, "accept", subject=answer)
    with pytest.raises(h.HistoryConflict):
        h.begin_execution(
            scope,
            aid,
            operation_id="dependent-after-remove",
            receipt={},
            requests=[dependent_request],
        )
    with SessionLocal() as db:
        assert h._current(db, aid, first)["active"] is False
        assert h._current(db, aid, second)["active"] is True
        assert (
            len(
                load_domain_envelope(db, envelope.envelope_id)
                .extracted_objects[0]
                .payload["sites"]
            )
            == 1
        )
        assert (
            len(
                load_domain_envelope(db, envelope.envelope_id, revision=1)
                .extracted_objects[0]
                .payload["sites"]
            )
            == 2
        )
    assert h.read_baseline(scope, aid)["typed"] == original
    assert h.read_fact(scope, aid, first)["content"]["value"] == value


def test_explicit_historical_import_preserves_known_refs_without_inventing_originals(
    scope,
):
    from src.schemas.domain_envelope import DomainEnvelope, CuratableObjectEnvelope
    from src.lib.domain_envelopes.persistence import (
        DomainEnvelopeCheckpointRequest,
        write_domain_envelope_checkpoint,
        load_domain_envelope,
    )
    from src.lib.validation_history.migration import import_historical_envelope
    from src.lib.domain_packs.validator_result_records import (
        validator_result_snapshot,
        read_validator_original,
    )

    old_ref, old_snapshot = validator_result_snapshot(
        {"status": "resolved", "known": None}
    )
    old_snapshot["recorded_order"] = 0
    envelope = DomainEnvelope(
        envelope_id="historical-" + uuid4().hex,
        domain_pack_id="fixture",
        extracted_objects=[
            CuratableObjectEnvelope(
                object_id="one",
                object_type="gene",
                payload={"name": "already validated"},
            )
        ],
        validator_results={old_ref: old_snapshot},
    )
    source_id = str(uuid4())
    with SessionLocal.begin() as db:
        write_domain_envelope_checkpoint(
            db,
            DomainEnvelopeCheckpointRequest(
                project_key="fixture",
                envelope=envelope,
                expected_revision=0,
                document_id=scope.document_id,
                adapter_key="fixture",
                source_extraction_result_id=source_id,
            ),
        )
    assert import_historical_envelope(envelope.envelope_id)
    assert not import_historical_envelope(envelope.envelope_id)
    with SessionLocal() as db:
        current = load_domain_envelope(db, envelope.envelope_id)
        earlier = load_domain_envelope(db, envelope.envelope_id, revision=1)
    assert earlier.validator_results[old_ref] == old_snapshot
    assert (
        read_validator_original(
            current.validator_results[old_ref],
            user_id=scope.owner_id,
            document_id=scope.document_id,
        )
        == old_snapshot
    )
    ref = current.metadata["validation_history"]
    history_scope = h.HistoryScope(scope.owner_id, scope.document_id, ref["session_id"])
    baseline = h.read_baseline(history_scope, ref["artifact_id"])
    assert baseline["typed"] is None and not baseline["original_available"]
    assert baseline["provenance"]["review_status"] == "historical_unknown"
    assert all(
        f["kind"] == "target" for f in h.list_history(history_scope, ref["artifact_id"])
    )
    target_id = current.extracted_objects[0].metadata["validation_history"]["target_id"]
    with pytest.raises(h.HistoryConflict):
        decision(
            history_scope,
            ref["artifact_id"],
            target_id,
            0,
            "accept",
            subject="baseline",
        )


def test_reordering_identical_elements_keeps_binding_target_identity(scope):
    from types import SimpleNamespace
    from src.lib.validation_history import runtime
    from src.schemas.domain_envelope import DomainEnvelope, CuratableObjectEnvelope
    from src.schemas.domain_validator import DomainValidationRequest

    envelope = DomainEnvelope(
        envelope_id="reorder-" + uuid4().hex,
        domain_pack_id="fixture",
        extracted_objects=[
            CuratableObjectEnvelope(
                object_id="one",
                object_type="gene",
                payload={
                    "items": [
                        {"label": "same", "nested": ["same"]},
                        {"label": "same", "nested": ["same"]},
                    ]
                },
            )
        ],
    )
    envelope = runtime.capture_extraction(
        scope=scope,
        producer_id="reorder",
        typed=envelope.model_dump(mode="json"),
        envelope=envelope,
        provenance={},
    )
    binding = runtime.bind_artifact(
        envelope, SimpleNamespace(user_id=scope.owner_id, document_id=scope.document_id)
    )
    obj = envelope.extracted_objects[0]
    ids = obj.metadata["validation_history"]["elements"]["items"]
    nested = obj.metadata["validation_history"]["elements"]["items[0].nested"]
    request = DomainValidationRequest(
        request_id="one",
        validator_binding_id="fixture",
        validator_agent={"package_id": "fixture", "agent_id": "fake"},
        target={"domain_pack_id": "fixture", "field_path": "items[0].nested[0]"},
        expected_result_fields={"value": "items[0].nested[0]"},
    )
    job = SimpleNamespace(match=SimpleNamespace(object_envelope=obj,
        field_path=request.target.field_path, binding=SimpleNamespace(binding_id=request.validator_binding_id)), request=request)
    original_target = runtime.request_target(binding, job)
    reordered = runtime.reorder_element_projection(obj, "items", list(reversed(ids)))
    assert reordered.payload == obj.payload
    assert (
        reordered.metadata["validation_history"]["elements"]["items[1].nested"]
        == nested
    )
    job.match.object_envelope = reordered
    request.target.field_path = "items[1].nested[0]"
    job.match.field_path = request.target.field_path
    request.expected_result_fields = {"value": "items[1].nested[0]"}
    assert runtime.request_target(binding, job) == original_target


def test_checkpoint_storage_grows_with_new_content_not_repeated_full_history(scope):
    import json
    from copy import deepcopy
    from src.lib.validation_history.checkpoints import (
        write_checkpoint_body,
        read_checkpoint_body,
    )
    from src.models.sql.validation_history import ValidationBody

    payload = {"history": [], "nested": {"items": [{"value": 1}, {"value": 2}]}}
    previous_payload = previous_sha = None
    versions = []
    with SessionLocal.begin() as db:
        for index in range(20):
            payload["history"].append(
                {"event": index, "content": str(uuid4()) + "x" * 4000}
            )
            sha = write_checkpoint_body(
                db,
                payload,
                previous_payload=previous_payload,
                previous_sha=previous_sha,
            )
            versions.append((sha, deepcopy(payload)))
            previous_payload, previous_sha = deepcopy(payload), sha
        payload["nested"]["items"] = [{"new": True}]
        del payload["nested"]["items"][0]["new"]
        final = write_checkpoint_body(
            db, payload, previous_payload=previous_payload, previous_sha=previous_sha
        )
        for sha, expected in versions:
            assert read_checkpoint_body(db, sha) == expected
        assert read_checkpoint_body(db, final) == payload
        total = sum(
            len(json.dumps(db.get(ValidationBody, sha).content)) for sha, _ in versions
        )
        assert total < 2 * len(json.dumps(payload))


@pytest.mark.parametrize("outcome", ["completed", "failed", "interrupted"])
def test_custom_flow_attachment_freezes_inputs_and_retains_truthful_outcome(
    scope, tmp_path, monkeypatch, outcome
):
    import asyncio
    from types import SimpleNamespace
    from src.lib.flows import executor
    from src.lib.validation_history import runtime
    from src.lib.domain_packs.validation_registry import DomainPackValidationRegistry
    from src.schemas.domain_validator import DomainValidatorResultBase
    from tests.unit.lib.domain_packs.test_validator_dispatch import (
        _loaded_pack,
        _envelope,
        _result_payload,
    )

    pack = _loaded_pack(tmp_path)
    source = _envelope()
    source = runtime.capture_extraction(
        scope=scope,
        producer_id="flow-attachment",
        typed=source.model_dump(mode="json"),
        envelope=source,
        provenance={},
    )
    artifact_id = source.metadata["validation_history"]["artifact_id"]
    seen = []

    async def custom(request, **kwargs):
        from agents import ModelSettings
        from openai.types.shared import Reasoning

        with SessionLocal() as db:
            att = db.scalar(
                select(ValidationFact).where(
                    ValidationFact.artifact_id == artifact_id,
                    ValidationFact.kind == "attempt",
                )
            )
        frozen = h.read_fact(scope, artifact_id, att.id)["content"]["input"]["request"]
        assert frozen == request.model_dump(mode="json")
        seen.append((att.id, frozen))
        runtime.record_runner_receipt(
            SimpleNamespace(
                instructions="Pinned prompt",
                tools=[],
                model_settings=ModelSettings(reasoning=Reasoning(effort="medium")),
            ),
            model="fixture-model",
            input_payload=frozen,
        )
        request.selected_inputs["new"] = "changed after capture"
        if outcome == "failed":
            raise RuntimeError("fixture operational failure")
        if outcome == "interrupted":
            raise asyncio.CancelledError()
        return DomainValidatorResultBase.model_validate(_result_payload(request))

    monkeypatch.setattr(executor, "_run_custom_flow_validator_agent", custom)
    flow = SimpleNamespace(
        flow_definition={
            "nodes": [{"id": "custom", "data": {"agent_id": "custom-agent"}}]
        }
    )

    async def run():
        return await executor._collect_flow_validator_materialization_inputs(
            source_envelope=source,
            source_envelope_revision=1,
            registry=DomainPackValidationRegistry.from_domain_pack(pack),
            groups=[
                {
                    "group_id": "group",
                    "binding_id": "fixture.identifier_lookup",
                    "state": "replaced",
                    "validator_node_id": "custom",
                }
            ],
            flow=flow,
            agent_context={},
            document_id=scope.document_id,
            user_id=scope.owner_id,
        )

    if outcome == "interrupted":
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(run())
    else:
        units, _, _ = asyncio.run(run())
        assert units[0].result._history_ref["execution_state"] == outcome
    assert len(seen) == 1
    attempt_id, original = seen[0]
    assert (
        h.read_fact(scope, artifact_id, attempt_id)["content"]["input"]["request"]
        == original
    )
    completion = h.completion_for_attempt(scope, artifact_id, attempt_id)
    assert completion["state"] == outcome
    body = h.read_fact(scope, artifact_id, completion["id"])["content"]
    assert (body["result"] is not None) == (outcome == "completed")
    with SessionLocal() as db:
        receipt = db.scalar(
            select(ValidationFact).where(
                ValidationFact.artifact_id == artifact_id,
                ValidationFact.kind == "runner_receipt",
            )
        )
    assert (
        h.read_fact(scope, artifact_id, receipt.id)["content"]["instructions"]
        == "Pinned prompt"
    )
    assert (
        h.read_fact(scope, artifact_id, receipt.id)["content"]["model_settings"][
            "reasoning"
        ]["effort"]
        == "medium"
    )


def test_historical_source_without_checkpoint_is_imported_without_source_rewrite(scope):
    from uuid import UUID
    from src.lib.curation_workspace.models import CurationExtractionResultRecord
    from src.schemas.curation_workspace import CurationExtractionSourceKind
    from src.lib.validation_history.migration import import_historical_extraction
    from src.lib.domain_envelopes.persistence import load_domain_envelope
    from tests.integration.persistence.test_inline_extraction_persistence import (
        _canonical_gene_envelope,
    )

    payload = _canonical_gene_envelope()
    source_id = uuid4()
    with SessionLocal.begin() as db:
        db.add(
            CurationExtractionResultRecord(
                id=source_id,
                document_id=UUID(scope.document_id),
                agent_key="gene_extractor",
                adapter_key="gene",
                user_id=scope.owner_id,
                source_kind=CurationExtractionSourceKind.CHAT,
                payload_json=payload,
                extraction_metadata={},
            )
        )
    assert import_historical_extraction(str(source_id))
    assert not import_historical_extraction(str(source_id))
    with SessionLocal() as db:
        assert db.get(CurationExtractionResultRecord, source_id).payload_json == payload
        envelope = load_domain_envelope(db, payload["envelope_id"])
    ref = envelope.metadata["validation_history"]
    baseline = h.read_baseline(
        h.HistoryScope(scope.owner_id, scope.document_id, ref["session_id"]),
        ref["artifact_id"],
    )
    assert baseline["typed"] is None
    from src.schemas.domain_envelope import DomainEnvelope

    assert (
        baseline["normalized"]["extracted_objects"]
        == DomainEnvelope.model_validate(payload).model_dump(mode="json")[
            "extracted_objects"
        ]
    )


def test_rejecting_previously_selected_answer_keeps_exact_accepted_version(scope):
    aid = artifact(scope)
    tid = target(scope, aid)
    att = attempt(scope, aid, tid)
    completion = h.complete_attempt(
        scope,
        aid,
        att,
        state="completed",
        result={"status": "unresolved"},
        diagnostics={},
    )
    accepted = decision(scope, aid, tid, 0, "accept", subject=completion)[0]
    rejected = decision(scope, aid, tid, 1, "reject", subject=completion)[0]
    assert h.read_fact(scope, aid, accepted)["content"]["selected"] == completion
    assert h.read_fact(scope, aid, rejected)["content"]["selected"] == h.read_fact(scope, aid, accepted)["content"]["selected"]
    assert h.read_fact(scope, aid, completion)["content"]["result"] == {
        "status": "unresolved"
    }


def test_extraction_only_selection_cannot_claim_a_later_contribution_as_original(scope):
    from src.lib.validation_history import runtime
    from src.schemas.domain_envelope import DomainEnvelope, CuratableObjectEnvelope

    source = DomainEnvelope(
        envelope_id=uuid4().hex,
        domain_pack_id="fixture",
        extracted_objects=[
            CuratableObjectEnvelope(
                object_id="original", object_type="gene", payload={"name": "A"}
            )
        ],
    )
    captured = runtime.capture_extraction(
        scope=scope,
        producer_id="original",
        typed=source.model_dump(mode="json"),
        envelope=source,
        provenance={},
    )
    aid = captured.metadata["validation_history"]["artifact_id"]
    tid = captured.extracted_objects[0].metadata["validation_history"]["target_id"]
    selected = decision(scope, aid, tid, 0, "accept", subject="baseline")[0]
    assert h.read_fact(scope, aid, selected)["content"]["selected"] == "baseline"
    assert not any(f["kind"] == "attempt" for f in h.list_history(scope, aid))
    captured.extracted_objects.append(
        CuratableObjectEnvelope(
            object_id="later", object_type="gene", payload={"name": "B"}
        )
    )
    updated = runtime.capture_curator_objects(
        runtime.CapturedArtifact(scope, aid), captured, actor_id=scope.owner_id
    )
    added = updated.extracted_objects[-1].metadata["validation_history"]["target_id"]
    with pytest.raises(h.HistoryConflict, match="no attested extraction baseline"):
        decision(scope, aid, added, 0, "accept", subject="baseline")


def test_defer_reject_and_revoke_do_not_stale_unchanged_answers(scope):
    aid, tid = artifact(scope), None
    tid = target(scope, aid)
    answers = [
        h.complete_attempt(
            scope,
            aid,
            attempt(scope, aid, tid, key),
            state="completed",
            result={"answer": key},
            diagnostics={},
        )
        for key in ("one", "two")
    ]
    decision(scope, aid, tid, 0, "defer")
    decision(scope, aid, tid, 1, "reject", subject=answers[0])
    selected = decision(scope, aid, tid, 2, "accept", subject=answers[1])[0]
    decision(scope, aid, tid, 3, "revoke")
    decision(scope, aid, tid, 4, "accept", subject=answers[1])
    assert h.read_fact(scope, aid, selected)["content"]["selected"] == answers[1]


def test_composite_units_and_pinned_prerequisite_selections(scope):
    aid = artifact(scope)
    tid = target(scope, aid)
    request = {
        "target_id": tid,
        "binding_id": "composite",
        "request": {},
        "result_fields": ["id", "label", "context"],
        "decision_units": [
            {"key": "identity", "fields": ["id", "label"]},
            {"key": "context", "fields": ["context"]},
        ],
    }
    invocation = h.begin_execution(
        scope, aid, operation_id="composite", receipt={}, requests=[request]
    )
    att = invocation["attempt_ids"][0]
    frozen = h.read_fact(scope, aid, att)["content"]
    first, second = frozen["coverage"]
    assert frozen["budget_scope_id"]
    answer = h.complete_attempt(
        scope,
        aid,
        att,
        state="completed",
        result={"id": "a", "label": "A", "context": "c"},
        diagnostics={},
    )
    selection = decision(scope, aid, first, 0, "accept", subject=answer)[0]
    decision(scope, aid, second, 0, "reject", subject=answer)
    with pytest.raises(h.HistoryConflict):
        decision(scope, aid, tid, 0, "accept", subject=answer)
    outsider = target(scope, aid, "outsider")
    with pytest.raises(h.HistoryConflict):
        decision(scope, aid, outsider, 0, "accept", subject=answer)
    dependent = {
        "target_id": outsider,
        "binding_id": "dependent",
        "request": {},
        "prerequisites": [
            {
                "artifact_id": aid,
                "target_id": first,
                "selection_id": selection,
                "selection_generation": 1,
            }
        ],
    }
    started = h.begin_execution(
        scope, aid, operation_id="dependent", receipt={}, requests=[dependent]
    )
    result = h.complete_attempt(
        scope,
        aid,
        started["attempt_ids"][0],
        state="completed",
        result={},
        diagnostics={},
    )
    decision(scope, aid, first, 1, "defer")  # No input change.
    decision(scope, aid, outsider, 0, "accept", subject=result)
    decision(scope, aid, first, 2, "revoke")
    with pytest.raises(h.HistoryConflict):
        decision(scope, aid, outsider, 1, "accept", subject=result)
    with pytest.raises(h.HistoryConflict):
        h.begin_execution(
            scope,
            aid,
            operation_id="stale-dependency",
            receipt={},
            requests=[dependent],
        )
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        h.begin_execution(
            scope,
            aid,
            operation_id="split",
            receipt={},
            requests=[
                {**request, "decision_units": [{"key": "only-id", "fields": ["id"]}]}
            ],
        )


def test_curator_identity_replacement_is_atomic_and_stales_inflight_answer(scope):
    from src.lib.validation_history.runtime import capture_extraction
    from src.lib.domain_envelopes.patches import (
        EnvelopeFieldPatch,
        EnvelopeFieldPatchOperation,
        apply_curator_field_patch,
    )
    from src.lib.domain_envelopes.persistence import (
        DomainEnvelopeCheckpointRequest,
        write_domain_envelope_checkpoint,
    )
    from tests.unit.lib.domain_packs.test_curator_override import (
        _envelope_with,
        _list_pack,
        unresolved_value,
        KEYS,
    )

    value = unresolved_value("same", identity_keys=KEYS)
    envelope = _envelope_with({"sites": [value.copy()]})
    envelope.envelope_id = "replace-" + uuid4().hex
    envelope = capture_extraction(
        scope=scope,
        producer_id="replace",
        typed=envelope.model_dump(mode="json"),
        envelope=envelope,
        provenance={},
    )
    aid = envelope.metadata["validation_history"]["artifact_id"]
    old = envelope.extracted_objects[0].metadata["validation_history"]["elements"][
        "sites"
    ][0]
    att = attempt(scope, aid, old)
    source = str(uuid4())

    def save(db, env, rev):
        write_domain_envelope_checkpoint(
            db,
            DomainEnvelopeCheckpointRequest(
                project_key="fixture",
                envelope=env,
                expected_revision=rev,
                document_id=scope.document_id,
                adapter_key="fixture",
                source_extraction_result_id=source,
            ),
        )

    with SessionLocal.begin() as db:
        save(db, envelope, 0)
    patch = EnvelopeFieldPatch(
        envelope_id=envelope.envelope_id,
        expected_revision=1,
        object_id="obs-1",
        field_path="sites[0].curie",
        before={"curie": value.get("curie"), "name": value.get("name")},
        value={"curie": "NEW:1", "name": "new"},
        operation=EnvelopeFieldPatchOperation.REPLACE_IDENTITY,
        reason="Use corrected identity",
    )
    edited = apply_curator_field_patch(
        envelope,
        _list_pack(),
        patch,
        current_revision=1,
        actor_id=scope.owner_id,
        actor_display_name="Curator",
    )
    assert edited.accepted, edited.errors
    new = edited.envelope.extracted_objects[0].metadata["validation_history"][
        "elements"
    ]["sites"][0]
    assert new != old
    with SessionLocal() as db:
        save(db, edited.envelope, 1)
        db.rollback()
    with SessionLocal() as db:
        assert h._current(db, aid, old)["active"]
    with SessionLocal.begin() as db:
        save(db, edited.envelope, 1)
    answer = h.complete_attempt(
        scope, aid, att, state="completed", result={"old": True}, diagnostics={}
    )
    with pytest.raises(h.HistoryConflict):
        decision(scope, aid, old, 1, "accept", subject=answer)
    assert h.read_fact(scope, aid, old)["content"]["value"] == value
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(ValidationFact).where(
                    ValidationFact.artifact_id == aid,
                    ValidationFact.kind == "replacement",
                    ValidationFact.target_id == new,
                )
            )
            is not None
        )


def test_documentless_chat_history_uses_owner_scope_without_fake_pdf(scope):
    from src.lib.openai_agents.streaming_tools import (
        _validator_runtime_context_for_chat,
    )
    from src.lib.validation_history import runtime
    from src.schemas.domain_envelope import DomainEnvelope, CuratableObjectEnvelope

    context = _validator_runtime_context_for_chat(
        document_id=None, user_id=scope.owner_id
    )
    assert context.document_id is None
    chat_scope = h.HistoryScope(scope.owner_id, None, "chat-" + uuid4().hex)
    envelope = DomainEnvelope(
        envelope_id="chat-" + uuid4().hex,
        domain_pack_id="fixture",
        extracted_objects=[
            CuratableObjectEnvelope(
                object_id="one", object_type="gene", payload={"name": "given in chat"}
            )
        ],
    )
    captured = runtime.capture_extraction(
        scope=chat_scope,
        producer_id="chat",
        typed=envelope.model_dump(mode="json"),
        envelope=envelope,
        provenance={"source": "chat"},
    )
    bound = runtime.bind_artifact(captured, context)
    assert (
        h.read_baseline(chat_scope, bound.artifact_id)["provenance"]["source_document"]
        is None
    )
    with pytest.raises(h.HistoryAccessError):
        h.read_baseline(scope, bound.artifact_id)


def test_malformed_model_output_is_retained_as_incomplete(scope):
    from src.lib.domain_packs.validator_dispatch import (
        _validated_result_from_agent_output,
    )
    from src.lib.validation_history.runtime import CapturedArtifact, complete_jobs
    from src.schemas.domain_validator import DomainValidationRequest

    request = DomainValidationRequest(
        request_id="malformed",
        validator_binding_id="fixture",
        validator_agent={"package_id": "fixture", "agent_id": "validator"},
        target={"domain_pack_id": "fixture"},
    )
    raw = {
        "status": "unexpected",
        "explanation": "Preserve this scientific judgment",
        "valid_part": "A",
    }
    result = _validated_result_from_agent_output(raw, request=request)
    assert result.output_issues and result._execution_failure is None
    aid = artifact(scope)
    tid = target(scope, aid)
    att = attempt(scope, aid, tid)
    complete_jobs(CapturedArtifact(scope, aid), {"attempt_ids": [att]}, [result])
    completion = h.read_fact(scope, aid, result._history_ref["fact_id"])["content"]
    assert completion["state"] == "incomplete"
    assert completion["result"]["raw_output"] == raw
    with pytest.raises(h.HistoryConflict):
        decision(scope, aid, tid, 0, "accept", subject=result._history_ref["fact_id"])


def test_collection_materialization_rejects_whole_rewrites_but_preserves_indexed_writes(
    scope,
):
    from src.lib.validation_history import runtime
    from src.schemas.domain_envelope import DomainEnvelope, CuratableObjectEnvelope

    envelope = DomainEnvelope(
        envelope_id="collections-" + uuid4().hex,
        domain_pack_id="fixture",
        extracted_objects=[
            CuratableObjectEnvelope(
                object_id="one",
                object_type="gene",
                payload={"terms": [{"label": "A"}, {"label": "B"}]},
            )
        ],
    )
    envelope = runtime.capture_extraction(
        scope=scope,
        producer_id="collections",
        typed=envelope.model_dump(mode="json"),
        envelope=envelope,
        provenance={},
    )
    for values in (
        [{"label": "B"}, {"label": "A"}],
        [{"label": "C"}, {"label": "D"}],
        [{"label": "A"}],
    ):
        changed = envelope.model_copy(deep=True)
        changed.extracted_objects[0].payload["terms"] = values
        with pytest.raises(h.HistoryConflict, match="materialization"):
            runtime._check_collection_materialization(
                envelope.extracted_objects[0], changed.extracted_objects[0]
            )
    changed = envelope.model_copy(deep=True)
    obj = changed.extracted_objects[0]
    obj.payload["terms"][0]["label"] = "resolved A"
    obj.metadata["validator_resolved_value_materialization"] = [
        {"materialized_field_paths": ["terms[0].label"]}
    ]
    runtime._check_collection_materialization(envelope.extracted_objects[0], obj)
    assert (
        obj.metadata["validation_history"]
        == envelope.extracted_objects[0].metadata["validation_history"]
    )
    corrupted = envelope.model_copy(deep=True)
    corrupted.extracted_objects[0].payload["terms"].pop()
    with pytest.raises(h.HistoryConflict):
        runtime.retire_element_projection(
            corrupted.extracted_objects[0].metadata,
            "terms[0]",
            payload=corrupted.extracted_objects[0].payload,
        )


def test_checkpoint_rejects_divergent_projection_before_committing(scope):
    from src.lib.validation_history.checkpoints import write_checkpoint_body

    with SessionLocal.begin() as db:
        sha = write_checkpoint_body(
            db, {"initial": True}, previous_payload=None, previous_sha=None
        )
        with pytest.raises(h.HistoryConflict):
            write_checkpoint_body(
                db, {"later": True}, previous_payload={"wrong": True}, previous_sha=sha
            )


def test_published_transition_fixture(scope):
    import json

    fixture = (
        Path(__file__).resolve().parents[2]
        / "fixtures/validation_history/transitions.json"
    )
    for case in json.loads(fixture.read_text())["cases"]:
        aid = artifact(scope)
        tid = target(scope, aid, case["name"])
        subjects = {
            key: h.complete_attempt(
                scope,
                aid,
                attempt(scope, aid, tid, case["name"] + key),
                state="completed",
                result={"answer": key},
                diagnostics={},
            )
            for key in ("answer", "other")
        }
        version = 0
        for step in case["steps"]:
            kwargs = {"subject": subjects[step["subject"]]} if "subject" in step else {}
            if step.get("conflict"):
                with pytest.raises(h.HistoryConflict):
                    decision(scope, aid, tid, version, step["action"], **kwargs)
            else:
                decision(scope, aid, tid, version, step["action"], **kwargs)
                version += 1


def test_history_page_size_is_environment_configurable(scope, monkeypatch):
    aid = artifact(scope)
    for index in range(6):
        target(scope, aid, str(index))
    monkeypatch.delenv("VALIDATION_HISTORY_PAGE_SIZE", raising=False)
    assert len(h.list_history(scope, aid)) == 5
    monkeypatch.setenv("VALIDATION_HISTORY_PAGE_SIZE", "2")
    first = h.list_history(scope, aid)
    second = h.list_history(scope, aid, after=first[-1]["sequence"])
    assert len(first) == len(second) == 2
    assert {r["id"] for r in first}.isdisjoint({r["id"] for r in second})


@pytest.mark.parametrize("whole_list", [False, True])
def test_real_two_wave_dispatch_retains_source_and_waits_for_selection(
    scope, tmp_path, whole_list
):
    from copy import deepcopy
    from dataclasses import replace
    from src.lib.validation_history import runtime
    from src.lib.domain_packs.validator_dispatch import (
        dispatch_active_validator_bindings,
        ValidatorRuntimeContext,
    )
    from src.schemas.domain_envelope import DomainEnvelope, CuratableObjectEnvelope
    from src.schemas.domain_pack_metadata import DomainPackMetadata
    from tests.unit.lib.domain_packs.test_validator_dispatch import (
        _multivalued_dispatch_pack,
        _result_payload,
    )

    pack = _multivalued_dispatch_pack(tmp_path)
    data = pack.metadata.model_dump(mode="json")
    first = data["metadata"]["validator_bindings"]["active"][0]
    if whole_list:
        field = data["object_definitions"][0]["fields"][0]
        field["field_type"] = "array"
        field["metadata"] = {}
        first["input_fields"]["curie"]["allow_multiple"] = True
    second = deepcopy(first)
    second["binding_id"] = "fixture.second"
    second["runs_after"] = [first["binding_id"]]
    data["metadata"]["validator_bindings"]["active"].append(second)
    pack = replace(pack, metadata=DomainPackMetadata.model_validate(data))
    envelope = DomainEnvelope(
        envelope_id="waves-" + uuid4().hex,
        domain_pack_id=pack.pack_id,
        extracted_objects=[
            CuratableObjectEnvelope(
                object_type="Annotation",
                pending_ref_id="one",
                payload={"evidence_code_curies": ["eco:1", "eco:2"]},
            )
        ],
    )
    envelope = runtime.capture_extraction(
        scope=scope,
        producer_id="waves",
        typed=envelope.model_dump(mode="json"),
        envelope=envelope,
        provenance={},
    )
    old_ids = envelope.extracted_objects[0].metadata["validation_history"]["elements"][
        "evidence_code_curies"
    ]
    calls = []

    def runner(request, *, binding):
        calls.append(binding.binding_id)
        value = request.selected_inputs["curie"]
        resolved = list(reversed(value)) if whole_list else value.upper()
        result = _result_payload(request, resolved_values={"curie": resolved})
        result["resolved_objects"] = []
        return result

    kwargs = dict(
        runner=runner,
        runtime_context=ValidatorRuntimeContext(
            user_id=scope.owner_id, document_id=scope.document_id
        ),
        max_parallel_validators=1,
    )
    result = dispatch_active_validator_bindings(envelope, pack, **kwargs)
    assert "fixture.second" not in calls
    obj = result.envelope.extracted_objects[0]
    assert obj.payload["evidence_code_curies"] == ["eco:1", "eco:2"]
    assert obj.metadata["validation_history"]["elements"]["evidence_code_curies"] == old_ids
    with SessionLocal() as db:
        attempts = list(db.scalars(select(ValidationFact).where(
            ValidationFact.artifact_id == envelope.metadata["validation_history"]["artifact_id"],
            ValidationFact.kind == "attempt",
        )))
        assert {h._content(db, row)["input"]["dispatch_wave"] for row in attempts} == {0}
        scheduling = list(db.scalars(select(ValidationFact).where(
            ValidationFact.artifact_id == envelope.metadata["validation_history"]["artifact_id"],
            ValidationFact.kind == "scheduling",
        )))
        assert any(h._content(db, row)["outcome"] == "awaiting_review" for row in scheduling)


def test_document_cleanup_retires_history_and_preserves_owner_exact_reads(scope):
    from uuid import UUID
    from src.lib.document_cleanup import cleanup_document_curation_dependencies
    from src.lib.validation_history.runtime import capture_extraction
    from src.lib.domain_envelopes.persistence import (
        DomainEnvelopeCheckpointRequest,
        write_domain_envelope_checkpoint,
    )
    from src.lib.curation_workspace.models import DomainEnvelopeModel
    from src.schemas.domain_envelope import DomainEnvelope, CuratableObjectEnvelope

    envelope = DomainEnvelope(
        envelope_id="retired-document-" + uuid4().hex,
        domain_pack_id="fixture",
        extracted_objects=[
            CuratableObjectEnvelope(
                object_id="one", object_type="gene", payload={"name": "original"}
            )
        ],
    )
    envelope = capture_extraction(
        scope=scope,
        producer_id="document-retirement",
        typed=envelope.model_dump(mode="json"),
        envelope=envelope,
        provenance={},
    )
    aid = envelope.metadata["validation_history"]["artifact_id"]
    tid = envelope.extracted_objects[0].metadata["validation_history"]["target_id"]
    att = attempt(scope, aid, tid)
    with SessionLocal.begin() as db:
        write_domain_envelope_checkpoint(
            db,
            DomainEnvelopeCheckpointRequest(
                project_key="fixture",
                envelope=envelope,
                expected_revision=0,
                document_id=scope.document_id,
                adapter_key="fixture",
                source_extraction_result_id=str(uuid4()),
            ),
        )
    with SessionLocal() as db:
        cleanup_document_curation_dependencies(db, UUID(scope.document_id))
        db.delete(db.get(PDFDocument, UUID(scope.document_id)))
        db.flush()
        db.rollback()
    assert h.read_baseline(scope, aid)["retired"] is False
    with SessionLocal.begin() as db:
        cleanup_document_curation_dependencies(db, UUID(scope.document_id))
        db.delete(db.get(PDFDocument, UUID(scope.document_id)))
    with SessionLocal() as db:
        assert db.get(DomainEnvelopeModel, envelope.envelope_id) is None
    assert h.read_baseline(scope, aid)["retired"] is True
    assert h.read_fact(scope, aid, att)["content"]["input"]["request"]["evidence"]
    archived = h.read_archived_checkpoint(
        scope, aid, envelope_id=envelope.envelope_id, revision=1
    )
    assert archived["extracted_objects"][0]["payload"]["name"] == "original"
    late = h.complete_attempt(
        scope,
        aid,
        att,
        state="completed",
        result={"status": "resolved"},
        diagnostics={},
    )
    for action in ("accept", "restore"):
        with pytest.raises(h.HistoryConflict):
            decision(
                scope,
                aid,
                tid,
                0,
                action,
                **({"subject": late} if action == "accept" else {}),
            )
    with pytest.raises(h.HistoryConflict):
        attempt(scope, aid, tid, "new-call")
    with pytest.raises(h.HistoryAccessError):
        h.read_baseline(
            h.HistoryScope("other-owner", scope.document_id, scope.session_id), aid
        )
    with pytest.raises(h.HistoryAccessError):
        h.read_archived_checkpoint(scope, aid, envelope_id="unrelated", revision=1)


@pytest.mark.parametrize("pack", ["allele", "phenotype", "disease"])
def test_converted_source_evidence_remains_exact_through_validation_history_and_readers(scope, pack):
    import importlib
    from copy import deepcopy
    from datetime import datetime, timezone
    from src.schemas.curation_workspace import CurationExtractionResultRecord
    from src.lib.curation_workspace.domain_envelope_normalization import domain_envelope_from_extraction_result
    from src.lib.domain_packs.materialization import project_evidence_anchor_projections
    from src.lib.flows.output_projection import _attach_source_linked_evidence
    from src.lib.openai_agents.inspect_results import _object_evidence_records
    from src.schemas.domain_envelope import DomainEnvelope

    fixture = importlib.import_module(f'tests.contract.alliance.domain_packs.test_{pack}_builder_domain_pack')
    converted = fixture._materialize_one_candidate()
    assert converted.ok, converted.summary()
    record = CurationExtractionResultRecord.model_validate({
        'extraction_result_id': str(uuid4()), 'document_id': scope.document_id,
        'adapter_key': pack, 'agent_key': pack + '_extractor', 'source_kind': 'chat',
        'candidate_count': len(converted.payload['curatable_objects']),
        'payload_json': converted.payload, 'created_at': datetime.now(timezone.utc), 'metadata': {},
    })
    envelope = domain_envelope_from_extraction_result(record, stored=False)
    original = envelope.model_dump(mode='json')
    aid = h.capture_baseline(scope, producer_id=str(uuid4()), subject_kind='extracted',
                             typed=converted.payload, normalized=original, provenance={'fixture': pack})
    tid = target(scope, aid)
    supplied = deepcopy(converted.payload['metadata']['evidence_records'])
    att = attempt(scope, aid, tid, request={'evidence': supplied})
    supplied[0]['verified_quote'] = 'Fake validator replacement must never become source'
    completion = h.complete_attempt(scope, aid, att, state='completed', result={'resolved_values': {'term': 'X'}}, diagnostics={})
    decision(scope, aid, tid, 0, 'accept', subject=completion)
    saved = h.read_baseline(scope, aid)['normalized']
    assert saved == original
    restored = DomainEnvelope.model_validate(saved)
    items = saved['extracted_objects']
    rows = [{} for _ in items]
    _attach_source_linked_evidence(rows, items, saved)
    supported = [(obj, row) for obj, row in zip(restored.extracted_objects, rows) if obj.evidence_record_ids]
    assert supported
    for obj, row in supported:
        assert not row['object.evidence_link_issues']
        assert row['object.supporting_evidence'] == _object_evidence_records(restored, obj)
    anchors = project_evidence_anchor_projections(restored, envelope_revision=1, document_id=scope.document_id)
    assert anchors
    assert 'Fake validator replacement' not in str([a.model_dump() for a in anchors])

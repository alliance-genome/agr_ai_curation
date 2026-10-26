"""Owned workspace history and durable explicit commands use real PostgreSQL."""

from uuid import UUID, uuid4
from datetime import datetime, timezone
import pytest
from sqlalchemy import select, func
from src.lib.validation_history import store as h
from src.lib.curation_workspace import (
    review_history_service as history,
    review_operations as operations,
)
from src.lib.curation_workspace.models import (
    CurationCandidate,
    CurationReviewSession,
    DomainEnvelopeModel,
)
from src.models.sql.validation_history import ValidationFact
from src.models.sql.validation_review import WorkspaceReviewOperation
from src.schemas.curation_workspace import CurationCandidateSource
from tests.integration.persistence.test_validation_history import (
    scope as scope,
    migrated as migrated,
)
from tests.integration.persistence.test_curator_selected_authority import (
    scenario as scenario,
)


@pytest.fixture
def candidate(scope, scenario, monkeypatch):
    from auth_runtime.base import AuthPrincipal

    monkeypatch.setattr(
        "src.lib.curation_workspace.review_authorization.get_current_subject_resolver",
        lambda: (
            lambda subject: AuthPrincipal(
                subject=subject, provider="fixture", groups=[]
            )
        ),
    )
    with h.SessionLocal.begin() as db:
        session = CurationReviewSession(
            adapter_key="fixture",
            document_id=UUID(scope.document_id),
            created_by_id=scope.owner_id,
            prepared_at=datetime.now(timezone.utc),
        )
        db.add(session)
        db.flush()
        db.get(DomainEnvelopeModel, scenario.env.envelope_id).session_id = session.id
        scenario.workspace_session_id = str(session.id)
        row = CurationCandidate(
            session=session,
            adapter_key="fixture",
            source=CurationCandidateSource.MANUAL,
            envelope_id=scenario.env.envelope_id,
            object_id="one",
            envelope_revision=scenario.revision,
        )
        db.add(row)
        db.flush()
        return str(session.id), str(row.id)


def test_history_is_owned_paged_and_read_only(scope, scenario, candidate, monkeypatch):
    monkeypatch.setenv("VALIDATION_HISTORY_PAGE_SIZE", "2")
    _, cid = candidate
    with h.SessionLocal() as db:
        before = db.scalar(select(func.count()).select_from(ValidationFact))
        page = history.history_page(db, cid, owner_id=scope.owner_id)
        seen = {entry["id"] for entry in page["entries"]}
        cursor = page["next_after"]
        while cursor is not None:
            continuation = history.history_page(
                db, cid, owner_id=scope.owner_id, after=cursor
            )
            ids = {entry["id"] for entry in continuation["entries"]}
            assert not seen.intersection(ids)
            seen.update(ids)
            assert (
                continuation["next_after"] is None
                or continuation["next_after"] > cursor
            )
            cursor = continuation["next_after"]
        baseline = history.history_detail(
            db,
            cid,
            owner_id=scope.owner_id,
            fact_id="baseline",
            sha256=page["baseline"]["sha256"],
        )
        assert "eco:1" in baseline["content"]
        assert db.scalar(select(func.count()).select_from(ValidationFact)) == before
        with pytest.raises(h.HistoryAccessError):
            history.history_page(db, cid, owner_id="another-user")


def test_submit_is_idempotent_and_freezes_intent_before_worker(
    scope, scenario, candidate
):
    sid, cid = candidate
    request_id = str(uuid4())
    with h.SessionLocal.begin() as db:
        first = operations.submit(
            db,
            owner_id=scope.owner_id,
            session_id=sid,
            request_id=request_id,
            candidate_ids=[cid],
            guidance="Check nomenclature",
            groups=[],
        )
    scenario.edit("ECO:LATER")
    with h.SessionLocal.begin() as db:
        second = operations.submit(
            db,
            owner_id=scope.owner_id,
            session_id=sid,
            request_id=request_id,
            candidate_ids=[cid],
            guidance="Check nomenclature",
            groups=[],
        )
        assert first == second
        row = db.get(WorkspaceReviewOperation, first["operation_id"])
        assert row.state == "queued"
        cutoff = row.artifacts[0]["intents"][0]["through_sequence"]
        newest = db.scalar(
            select(func.max(ValidationFact.sequence)).where(
                ValidationFact.artifact_id == scenario.aid
            )
        )
        assert cutoff < newest
    with h.SessionLocal.begin() as db:
        with pytest.raises(h.HistoryConflict):
            operations.submit(
                db,
                owner_id=scope.owner_id,
                session_id=sid,
                request_id=request_id,
                candidate_ids=[cid],
                guidance="Different request",
                groups=[],
            )


def test_reopening_record_only_reads_its_saved_operation(scope, scenario, candidate):
    sid, cid = candidate
    with h.SessionLocal.begin() as db:
        assert operations.latest_for_candidate(db, cid, owner_id=scope.owner_id) is None
        saved = operations.submit(
            db,
            owner_id=scope.owner_id,
            session_id=sid,
            request_id=str(uuid4()),
            candidate_ids=[cid],
            guidance=None,
            groups=[],
        )
    with h.SessionLocal() as db:
        before = db.scalar(select(func.count()).select_from(ValidationFact))
        assert (
            operations.latest_for_candidate(db, cid, owner_id=scope.owner_id) == saved
        )
        assert (
            operations.latest_for_candidate(db, cid, owner_id=scope.owner_id) == saved
        )
        assert db.scalar(select(func.count()).select_from(ValidationFact)) == before
        with pytest.raises(h.HistoryAccessError):
            operations.latest_for_candidate(db, cid, owner_id="someone-else")


def test_prepare_recovers_the_same_saved_plan_without_new_episode(
    scope, scenario, candidate, monkeypatch
):
    from src.lib.validation_review import validator_adapter

    monkeypatch.setattr(
        validator_adapter,
        "binding_contract",
        lambda match: {"binding_id": match.binding.binding_id},
    )
    sid, cid = candidate
    with h.SessionLocal.begin() as db:
        saved = operations.submit(
            db,
            owner_id=scope.owner_id,
            session_id=sid,
            request_id=str(uuid4()),
            candidate_ids=[cid],
            guidance="Check this exact record again",
            groups=[],
        )
        row = db.get(WorkspaceReviewOperation, saved["operation_id"])
        row.state, row.lease_token = "preparing", "test-lease"
    first = operations.prepare(saved["operation_id"], "test-lease")
    with h.SessionLocal.begin() as db:
        before = db.scalar(
            select(func.count())
            .select_from(ValidationFact)
            .where(ValidationFact.artifact_id == scenario.aid)
        )
        row = db.get(WorkspaceReviewOperation, saved["operation_id"])
        row.state = "preparing"
    recovered = operations.prepare(saved["operation_id"], "test-lease")
    assert first == recovered
    with h.SessionLocal() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(ValidationFact)
                .where(ValidationFact.artifact_id == scenario.aid)
            )
            == before
        )


def test_queue_serializes_shared_artifacts_and_never_replays_expired_running(
    scope, scenario, candidate
):
    from datetime import timedelta

    sid, cid = candidate
    with h.SessionLocal.begin() as db:
        # Remove unrelated durable fixtures from eligibility, retaining their history.
        from sqlalchemy import update

        db.execute(
            update(WorkspaceReviewOperation)
            .where(
                WorkspaceReviewOperation.state.in_(["queued", "preparing", "running"])
            )
            .values(state="cancelled")
        )
        first = operations.submit(
            db,
            owner_id=scope.owner_id,
            session_id=sid,
            request_id=str(uuid4()),
            candidate_ids=[cid],
            guidance=None,
            groups=[],
        )
        second = operations.submit(
            db,
            owner_id=scope.owner_id,
            session_id=sid,
            request_id=str(uuid4()),
            candidate_ids=[cid],
            guidance=None,
            groups=[],
        )
    claimed = operations.claim()
    assert claimed[0] in {first["operation_id"], second["operation_id"]}
    assert operations.claim() is None
    with h.SessionLocal.begin() as db:
        row = db.get(WorkspaceReviewOperation, claimed[0])
        row.state = "running"
        row.lease_until = operations.admission.now() - timedelta(seconds=1)
    next_claim = operations.claim()
    assert {next_claim[0], claimed[0]} == {
        first["operation_id"],
        second["operation_id"],
    }
    with h.SessionLocal() as db:
        assert db.get(WorkspaceReviewOperation, claimed[0]).state == "uncertain"
    assert not operations.heartbeat(*claimed)


def test_operation_request_identity_and_history_cannot_be_rewritten(
    scope, scenario, candidate
):
    from sqlalchemy import update, delete
    from sqlalchemy.exc import DBAPIError

    sid, cid = candidate
    with h.SessionLocal.begin() as db:
        saved = operations.submit(
            db,
            owner_id=scope.owner_id,
            session_id=sid,
            request_id=str(uuid4()),
            candidate_ids=[cid],
            guidance=None,
            groups=[],
        )
    for statement in (
        update(WorkspaceReviewOperation)
        .where(WorkspaceReviewOperation.id == saved["operation_id"])
        .values(request={"fake": True}),
        delete(WorkspaceReviewOperation).where(
            WorkspaceReviewOperation.id == saved["operation_id"]
        ),
    ):
        with pytest.raises(DBAPIError):
            with h.SessionLocal.begin() as db:
                db.execute(statement)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "expected_stop", [None, "scientific_attempts_exhausted", "expired"]
)
async def test_worker_failure_has_one_sanitized_owner_and_expected_stops_do_not_alert(
    scope, scenario, candidate, monkeypatch, expected_stop
):
    from src.lib.observability import runtime as reporting

    sid, cid = candidate
    with h.SessionLocal.begin() as db:
        saved = operations.submit(
            db,
            owner_id=scope.owner_id,
            session_id=sid,
            request_id=str(uuid4()),
            candidate_ids=[cid],
            guidance="private curator text",
            groups=[],
        )
        row = db.get(WorkspaceReviewOperation, saved["operation_id"])
        row.state, row.lease_token = "preparing", "test-lease"
    error = (
        operations.admission.ReviewStopped(expected_stop)
        if expected_stop
        else RuntimeError("Bearer SECRET-TOKEN private curator text postgres://private")
    )

    def fail(*args):
        raise error

    reports = []
    monkeypatch.setattr(operations, "prepare", fail)
    monkeypatch.setattr(
        reporting,
        "report_runtime_exception",
        lambda error, **kw: reports.append((error, kw)) or True,
    )
    await operations._execute(saved["operation_id"], "test-lease", "trace-test")
    with h.SessionLocal() as db:
        row = db.get(WorkspaceReviewOperation, saved["operation_id"])
        assert row.state == ("needs_review" if expected_stop else "failed")
        assert "SECRET-TOKEN" not in str(row.outcome)
        if expected_stop:
            assert row.outcome["reason"] == expected_stop
    assert len(reports) == (0 if expected_stop else 1)
    if reports:
        captured, tags = reports[0]
        assert str(captured) == "Validation review operational failure (RuntimeError)"
        assert captured.__cause__ is None and captured.__context__ is None
        assert "private" not in str(tags)


@pytest.mark.asyncio
@pytest.mark.parametrize("action,caveat", [("accept", False), ("accept", True), ("reject", True)])
async def test_worker_finishes_real_runner_admission_and_publication_with_fake_models(
    scope, scenario, candidate, monkeypatch, action, caveat
):
    from agents import Agent
    from agents.tracing import get_trace_provider

    # Keep the real SDK runner while preventing its optional network trace export.
    # Restore both cached flags through monkeypatch so later tests retain their policy.
    provider = get_trace_provider()
    monkeypatch.setattr(provider, "_manual_disabled", True)
    monkeypatch.setattr(provider, "_disabled", True)
    from unittest.mock import AsyncMock
    from src.lib.curation_workspace.models import CurationDraft
    from src.lib.validation_review import validator_adapter, reviewer
    from tests.unit.lib.domain_packs.test_validator_dispatch import _result_payload

    monkeypatch.setattr(
        "src.lib.redis_client.check_cancel_signal", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(
        validator_adapter,
        "binding_contract",
        lambda match: {"binding_id": match.binding.binding_id},
    )
    monkeypatch.setattr(
        validator_adapter,
        "_default_package_scoped_validator_runner",
        lambda: (
            lambda request, **kw: {
                **_result_payload(request, resolved_values={"curie": "ECO:NEW"}),
                "resolved_objects": [],
                "unresolved_questions": ["The supplied evidence leaves a caveat"] if caveat else [],
            }
        ),
    )
    monkeypatch.setattr(
        validator_adapter,
        "_default_package_scoped_validator_batch_runner",
        lambda: lambda *a, **k: pytest.fail("Unexpected batch"),
    )

    from tests.unit.lib.openai_agents.test_model_request_measurement import (
        _tool_call, _final,
    )
    import json

    monkeypatch.setenv(
        "AGENT_MODEL_CAPACITY_OVERRIDES_JSON",
        '{"gpt-test":{"context_window_tokens":1050000,"max_input_tokens":922000,"max_output_tokens":128000}}',
    )
    monkeypatch.setattr(
        "src.lib.openai_agents.context_capacity.NativeCapacityRequest.count",
        AsyncMock(return_value=1000),
    )

    from tests.validation_review_runtime_support import HistoryReadingReviewer

    monkeypatch.setenv("VALIDATION_REVIEW_PACKET_BYTES", "2048")

    class ScriptedReviewer(HistoryReadingReviewer):
        def review(self):
            reference = yield _tool_call("get_validation_review_packet", "packet", '{"operation_id":"inspect"}')
            packet = yield from self.read(reference)
            baseline = yield from self.read(packet["baseline"])
            assert "eco:1" in json.dumps(baseline)
            entries = []
            for unit in packet["manifest"]["units"]:
                source_ref = yield _tool_call("get_validation_record_source", f"source-{len(self.calls)}",
                                             json.dumps({"target_id": unit["target_id"]}))
                source = yield from self.read(source_ref)
                assert source["record"] in baseline["normalized"]["extracted_objects"]
                candidates = sorted((fact for fact in packet["facts"]
                    if fact["kind"] == "attempt" and fact["target_id"] == unit["target_id"]),
                    key=lambda fact: fact["sequence"], reverse=True)
                assert candidates
                attempt = candidates[0]
                attempt_body = yield from self.read(attempt)
                assert attempt_body["input"]["review_episode_id"] == packet["episode_id"]
                completion = next(fact for fact in packet["facts"]
                    if fact["kind"] == "completion" and fact["parent_id"] == attempt["id"])
                answer = yield from self.read(completion)
                assert "ECO:NEW" in json.dumps(answer)
                assert ("The supplied evidence leaves a caveat" in json.dumps(answer)) is caveat
                entries.append({
                    "target_id": unit["target_id"], "decision_unit_id": unit["target_id"],
                    "expected_version": unit["version"], "logical_attempt_id": attempt["id"],
                    "extraction_ref": None, "action": action,
                    "reason": "Scripted supervisor reviewed the original and exact saved answer",
                })
            decision = yield _tool_call("record_validation_review", "decide", json.dumps({
                "operation_id": "decide", "entries": entries,
            }))
            assert decision["ready"] is (action == "accept")
            status = yield _tool_call("get_validation_review_status", "status")
            assert status["ready"] is (action == "accept")
            yield _final("Reviewed")

    model = ScriptedReviewer()

    def supervisor(**kwargs):
        from src.lib.observability.cost_context import attach_agent_cost_identity

        agent = Agent(name="Supervisor", model=model, instructions="Review saved answers.")
        attach_agent_cost_identity(
            agent,
            {
                "agent_id": "supervisor",
                "agent_name": "Supervisor",
                "agent_role": "supervisor",
            },
        )
        return agent

    monkeypatch.setattr(reviewer, "create_direct_supervisor", supervisor)
    monkeypatch.setattr("src.lib.prompts.assembly.get_all_active_prompts", lambda: {})

    def unexpected(error):
        raise error

    monkeypatch.setattr(
        "src.lib.validation_review.audit.report_operational_failure", unexpected
    )
    sid, cid = candidate
    with h.SessionLocal.begin() as db:
        db.add(
            CurationDraft(
                candidate_id=UUID(cid),
                adapter_key="fixture",
                version=1,
                fields=[],
                draft_metadata={},
            )
        )
        saved = operations.submit(
            db,
            owner_id=scope.owner_id,
            session_id=sid,
            request_id=str(uuid4()),
            candidate_ids=[cid],
            guidance="Check this record",
            groups=[],
        )
        row = db.get(WorkspaceReviewOperation, saved["operation_id"])
        row.state, row.lease_token = "preparing", "test-lease"

    await operations._execute(saved["operation_id"], "test-lease", "test-trace")
    with h.SessionLocal() as db:
        row = db.get(WorkspaceReviewOperation, saved["operation_id"])
        assert model.calls, "The real SDK runner must reach the scripted provider"
        assert len(model.read_bodies) >= 4  # Packet, original, attempt, answer.
        assert model.read_pages > len(model.read_bodies)  # Complete paginated history.
        assert row.state == ("complete" if action == "accept" else "needs_review"), row.outcome
        assert row.outcome["results"][0]["review"]["ready"] is (action == "accept")
        assert row.outcome["trace_id"] == "test-trace"
        envelope = db.get(DomainEnvelopeModel, scenario.env.envelope_id)
        assert envelope.envelope_json["extracted_objects"][0]["payload"][
            "evidence_code_curies"
        ] == (["ECO:NEW"] if action == "accept" else ["ECO:VALIDATED"])
        responses = list(db.scalars(select(ValidationFact).where(
            ValidationFact.artifact_id == scenario.aid,
            ValidationFact.kind == "review_response",
            ValidationFact.parent_id == row.artifacts[0]["episode_id"],
        )))
        assert len(responses) == 1
        assert {entry["action"] for entry in h._content(db, responses[0])["response"]["applied"]} == {action}


def test_uninitialized_manual_record_can_observe_absent_round(scope, candidate):
    _, cid = candidate
    with h.SessionLocal.begin() as db:
        row = db.get(CurationCandidate, UUID(cid))
        row.envelope_id = None
        row.object_id = None
        row.envelope_revision = None
        db.flush()
        assert operations.latest_for_candidate(db, cid, owner_id=scope.owner_id) is None
        assert row.envelope_id is None
        with pytest.raises(h.HistoryAccessError):
            operations.latest_for_candidate(db, cid, owner_id="another-user")


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["revoked_group", "disabled", "unavailable"])
async def test_queue_reauthorizes_before_paid_work(
    scope, candidate, monkeypatch, failure
):
    from auth_runtime.base import AuthPrincipal
    from src.models.sql.user import User

    sid, cid = candidate
    with h.SessionLocal.begin() as db:
        saved = operations.submit(
            db,
            owner_id=scope.owner_id,
            session_id=sid,
            request_id=str(uuid4()),
            candidate_ids=[cid],
            guidance=None,
            groups=["lost-group"] if failure == "revoked_group" else [],
        )
        row = db.get(WorkspaceReviewOperation, saved["operation_id"])
        row.state, row.lease_token = "preparing", "test-lease"
        if failure == "disabled":
            db.scalar(
                select(User).where(User.auth_sub == scope.owner_id)
            ).is_active = False

    def lookup(subject):
        if failure == "unavailable":
            raise RuntimeError("private provider error")
        return AuthPrincipal(subject=subject, provider="fixture", groups=[])

    monkeypatch.setattr(
        "src.lib.curation_workspace.review_authorization.get_current_subject_resolver",
        lambda: lookup,
    )
    from unittest.mock import Mock

    provider = Mock(side_effect=AssertionError("No paid execution allowed"))
    monkeypatch.setattr(operations.reviewer, "create_direct_supervisor", provider)
    report = Mock()
    monkeypatch.setattr(
        "src.lib.validation_review.audit.report_operational_failure", report
    )
    await operations._execute(saved["operation_id"], "test-lease", "trace")
    provider.assert_not_called()
    with h.SessionLocal() as db:
        row = db.get(WorkspaceReviewOperation, saved["operation_id"])
        assert row.state == ("failed" if failure == "unavailable" else "needs_review")
    assert report.call_count == (1 if failure == "unavailable" else 0)


def test_record_summary_tracks_rejection_selection_and_curator_overlay(
    scope, scenario, candidate
):
    _, cid = candidate
    x = scenario
    x.episode_fn()
    rejected = x.propose("ECO:REJECTED")
    x.publish(rejected, "reject")
    accepted = x.propose(None, status="unresolved")
    x.publish(accepted)
    x.edit("ECO:CURATOR")
    x.publish(accepted)
    with h.SessionLocal() as db:
        page = history.history_page(db, cid, owner_id=scope.owner_id)
        entries = page["entries"]
        while page["next_after"] is not None:
            page = history.history_page(
                db, cid, owner_id=scope.owner_id, after=page["next_after"]
            )
            entries.extend(page["entries"])
        by_id = {entry["id"]: entry for entry in entries}
        assert by_id[rejected]["answer_states"][0]["review"] == "reject"
        assert by_id[accepted]["scientific_status"] == "unresolved"
        assert by_id[accepted]["answer_states"][0]["selected"] is True
        assert by_id[accepted]["answer_states"][0]["review"] == "accept"
        assert "eco:1" in str(page["record"]["original"])
        assert "ECO:CURATOR" in str(page["record"]["current"])
        assert page["record"]["curator_overrides"]


def test_current_access_never_expands_saved_groups(scope, candidate, monkeypatch):
    from auth_runtime.base import AuthPrincipal
    from src.lib.curation_workspace.review_authorization import require_current_access

    monkeypatch.setattr(
        "src.lib.curation_workspace.review_authorization.get_current_subject_resolver",
        lambda: (
            lambda subject: AuthPrincipal(
                subject=subject, provider="fixture", groups=["new-provider-group"]
            )
        ),
    )
    monkeypatch.setattr(
        "src.lib.curation_workspace.review_authorization.get_groups_from_provider_groups",
        lambda groups: ["old", "new"],
    )
    assert require_current_access(scope.owner_id, ["old"]) == ("old",)


def test_all_records_status_is_distinct_and_survives_candidate_deletion(
    scope, candidate
):
    sid, cid = candidate
    with h.SessionLocal.begin() as db:
        all_records = operations.submit(
            db,
            owner_id=scope.owner_id,
            session_id=sid,
            request_id=str(uuid4()),
            candidate_ids=[],
            guidance=None,
            groups=[],
        )
        record_only = operations.submit(
            db,
            owner_id=scope.owner_id,
            session_id=sid,
            request_id=str(uuid4()),
            candidate_ids=[cid],
            guidance=None,
            groups=[],
        )
        assert (
            operations.latest_for_session(db, sid, owner_id=scope.owner_id)[
                "operation_id"
            ]
            == all_records["operation_id"]
        )
        assert len(operations.active_for_session(db, sid, owner_id=scope.owner_id)) == 2
        db.delete(db.get(CurationCandidate, UUID(cid)))
    with h.SessionLocal() as db:
        assert (
            operations.get(
                db, record_only["operation_id"], owner_id=scope.owner_id
            ).state
            == "queued"
        )
        assert (
            operations.latest_for_session(db, sid, owner_id=scope.owner_id)[
                "operation_id"
            ]
            == all_records["operation_id"]
        )
    assert (
        operations.cancel(all_records["operation_id"], owner_id=scope.owner_id)["state"]
        == "cancelled"
    )


def test_all_records_reports_excluded_sources_and_groups_shared_artifact(
    scope, candidate
):
    from fastapi import HTTPException

    sid, cid = candidate
    with h.SessionLocal.begin() as db:
        original = db.get(CurationCandidate, UUID(cid))
        duplicate = CurationCandidate(
            session=original.session,
            adapter_key="fixture",
            source=CurationCandidateSource.MANUAL,
            envelope_id=original.envelope_id,
            object_id=original.object_id,
            envelope_revision=original.envelope_revision,
        )
        unavailable = CurationCandidate(
            session=original.session,
            adapter_key="fixture",
            source=CurationCandidateSource.MANUAL,
        )
        db.add_all([duplicate, unavailable])
        db.flush()
        saved = operations.submit(
            db,
            owner_id=scope.owner_id,
            session_id=sid,
            request_id=str(uuid4()),
            candidate_ids=[],
            guidance=None,
            groups=[],
        )
        row = db.get(WorkspaceReviewOperation, saved["operation_id"])
        assert len(row.artifacts) == 1
        assert len(row.artifacts[0]["intents"]) == 2
        assert saved["outcome"]["excluded_records"][0]["candidate_id"] == str(
            unavailable.id
        )
        with pytest.raises(HTTPException) as exc:
            operations.submit(
                db,
                owner_id=scope.owner_id,
                session_id=sid,
                request_id=str(uuid4()),
                candidate_ids=[str(unavailable.id)],
                guidance=None,
                groups=[],
            )
        assert exc.value.status_code == 409


@pytest.mark.parametrize("state", ["queued", "preparing", "running"])
def test_cancel_preserves_command_and_closes_admitted_children(
    scope, scenario, candidate, monkeypatch, state
):
    from src.lib.validation_review import validator_adapter
    from src.models.sql.validation_review import ValidationReviewEpisode

    monkeypatch.setattr(
        validator_adapter,
        "binding_contract",
        lambda match: {"binding_id": match.binding.binding_id},
    )
    sid, cid = candidate
    with h.SessionLocal.begin() as db:
        saved = operations.submit(
            db,
            owner_id=scope.owner_id,
            session_id=sid,
            request_id=str(uuid4()),
            candidate_ids=[cid],
            guidance=None,
            groups=[],
        )
        row = db.get(WorkspaceReviewOperation, saved["operation_id"])
        if state != "queued":
            row.state, row.lease_token = "preparing", "lease"
    if state != "queued":
        _, _, artifacts = operations.prepare(saved["operation_id"], "lease")
        with h.SessionLocal.begin() as db:
            db.get(WorkspaceReviewOperation, saved["operation_id"]).state = state
    assert (
        operations.cancel(saved["operation_id"], owner_id=scope.owner_id)["state"]
        == "cancelled"
    )
    with h.SessionLocal() as db:
        row = operations.get(db, saved["operation_id"], owner_id=scope.owner_id)
        assert row.request["candidate_ids"] == [cid]
        if state != "queued":
            assert (
                db.get(ValidationReviewEpisode, artifacts[0]["episode_id"]).state
                == "cancelled"
            )


def test_resume_reuses_grant_and_saved_plan(scope, scenario, candidate, monkeypatch):
    from src.lib.validation_review import validator_adapter
    from src.models.sql.validation_review import ValidationReviewEpisode

    monkeypatch.setattr(
        validator_adapter,
        "binding_contract",
        lambda match: {"binding_id": match.binding.binding_id},
    )
    sid, cid = candidate
    with h.SessionLocal.begin() as db:
        saved = operations.submit(
            db,
            owner_id=scope.owner_id,
            session_id=sid,
            request_id=str(uuid4()),
            candidate_ids=[cid],
            guidance=None,
            groups=[],
        )
        row = db.get(WorkspaceReviewOperation, saved["operation_id"])
        row.state, row.lease_token = "preparing", "lease"
    first = operations.prepare(saved["operation_id"], "lease")
    with h.SessionLocal.begin() as db:
        row = db.get(WorkspaceReviewOperation, saved["operation_id"])
        row.state = "needs_review"
        episode = db.get(ValidationReviewEpisode, first[2][0]["episode_id"])
        grant = episode.scope_id, episode.allowances, episode.charged
        operations.resume(
            db, saved["operation_id"], owner_id=scope.owner_id, groups=["newly-granted"]
        )
        row.state = "preparing"
    assert operations.prepare(saved["operation_id"], "lease") == first
    with h.SessionLocal() as db:
        episode = db.get(ValidationReviewEpisode, first[2][0]["episode_id"])
        assert (episode.scope_id, episode.allowances, episode.charged) == grant


@pytest.mark.parametrize("interruption", ["stop", "failure", "admitted_stop"])
def test_stop_before_admission_preserves_manual_decision_and_finalization(
    scope, scenario, candidate, monkeypatch, interruption
):
    from src.lib.validation_review import validator_adapter, admission
    from src.lib.validation_history.episodes import is_abandoned
    from src.lib.validation_history.projection import read_published_projection
    from src.models.sql.validation_review import (
        ValidationReviewEpisode,
        ValidationReviewScope,
    )
    from src.lib.curation_workspace.models import CurationDraft
    from src.schemas.workspace_review import WorkspaceReviewDecisionRequest

    monkeypatch.setattr(
        validator_adapter,
        "binding_contract",
        lambda match: {"binding_id": match.binding.binding_id},
    )
    sid, cid = candidate
    with h.SessionLocal.begin() as db:
        prior = operations.submit(
            db,
            owner_id=scope.owner_id,
            session_id=sid,
            request_id=str(uuid4()),
            candidate_ids=[cid],
            guidance=None,
            groups=[],
        )
        row = db.get(WorkspaceReviewOperation, prior["operation_id"])
        row.state, row.lease_token = "preparing", "prior-lease"
    _, _, prior_artifacts = operations.prepare(prior["operation_id"], "prior-lease")
    prior_episode = prior_artifacts[0]["episode_id"]
    with h.SessionLocal.begin() as db:
        db.add(
            CurationDraft(
                candidate_id=UUID(cid), adapter_key="fixture", fields=[], version=1
            )
        )
        db.get(WorkspaceReviewOperation, prior["operation_id"]).state = "needs_review"
        run = db.get(ValidationReviewEpisode, prior_episode).scope_id
        grants_before = db.scalar(
            select(func.count()).select_from(ValidationReviewScope)
        )
        saved = operations.submit(
            db,
            owner_id=scope.owner_id,
            session_id=sid,
            request_id=str(uuid4()),
            candidate_ids=[cid],
            guidance=None,
            groups=[],
        )
        row = db.get(WorkspaceReviewOperation, saved["operation_id"])
        row.state, row.lease_token = "preparing", "lease"
    save_prepared = operations._save_prepared
    abandoned_episode = []

    def stop_between_plan_and_admission(operation_id, lease, prepared):
        abandoned_episode.append(prepared[0]["episode_id"])
        if interruption == "failure":
            raise h.HistoryConflict("A later source changed during preparation")
        if interruption == "stop":
            operations.cancel(operation_id, owner_id=scope.owner_id)
        return save_prepared(operation_id, lease, prepared)

    monkeypatch.setattr(operations, "_save_prepared", stop_between_plan_and_admission)
    if interruption == "failure":
        import asyncio

        asyncio.run(operations._execute(saved["operation_id"], "lease", "test-trace"))
        with h.SessionLocal() as db:
            stopped = db.get(WorkspaceReviewOperation, saved["operation_id"])
            assert stopped.state == "needs_review"
            assert stopped.outcome["reason"] == "preparation_abandoned"
            with pytest.raises(h.HistoryConflict, match="Preparation was abandoned"):
                operations.resume(db, saved["operation_id"], owner_id=scope.owner_id, groups=[])
    elif interruption == "stop":
        with pytest.raises(admission.ReviewStopped):
            operations.prepare(saved["operation_id"], "lease")
    else:
        operations.prepare(saved["operation_id"], "lease")
        operations.cancel(saved["operation_id"], owner_id=scope.owner_id)
        prior_episode = abandoned_episode[0]
    with h.SessionLocal() as db:
        assert is_abandoned(db, scenario.aid, abandoned_episode[0]) == (
            interruption != "admitted_stop"
        )
        if interruption != "admitted_stop":
            assert db.get(ValidationReviewEpisode, abandoned_episode[0]) is None
            assert (
                db.scalar(select(func.count()).select_from(ValidationReviewScope))
                == grants_before
            )
        else:
            assert (
                db.get(ValidationReviewEpisode, abandoned_episode[0]).state
                == "cancelled"
            )
        page = history.history_page(db, cid, owner_id=scope.owner_id)
        assert page["episode_id"] == prior_episode
        completion = h._fact(db, scenario.aid, scenario.first)
        version = h._current(db, scenario.aid, scenario.target)["version"]
        attempt_id = completion.parent_id
    result = history.decide(
        cid,
        owner_id=scope.owner_id,
        groups=[],
        request=WorkspaceReviewDecisionRequest(
            request_id=uuid4(),
            episode_id=prior_episode,
            entries=[
                {
                    "target_id": scenario.target,
                    "decision_unit_id": scenario.target,
                    "logical_attempt_id": attempt_id,
                    "expected_version": version,
                    "action": "accept",
                    "reason": "Curator confirms the saved answer after cancelling preparation",
                }
            ],
        ),
    )
    assert result["ready"]
    published, manifest = read_published_projection(
        scope, result["publication"], finalized=True
    )
    assert manifest["ready"]
    assert published.extracted_objects[0].payload["evidence_code_curies"] == [
        "ECO:VALIDATED"
    ]
    if interruption != "admitted_stop":
        with pytest.raises(admission.ReviewStopped, match="preparation_abandoned"):
            admission.attach_episode(
                scope,
                artifact_id=scenario.aid,
                episode_id=abandoned_episode[0],
                scope_id=run,
            )
    with h.SessionLocal() as db:
        assert h._fact(db, scenario.aid, abandoned_episode[0]).kind == "review_episode"


@pytest.mark.asyncio
async def test_partial_admission_failure_closes_admitted_children(scope, scenario, candidate, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from src.lib.validation_review import validator_adapter, admission
    from src.lib.validation_history.episodes import is_abandoned
    from src.models.sql.validation_review import ValidationReviewEpisode
    from .test_curator_selected_authority import scenario as make_scenario
    second_path = tmp_path / "second"
    second_path.mkdir()
    second = make_scenario.__wrapped__(scope, second_path, monkeypatch, SimpleNamespace(param=False))
    sid, _ = candidate
    with h.SessionLocal.begin() as db:
        db.get(DomainEnvelopeModel, second.env.envelope_id).session_id = UUID(sid)
        db.add(CurationCandidate(session_id=UUID(sid), adapter_key="fixture",
            source=CurationCandidateSource.MANUAL, envelope_id=second.env.envelope_id,
            object_id="one", envelope_revision=second.revision))
        db.flush()
        saved = operations.submit(db, owner_id=scope.owner_id, session_id=sid,
            request_id=str(uuid4()), candidate_ids=[], guidance=None, groups=[])
        row = db.get(WorkspaceReviewOperation, saved["operation_id"])
        assert len(row.artifacts) == 2
        attempts_before = {item["artifact_id"]: db.scalar(select(func.count()).select_from(ValidationFact).where(
            ValidationFact.artifact_id == item["artifact_id"], ValidationFact.kind == "attempt")) for item in row.artifacts}
        row.state, row.lease_token = "preparing", "partial-lease"
    monkeypatch.setattr(validator_adapter, "binding_contract", lambda match: {"binding_id": match.binding.binding_id})
    original_attach = admission.attach_episode
    attached = []
    def attach_first(*args, **kwargs):
        if attached:
            raise h.HistoryConflict("Second source changed before admission")
        result = original_attach(*args, **kwargs)
        attached.append(kwargs["episode_id"])
        return result
    monkeypatch.setattr(admission, "attach_episode", attach_first)
    monkeypatch.setattr(validator_adapter, "_default_package_scoped_validator_runner", lambda: pytest.fail("No execution before full preparation"))
    await operations._execute(saved["operation_id"], "partial-lease", "partial-trace")
    with h.SessionLocal() as db:
        row = db.get(WorkspaceReviewOperation, saved["operation_id"])
        assert row.state == "needs_review"
        assert row.outcome["reason"] == "preparation_abandoned"
        first, second = row.artifacts
        assert db.get(ValidationReviewEpisode, first["episode_id"]).state == "cancelled"
        assert not is_abandoned(db, first["artifact_id"], first["episode_id"])
        assert db.get(ValidationReviewEpisode, second["episode_id"]) is None
        assert is_abandoned(db, second["artifact_id"], second["episode_id"])
        for item in row.artifacts:
            assert db.scalar(select(func.count()).select_from(ValidationFact).where(
                ValidationFact.artifact_id == item["artifact_id"], ValidationFact.kind == "attempt")) == attempts_before[item["artifact_id"]]

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
def candidate(scope, scenario):
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
@pytest.mark.parametrize("expected_stop", [False, True])
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
        operations.admission.ReviewStopped("scientific_attempts_exhausted")
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
    assert len(reports) == (0 if expected_stop else 1)
    if reports:
        captured, tags = reports[0]
        assert str(captured) == "Validation review operational failure (RuntimeError)"
        assert captured.__cause__ is None and captured.__context__ is None
        assert "private" not in str(tags)


@pytest.mark.asyncio
async def test_worker_finishes_real_admission_and_publication_with_fake_models(scope, scenario, candidate, monkeypatch):
    from agents import Agent
    from unittest.mock import AsyncMock
    from src.lib.curation_workspace.models import CurationDraft
    from src.lib.validation_review import validator_adapter, decisions, reviewer
    from tests.unit.lib.domain_packs.test_validator_dispatch import _result_payload
    monkeypatch.setattr('src.lib.redis_client.check_cancel_signal', AsyncMock(return_value=False))
    monkeypatch.setattr(validator_adapter, 'binding_contract', lambda match: {'binding_id': match.binding.binding_id})
    monkeypatch.setattr(validator_adapter, '_default_package_scoped_validator_runner',
        lambda: lambda request, **kw: {**_result_payload(request, resolved_values={'curie': 'ECO:NEW'}), 'resolved_objects': []})
    monkeypatch.setattr(validator_adapter, '_default_package_scoped_validator_batch_runner', lambda: lambda *a, **k: pytest.fail('Unexpected batch'))
    def supervisor(**kwargs):
        from src.lib.observability.cost_context import attach_agent_cost_identity
        agent = Agent(name='Supervisor', instructions='Review saved answers.')
        attach_agent_cost_identity(agent, {'agent_id': 'supervisor', 'agent_name': 'Supervisor', 'agent_role': 'supervisor'})
        return agent
    monkeypatch.setattr(reviewer, 'create_direct_supervisor', supervisor)
    monkeypatch.setattr('src.lib.prompts.assembly.get_all_active_prompts', lambda: {})
    def unexpected(error):
        raise error
    monkeypatch.setattr('src.lib.validation_review.audit.report_operational_failure', unexpected)
    sid, cid = candidate
    with h.SessionLocal.begin() as db:
        db.add(CurationDraft(candidate_id=UUID(cid), adapter_key="fixture", version=1, fields=[], draft_metadata={}))
        saved = operations.submit(db, owner_id=scope.owner_id, session_id=sid, request_id=str(uuid4()), candidate_ids=[cid], guidance='Check this record', groups=[])
        row = db.get(WorkspaceReviewOperation, saved['operation_id'])
        row.state, row.lease_token = 'preparing', 'test-lease'
    async def run(context, agent, **kwargs):
        assert all(tool.name != 'read_section' for tool in agent.tools)
        with h.SessionLocal() as db:
            row = db.get(WorkspaceReviewOperation, saved['operation_id'])
            item = row.artifacts[0]
            attempts = list(db.scalars(select(ValidationFact).where(ValidationFact.artifact_id == scenario.aid, ValidationFact.kind == 'attempt')))
            attempts = [a for a in attempts if h._content(db, a)['input']['review_episode_id'] == context.episode_id]
            entries = [{'target_id': a.target_id, 'decision_unit_id': a.target_id,
                'expected_version': h._current(db, scenario.aid, a.target_id)['version'],
                'logical_attempt_id': a.id, 'action': 'accept', 'reason': 'Fake supervisor reviewed the saved answer'} for a in attempts]
        decisions.record_decisions(context, checkpoint_request=operations.checkpoint(scope.owner_id, item, ()), operation_id='accept', entries=entries)
        return {'output': 'Reviewed', 'review': operations.admission.status(scope, context.episode_id)}
    monkeypatch.setattr(reviewer, 'run', run)
    await operations._execute(saved['operation_id'], 'test-lease', 'test-trace')
    with h.SessionLocal() as db:
        row = db.get(WorkspaceReviewOperation, saved['operation_id'])
        assert row.state == 'complete', row.outcome
        assert row.outcome['trace_id'] == 'test-trace'
        envelope = db.get(DomainEnvelopeModel, scenario.env.envelope_id)
        assert envelope.envelope_json['extracted_objects'][0]['payload']['evidence_code_curies'] == ['ECO:NEW']

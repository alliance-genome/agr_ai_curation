"""Synthetic database evidence for the shared price catalog and report API."""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from src.lib.cost_ledger.facts import TokenUsage, RecordedCharge
from src.lib.cost_ledger.pricing import import_snapshot, load_snapshot, value_usage
from src.lib.cost_ledger.reports import runtime_report, ReportTooLarge
from src.lib.cost_ledger.runtime_context import RuntimeCostContext, runtime_cost_scope
from src.lib.cost_ledger.runtime_writes import reserve_runtime_request
from src.lib.cost_ledger.summary import fold_fact_revisions
from src.models.sql.cost_ledger import CostFactRevision
from src.models.sql.database import SessionLocal


@pytest.fixture(scope="module", autouse=True)
def migrate():
    command.upgrade(Config(str(Path(__file__).resolve().parents[3] / 'alembic.ini')), 'head')


@pytest.fixture
def fixture(monkeypatch):
    deployment, session = uuid4().hex, uuid4().hex
    monkeypatch.setenv('COST_LEDGER_RUNTIME_ENABLED', 'true')
    monkeypatch.setenv('COST_LEDGER_DEPLOYMENT_ID', deployment)
    monkeypatch.setenv('COST_LEDGER_RUNTIME_SOURCE_NAMESPACE', 'admin-report-test')
    prices = {"schema_version": 1, "currency": "USD", "source": "synthetic-test", "captured_at": "2026-01-01T00:00:00Z",
              "providers": {"fixture": [{"id": "test", "unit": "TOKENS", "matchPattern": "^test$", "startDate": "2020-01-01T00:00:00Z",
                 "prices": {"input": "0.00001", "input_cached_tokens": "0.000001", "input_cache_creation": "0.0000125",
                            "output": "0.00005", "output_reasoning_tokens": "0.00005"}}]}}
    with SessionLocal() as db:
        snapshot = import_snapshot(db, prices)
        assert import_snapshot(db, prices) == snapshot
        db.commit()
    attempts = []
    for run in ['one', 'one', 'two']:
        with runtime_cost_scope(RuntimeCostContext('owner', session, run, 'interactive_chat')):
            attempts.append(reserve_runtime_request({'measurement_id': str(uuid4()), 'provider': 'fixture', 'model': 'test'}))
    attempts[0].finish(usage=TokenUsage(1000, 200, 1200, 600, 300, 150), charge=None, outcome='completed')
    attempts[1].finish(usage=TokenUsage(0, 0, 0, 0, 0, 0), charge=RecordedCharge(Decimal('0'), 'credits', 'fixture'), outcome='completed')
    return deployment, session, snapshot, attempts


def read(**kwargs):
    with SessionLocal() as db:
        db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
        return runtime_report(db, **kwargs)


def test_exact_totals_provenance_and_unknowns(fixture):
    deployment, session, snapshot, _ = fixture
    result = read(session_id=session, snapshot_id=snapshot)
    assert result['totals']['attempt_count'] == 3
    assert result['totals']['estimates']['lower'] == '0.0153500'
    assert result['totals']['estimates']['unpriced_attempts'] == 1
    assert result['totals']['recorded_charges'] == [{'unit': 'credits', 'source': 'fixture', 'amount': '0', 'attempts': 1}]
    assert result['totals']['unknown_charge_attempts'] == 2
    assert sorted(r['fact_revision'] for r in result['requests']) == [0, 1, 1]
    assert read(session_id=session, run_id='one', snapshot_id=snapshot)['totals']['attempt_count'] == 2
    now = datetime.now(timezone.utc)
    assert read(start=now - timedelta(days=1), end=now, snapshot_id=snapshot)['scope'] == 'time_window'
    assert result['scope'] == 'full_recorded_session_or_turn'


def test_runtime_metadata_round_trips_without_duplicate_attempt(fixture):
    _, session, snapshot, _ = fixture
    metadata = {'agent_id': 'helper', 'agent_name': 'Helper', 'agent_role': 'extraction',
                'agent_revision': 'revision-a', 'node_id': 'step-a', 'requested_service_tier': 'flex'}
    with runtime_cost_scope(RuntimeCostContext('owner', session, 'flow-turn', 'extraction_flow', 'flow', 'flow-run')):
        attempt = reserve_runtime_request({'measurement_id': str(uuid4()), 'provider': 'fixture', 'model': 'test', **metadata})
    args = dict(usage=TokenUsage(100, 20, 120, 0, 0, 0), charge=None, outcome='completed',
                service_tiers={'requested': 'flex', 'effective': 'default'})
    attempt.finish(**args)
    attempt.finish(**args)
    # An additional usage-only callback cannot erase provider tier evidence.
    attempt.finish(usage=TokenUsage(), charge=None, outcome='completed')
    report = read(session_id=session, run_id='flow-turn', snapshot_id=snapshot)
    assert report['totals']['attempt_count'] == 1
    [row] = report['requests']
    assert {key: row[key] for key in metadata} == metadata
    assert row['effective_service_tier'] == 'default'
    assert row['fact_revision'] == 1
    assert row['flow_run_id'] == 'flow-run'
    assert row['recorded_charge'] is None


def test_late_fact_enrichment_keeps_pinned_revision_reproducible(fixture):
    deployment, session, snapshot, attempts = fixture
    original = read(session_id=session, snapshot_id=snapshot)
    attempts[0].finish(usage=TokenUsage(), charge=RecordedCharge(Decimal('0.02'), 'USD', 'fixture'), outcome='completed')
    updated = read(session_id=session, snapshot_id=snapshot)
    assert updated['totals']['unknown_charge_attempts'] == 1
    pin = next(r for r in original['requests'] if r['attempt_id'] == str(attempts[0].attempt_id))
    with SessionLocal() as db:
        usage, charge, _ = fold_fact_revisions(db.scalars(select(CostFactRevision).where(
            CostFactRevision.deployment_id == deployment, CostFactRevision.attempt_id == attempts[0].attempt_id,
            CostFactRevision.revision <= pin['fact_revision']).order_by(CostFactRevision.revision)))
        assert charge is None
        assert value_usage(usage, provider='fixture', model='test', timestamp=datetime.fromisoformat(pin['created_at']),
                           snapshot=load_snapshot(db, snapshot))['cost'] == pin['estimate']['cost']


def test_snapshot_is_immutable_and_report_refuses_truncation(fixture, monkeypatch):
    _, session, snapshot, _ = fixture
    with SessionLocal() as db:
        with pytest.raises(DBAPIError, match='immutable'):
            db.execute(text('UPDATE cost_price_snapshots SET payload = payload WHERE id = :id'), {'id': snapshot})
    monkeypatch.setenv('COST_REPORT_MAX_ATTEMPTS', '2')
    with pytest.raises(ReportTooLarge):
        read(session_id=session, snapshot_id=snapshot)


def test_provider_isolation_and_unknown_snapshot(fixture):
    _, session, _, _ = fixture
    result = read(session_id=session, provider='another')
    assert result['totals']['attempt_count'] == 0
    with pytest.raises(LookupError):
        read(session_id=session, snapshot_id='absent')


def test_window_does_not_combine_collided_session_owners(fixture):
    _, session, snapshot, _ = fixture
    with runtime_cost_scope(RuntimeCostContext('another-owner', session, 'one', 'interactive_chat')):
        reserve_runtime_request({'measurement_id': str(uuid4()), 'provider': 'fixture', 'model': 'test'})
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError, match='Ambiguous'):
        read(start=now - timedelta(days=1), end=now, snapshot_id=snapshot)

"""Synthetic ordinary request reservations and canonical fact readback."""
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import text

from src.lib.cost_ledger.facts import RecordedCharge, TokenUsage
from src.lib.cost_ledger.runtime_context import RuntimeCostContext, runtime_cost_scope
from src.lib.cost_ledger.runtime_reads import read_runtime_accounting
from src.lib.cost_ledger.runtime_writes import reserve_runtime_request
from src.lib.cost_ledger import runtime_writes
from src.models.sql.database import SessionLocal


@pytest.fixture(scope="module", autouse=True)
def migrate():
    command.upgrade(Config(str(Path(__file__).resolve().parents[3] / 'alembic.ini')), 'head')


def test_runtime_exact_facts_unknown_and_run_filter(monkeypatch):
    deployment, session = uuid4().hex, uuid4().hex
    monkeypatch.setenv('COST_LEDGER_RUNTIME_ENABLED', 'true')
    monkeypatch.setenv('COST_LEDGER_DEPLOYMENT_ID', deployment)
    monkeypatch.setenv('COST_LEDGER_RUNTIME_SOURCE_NAMESPACE', 'runtime-test')
    attempts = []
    for run in ['turn-a', 'turn-a', 'turn-b']:
        with runtime_cost_scope(RuntimeCostContext('owner', session, run, 'interactive_chat')):
            attempts.append(reserve_runtime_request({'measurement_id': str(uuid4()), 'provider': 'fixture'}))
    args = dict(usage=TokenUsage(100, 40, 140), charge=RecordedCharge(Decimal('1.23E-16'), 'credits', 'fixture'), outcome='completed')
    attempts[0].finish(**args)
    attempts[0].finish(**args)  # identical delivery is not another charge
    attempts[1].finish(usage=TokenUsage(0, 0, 0), charge=RecordedCharge(Decimal(0), 'credits', 'fixture'), outcome='completed')
    with SessionLocal() as db:
        db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
        result = read_runtime_accounting(db, session_id=session)
        assert result['attempt_count'] == 3 and result['unknown_charge_attempts'] == 1
        assert result['recorded_charges'][0].amount == Decimal('1.23E-16')
        assert result['usage']['input_tokens'].known_attempts == 2
        assert read_runtime_accounting(db, session_id=session, run_id='turn-a')['attempt_count'] == 2
        with pytest.raises(LookupError):
            read_runtime_accounting(db, session_id='absent')


def test_completion_failure_leaves_visible_pending_unknown(monkeypatch, caplog):
    deployment, session = uuid4().hex, uuid4().hex
    monkeypatch.setenv('COST_LEDGER_RUNTIME_ENABLED', 'true')
    monkeypatch.setenv('COST_LEDGER_DEPLOYMENT_ID', deployment)
    monkeypatch.setenv('COST_LEDGER_RUNTIME_SOURCE_NAMESPACE', 'runtime-test')
    with runtime_cost_scope(RuntimeCostContext('owner', session, 'turn', 'extraction_flow', 'flow', 'flow-run')):
        attempt = reserve_runtime_request({'measurement_id': str(uuid4()), 'provider': 'fixture'})
    def unavailable():
        raise RuntimeError('synthetic secret-bearing database diagnostic')
    monkeypatch.setattr(runtime_writes, 'SessionLocal', unavailable)
    attempt.finish(usage=TokenUsage(10, 5, 15), charge=None, outcome='completed')
    assert 'secret-bearing' not in caplog.text
    with SessionLocal() as db:
        db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
        result = read_runtime_accounting(db, session_id=session)
        assert result['outcomes'] == {'pending': 1}
        assert result['usage']['total_tokens'].known_total is None
        assert result['unknown_charge_attempts'] == 1

"""Admin gating and exact monetary JSON on the runtime read endpoint."""
from decimal import Decimal
import csv
import io
from unittest.mock import MagicMock

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.api.admin import costs
from src.lib import http_errors
import pytest


def test_admin_costs_requires_admin_before_database(monkeypatch):
    app = FastAPI()
    app.include_router(costs.router)
    async def denied():
        raise HTTPException(403, 'Admin required')
    app.dependency_overrides[costs.require_admin] = denied
    database = MagicMock()
    monkeypatch.setattr(costs, 'SessionLocal', database)
    assert TestClient(app).get('/api/admin/cost/sessions/synthetic').status_code == 403
    database.assert_not_called()


def test_admin_costs_decimal_is_string_and_uncached(monkeypatch):
    app = FastAPI()
    app.include_router(costs.router)
    app.dependency_overrides[costs.require_admin] = lambda: {'email': 'admin@example.invalid'}
    monkeypatch.setattr(costs, 'SessionLocal', MagicMock())
    monkeypatch.setattr(costs, 'read_runtime_accounting', lambda *a, **kw: {'amount': Decimal('1.23E-16')})
    response = TestClient(app).get('/api/admin/cost/sessions/synthetic')
    assert response.status_code == 200
    assert response.json() == {'amount': '1.23E-16'}
    assert response.headers['cache-control'] == 'no-store'


def test_server_fault_reports_sanitized_error_but_not_missing_session(monkeypatch, caplog):
    app = FastAPI()
    app.include_router(costs.router)
    app.dependency_overrides[costs.require_admin] = lambda: {'email': 'admin@example.invalid'}
    monkeypatch.setattr(costs, 'SessionLocal', MagicMock())
    capture = MagicMock()
    monkeypatch.setattr(http_errors, 'report_runtime_exception', capture)
    def failed(*args, **kwargs):
        raise ValueError('secret SQL curator text')
    monkeypatch.setattr(costs, 'read_runtime_accounting', failed)
    response = TestClient(app).get('/api/admin/cost/sessions/synthetic')
    assert response.status_code == 503 and response.headers['cache-control'] == 'no-store'
    capture.assert_called_once()
    error = capture.call_args.args[0]
    assert error.__context__ is None and error.__cause__ is None
    assert 'secret SQL' not in str(error) + caplog.text + response.text
    capture.reset_mock()
    def missing(*args, **kwargs):
        raise LookupError('missing')
    monkeypatch.setattr(costs, 'read_runtime_accounting', missing)
    assert TestClient(app).get('/api/admin/cost/sessions/synthetic').status_code == 404
    capture.assert_not_called()


@pytest.mark.parametrize('endpoint', ['/access', '/reports?session_id=test', '/export?session_id=test&format=csv', '/export?session_id=test'])
@pytest.mark.parametrize('status', [401, 403])
def test_every_report_surface_authorized_before_read(monkeypatch, endpoint, status):
    app = FastAPI()
    app.include_router(costs.router)
    async def denied():
        raise HTTPException(status)
    app.dependency_overrides[costs.require_admin] = denied
    database = MagicMock()
    monkeypatch.setattr(costs, 'SessionLocal', database)
    assert TestClient(app).get('/api/admin/cost' + endpoint).status_code == status
    database.assert_not_called()


@pytest.mark.parametrize('query', ['', '?start=2026-01-01T00:00:00Z', '?start=2026-01-01&end=2026-01-02',
                                   '?start=2026-01-01T00:00:00Z&end=2026-06-01T00:00:00Z', '?run_id=turn'])
def test_reports_require_bounded_scope(monkeypatch, query):
    app = FastAPI()
    app.include_router(costs.router)
    app.dependency_overrides[costs.require_admin] = lambda: {}
    read = MagicMock()
    monkeypatch.setattr(costs, '_report', read)
    assert TestClient(app).get('/api/admin/cost/reports' + query).status_code == 422
    read.assert_not_called()


@pytest.mark.parametrize('value', ['=1+1', ' +formula', '-cmd', '@SUM(1)', '\tword', '\rword', '\nword'])
def test_csv_formula_neutralization(value):
    assert costs._csv_cell(value).startswith("'")


def test_pagination_does_not_change_totals_or_export(monkeypatch):
    app = FastAPI()
    app.include_router(costs.router)
    app.dependency_overrides[costs.require_admin] = lambda: {}
    report = {'requests': [{'attempt_id': str(i)} for i in range(3)], 'runs': [{'run_id': str(i)} for i in range(3)], 'totals': {'attempt_count': 3}}
    monkeypatch.setattr(costs, '_report', lambda _: report)
    monkeypatch.setenv('COST_REPORT_PAGE_SIZE', '1')
    client = TestClient(app)
    page = client.get('/api/admin/cost/reports?session_id=test&offset=1')
    assert page.json()['requests'] == [{'attempt_id': '1'}]
    assert page.json()['totals']['attempt_count'] == 3
    exported = client.get('/api/admin/cost/export?session_id=test')
    assert exported.json() == report
    assert exported.headers['cache-control'] == 'no-store'


def test_csv_export_preserves_exact_amounts_and_provenance(monkeypatch):
    app = FastAPI()
    app.include_router(costs.router)
    app.dependency_overrides[costs.require_admin] = lambda: {}
    report = {
        'deployment_id': 'synthetic', 'pricing_snapshot_id': 'sha256:fixture',
        'valuation_algorithm': 'fixture-v1', 'scope': 'full_session',
        'filters': {'session_id': 'test'}, 'generated_at': '2026-09-26T00:00:00Z',
        'requests': [{
            'attempt_id': 'request-1', 'fact_revision': 2, 'model': '=formula',
            'agent_id': 'helper', 'agent_name': '=untrusted name', 'agent_role': 'extraction',
            'agent_revision': 'revision-a', 'node_id': 'step-a',
            'requested_service_tier': 'flex', 'effective_service_tier': 'default',
            'usage': {'input_tokens': 10, 'cache_write_tokens': None},
            'recorded_charge': {'amount': '0.000000000000000123', 'unit': 'USD', 'source': 'provider'},
            'estimate': {'cost': '0.0001', 'estimated_cost_upper': '0.0002'},
        }],
    }
    monkeypatch.setattr(costs, '_report', lambda _: report)
    response = TestClient(app).get('/api/admin/cost/export?session_id=test&format=csv')
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert len(rows) == 1
    assert rows[0]['recorded_amount'] == '0.000000000000000123'
    assert rows[0]['estimated_usd_lower'] == '0.0001'
    assert rows[0]['estimated_usd_upper'] == '0.0002'
    assert rows[0]['pricing_snapshot_id'] == 'sha256:fixture'
    assert rows[0]['fact_revision'] == '2'
    assert rows[0]['cache_write_tokens'] == ''
    assert rows[0]['model'] == "'=formula"
    assert rows[0]['agent_name'] == "'=untrusted name"
    assert rows[0]['agent_id'] == 'helper'
    assert rows[0]['agent_revision'] == 'revision-a'
    assert rows[0]['node_id'] == 'step-a'
    assert rows[0]['requested_service_tier'] == 'flex'
    assert rows[0]['effective_service_tier'] == 'default'

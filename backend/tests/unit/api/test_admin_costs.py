"""Admin gating and exact monetary JSON on the runtime read endpoint."""
from decimal import Decimal
from unittest.mock import MagicMock

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.api.admin import costs
from src.lib import http_errors


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

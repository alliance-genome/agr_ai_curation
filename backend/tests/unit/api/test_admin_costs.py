"""Admin gating and exact monetary JSON on the runtime read endpoint."""
from decimal import Decimal
from unittest.mock import MagicMock

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.api.admin import costs


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

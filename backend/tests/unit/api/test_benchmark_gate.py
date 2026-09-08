"""API visibility and admission gates must remain independent of worker polling."""

from itertools import product

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from src.api.benchmark_gate import require_benchmark_api, require_benchmark_execution


@pytest.mark.parametrize("api,execution,worker", list(product((False, True), repeat=3)))
def test_gate_matrix(monkeypatch, api, execution, worker):
    for key, value in (("API", api), ("EXECUTION", execution), ("WORKER", worker)):
        monkeypatch.setenv(f"BENCHMARK_{key}_ENABLED", str(value).lower())
    app = FastAPI()

    @app.get("/read", dependencies=[Depends(require_benchmark_api)])
    def read():
        return {"available": True}

    @app.post("/submit", dependencies=[Depends(require_benchmark_execution)])
    def submit():
        return {"accepted": True}

    with TestClient(app) as client:
        assert client.get("/read").status_code == (200 if api else 404)
        response = client.post("/submit")
        expected = 404 if not api else (200 if execution else 409)
        assert response.status_code == expected
        if expected != 200:
            assert response.json()["detail"]["code"] == (
                "api_disabled" if not api else "execution_disabled"
            )

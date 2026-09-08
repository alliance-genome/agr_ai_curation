"""Result retrieval preserves canonical bytes and the lifecycle auth boundary."""

import hashlib
from unittest.mock import MagicMock
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from src.api import benchmark_jobs
from src.api.benchmark_auth import require_benchmark_read
from src.lib.benchmarks.persistence import BenchmarkResultArtifact, BenchmarkResultArtifactError


@pytest.fixture
def result_client(monkeypatch):
    monkeypatch.setenv("BENCHMARK_API_ENABLED", "true")
    app = FastAPI()
    app.dependency_overrides[require_benchmark_read] = lambda: {"sub": "owner"}
    repository = MagicMock()
    monkeypatch.setattr(benchmark_jobs, "SessionLocal", MagicMock())
    monkeypatch.setattr(benchmark_jobs, "BenchmarkRepository", lambda session: repository)
    app.include_router(benchmark_jobs.router)
    with TestClient(app) as client:
        yield client, repository, app


def test_result_returns_exact_bytes_and_identity(result_client):
    client, repository, _ = result_client
    job, cell = uuid4(), uuid4()
    content = '{"invocations":[],"output":{"n":1e+20,"text":"β","zero":-0.0}}'.encode()
    digest = "sha256:" + hashlib.sha256(content).hexdigest()
    repository.get_result_artifact.return_value = BenchmarkResultArtifact(job, cell, 2, digest, content)
    response = client.get(f"/api/v1/benchmarks/jobs/{job}/cells/{cell}/result")
    assert response.status_code == 200
    assert response.content == content
    assert response.headers["X-Benchmark-Result-Digest"] == digest
    assert response.headers["X-Benchmark-Job-ID"] == str(job)
    assert response.headers["X-Benchmark-Cell-ID"] == str(cell)
    assert response.headers["X-Benchmark-Attempt-Count"] == "2"
    assert response.headers["Cache-Control"] == "no-store"
    repository.get_result_artifact.assert_called_once_with(job_id=job, cell_id=cell, owner_subject="owner")


@pytest.mark.parametrize("code,status", [
    ("result_not_terminal", 409), ("result_artifact_unavailable", 409),
    ("result_artifact_oversize", 413), ("result_artifact_corrupt", 503),
])
def test_result_errors_are_explicit(result_client, code, status, monkeypatch):
    client, repository, _ = result_client
    report = MagicMock()
    monkeypatch.setattr(benchmark_jobs, "report_runtime_exception", report)
    repository.get_result_artifact.side_effect = BenchmarkResultArtifactError(code)
    response = client.get(f"/api/v1/benchmarks/jobs/{uuid4()}/cells/{uuid4()}/result")
    assert response.status_code == status
    assert response.json()["detail"]["code"] == code
    assert response.headers["Cache-Control"] == "no-store"
    assert report.call_count == int(status == 503)
    if status == 503:
        error = report.call_args.args[0]
        assert error.__context__ is None
        assert error.__cause__ is None
        assert str(error) == "Benchmark result_artifact_read failed (BenchmarkResultArtifactError)"


def test_missing_capability_denied_before_repository(result_client):
    client, repository, app = result_client
    def deny():
        raise HTTPException(403, "Benchmark capability required")
    app.dependency_overrides[require_benchmark_read] = deny
    response = client.get(f"/api/v1/benchmarks/jobs/{uuid4()}/cells/{uuid4()}/result")
    assert response.status_code == 403
    repository.get_result_artifact.assert_not_called()

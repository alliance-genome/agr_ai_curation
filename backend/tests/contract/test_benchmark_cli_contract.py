"""CLI mappings checked against the actual versioned API, without executing jobs."""

import json

import httpx
from fastapi import FastAPI

from src.api.benchmark_catalog import router as catalog_router
from src.api.benchmark_jobs import router as jobs_router
from src.lib.benchmark_cli.client import BenchmarkClient, Credentials
from src.lib.benchmark_cli.commands import build_parser, execute
from src.schemas.benchmark_jobs import BenchmarkSubmitRequest, BenchmarkRerunRequest
from src.schemas.benchmark_catalog import BenchmarkPlanPreviewRequest
from src.schemas.benchmark_job_examples import SUBMIT

JOB = "00000000-0000-4000-8000-000000000001"
CELL = "00000000-0000-4000-8000-000000000002"


def test_commands_match_published_operations_and_credentials(tmp_path):
    app = FastAPI()
    app.include_router(catalog_router)
    app.include_router(jobs_router)
    paths = app.openapi()["paths"]
    submit = tmp_path / "submit.json"
    submit.write_text(json.dumps(SUBMIT))
    preview = tmp_path / "preview.json"
    preview.write_text(json.dumps({"catalog_digest": SUBMIT["plan"]["catalog_digest"], "suite": SUBMIT["suite"]}))
    calls = []

    def handle(request):
        path = request.url.path.replace(JOB, "{job_id}").replace(CELL, "{cell_id}").replace("example.v2", "{suite_id}")
        operation = paths[path][request.method.lower()]
        params = operation.get("parameters", [])
        needs_human = any(item["name"] == "X-Benchmark-Curator-Authorization" for item in params)
        assert ("x-benchmark-curator-authorization" in request.headers) is needs_human
        assert request.headers["Authorization"] == "Bearer test-access"
        assert "x-benchmark-delegated-source-authorization" not in request.headers
        if request.content:
            model = BenchmarkPlanPreviewRequest if path.endswith("/validate") else BenchmarkRerunRequest if path.endswith("/rerun") else BenchmarkSubmitRequest
            model.model_validate_json(request.content)
        if request.method == "POST" and (path.endswith("/jobs") or path.endswith("/rerun")):
            assert request.headers["Idempotency-Key"] == "contract-key"
        calls.append(path)
        return httpx.Response(204) if request.method == "DELETE" else httpx.Response(200, json={"contract": "matched"})

    commands = [
        ["catalog", "targets"], ["catalog", "models"], ["catalog", "route_slots"],
        ["suites"], ["suite", "example.v2"], ["jobs"], ["cells", JOB],
        ["get", JOB], ["get", JOB, "--cell-id", CELL], ["cancel", JOB],
        ["delete", JOB, "--confirm", JOB],
        ["rerun", JOB, "--cell-id", CELL, "--idempotency-key", "contract-key"],
        ["submit", "--request", str(submit), "--idempotency-key", "contract-key"],
        ["validate", "--request", str(preview)],
    ]
    with BenchmarkClient("https://benchmark.invalid", Credentials("test-access", "test-human"), transport=httpx.MockTransport(handle)) as client:
        for command in commands:
            execute(build_parser().parse_args(command), client)
    assert len(calls) == len(commands)

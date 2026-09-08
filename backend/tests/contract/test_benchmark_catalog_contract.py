"""Discovery and admission publish the same existing immutable plan models."""

from fastapi import FastAPI

from src.api.benchmark_catalog import router as catalog_router
from src.api.benchmark_jobs import router as jobs_router


def test_catalog_and_jobs_share_v2_plan_and_suite_contracts():
    app = FastAPI()
    app.include_router(catalog_router)
    app.include_router(jobs_router)
    document = app.openapi()
    schemas = document["components"]["schemas"]
    assert schemas["BenchmarkPlanPreviewResponse"]["properties"]["plan"] == {
        "$ref": "#/components/schemas/ResolvedBenchmarkPlan",
    }
    assert schemas["BenchmarkSuiteResponse"]["properties"]["suite"] == {
        "$ref": "#/components/schemas/BenchmarkSuite",
    }
    preview = document["paths"]["/api/v1/benchmarks/plans/validate"]["post"]
    body = preview["requestBody"]["content"]["application/json"]["schema"]
    assert body["additionalProperties"] is False
    assert set(body["properties"]) == {"catalog_digest", "suite", "checked_in_suite"}
    assert {"schema_version", "suite_id", "cases", "configurations", "repetitions"} == set(schemas["BenchmarkSuite"]["properties"])
    assert set(schemas["ResolvedBenchmarkPlan"]["properties"]) == {
        "schema_version", "suite_id", "suite_digest", "catalog_digest", "repetitions",
        "cases", "configurations", "cells", "plan_digest",
    }
    header_names = {parameter["name"] for parameter in preview["parameters"] if parameter["in"] == "header"}
    assert header_names == {"X-Benchmark-Curator-Authorization"}
    assert set(document["paths"]["/api/v1/benchmarks/plans/validate"]) == {"post"}

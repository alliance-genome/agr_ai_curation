"""Public contract for read-only registered benchmark source materialization."""

from fastapi import FastAPI

from src.api.benchmark_sources import router


def test_benchmark_source_materialization_openapi_contract():
    app = FastAPI()
    app.include_router(router)
    schema = app.openapi()

    operation = schema["paths"][
        "/api/v1/benchmarks/sources/materialize"
    ]["post"]
    request_schema = operation["requestBody"]["content"]["application/json"]["schema"]
    assert request_schema["$ref"].endswith("/BenchmarkInputReference")

    response_schema = operation["responses"]["200"]["content"]["application/json"][
        "schema"
    ]
    assert response_schema["$ref"].endswith("/MaterializedBenchmarkInput")

    components = schema["components"]["schemas"]
    request = components["BenchmarkInputReference"]
    assert set(request["required"]) == {"resolver", "reference", "version", "digest"}
    assert request["additionalProperties"] is False
    assert request["properties"]["digest"]["pattern"].startswith("^sha256:")

    response = components["MaterializedBenchmarkInput"]
    assert {
        "resolver",
        "reference",
        "version",
        "digest",
        "content",
        "metadata",
        "provenance",
    }.issubset(response["required"])
    assert response["properties"]["content"]["type"] == "string"

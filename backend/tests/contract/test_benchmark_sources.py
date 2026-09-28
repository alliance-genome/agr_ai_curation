"""Public contract for read-only registered benchmark source materialization."""

import json

from fastapi import FastAPI

from src.api.benchmark_document_conversions import router as conversions_router
from src.lib.benchmarks.document_conversions import SOURCE_REFERENCE_PATTERN
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
    assert response_schema["$ref"].endswith("/FrozenBenchmarkInputSnapshot")

    components = schema["components"]["schemas"]
    request = components["BenchmarkInputReference"]
    assert set(request["required"]) == {"resolver", "reference", "version", "digest"}
    assert request["additionalProperties"] is False
    assert request["properties"]["digest"]["pattern"].startswith("^sha256:")

    response = components["FrozenBenchmarkInputSnapshot"]
    assert {
        "snapshot_id",
        "digest",
        "source_version",
        "content_type",
        "content_bytes",
        "resolver_id",
        "source_reference",
        "sanitized_provenance",
        "owner_subject",
        "service_principal",
        "blob_reference",
        "created_at",
    }.issubset(response["required"])
    assert "content" not in response["properties"]


def test_benchmark_document_conversion_openapi_contract():
    app = FastAPI()
    app.include_router(conversions_router)
    schema = app.openapi()
    base = "/api/v1/benchmarks/sources/document-conversions"

    start = schema["paths"][base]["post"]
    assert set(start["requestBody"]["content"]) == {"application/pdf", "application/json"}
    reference = start["requestBody"]["content"]["application/json"]["schema"]
    assert reference["required"] == ["source_reference"]
    assert reference["additionalProperties"] is False
    field = reference["properties"]["source_reference"]
    assert field["type"] == "string"
    assert (field["minLength"], field["maxLength"]) == (1, 256)
    assert field["pattern"] == SOURCE_REFERENCE_PATTERN
    assert "AGRKB" not in json.dumps(schema)
    headers = {parameter["name"] for parameter in start["parameters"]}
    assert {"Idempotency-Key", "X-Benchmark-Content-Digest",
            "X-Benchmark-Curator-Authorization"} <= headers
    accepted = start["responses"]["202"]["content"]["application/json"]["schema"]
    assert accepted["$ref"].endswith("/BenchmarkDocumentConversionAccepted")

    status = schema["paths"][f"{base}/{{conversion_id}}"]["get"]
    response = status["responses"]["200"]["content"]["application/json"]["schema"]
    assert response["$ref"].endswith("/BenchmarkDocumentConversionStatus")
    components = schema["components"]["schemas"]
    assert set(components["BenchmarkDocumentConversionAccepted"]["required"]) == {
        "conversion_id", "status",
    }
    assert set(components["BenchmarkDocumentConversionStatus"]["properties"]) == {
        "conversion_id", "status", "error", "snapshot", "conversion_identity",
        "created_at", "completed_at",
    }
    assert "X-Benchmark-Curator-Authorization" not in {
        parameter["name"] for parameter in status.get("parameters", ())
    }

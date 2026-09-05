"""Synthetic OpenAPI examples; never used as execution defaults.

The normalized submit example is checked against a synthetic catalog in tests.
Actual clients must resolve their suite against the deployment's current catalog.
"""

from copy import deepcopy
from fastapi import FastAPI
from fastapi.routing import APIRoute

SUBMIT = {
    "suite": {
        "schema_version": 2,
        "suite_id": "suite-1",
        "cases": [
            {
                "case_id": "case-1",
                "target": {
                    "kind": "agent",
                    "id": "extractor",
                },
                "input": {
                    "resolver": "checked_in_fixture",
                    "reference": "case-1.json",
                    "version": "v1",
                    "digest": "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                },
                "user_query": "Extract the experimentally relevant entities.",
            },
        ],
        "configurations": [
            {
                "configuration_id": "defaults",
                "routes": {},
            },
        ],
        "repetitions": 1,
    },
    "plan": {
        "schema_version": 2,
        "suite_id": "suite-1",
        "suite_digest": "sha256:2283682a0dfddaade047577c7a72ba3f5bd2707c872a00434914b2ce0419e464",
        "catalog_digest": "sha256:7eacb366d18a6b4ec88178dc9ffe26ba7e31d5888ecbcdd0aac74cd554139e8f",
        "repetitions": 1,
        "cases": [
            {
                "case_id": "case-1",
                "target": {
                    "kind": "agent",
                    "id": "extractor",
                },
                "input": {
                    "resolver": "checked_in_fixture",
                    "reference": "case-1.json",
                    "version": "v1",
                    "digest": "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                },
                "user_query": "Extract the experimentally relevant entities.",
            },
        ],
        "configurations": [
            {
                "configuration_id": "defaults",
                "routes": {
                    "agent:extractor": {
                        "provider": "provider-a",
                        "model": "model-a",
                        "reasoning_effort": "high",
                    },
                },
            },
        ],
        "cells": [
            {
                "cell_id": "sha256:6b391bafe6e696cede41ae5b797e283717976a8269af42c6e13ebb762fcf59ca",
                "case_id": "case-1",
                "configuration_id": "defaults",
                "repetition": 1,
                "target": {
                    "kind": "agent",
                    "id": "extractor",
                },
                "input": {
                    "resolver": "checked_in_fixture",
                    "reference": "case-1.json",
                    "version": "v1",
                    "digest": "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                },
                "user_query": "Extract the experimentally relevant entities.",
                "routes": {
                    "agent:extractor": {
                        "provider": "provider-a",
                        "model": "model-a",
                        "reasoning_effort": "high",
                    },
                },
            },
        ],
        "plan_digest": "sha256:05c2de013acaa41bd5e6b0579a1f9b810e267400c2cb18d0d4ee485b8539e234",
    },
}

JOB_ID = "00000000-0000-4000-8000-000000000001"
CELL_ID = "00000000-0000-4000-8000-000000000002"
CREATED_AT = "2026-01-01T00:00:00Z"
DIGEST = "sha256:" + "a" * 64
ACCEPTED = {"job_id": JOB_ID, "replayed": False}
JOB_SUMMARY = {
    "id": JOB_ID, "owner_subject": "service:example-client",
    "status": "queued", "suite_id": SUBMIT["suite"]["suite_id"],
    "suite_digest": SUBMIT["plan"]["suite_digest"],
    "catalog_digest": SUBMIT["plan"]["catalog_digest"],
    "plan_digest": SUBMIT["plan"]["plan_digest"],
    "config_digest": DIGEST, "code_digest": DIGEST, "inputs_digest": DIGEST,
    "total_cells": 1, "queued_cells": 1, "running_cells": 0,
    "succeeded_cells": 0, "failed_cells": 0, "cancelled_cells": 0,
    "rerun_of_job_id": None, "created_at": CREATED_AT,
    "started_at": None, "completed_at": None,
}
JOB = {
    "summary": JOB_SUMMARY, "suite_specification": SUBMIT["suite"],
    "resolved_plan": SUBMIT["plan"],
    "suite_digest": JOB_SUMMARY["suite_digest"],
    "catalog_digest": JOB_SUMMARY["catalog_digest"],
    "config_digest": DIGEST, "code_digest": DIGEST, "inputs_digest": DIGEST,
    "cancel_requested_at": None, "lease_owner": None,
    "lease_expires_at": None, "lease_heartbeat_at": None,
}
CELL_SUMMARY = {
    "id": CELL_ID, "job_id": JOB_ID,
    "cell_key": SUBMIT["plan"]["cells"][0]["cell_id"],
    "position": 0, "case_id": "case-1", "configuration_id": "defaults",
    "repetition": 1, "status": "queued",
    "input_digest": SUBMIT["suite"]["cases"][0]["input"]["digest"],
    "source_cell_id": None, "created_at": CREATED_AT,
    "started_at": None, "completed_at": None,
}
CELL = {
    "summary": CELL_SUMMARY, "target_kind": "agent", "target_id": "extractor",
    "routes": SUBMIT["plan"]["cells"][0]["routes"],
    "input_resolver": "checked_in_fixture", "input_reference": "case-1.json",
    "input_version": "v1", "generated_envelope": None,
    "envelope_size_bytes": None, "envelope_digest": None,
    "result_digest": None, "failure": None,
}
CANCELLED = {
    **JOB,
    "summary": {**JOB_SUMMARY, "status": "cancelled", "queued_cells": 0,
                "cancelled_cells": 1, "completed_at": CREATED_AT},
    "cancel_requested_at": CREATED_AT,
}


def json_example(value: object, *, status: int = 200) -> dict[int | str, dict]:
    return {status: {"content": {"application/json": {"example": value}}}}


def install_openapi_examples(application: FastAPI) -> None:
    """Preserve explicit nulls removed by FastAPI's OpenAPI model serialization.

    Null timestamps/results/costs are meaningful, not absent required fields.
    Restore only declared lifecycle response examples after schema generation.
    """
    original = application.openapi

    def openapi():
        schema = original()
        for route in application.routes:
            if not isinstance(route, APIRoute) or not route.path.startswith("/api/v1/benchmarks/jobs"):
                continue
            for status, response in route.responses.items():
                for media, content in response.get("content", {}).items():
                    if "example" not in content:
                        continue
                    for method in route.methods:
                        schema["paths"][route.path][method.lower()]["responses"][str(status)]["content"][media]["example"] = deepcopy(content["example"])
        return schema

    application.openapi = openapi

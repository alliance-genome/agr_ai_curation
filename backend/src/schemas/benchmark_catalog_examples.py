"""Synthetic protocol examples, never deployment configuration or defaults."""

from typing import Any

from .benchmark_job_examples import SUBMIT

PREVIEW_REQUEST = {"catalog_digest": SUBMIT["plan"]["catalog_digest"], "suite": SUBMIT["suite"]}
PREVIEW = {
    "schema_version": 1, "catalog_schema_version": 1, "suite_schema_version": 2,
    "plan": SUBMIT["plan"], "cell_count": 1,
    "warnings": [{"code": "inputs_not_materialized", "message": "Source verification is deferred to admission."}],
}
CATALOG = {
    "schema_version": 1, "catalog_schema_version": 1,
    "catalog_digest": SUBMIT["plan"]["catalog_digest"],
    "environment_id": "synthetic-preview", "api_enabled": True,
    "execution_enabled": False, "worker_enabled": False,
    "resolver_ids": ["checked_in_fixture"], "section": "targets",
    "items": [{"target": {"kind": "agent", "id": "extractor"}, "route_slots": ["agent:extractor"]}],
    "total_items": 2, "next_cursor": '["agent","extractor"]',
}
SUITE = {"schema_version": 1, "suite_digest": SUBMIT["plan"]["suite_digest"], "suite": SUBMIT["suite"]}
SUITES = {
    "schema_version": 1, "suite_catalog_digest": "sha256:" + "c" * 64,
    "items": [{"suite_id": "suite-1", "suite_digest": SUBMIT["plan"]["suite_digest"],
               "schema_version": 2, "case_count": 1, "configuration_count": 1, "repetitions": 1}],
    "total_items": 1,
}
SAVED_FLOW = {
    "source_kind": "saved_flow", "flow_id": "00000000-0000-4000-8000-000000000001",
    "title": "Paper extraction", "description": "Synthetic discovery example",
    "revision": "sha256:" + "d" * 64,
}
SAVED_FLOWS = {"items": [SAVED_FLOW], "total_items": 1, "next_offset": None}
SAVED_FLOW_CONTRACTS = {
    "flow": SAVED_FLOW, "nodes": [], "status": "not_verified",
    "reason": "Define and verify an output structure in AI Curation before mapping fields.",
}


def response(value: dict[str, Any]) -> dict[int | str, dict[str, Any]]:
    return {200: {"content": {"application/json": {"example": value}}}}

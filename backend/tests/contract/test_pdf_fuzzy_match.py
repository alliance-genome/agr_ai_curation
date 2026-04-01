"""Contract validation for PDF viewer fuzzy-match endpoint."""

from typing import Any, Dict
import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def _load_openapi_schema() -> Dict[str, Any]:
    module_path = Path(__file__).resolve().parents[2] / 'src' / 'api' / 'pdf_viewer.py'
    spec = importlib.util.spec_from_file_location('tests.pdf_viewer', module_path)
    if spec is None or spec.loader is None:
        pytest.fail("Unable to load pdf_viewer module for contract tests")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    app = FastAPI()
    app.include_router(module.router)
    return app.openapi()


def _resolve(schema: Dict[str, Any], components: Dict[str, Any]) -> Dict[str, Any]:
    if "$ref" not in schema:
        return schema
    ref_name = schema["$ref"].split("/")[-1]
    return components["schemas"][ref_name]


def test_pdf_viewer_fuzzy_match_contract():
    schema = _load_openapi_schema()
    paths = schema.get("paths", {})

    endpoint_path = "/api/pdf-viewer/evidence/fuzzy-match"
    assert endpoint_path in paths, "Fuzzy match endpoint path missing"

    operation = paths[endpoint_path]
    assert "post" in operation, "Fuzzy match endpoint must expose HTTP POST"
    post_operation = operation["post"]

    request_body = post_operation.get("requestBody", {})
    content = request_body.get("content", {})
    assert "application/json" in content
    request_schema = _resolve(content["application/json"]["schema"], schema["components"])
    request_required = set(request_schema.get("required", []))
    assert {"quote", "pages"}.issubset(request_required)

    responses = post_operation.get("responses", {})
    assert "200" in responses
    response_schema = _resolve(
        responses["200"]["content"]["application/json"]["schema"],
        schema["components"],
    )
    response_required = set(response_schema.get("required", []))
    assert {"found", "strategy", "score", "page_ranges", "cross_page", "note"}.issubset(response_required)

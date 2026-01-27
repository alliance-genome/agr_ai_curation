"""Contract validation for PDF viewer list endpoint."""

from typing import Any, Dict
import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def _load_openapi_schema() -> Dict[str, Any]:
    """Load the pdf_viewer router module without importing the full package."""
    module_path = Path(__file__).resolve().parents[2] / 'src' / 'api' / 'pdf_viewer.py'
    spec = importlib.util.spec_from_file_location('tests.pdf_viewer', module_path)
    if spec is None or spec.loader is None:
        pytest.fail("Unable to load pdf_viewer module for contract tests")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    app = FastAPI()
    app.include_router(module.router)
    return app.openapi()


def _resolve_schema(schema: Dict[str, Any], components: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve a schema that might be a `$ref`."""
    if "$ref" not in schema:
        return schema

    ref_name = schema["$ref"].split("/")[-1]
    return components["schemas"][ref_name]


def test_pdf_viewer_list_contract():
    """Ensure OpenAPI contract matches the documented PDF list response."""
    schema = _load_openapi_schema()
    paths = schema.get("paths", {})

    assert "/api/pdf-viewer/documents" in paths, "List endpoint path missing from router"

    list_path = paths["/api/pdf-viewer/documents"]
    assert "get" in list_path, "List endpoint must expose HTTP GET"

    get_operation = list_path["get"]

    # No auth / body parameters expected by spec
    parameters = get_operation.get("parameters", [])
    allowed_params = {"limit", "offset"}
    for param in parameters:
        assert param["name"] in allowed_params
        assert param.get("in") == "query"

    responses = get_operation.get("responses", {})
    assert "200" in responses, "Successful response (200) must be documented"

    content = responses["200"].get("content", {})
    assert "application/json" in content, "JSON response schema missing"

    resolved = _resolve_schema(content["application/json"]["schema"], schema["components"])
    assert resolved.get("type") == "object"

    required = set(resolved.get("required", []))
    assert {"documents", "total", "limit", "offset"}.issubset(required)

    properties = resolved.get("properties", {})
    assert properties.get("documents", {}).get("type") == "array"

    document_schema = properties["documents"].get("items", {})
    document_schema = _resolve_schema(document_schema, schema["components"])

    doc_required = set(document_schema.get("required", []))
    expected_fields = {
        "id",
        "filename",
        "page_count",
        "file_size",
        "upload_timestamp",
        "viewer_url",
    }
    assert expected_fields.issubset(doc_required), "Document summary missing required fields"

    assert document_schema.get("properties", {}).get("viewer_url", {}).get("type") == "string"

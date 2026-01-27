"""Contract validation for PDF viewer URL endpoint."""

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


def test_pdf_viewer_url_contract():
    """Ensure OpenAPI contract for viewer URL endpoint aligns with spec."""
    schema = _load_openapi_schema()
    paths = schema.get("paths", {})

    url_path_key = "/api/pdf-viewer/documents/{document_id}/url"
    assert url_path_key in paths, "Viewer URL endpoint missing"

    url_path = paths[url_path_key]
    assert "get" in url_path
    get_operation = url_path["get"]

    parameters = get_operation.get("parameters", [])
    assert parameters, "document_id path parameter must be documented"
    document_param = parameters[0]
    assert document_param.get("name") == "document_id"
    assert document_param.get("schema", {}).get("format") == "uuid"

    responses = get_operation.get("responses", {})
    assert "200" in responses

    content = responses["200"].get("content", {})
    assert "application/json" in content

    resolved = _resolve(content["application/json"]["schema"], schema["components"])
    assert resolved.get("type") == "object"

    required = set(resolved.get("required", []))
    assert "viewer_url" in required, "viewer_url field is required in response"

    viewer_url = resolved.get("properties", {}).get("viewer_url", {})
    assert viewer_url.get("type") == "string"
    pattern = viewer_url.get("pattern")
    if pattern is not None:
        assert pattern.startswith("^/uploads/"), "viewer_url must reference /uploads/ path"

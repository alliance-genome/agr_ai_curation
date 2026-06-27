"""Contract validation for PDF viewer detail endpoint."""

from typing import Any, Dict
import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.lib.pdf_limits import MAX_PDF_FILE_SIZE_BYTES


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


def _assert_nullable_upload_viewer_url(viewer_url: Dict[str, Any]) -> None:
    any_of = viewer_url.get("anyOf", [])
    string_schema = next(
        (item for item in any_of if item.get("type") == "string"),
        None,
    )
    null_schema = next((item for item in any_of if item.get("type") == "null"), None)
    assert string_schema is not None, "viewer_url must allow /uploads/ string values"
    assert null_schema is not None, "viewer_url must allow null for text-only documents"
    pattern = string_schema.get("pattern")
    if pattern is not None:
        assert pattern.startswith("^/uploads/"), "viewer_url must live under /uploads"


def test_pdf_viewer_detail_contract():
    """Ensure OpenAPI contract for document detail matches specification."""
    schema = _load_openapi_schema()

    paths = schema.get("paths", {})
    detail_path_key = "/api/pdf-viewer/documents/{document_id}"
    assert detail_path_key in paths, "Detail endpoint path missing"

    detail_path = paths[detail_path_key]
    assert "get" in detail_path, "Detail endpoint must expose HTTP GET"

    get_operation = detail_path["get"]

    # Validate path parameter metadata
    parameters = get_operation.get("parameters", [])
    assert len(parameters) == 1, "Detail endpoint should only accept document_id path parameter"
    document_param = parameters[0]
    assert document_param.get("name") == "document_id"
    assert document_param.get("in") == "path"
    schema_ref = document_param.get("schema", {})
    assert schema_ref.get("type") == "string"
    assert schema_ref.get("format") == "uuid"

    # Validate response schema
    responses = get_operation.get("responses", {})
    assert "200" in responses
    content = responses["200"].get("content", {})
    assert "application/json" in content

    resolved = _resolve(content["application/json"]["schema"], schema["components"])
    assert resolved.get("type") == "object"

    required = set(resolved.get("required", []))
    expected = {
        "id",
        "filename",
        "page_count",
        "file_size",
        "upload_timestamp",
        "last_accessed",
        "viewer_url",
        "file_hash",
    }
    assert expected.issubset(required), "Detail response missing required fields"

    properties = resolved.get("properties", {})
    viewer_url = properties.get("viewer_url", {})
    _assert_nullable_upload_viewer_url(viewer_url)

    file_size = properties.get("file_size", {})
    assert file_size.get("exclusiveMinimum") == 0
    assert file_size.get("maximum") == MAX_PDF_FILE_SIZE_BYTES

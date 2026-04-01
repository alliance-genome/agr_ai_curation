"""Integration test for the PDF viewer fuzzy-match endpoint."""

import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


@pytest.fixture(scope="module")
def viewer_app() -> FastAPI:
    module_path = Path(__file__).resolve().parents[2] / 'src' / 'api' / 'pdf_viewer.py'
    spec = importlib.util.spec_from_file_location('tests.pdf_viewer', module_path)
    if spec is None or spec.loader is None:
        pytest.fail("Unable to load pdf_viewer module for integration test")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    app = FastAPI()
    app.include_router(module.router)
    return app


@pytest.fixture
def client(viewer_app: FastAPI) -> TestClient:
    return TestClient(viewer_app)


def test_pdf_fuzzy_match_endpoint_localizes_quote_against_page_text(client: TestClient):
    response = client.post(
        "/api/pdf-viewer/evidence/fuzzy-match",
        json={
            "quote": "Perturbing Crb affects rhabdomere morphogenesis and eventually leads to retinal degeneration.",
            "page_hints": [2],
            "pages": [
                {"page_number": 1, "text": "Introduction and background."},
                {
                    "page_number": 2,
                    "text": (
                        "Results. Perturbing Crb affects rhabdomere morphogenesis and, eventually, "
                        "leads to retinal degeneration. However, the downstream effects vary."
                    ),
                },
                {"page_number": 3, "text": "Discussion and references."},
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["found"] is True
    assert payload["matched_page"] == 2
    assert payload["matched_range"]["page_number"] == 2
    assert "Perturbing Crb affects rhabdomere morphogenesis" in payload["matched_query"]

"""Integration test for the PDF viewer fuzzy-match endpoint."""

import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.api.auth import _get_user_from_cookie_impl  # noqa: E402

FUZZY_MATCH_PATH = "/api/pdf-viewer/evidence/fuzzy-match"


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
def client(viewer_app: FastAPI):
    viewer_app.dependency_overrides[_get_user_from_cookie_impl] = lambda: {
        "sub": "fuzzy-match-test-user",
        "email": "fuzzy-match@example.org",
    }
    try:
        yield TestClient(viewer_app)
    finally:
        viewer_app.dependency_overrides.pop(_get_user_from_cookie_impl, None)


@pytest.fixture
def anonymous_client(viewer_app: FastAPI, monkeypatch) -> TestClient:
    monkeypatch.setenv("DEV_MODE", "false")
    monkeypatch.delenv("AUTH_PROVIDER", raising=False)
    monkeypatch.delenv("TESTING_API_KEY", raising=False)
    viewer_app.dependency_overrides.pop(_get_user_from_cookie_impl, None)
    return TestClient(viewer_app)


def test_pdf_fuzzy_match_endpoint_localizes_quote_against_page_text(client: TestClient):
    response = client.post(
        FUZZY_MATCH_PATH,
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


def test_pdf_fuzzy_match_endpoint_refuses_anonymous_request(anonymous_client: TestClient):
    response = anonymous_client.post(
        FUZZY_MATCH_PATH,
        json={
            "quote": "short quote",
            "pages": [{"page_number": 1, "text": "page text with a short quote in it"}],
        },
    )

    assert response.status_code == 401

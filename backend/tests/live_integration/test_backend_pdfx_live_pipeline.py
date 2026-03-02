"""Manual live test: backend-through-PDFX document upload pipeline.

This test exercises the backend API surface end-to-end with a real PDFX service:
1) check backend PDF extraction health endpoint
2) upload a PDF via backend
3) poll backend status endpoint until completion/failure
4) validate download endpoints
5) cleanup uploaded document
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient


def _require_live_pdfx_enabled() -> None:
    if os.getenv("LIVE_BACKEND_PDFX_ENABLE", "").strip() != "1":
        pytest.skip("Set LIVE_BACKEND_PDFX_ENABLE=1 to run live backend-through-PDFX tests")

    if not os.getenv("PDF_EXTRACTION_SERVICE_URL", "").strip():
        pytest.skip("PDF_EXTRACTION_SERVICE_URL is required for live PDFX pipeline test")

    if not os.getenv("OPENAI_API_KEY", "").strip():
        pytest.skip("OPENAI_API_KEY is required for live PDFX pipeline test")


def _sample_pdf_path() -> Path:
    configured = os.getenv("PDFX_LIVE_SAMPLE_PDF", "").strip()
    if configured:
        path = Path(configured)
    else:
        path = (
            Path(__file__).resolve().parents[1]
            / "fixtures"
            / "sample_fly_publication.pdf"
        )
    if not path.exists():
        pytest.skip(f"Sample PDF not found: {path}")
    return path


def _ensure_worker_ready(live_client: TestClient, headers: dict[str, str]) -> dict:
    timeout_seconds = int(os.getenv("LIVE_BACKEND_PDFX_WAKE_TIMEOUT_SECONDS", "300"))
    poll_interval_seconds = float(os.getenv("LIVE_BACKEND_PDFX_WAKE_POLL_INTERVAL_SECONDS", "2"))
    deadline = time.monotonic() + timeout_seconds
    wake_attempted = False
    last_health_payload = {}

    while time.monotonic() < deadline:
        health_resp = live_client.get("/weaviate/documents/pdf-extraction-health", headers=headers)
        assert health_resp.status_code == 200, health_resp.text
        last_health_payload = health_resp.json()
        assert last_health_payload.get("status") in {"healthy", "degraded"}, last_health_payload

        if bool(last_health_payload.get("worker_available")):
            return last_health_payload

        if not wake_attempted:
            wake_resp = live_client.post("/weaviate/documents/pdf-extraction-wake", headers=headers)
            assert wake_resp.status_code == 200, wake_resp.text
            wake_attempted = True

        time.sleep(poll_interval_seconds)

    pytest.fail(
        f"PDF extraction worker did not become ready within {timeout_seconds}s; "
        f"last_health={last_health_payload}"
    )


@pytest.fixture
def live_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    _require_live_pdfx_enabled()

    api_key = os.getenv("TESTING_API_KEY", "").strip() or f"live-pdfx-key-{uuid4().hex[:10]}"
    api_user = f"live-pdfx-{uuid4().hex[:8]}"

    monkeypatch.setenv("TESTING_API_KEY", api_key)
    monkeypatch.setenv("TESTING_API_KEY_USER", api_user)
    monkeypatch.setenv("TESTING_API_KEY_EMAIL", f"{api_user}@alliancegenome.org")
    monkeypatch.setenv("TESTING_API_KEY_GROUPS", "developers")
    monkeypatch.setenv("LLM_PROVIDER_STRICT_MODE", "false")
    monkeypatch.setenv("HEALTH_CHECK_STRICT_MODE", "false")

    # Host-run live tests may inherit repo-local `pdf_storage/` owned by root
    # from previous Docker runs. Force an isolated writable storage root.
    configured_storage = os.getenv("LIVE_BACKEND_STORAGE_PATH", "").strip()
    temp_storage_dir: str | None = None
    if configured_storage:
        storage_root = Path(configured_storage)
        storage_root.mkdir(parents=True, exist_ok=True)
    else:
        temp_storage_dir = tempfile.mkdtemp(prefix="live-backend-pdfx-storage-")
        storage_root = Path(temp_storage_dir)
    monkeypatch.setenv("PDF_STORAGE_PATH", str(storage_root))
    monkeypatch.setenv("PDFX_JSON_STORAGE_PATH", str(storage_root / "pdfx_json"))
    monkeypatch.setenv("PROCESSED_JSON_STORAGE_PATH", str(storage_root / "processed_json"))

    # Ensure env changes are respected by a fresh app import.
    modules_to_clear = [
        name for name in list(sys.modules.keys())
        if name == "main" or name.startswith("src.")
    ]
    for module_name in modules_to_clear:
        del sys.modules[module_name]

    from main import app

    try:
        with TestClient(app) as client:
            yield client
    finally:
        if temp_storage_dir:
            shutil.rmtree(temp_storage_dir, ignore_errors=True)


@pytest.mark.integration
@pytest.mark.live_pdfx
@pytest.mark.manual_only
def test_backend_pdfx_live_upload_process_download(live_client: TestClient):
    sample_pdf = _sample_pdf_path()
    api_key = os.environ["TESTING_API_KEY"]
    headers = {"X-API-Key": api_key}

    _ensure_worker_ready(live_client, headers)

    with sample_pdf.open("rb") as file_handle:
        upload_resp = live_client.post(
            "/weaviate/documents/upload",
            files={"file": (sample_pdf.name, file_handle, "application/pdf")},
            headers=headers,
        )
    assert upload_resp.status_code == 201, upload_resp.text
    upload_payload = upload_resp.json()
    document_id = upload_payload.get("document_id")
    assert document_id, upload_payload

    timeout_seconds = int(os.getenv("LIVE_BACKEND_PDFX_TIMEOUT_SECONDS", "900"))
    poll_interval_seconds = float(os.getenv("LIVE_BACKEND_PDFX_POLL_INTERVAL_SECONDS", "2"))
    deadline = time.monotonic() + timeout_seconds

    final_status_payload = None
    final_processing_status = None
    while time.monotonic() < deadline:
        status_resp = live_client.get(
            f"/weaviate/documents/{document_id}/status",
            headers=headers,
        )
        assert status_resp.status_code == 200, status_resp.text
        status_payload = status_resp.json()
        processing_status = str(status_payload.get("processing_status", "")).lower()
        if processing_status in {"completed", "failed"}:
            final_status_payload = status_payload
            final_processing_status = processing_status
            break
        time.sleep(poll_interval_seconds)

    assert final_processing_status == "completed", final_status_payload
    final_chunk_count = int((final_status_payload or {}).get("chunk_count") or 0)
    assert final_chunk_count > 0, final_status_payload

    chunks_resp = live_client.get(
        f"/weaviate/documents/{document_id}/chunks?page=1&page_size=100&include_metadata=false",
        headers=headers,
    )
    assert chunks_resp.status_code == 200, chunks_resp.text
    chunks_payload = chunks_resp.json()
    total_items = int(chunks_payload.get("pagination", {}).get("total_items") or 0)
    assert total_items > 0, chunks_payload
    assert total_items == final_chunk_count, {"status": final_status_payload, "chunks": chunks_payload}

    download_info_resp = live_client.get(
        f"/weaviate/documents/{document_id}/download-info",
        headers=headers,
    )
    assert download_info_resp.status_code == 200, download_info_resp.text
    download_info = download_info_resp.json()
    assert download_info.get("pdf_available") is True, download_info

    pdfx_resp = live_client.get(
        f"/weaviate/documents/{document_id}/download/pdfx_json",
        headers=headers,
    )
    assert pdfx_resp.status_code == 200, pdfx_resp.text

    delete_resp = live_client.delete(f"/weaviate/documents/{document_id}", headers=headers)
    assert delete_resp.status_code == 200, delete_resp.text

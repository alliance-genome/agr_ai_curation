"""Manual live test: cancel two backend PDFX jobs and ensure they reach terminal states."""

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
        pytest.skip("Set LIVE_BACKEND_PDFX_ENABLE=1 to run live backend cancellation tests")
    if not os.getenv("PDF_EXTRACTION_SERVICE_URL", "").strip():
        pytest.skip("PDF_EXTRACTION_SERVICE_URL is required for live backend cancellation tests")
    if not os.getenv("OPENAI_API_KEY", "").strip():
        pytest.skip("OPENAI_API_KEY is required for live backend cancellation tests")


def _sample_pdf_paths() -> list[Path]:
    configured = os.getenv("LIVE_BACKEND_CANCEL_SAMPLE_PDFS", "").strip()
    if configured:
        candidates = [Path(raw.strip()).expanduser() for raw in configured.split(",") if raw.strip()]
    else:
        fixtures = Path(__file__).resolve().parents[1] / "fixtures"
        candidates = [
            fixtures / "sample_fly_publication.pdf",
            fixtures / "micropub-biology-001725.pdf",
        ]

    existing = [path for path in candidates if path.exists()]
    if len(existing) < 2:
        pytest.skip(
            "Need two sample PDFs for cancellation test. "
            "Set LIVE_BACKEND_CANCEL_SAMPLE_PDFS='/path/one.pdf,/path/two.pdf'."
        )
    return existing[:2]


def _ensure_worker_ready(live_client: TestClient, headers: dict[str, str]) -> None:
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
            return

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

    api_key = os.getenv("TESTING_API_KEY", "").strip() or f"live-pdfx-cancel-key-{uuid4().hex[:10]}"
    api_user = f"live-pdfx-cancel-{uuid4().hex[:8]}"

    monkeypatch.setenv("TESTING_API_KEY", api_key)
    monkeypatch.setenv("TESTING_API_KEY_USER", api_user)
    monkeypatch.setenv("TESTING_API_KEY_EMAIL", f"{api_user}@alliancegenome.org")
    monkeypatch.setenv("TESTING_API_KEY_GROUPS", "developers")
    monkeypatch.setenv("LLM_PROVIDER_STRICT_MODE", "false")
    monkeypatch.setenv("HEALTH_CHECK_STRICT_MODE", "false")

    configured_storage = os.getenv("LIVE_BACKEND_STORAGE_PATH", "").strip()
    temp_storage_dir: str | None = None
    if configured_storage:
        storage_root = Path(configured_storage)
        storage_root.mkdir(parents=True, exist_ok=True)
    else:
        temp_storage_dir = tempfile.mkdtemp(prefix="live-backend-pdfx-cancel-storage-")
        storage_root = Path(temp_storage_dir)
    monkeypatch.setenv("PDF_STORAGE_PATH", str(storage_root))
    monkeypatch.setenv("PDFX_JSON_STORAGE_PATH", str(storage_root / "pdfx_json"))
    monkeypatch.setenv("PROCESSED_JSON_STORAGE_PATH", str(storage_root / "processed_json"))

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


def _wait_for_job_terminal(live_client: TestClient, headers: dict[str, str], job_id: str) -> dict:
    timeout_seconds = int(os.getenv("LIVE_BACKEND_PDFX_TIMEOUT_SECONDS", "900"))
    poll_interval_seconds = float(os.getenv("LIVE_BACKEND_PDFX_POLL_INTERVAL_SECONDS", "2"))
    deadline = time.monotonic() + timeout_seconds
    last_payload: dict = {}

    while time.monotonic() < deadline:
        job_resp = live_client.get(f"/weaviate/pdf-jobs/{job_id}", headers=headers)
        assert job_resp.status_code == 200, job_resp.text
        payload = job_resp.json()
        last_payload = payload
        status = str(payload.get("status", "")).lower()
        if status in {"completed", "failed", "cancelled"}:
            return payload
        time.sleep(poll_interval_seconds)

    pytest.fail(f"PDF job {job_id} did not reach terminal state within {timeout_seconds}s: {last_payload}")


@pytest.mark.integration
@pytest.mark.live_pdfx
@pytest.mark.manual_only
def test_backend_pdfx_live_cancel_two_jobs(live_client: TestClient):
    pdf_paths = _sample_pdf_paths()
    headers = {"X-API-Key": os.environ["TESTING_API_KEY"]}
    uploaded_doc_ids: list[str] = []
    submitted_jobs: list[tuple[str, str]] = []

    _ensure_worker_ready(live_client, headers)

    try:
        for pdf_path in pdf_paths:
            with pdf_path.open("rb") as file_handle:
                upload_resp = live_client.post(
                    "/weaviate/documents/upload",
                    files={"file": (pdf_path.name, file_handle, "application/pdf")},
                    headers=headers,
                )
            assert upload_resp.status_code == 201, upload_resp.text
            upload_payload = upload_resp.json()
            document_id = str(upload_payload.get("document_id"))
            job_id = str(upload_payload.get("job_id"))
            assert document_id, upload_payload
            assert job_id, upload_payload

            uploaded_doc_ids.append(document_id)
            submitted_jobs.append((document_id, job_id))

            cancel_resp = live_client.post(f"/weaviate/pdf-jobs/{job_id}/cancel", headers=headers)
            assert cancel_resp.status_code == 200, cancel_resp.text
            cancel_payload = cancel_resp.json()
            assert cancel_payload.get("success") is True, cancel_payload
            assert bool((cancel_payload.get("job") or {}).get("cancel_requested")) is True, cancel_payload

        terminals = []
        for document_id, job_id in submitted_jobs:
            final_job = _wait_for_job_terminal(live_client, headers, job_id)
            terminals.append((document_id, final_job))
            assert final_job["status"] in {"completed", "failed", "cancelled"}, final_job
            assert bool(final_job.get("cancel_requested")) is True, final_job

        assert len(terminals) == 2
        assert any(job["status"] in {"failed", "cancelled"} for _, job in terminals), terminals
    finally:
        for document_id in uploaded_doc_ids:
            # Best-effort cleanup; cancellation may still be racing in rare cases.
            for _ in range(5):
                delete_resp = live_client.delete(f"/weaviate/documents/{document_id}", headers=headers)
                if delete_resp.status_code == 200:
                    break
                time.sleep(2)

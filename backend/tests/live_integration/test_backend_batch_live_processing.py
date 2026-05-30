"""Manual live test: batch processing two PDFs through real OpenAI + PDFX stack.

This test exercises the backend API surface end-to-end:
1) ensure PDF extraction worker readiness
2) upload two PDFs and wait for document processing completion
3) create a batch-compatible flow and validate it
4) run batch processing and poll until terminal status
5) download ZIP results and verify files exist
"""

from __future__ import annotations

import io
import os
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient


def _require_live_batch_enabled() -> None:
    if os.getenv("LIVE_BATCH_ENABLE", "").strip() != "1":
        pytest.skip("Set LIVE_BATCH_ENABLE=1 to run live batch integration tests")
    if os.getenv("LIVE_LLM_ENABLE", "").strip() != "1":
        pytest.skip("Set LIVE_LLM_ENABLE=1 to run live batch integration tests")
    if os.getenv("LIVE_BACKEND_PDFX_ENABLE", "").strip() != "1":
        pytest.skip("Set LIVE_BACKEND_PDFX_ENABLE=1 to run live batch integration tests")
    if not os.getenv("PDF_EXTRACTION_SERVICE_URL", "").strip():
        pytest.skip("PDF_EXTRACTION_SERVICE_URL is required for live batch integration tests")
    if not os.getenv("OPENAI_API_KEY", "").strip():
        pytest.skip("OPENAI_API_KEY is required for live batch integration tests")


def _sample_pdf_paths() -> list[Path]:
    configured = os.getenv("LIVE_BATCH_SAMPLE_PDFS", "").strip()
    if configured:
        candidates = [Path(raw.strip()).expanduser() for raw in configured.split(",") if raw.strip()]
    else:
        fixtures = Path(__file__).resolve().parents[1] / "fixtures"
        candidates = [
            fixtures / "live_tiny_chat.pdf",
            fixtures / "micropub-biology-001725.pdf",
            fixtures / "sample_fly_publication.pdf",
        ]

    existing = [path for path in candidates if path.exists()]
    if len(existing) < 2:
        pytest.skip(
            "Need at least two sample PDFs. "
            "Set LIVE_BATCH_SAMPLE_PDFS='/path/one.pdf,/path/two.pdf' or add fixtures."
        )
    # Prefer smaller PDFs for live reliability and lower cost unless explicitly configured.
    return sorted(existing, key=lambda path: path.stat().st_size)[:2]


def _flow_definition() -> dict:
    return {
        "version": "1.0",
        "entry_node_id": "task_input_1",
        "nodes": [
            {
                "id": "task_input_1",
                "type": "task_input",
                "position": {"x": 0, "y": 0},
                "data": {
                    "agent_id": "task_input",
                    "agent_display_name": "Initial Instructions",
                    "task_instructions": (
                        "Read the loaded PDF and extract up to 5 key curator-relevant findings. "
                        "Include concise evidence snippets. Then format structured output."
                    ),
                    "output_key": "task_input_text",
                    "input_source": "user_query",
                },
            },
            {
                "id": "pdf_1",
                "type": "agent",
                "position": {"x": 280, "y": 0},
                "data": {
                    "agent_id": "pdf_extraction",
                    "agent_display_name": "PDF Specialist",
                    "output_key": "pdf_findings",
                    "input_source": "previous_output",
                    "step_goal": "Read the document and return concise evidence-based findings.",
                },
            },
            {
                "id": "json_1",
                "type": "agent",
                "position": {"x": 560, "y": 0},
                "data": {
                    "agent_id": "json_formatter",
                    "agent_display_name": "JSON Formatter",
                    "output_key": "final_output",
                    "input_source": "previous_output",
                    "step_goal": "Save the final findings as downloadable JSON output.",
                },
            },
        ],
        "edges": [
            {"id": "edge_1", "source": "task_input_1", "target": "pdf_1"},
            {"id": "edge_2", "source": "pdf_1", "target": "json_1"},
        ],
    }


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


def _upload_pdf_and_wait_complete(
    live_client: TestClient,
    headers: dict[str, str],
    pdf_path: Path,
) -> str:
    with pdf_path.open("rb") as file_handle:
        upload_resp = live_client.post(
            "/weaviate/documents/upload",
            files={"file": (pdf_path.name, file_handle, "application/pdf")},
            headers=headers,
        )
    assert upload_resp.status_code == 201, upload_resp.text
    document_id = upload_resp.json().get("document_id")
    assert document_id, upload_resp.json()

    timeout_seconds = int(os.getenv("LIVE_BACKEND_PDFX_TIMEOUT_SECONDS", "900"))
    poll_interval_seconds = float(os.getenv("LIVE_BACKEND_PDFX_POLL_INTERVAL_SECONDS", "2"))
    deadline = time.monotonic() + timeout_seconds
    final_status_payload = None
    final_processing_status = None

    while time.monotonic() < deadline:
        status_resp = live_client.get(f"/weaviate/documents/{document_id}/status", headers=headers)
        assert status_resp.status_code == 200, status_resp.text
        status_payload = status_resp.json()
        processing_status = str(status_payload.get("processing_status", "")).lower()
        if processing_status in {"completed", "failed"}:
            final_status_payload = status_payload
            final_processing_status = processing_status
            break
        time.sleep(poll_interval_seconds)

    assert final_processing_status == "completed", {
        "document_id": document_id,
        "pdf_path": str(pdf_path),
        "status_payload": final_status_payload,
    }
    return str(document_id)


def _wait_for_batch_terminal(
    live_client: TestClient,
    headers: dict[str, str],
    batch_id: str,
) -> dict:
    timeout_seconds = int(os.getenv("LIVE_BATCH_TIMEOUT_SECONDS", "1800"))
    poll_interval_seconds = float(os.getenv("LIVE_BATCH_POLL_INTERVAL_SECONDS", "3"))
    deadline = time.monotonic() + timeout_seconds
    last_payload: dict = {}

    while time.monotonic() < deadline:
        resp = live_client.get(f"/api/batches/{batch_id}", headers=headers)
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        last_payload = payload
        status = str(payload.get("status", "")).lower()
        if status in {"completed", "cancelled"}:
            return payload
        time.sleep(poll_interval_seconds)

    pytest.fail(
        f"Batch {batch_id} did not reach terminal status within {timeout_seconds}s; "
        f"last_payload={last_payload}"
    )


def _create_live_client(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    _require_live_batch_enabled()

    api_key = os.getenv("TESTING_API_KEY", "").strip() or f"live-batch-key-{uuid4().hex[:10]}"
    api_user = f"live-batch-{uuid4().hex[:8]}"
    supervisor_model = os.getenv("LIVE_BATCH_OPENAI_MODEL", "").strip()
    if not supervisor_model:
        supervisor_model = os.getenv("LIVE_LLM_OPENAI_MODEL", "gpt-5-mini").strip()

    monkeypatch.setenv("TESTING_API_KEY", api_key)
    monkeypatch.setenv("TESTING_API_KEY_USER", api_user)
    monkeypatch.setenv("TESTING_API_KEY_EMAIL", f"{api_user}@alliancegenome.org")
    monkeypatch.setenv("TESTING_API_KEY_GROUPS", "developers")
    monkeypatch.setenv("LLM_PROVIDER_STRICT_MODE", "false")
    monkeypatch.setenv("HEALTH_CHECK_STRICT_MODE", "false")
    monkeypatch.setenv("AGENT_SUPERVISOR_MODEL", supervisor_model)
    monkeypatch.setenv("AGENT_SUPERVISOR_REASONING", "low")
    monkeypatch.setenv("AGENT_MAX_TURNS", "10")

    configured_storage = os.getenv("LIVE_BACKEND_STORAGE_PATH", "").strip()
    temp_storage_dir: str | None = None
    if configured_storage:
        storage_root = Path(configured_storage)
        storage_root.mkdir(parents=True, exist_ok=True)
    else:
        temp_storage_dir = tempfile.mkdtemp(prefix="live-backend-batch-storage-")
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


@pytest.mark.integration
@pytest.mark.live_llm
@pytest.mark.live_pdfx
@pytest.mark.provider_openai
@pytest.mark.manual_only
def test_live_batch_processing_two_pdfs_openai(monkeypatch: pytest.MonkeyPatch):
    pdf_paths = _sample_pdf_paths()

    for live_client in _create_live_client(monkeypatch):
        headers = {"X-API-Key": os.environ["TESTING_API_KEY"]}
        flow_id: str | None = None
        uploaded_doc_ids: list[str] = []

        try:
            _ensure_worker_ready(live_client, headers)

            for pdf_path in pdf_paths:
                document_id = _upload_pdf_and_wait_complete(live_client, headers, pdf_path)
                uploaded_doc_ids.append(document_id)

            flow_payload = {
                "name": f"Live Batch E2E {uuid4().hex[:8]}",
                "description": "Manual live batch integration test (OpenAI + PDFX)",
                "flow_definition": _flow_definition(),
            }
            create_flow_resp = live_client.post("/api/flows", json=flow_payload, headers=headers)
            assert create_flow_resp.status_code == 201, create_flow_resp.text
            flow_id = str(create_flow_resp.json()["id"])

            validate_resp = live_client.get(f"/api/flows/{flow_id}/validate-batch", headers=headers)
            assert validate_resp.status_code == 200, validate_resp.text
            validate_payload = validate_resp.json()
            assert validate_payload.get("valid") is True, validate_payload

            create_batch_resp = live_client.post(
                "/api/batches",
                json={"flow_id": flow_id, "document_ids": uploaded_doc_ids},
                headers=headers,
            )
            assert create_batch_resp.status_code == 201, create_batch_resp.text
            batch_payload = create_batch_resp.json()
            batch_id = str(batch_payload["id"])
            assert int(batch_payload.get("total_documents", 0)) == len(uploaded_doc_ids), batch_payload

            terminal_batch = _wait_for_batch_terminal(live_client, headers, batch_id)
            assert terminal_batch.get("status") == "completed", terminal_batch
            assert int(terminal_batch.get("total_documents", 0)) == len(uploaded_doc_ids), terminal_batch
            assert int(terminal_batch.get("completed_documents", 0)) == len(uploaded_doc_ids), terminal_batch
            assert int(terminal_batch.get("failed_documents", 0)) == 0, terminal_batch

            batch_docs = terminal_batch.get("documents") or []
            assert len(batch_docs) == len(uploaded_doc_ids), terminal_batch
            for batch_doc in batch_docs:
                assert str(batch_doc.get("status", "")).lower() == "completed", batch_doc
                assert batch_doc.get("result_file_path"), batch_doc

            zip_resp = live_client.get(f"/api/batches/{batch_id}/download-zip", headers=headers)
            assert zip_resp.status_code == 200, zip_resp.text
            assert zip_resp.headers.get("content-type", "").startswith("application/zip"), zip_resp.headers

            zip_bytes = zip_resp.content
            assert zip_bytes.startswith(b"PK"), "Expected ZIP payload signature"
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
                members = [name for name in archive.namelist() if not name.endswith("/")]
                assert len(members) >= len(uploaded_doc_ids), members
        finally:
            if flow_id:
                live_client.delete(f"/api/flows/{flow_id}", headers=headers)
            for document_id in uploaded_doc_ids:
                live_client.delete(f"/weaviate/documents/{document_id}", headers=headers)

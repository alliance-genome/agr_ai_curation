"""Manual live test: upload PDF, load it into chat, and ask a document question."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient


def _is_retryable_groq_tool_parse_failure(status_code: int, body: str) -> bool:
    if status_code < 500:
        return False
    lowered = body.lower()
    return (
        "failed to parse tool call arguments as json" in lowered
        or "midstreamfallbackerror" in lowered
        or "groqexception" in lowered
    )


def _is_empty_chat_answer(payload: dict) -> bool:
    return not str((payload or {}).get("response", "")).strip()


def _sample_pdf_path() -> Path:
    configured = os.getenv("PDFX_LIVE_SAMPLE_PDF", "").strip()
    if configured:
        path = Path(configured)
    else:
        preferred = (
            Path(__file__).resolve().parents[1]
            / "fixtures"
            / "live_tiny_chat.pdf"
        )
        if preferred.exists():
            path = preferred
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


def _upload_pdf_and_wait_complete(live_client: TestClient, headers: dict[str, str]) -> str:
    _ensure_worker_ready(live_client, headers)
    sample_pdf = _sample_pdf_path()

    with sample_pdf.open("rb") as file_handle:
        upload_resp = live_client.post(
            "/weaviate/documents/upload",
            files={"file": (sample_pdf.name, file_handle, "application/pdf")},
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

    assert final_processing_status == "completed", final_status_payload
    return str(document_id)


def _require_live_chat_pdf_enabled(required_key_env: str) -> None:
    if os.getenv("LIVE_LLM_ENABLE", "").strip() != "1":
        pytest.skip("Set LIVE_LLM_ENABLE=1 to run live chat tests")
    if os.getenv("LIVE_BACKEND_PDFX_ENABLE", "").strip() != "1":
        pytest.skip("Set LIVE_BACKEND_PDFX_ENABLE=1 to run live chat with PDF tests")
    if not os.getenv("PDF_EXTRACTION_SERVICE_URL", "").strip():
        pytest.skip("PDF_EXTRACTION_SERVICE_URL is required for live chat with PDF tests")
    if not os.getenv(required_key_env, "").strip():
        pytest.skip(f"{required_key_env} is required for live chat with PDF tests")


def _create_live_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    required_key_env: str,
    supervisor_model: str,
) -> Iterator[TestClient]:
    _require_live_chat_pdf_enabled(required_key_env)

    api_key = os.getenv("TESTING_API_KEY", "").strip() or f"live-chat-key-{uuid4().hex[:10]}"
    api_user = f"live-chat-{uuid4().hex[:8]}"

    monkeypatch.setenv("TESTING_API_KEY", api_key)
    monkeypatch.setenv("TESTING_API_KEY_USER", api_user)
    monkeypatch.setenv("TESTING_API_KEY_EMAIL", f"{api_user}@alliancegenome.org")
    monkeypatch.setenv("TESTING_API_KEY_GROUPS", "developers")
    monkeypatch.setenv("LLM_PROVIDER_STRICT_MODE", "false")
    monkeypatch.setenv("HEALTH_CHECK_STRICT_MODE", "false")
    monkeypatch.setenv("AGENT_SUPERVISOR_MODEL", supervisor_model)
    monkeypatch.setenv("AGENT_SUPERVISOR_REASONING", "low")
    monkeypatch.setenv("AGENT_MAX_TURNS", "8")

    # Host-run live tests may inherit a repo-local `pdf_storage/` owned by root
    # (for example from previous Docker runs). Force an isolated writable path.
    configured_storage = os.getenv("LIVE_BACKEND_STORAGE_PATH", "").strip()
    temp_storage_dir: str | None = None
    if configured_storage:
        storage_root = Path(configured_storage)
        storage_root.mkdir(parents=True, exist_ok=True)
    else:
        temp_storage_dir = tempfile.mkdtemp(prefix="live-backend-pdf-storage-")
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


def _run_live_chat_pdf_case(
    monkeypatch: pytest.MonkeyPatch,
    *,
    provider: str,
    model_id: str,
    required_key_env: str,
) -> None:
    for client in _create_live_client(
        monkeypatch,
        required_key_env=required_key_env,
        supervisor_model=model_id,
    ):
        headers = {"X-API-Key": os.environ["TESTING_API_KEY"]}
        document_id = None
        try:
            document_id = _upload_pdf_and_wait_complete(client, headers)

            load_resp = client.post(
                "/api/chat/document/load",
                json={"document_id": document_id},
                headers=headers,
            )
            assert load_resp.status_code == 200, load_resp.text
            load_payload = load_resp.json()
            assert load_payload.get("active") is True, load_payload
            assert str(load_payload.get("document", {}).get("id", "")) == document_id, load_payload

            max_attempts = int(os.getenv("LIVE_CHAT_GROQ_MAX_RETRIES", "5")) if provider == "groq" else 1
            retry_delay_seconds = float(os.getenv("LIVE_CHAT_GROQ_RETRY_DELAY_SECONDS", "2.0"))

            chat_resp = None
            for attempt in range(1, max_attempts + 1):
                chat_resp = client.post(
                    "/api/chat",
                    json={
                        "session_id": f"live-chat-{provider}-session-{uuid4().hex[:8]}",
                        "model": model_id,
                        "specialist_model": model_id,
                        "supervisor_reasoning": "low",
                        "specialist_reasoning": "low",
                        "message": (
                            "Based on the loaded document, provide a concise one-sentence summary "
                            "for a curator."
                        ),
                    },
                    headers=headers,
                )
                if chat_resp.status_code == 200:
                    if provider == "groq":
                        try:
                            maybe_payload = chat_resp.json()
                        except Exception:
                            maybe_payload = {}
                        if _is_empty_chat_answer(maybe_payload) and attempt < max_attempts:
                            time.sleep(retry_delay_seconds * attempt)
                            continue
                    break
                if (
                    provider == "groq"
                    and attempt < max_attempts
                    and _is_retryable_groq_tool_parse_failure(chat_resp.status_code, chat_resp.text)
                ):
                    time.sleep(retry_delay_seconds * attempt)
                    continue
                break

            assert chat_resp is not None
            if provider == "groq" and _is_retryable_groq_tool_parse_failure(chat_resp.status_code, chat_resp.text):
                pytest.xfail(
                    "Groq transient tool-call JSON parsing failure persisted after retries; "
                    "treating as upstream provider instability."
                )
            assert chat_resp.status_code == 200, chat_resp.text
            chat_payload = chat_resp.json()
            if provider == "groq" and _is_empty_chat_answer(chat_payload):
                pytest.xfail(
                    "Groq returned empty response payload after retries; "
                    "treating as upstream provider instability."
                )
            answer = str(chat_payload.get("response", "")).strip()
            assert answer, chat_payload
            assert len(answer) >= 20, chat_payload
            lowered = answer.lower()
            assert "no document is currently loaded" not in lowered, chat_payload
            assert "i don't have access to the document" not in lowered, chat_payload
            assert chat_payload.get("session_id"), chat_payload
        finally:
            client.delete("/api/chat/document", headers=headers)
            if document_id:
                client.delete(f"/weaviate/documents/{document_id}", headers=headers)


@pytest.mark.integration
@pytest.mark.live_llm
@pytest.mark.live_pdfx
@pytest.mark.provider_openai
@pytest.mark.manual_only
def test_live_chat_with_loaded_pdf_openai(monkeypatch: pytest.MonkeyPatch):
    model_id = os.getenv("LIVE_LLM_OPENAI_MODEL", "gpt-5.4-mini").strip()
    _run_live_chat_pdf_case(
        monkeypatch,
        provider="openai",
        model_id=model_id,
        required_key_env="OPENAI_API_KEY",
    )


@pytest.mark.integration
@pytest.mark.live_llm
@pytest.mark.live_pdfx
@pytest.mark.provider_groq
@pytest.mark.manual_only
def test_live_chat_with_loaded_pdf_groq(monkeypatch: pytest.MonkeyPatch):
    model_id = os.getenv("LIVE_LLM_GROQ_MODEL", "openai/gpt-oss-120b").strip()
    _run_live_chat_pdf_case(
        monkeypatch,
        provider="groq",
        model_id=model_id,
        required_key_env="GROQ_API_KEY",
    )

"""Manual live tests: real flow execution against OpenAI/Groq providers.

These tests perform real LLM calls through the flow execution endpoint.
They are opt-in and provider-key gated to avoid accidental cost.
"""

from __future__ import annotations

import json
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


def _require_live_llm_enabled() -> None:
    if os.getenv("LIVE_LLM_ENABLE", "").strip() != "1":
        pytest.skip("Set LIVE_LLM_ENABLE=1 to run live LLM flow tests")


def _flow_definition(agent_id: str, agent_name: str) -> dict:
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
                    "task_instructions": "Read the user query and produce a direct short answer.",
                    "output_key": "task_input_text",
                    "input_source": "user_query",
                },
            },
            {
                "id": "agent_1",
                "type": "agent",
                "position": {"x": 260, "y": 0},
                "data": {
                    "agent_id": agent_id,
                    "agent_display_name": agent_name,
                    "output_key": "final_output",
                    "input_source": "user_query",
                },
            },
        ],
        "edges": [
            {"id": "edge_1", "source": "task_input_1", "target": "agent_1"},
        ],
    }


def _sse_events(response) -> list[dict]:
    events: list[dict] = []
    for line in response.iter_lines():
        if not line:
            continue
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


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


def _create_live_client(monkeypatch: pytest.MonkeyPatch, *, supervisor_model: str) -> Iterator[TestClient]:
    _require_live_llm_enabled()

    api_key = os.getenv("TESTING_API_KEY", "").strip() or f"live-llm-key-{uuid4().hex[:10]}"
    api_user = f"live-llm-{uuid4().hex[:8]}"

    monkeypatch.setenv("TESTING_API_KEY", api_key)
    monkeypatch.setenv("TESTING_API_KEY_USER", api_user)
    monkeypatch.setenv("TESTING_API_KEY_EMAIL", f"{api_user}@alliancegenome.org")
    monkeypatch.setenv("TESTING_API_KEY_GROUPS", "developers")
    monkeypatch.setenv("LLM_PROVIDER_STRICT_MODE", "false")
    monkeypatch.setenv("HEALTH_CHECK_STRICT_MODE", "false")
    monkeypatch.setenv("AGENT_SUPERVISOR_MODEL", supervisor_model)
    monkeypatch.setenv("AGENT_SUPERVISOR_REASONING", "low")
    monkeypatch.setenv("AGENT_MAX_TURNS", "6")

    # Host-run live tests may inherit repo-local `pdf_storage/` owned by root
    # from previous Docker runs. Force an isolated writable storage root.
    configured_storage = os.getenv("LIVE_BACKEND_STORAGE_PATH", "").strip()
    temp_storage_dir: str | None = None
    if configured_storage:
        storage_root = Path(configured_storage)
        storage_root.mkdir(parents=True, exist_ok=True)
    else:
        temp_storage_dir = tempfile.mkdtemp(prefix="live-backend-flow-storage-")
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


def _run_live_flow_case(
    monkeypatch: pytest.MonkeyPatch,
    *,
    provider: str,
    model_id: str,
    required_key_env: str,
    document_id: str | None = None,
    client: TestClient | None = None,
) -> None:
    if not os.getenv(required_key_env, "").strip():
        pytest.skip(f"{required_key_env} is required for live {provider} flow execution test")

    def _execute_with_client(active_client: TestClient) -> None:
        custom_agent_id = None
        flow_id = None
        headers = {"X-API-Key": os.environ["TESTING_API_KEY"]}
        try:
            custom_agent_payload = {
                "template_source": "chat_output",
                "name": f"Live {provider.title()} Flow Agent {uuid4().hex[:8]}",
                "custom_prompt": "Answer the user request directly in one short sentence.",
                "description": f"Manual live {provider} test agent",
                "include_group_rules": False,
                "model_id": model_id,
                "model_reasoning": "low",
            }
            create_custom_resp = active_client.post(
                "/api/agent-studio/custom-agents",
                json=custom_agent_payload,
                headers=headers,
            )
            assert create_custom_resp.status_code == 201, create_custom_resp.text
            custom_payload = create_custom_resp.json()
            custom_agent_id = custom_payload["id"]
            custom_agent_key = custom_payload["agent_id"]

            flow_payload = {
                "name": f"Live {provider.title()} Flow {uuid4().hex[:8]}",
                "description": f"Manual live {provider} flow execution test",
                "flow_definition": _flow_definition(
                    agent_id=custom_agent_key,
                    agent_name=custom_payload["name"],
                ),
            }
            create_flow_resp = active_client.post("/api/flows", json=flow_payload, headers=headers)
            assert create_flow_resp.status_code == 201, create_flow_resp.text
            flow_id = create_flow_resp.json()["id"]

            execute_payload = {
                "flow_id": flow_id,
                "session_id": f"live-{provider}-session-{uuid4().hex[:8]}",
                "user_query": "Reply with the word OK and one short reason.",
            }
            if document_id:
                execute_payload["document_id"] = document_id

            with active_client.stream(
                "POST",
                "/api/chat/execute-flow",
                json=execute_payload,
                headers=headers,
            ) as stream_resp:
                assert stream_resp.status_code == 200, stream_resp.text
                events = _sse_events(stream_resp)

            event_types = [event.get("type") for event in events]
            assert "FLOW_STARTED" in event_types, events
            assert "RUN_STARTED" in event_types, events
            run_errors = [event for event in events if event.get("type") == "RUN_ERROR"]
            supervisor_errors = [event for event in events if event.get("type") == "SUPERVISOR_ERROR"]
            assert not run_errors, run_errors
            assert not supervisor_errors, supervisor_errors
            assert "FLOW_FINISHED" in event_types, events

            flow_finished = next(event for event in events if event.get("type") == "FLOW_FINISHED")
            assert flow_finished.get("status") == "completed", flow_finished

            run_started = next(event for event in events if event.get("type") == "RUN_STARTED")
            model_blob = str(run_started.get("model", "")).lower()
            assert model_blob, run_started
            # OpenAI native emits model IDs directly.
            # LiteLLM-backed providers may emit an object repr in RUN_STARTED.
            if provider == "openai":
                assert model_id.lower() in model_blob, run_started
            else:
                expected_suffix = model_id.lower().split("/")[-1]
                assert expected_suffix in model_blob or "litellmmodel" in model_blob, run_started
        finally:
            if flow_id:
                active_client.delete(f"/api/flows/{flow_id}", headers=headers)
                flow_id = None
            if custom_agent_id:
                active_client.delete(
                    f"/api/agent-studio/custom-agents/{custom_agent_id}",
                    headers=headers,
                )
                custom_agent_id = None

    if client is not None:
        _execute_with_client(client)
        return

    for auto_client in _create_live_client(monkeypatch, supervisor_model=model_id):
        _execute_with_client(auto_client)


@pytest.mark.integration
@pytest.mark.live_llm
@pytest.mark.provider_openai
@pytest.mark.streaming
@pytest.mark.manual_only
def test_live_flow_execution_openai(monkeypatch: pytest.MonkeyPatch):
    model_id = os.getenv("LIVE_LLM_OPENAI_MODEL", "gpt-5.4-nano").strip()
    _run_live_flow_case(
        monkeypatch,
        provider="openai",
        model_id=model_id,
        required_key_env="OPENAI_API_KEY",
    )


@pytest.mark.integration
@pytest.mark.live_llm
@pytest.mark.provider_groq
@pytest.mark.streaming
@pytest.mark.manual_only
def test_live_flow_execution_groq(monkeypatch: pytest.MonkeyPatch):
    model_id = os.getenv("LIVE_LLM_GROQ_MODEL", "openai/gpt-oss-120b").strip()
    _run_live_flow_case(
        monkeypatch,
        provider="groq",
        model_id=model_id,
        required_key_env="GROQ_API_KEY",
    )


@pytest.mark.integration
@pytest.mark.live_llm
@pytest.mark.live_pdfx
@pytest.mark.provider_openai
@pytest.mark.streaming
@pytest.mark.manual_only
def test_live_flow_execution_openai_with_pdf_context(monkeypatch: pytest.MonkeyPatch):
    if os.getenv("LIVE_BACKEND_PDFX_ENABLE", "").strip() != "1":
        pytest.skip("Set LIVE_BACKEND_PDFX_ENABLE=1 to run live PDF-backed flow execution test")
    if not os.getenv("PDF_EXTRACTION_SERVICE_URL", "").strip():
        pytest.skip("PDF_EXTRACTION_SERVICE_URL is required for live PDF-backed flow execution test")
    if not os.getenv("OPENAI_API_KEY", "").strip():
        pytest.skip("OPENAI_API_KEY is required for live PDF-backed flow execution test")

    model_id = os.getenv("LIVE_LLM_OPENAI_MODEL", "gpt-5.4-nano").strip()
    document_id = None
    for client in _create_live_client(monkeypatch, supervisor_model=model_id):
        headers = {"X-API-Key": os.environ["TESTING_API_KEY"]}
        try:
            document_id = _upload_pdf_and_wait_complete(client, headers)
            _run_live_flow_case(
                monkeypatch,
                provider="openai",
                model_id=model_id,
                required_key_env="OPENAI_API_KEY",
                document_id=document_id,
                client=client,
            )
        finally:
            if document_id:
                client.delete(f"/weaviate/documents/{document_id}", headers=headers)

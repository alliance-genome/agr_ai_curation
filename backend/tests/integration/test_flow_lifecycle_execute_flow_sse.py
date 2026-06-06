"""Integration tests for flow lifecycle and execute-flow SSE behavior.

These tests exercise a larger-scope path:
1) create flow via API
2) update/save flow via API
3) execute flow via SSE endpoint
4) validate persisted execution stats + streamed events
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


def _flow_definition(*, agent_id: str, agent_display_name: str, output_key: str = "final_output") -> dict:
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
                    "task_instructions": "Extract key curation-ready findings.",
                    "output_key": "task_input_text",
                },
            },
            {
                "id": "agent_1",
                "type": "agent",
                "position": {"x": 260, "y": 0},
                "data": {
                    "agent_id": agent_id,
                    "agent_display_name": agent_display_name,
                    "output_key": output_key,
                },
            },
        ],
        "edges": [
            {"id": "edge_1", "source": "task_input_1", "target": "agent_1"},
        ],
    }


def _sample_pdf_projection_flow_definition() -> dict:
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
                    "task_instructions": "Extract curation-ready gene findings from the loaded sample PDF.",
                    "output_key": "task_input_text",
                },
            },
            {
                "id": "agent_1",
                "type": "agent",
                "position": {"x": 260, "y": 0},
                "data": {
                    "agent_id": "gene_extractor",
                    "agent_display_name": "Gene Extractor",
                    "step_goal": "Extract gene candidates with evidence anchors.",
                    "output_key": "gene_candidates",
                },
            },
            {
                "id": "agent_2",
                "type": "agent",
                "position": {"x": 520, "y": 0},
                "data": {
                    "agent_id": "json_formatter",
                    "agent_display_name": "JSON File Formatter",
                    "step_goal": "Export one row per gene candidate with evidence identifiers.",
                    "output_key": "gene_json_export",
                    "projection_plan": {
                        "format": "json",
                        "row_source": "object",
                        "json_shape": "rows",
                        "filters": [
                            {
                                "field_ref": "object.evidence_record_ids",
                                "op": "is_not_empty",
                            }
                        ],
                        "columns": [
                            {
                                "key": "gene",
                                "header": "Gene",
                                "field_ref": "object.label",
                            },
                            {
                                "key": "primary_external_id",
                                "header": "Primary External ID",
                                "field_ref": "object.payload.primary_external_id",
                            },
                            {
                                "key": "evidence_record_ids",
                                "header": "Evidence IDs",
                                "field_ref": "object.evidence_record_ids",
                            },
                        ],
                    },
                },
            },
        ],
        "edges": [
            {"id": "edge_1", "source": "task_input_1", "target": "agent_1"},
            {"id": "edge_2", "source": "agent_1", "target": "agent_2"},
        ],
    }


def _sse_events(response) -> list[dict]:
    events: list[dict] = []
    for line in response.iter_lines():
        if not line:
            continue
        if line.startswith("data: "):
            event = json.loads(line[6:])
            events.append(event)
            if event.get("type") in {"FLOW_FINISHED", "RUN_ERROR"}:
                break
    return events


def _mark_fake_streaming_tool_complete(agent, *, output: str = "done") -> None:
    """Mirror the flow tool wrapper state update when runner calls are mocked.

    The production streaming tool wrapper records completed_steps before the
    output agent emits CHAT_OUTPUT_READY. These integration tests patch the
    runner itself, so the wrapper never executes unless we mark the state here.
    Keep this helper close to the fake streams so future test edits do not
    accidentally bypass the executor's incomplete-flow guard.
    """
    execution_state = getattr(agent, "_flow_execution_state", None)
    if not isinstance(execution_state, dict):
        return
    ordered_tool_names = execution_state.get("ordered_tool_names") or []
    next_tool_index = int(execution_state.get("next_tool_index") or 0)
    if next_tool_index >= len(ordered_tool_names):
        return
    tool_name = ordered_tool_names[next_tool_index]
    execution_state.setdefault("completed_steps", []).append(
        {
            "step": next_tool_index + 1,
            "agent_id": "chat_output",
            "agent_name": "Chat Output Agent",
            "tool_name": tool_name,
            "output": output,
            "output_preview": output,
            "evidence_records": [],
            "evidence_count": 0,
        }
    )
    execution_state["next_tool_index"] = next_tool_index + 1


def _sample_pdf_gene_envelope() -> dict:
    return {
        "envelope_id": "sample-fly-publication-gene-envelope",
        "domain_pack_id": "gene",
        "objects": [
            {
                "object_type": "Gene",
                "pending_ref_id": "gene-crumb",
                "status": "candidate",
                "payload": {
                    "symbol": "crumb",
                    "name": "crumbs",
                    "primary_external_id": "FlyBase:FBgn0259211",
                },
                "evidence_record_ids": ["sample-pdf-ev-1"],
                "evidence_records": [
                    {
                        "evidence_record_id": "sample-pdf-ev-1",
                        "entity": "crumb",
                        "verified_quote": "The sample Fly publication discusses crumb-associated findings.",
                        "source": "sample_fly_publication.pdf",
                        "section": "Results",
                        "chunk_id": "sample-pdf-chunk-1",
                        "page": 1,
                    }
                ],
            }
        ],
    }


def _tool_by_name(agent, tool_name: str):
    for tool in getattr(agent, "tools", []) or []:
        if getattr(tool, "name", "") == tool_name:
            return tool
    raise AssertionError(f"Flow supervisor did not expose tool {tool_name!r}")


@contextmanager
def _patched_flow_runner(run_agent_streamed):
    with patch("src.api.chat_execute_flow.run_agent_streamed", run_agent_streamed), \
         patch("src.api.chat_common.run_agent_streamed", run_agent_streamed), \
         patch("src.lib.openai_agents.runner.run_agent_streamed", run_agent_streamed):
        yield


@pytest.fixture
def client(test_db, get_auth_mock, monkeypatch):
    """Create isolated app client with explicit auth + DB dependency overrides."""
    monkeypatch.setenv("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY", "test-key"))
    monkeypatch.setenv("GROQ_API_KEY", os.getenv("GROQ_API_KEY", "test-key"))
    monkeypatch.setenv("LLM_PROVIDER_STRICT_MODE", "false")

    get_auth_mock.set_user("valid_user")

    from main import create_app
    from src.api.auth import _get_user_from_cookie_impl
    from src.lib.config.prompt_loader import load_prompts
    from src.lib.agent_studio.system_agent_sync import sync_system_agents
    from src.lib.prompts import cache as prompt_cache
    from src.models.sql.agent import Agent as UnifiedAgent
    from src.models.sql.agent import Project
    from src.models.sql.agent import ProjectMember
    from src.models.sql.chat_message import ChatMessage
    from src.models.sql.chat_session import ChatSession
    from src.models.sql.curation_flow import CurationFlow
    from src.models.sql.database import Base
    from src.models.sql.database import get_db
    from src.models.sql.prompts import PromptExecutionLog, PromptTemplate
    from src.models.sql.user import User

    # Flow execution now resolves every step through the unified agents table
    # before the runner is invoked. Seed system agents in this isolated DB.
    Base.metadata.create_all(
        bind=test_db.get_bind(),
        tables=[
            User.__table__,
            Project.__table__,
            ProjectMember.__table__,
            UnifiedAgent.__table__,
            CurationFlow.__table__,
            ChatSession.__table__,
            ChatMessage.__table__,
            PromptTemplate.__table__,
            PromptExecutionLog.__table__,
        ],
    )
    load_prompts(db=test_db, force_reload=True)
    prompt_cache.initialize(test_db)
    sync_system_agents(test_db, force_reload=True)
    test_db.commit()

    app = create_app()

    def override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[_get_user_from_cookie_impl] = get_auth_mock.get_user
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_flow_lifecycle_create_update_execute_stream_and_stats(client: TestClient):
    flow_name = f"it-flow-{uuid4().hex[:12]}"

    create_payload = {
        "name": flow_name,
        "description": "integration lifecycle test",
        "flow_definition": _flow_definition(
            agent_id="chat_output",
            agent_display_name="Chat Output Agent",
        ),
    }
    create_resp = client.post("/api/flows", json=create_payload)
    assert create_resp.status_code == 201, create_resp.text
    created = create_resp.json()
    flow_id = created["id"]
    assert created["execution_count"] == 0

    update_payload = {
        "description": "integration lifecycle test (updated)",
        "flow_definition": _flow_definition(
            agent_id="chat_output",
            agent_display_name="Chat Output Agent",
            output_key="final_output_updated",
        ),
    }
    update_resp = client.put(f"/api/flows/{flow_id}", json=update_payload)
    assert update_resp.status_code == 200, update_resp.text
    updated = update_resp.json()
    assert updated["description"] == "integration lifecycle test (updated)"
    assert updated["flow_definition"]["nodes"][1]["data"]["output_key"] == "final_output_updated"

    async def _fake_run_agent_streamed(**kwargs):
        yield {"type": "RUN_STARTED", "data": {"trace_id": "trace-it-flow"}}
        _mark_fake_streaming_tool_complete(kwargs.get("agent"), output="done")
        yield {"type": "CHAT_OUTPUT_READY", "data": {"response": "done"}}

    execute_payload = {
        "flow_id": flow_id,
        "session_id": f"session-{uuid4().hex[:10]}",
        "user_query": "Run this flow end-to-end",
    }
    with _patched_flow_runner(_fake_run_agent_streamed):
        with client.stream("POST", "/api/chat/execute-flow", json=execute_payload) as stream_resp:
            events = _sse_events(stream_resp)
            assert stream_resp.status_code == 200

    event_types = [event.get("type") for event in events]
    assert "FLOW_STARTED" in event_types
    assert "RUN_STARTED" in event_types
    assert "CHAT_OUTPUT_READY" in event_types
    assert "FLOW_FINISHED" in event_types

    flow_started = next(event for event in events if event.get("type") == "FLOW_STARTED")
    assert flow_started.get("flow_id") == flow_id
    assert flow_started.get("session_id") == execute_payload["session_id"]

    flow_finished = next(event for event in events if event.get("type") == "FLOW_FINISHED")
    assert flow_finished.get("status") == "completed"

    get_resp = client.get(f"/api/flows/{flow_id}")
    assert get_resp.status_code == 200, get_resp.text
    fetched = get_resp.json()
    assert fetched["execution_count"] >= 1
    assert fetched["last_executed_at"] is not None


def test_execute_flow_persists_durable_history_and_replays_completed_turn(client: TestClient):
    flow_name = f"it-flow-replay-{uuid4().hex[:12]}"

    create_resp = client.post(
        "/api/flows",
        json={
            "name": flow_name,
            "description": "integration durable replay test",
            "flow_definition": _flow_definition(
                agent_id="chat_output",
                agent_display_name="Chat Output Agent",
            ),
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    flow_id = create_resp.json()["id"]

    session_id = f"session-{uuid4().hex[:10]}"
    turn_id = f"turn-{uuid4().hex[:10]}"
    runner_calls = 0

    async def _fake_run_agent_streamed(**kwargs):
        nonlocal runner_calls
        runner_calls += 1
        yield {"type": "RUN_STARTED", "data": {"trace_id": "trace-it-flow-replay"}}
        _mark_fake_streaming_tool_complete(
            kwargs.get("agent"),
            output="Selected TP53 for highest evidence confidence.",
        )
        yield {
            "type": "CHAT_OUTPUT_READY",
            "timestamp": "2026-02-26T00:00:03+00:00",
            "details": {"output": "Selected TP53 for highest evidence confidence."},
        }

    execute_payload = {
        "flow_id": flow_id,
        "session_id": session_id,
        "turn_id": turn_id,
        "user_query": "Run this flow end-to-end",
    }

    with _patched_flow_runner(_fake_run_agent_streamed):
        with client.stream("POST", "/api/chat/execute-flow", json=execute_payload) as stream_resp:
            events = _sse_events(stream_resp)
            assert stream_resp.status_code == 200

    assert runner_calls == 1
    assert [event.get("type") for event in events] == [
        "FLOW_STARTED",
        "RUN_STARTED",
        "CHAT_OUTPUT_READY",
        "FLOW_FINISHED",
    ]
    assert all(event.get("turn_id") == turn_id for event in events)

    history_resp = client.get(f"/api/chat/history/{session_id}")
    assert history_resp.status_code == 200, history_resp.text
    history_payload = history_resp.json()
    assert [(message["role"], message["message_type"], message["turn_id"]) for message in history_payload["messages"]] == [
        ("user", "text", turn_id),
        ("flow", "flow_summary", turn_id),
    ]
    assert history_payload["messages"][1]["content"] == "Selected TP53 for highest evidence confidence."
    assert "_assistant_message" not in (history_payload["messages"][1]["payload_json"] or {})

    async def _unexpected_run_agent_streamed(**_kwargs):
        raise AssertionError("run_agent_streamed should not run for a completed replayed flow turn")
        yield  # pragma: no cover

    with _patched_flow_runner(_unexpected_run_agent_streamed):
        with client.stream("POST", "/api/chat/execute-flow", json=execute_payload) as stream_resp:
            replay_events = _sse_events(stream_resp)
            assert stream_resp.status_code == 200

    assert [event.get("type") for event in replay_events] == [
        "RUN_STARTED",
        "CHAT_OUTPUT_READY",
        "FLOW_FINISHED",
    ]
    assert all(event.get("turn_id") == turn_id for event in replay_events)
    assert replay_events[0]["trace_id"] == "trace-it-flow-replay"

    fetched = client.get(f"/api/flows/{flow_id}").json()
    assert fetched["execution_count"] == 1


def test_execute_flow_emits_stream_error_for_unresolvable_agent(client: TestClient):
    flow_name = f"it-fail-flow-{uuid4().hex[:12]}"
    create_payload = {
        "name": flow_name,
        "description": "integration failure path",
        "flow_definition": _flow_definition(
            agent_id="unknown_agent_for_failure_path",
            agent_display_name="Unknown Agent",
        ),
    }
    create_resp = client.post("/api/flows", json=create_payload)
    assert create_resp.status_code == 201, create_resp.text
    flow_id = create_resp.json()["id"]

    execute_payload = {
        "flow_id": flow_id,
        "session_id": f"session-{uuid4().hex[:10]}",
        "user_query": "Run flow with invalid agent",
    }
    with client.stream("POST", "/api/chat/execute-flow", json=execute_payload) as stream_resp:
        events = _sse_events(stream_resp)
        assert stream_resp.status_code == 200

    event_types = [event.get("type") for event in events]
    assert "SUPERVISOR_ERROR" in event_types
    assert "RUN_ERROR" in event_types

    run_error = next(event for event in events if event.get("type") == "RUN_ERROR")
    run_error_text = (run_error.get("message") or "").lower()
    assert "no agent tools" in run_error_text or "could be created" in run_error_text


def test_execute_flow_projects_sample_pdf_artifact_to_runtime_json_file(
    client: TestClient,
    test_db,
    monkeypatch,
    tmp_path: Path,
):
    from src.lib.context import set_current_trace_id
    from src.lib.curation_workspace.extraction_results import ExtractionEnvelopeCandidate
    from src.models.sql.database import Base
    from src.models.sql.file_output import FileOutput
    from src.models.sql.pdf_document import PDFDocument

    sample_pdf_path = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "sample_fly_publication.pdf"
    )
    assert sample_pdf_path.exists()

    Base.metadata.create_all(
        bind=test_db.get_bind(),
        tables=[
            PDFDocument.__table__,
            FileOutput.__table__,
        ],
    )
    monkeypatch.setenv("FILE_OUTPUT_STORAGE_PATH", str(tmp_path / "file_outputs"))
    def _fake_document_context(document_id_arg, user_id_arg, *_args, **_kwargs):
        return SimpleNamespace(
            section_count=lambda: 1,
            abstract=None,
            to_agent_kwargs=lambda: {
                "document_id": document_id_arg,
                "user_id": user_id_arg,
            },
        )

    monkeypatch.setattr(
        "src.lib.flows.executor.DocumentContext.fetch",
        _fake_document_context,
    )
    monkeypatch.setattr(
        "src.lib.flows.executor._persist_flow_extraction_candidates_or_build_error",
        lambda **_kwargs: (True, None, None, []),
    )

    document_id = uuid4()
    test_db.add(
        PDFDocument(
            id=document_id,
            filename="test_sample_fly_publication.pdf",
            file_path=str(sample_pdf_path),
            file_hash="f" * 64,
            file_size=sample_pdf_path.stat().st_size,
            page_count=1,
        )
    )
    test_db.commit()

    flow_name = f"it-flow-sample-pdf-projection-{uuid4().hex[:12]}"
    create_resp = client.post(
        "/api/flows",
        json={
            "name": flow_name,
            "description": "integration sample PDF projection test",
            "flow_definition": _sample_pdf_projection_flow_definition(),
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    flow_id = create_resp.json()["id"]

    trace_id = "a" * 32

    async def _fake_run_agent_streamed(**kwargs):
        agent = kwargs["agent"]
        execution_state = agent._flow_execution_state
        ordered_tool_names = execution_state["ordered_tool_names"]
        assert ordered_tool_names == [
            "ask_gene_extractor_specialist",
            "ask_json_formatter_specialist",
        ]

        set_current_trace_id(trace_id)
        yield {"type": "RUN_STARTED", "data": {"trace_id": trace_id}}

        payload = _sample_pdf_gene_envelope()
        candidate = ExtractionEnvelopeCandidate(
            agent_key="gene_extractor",
            payload_json=payload,
            candidate_count=1,
            adapter_key="gene",
            conversation_summary="Seeded sample PDF gene artifact for projection.",
            metadata={
                "tool_name": ordered_tool_names[0],
                "flow_id": flow_id,
                "flow_name": flow_name,
                "step": 1,
                "agent_name": "Gene Extractor",
            },
        )
        evidence_records = payload["objects"][0]["evidence_records"]
        execution_state["evidence_registry"].add_many(evidence_records)
        execution_state["completed_steps"].append(
            {
                "step": 1,
                "agent_id": "gene_extractor",
                "agent_name": "Gene Extractor",
                "tool_name": ordered_tool_names[0],
                "output": json.dumps(payload),
                "output_preview": "Seeded sample PDF gene artifact.",
                "candidate": candidate,
                "evidence_records": evidence_records,
                "evidence_count": 1,
            }
        )
        execution_state["next_tool_index"] = 1
        yield {"type": "TOOL_COMPLETE", "details": {"toolName": ordered_tool_names[0]}}

        formatter_tool = _tool_by_name(agent, ordered_tool_names[1])
        formatter_result = await formatter_tool.on_invoke_tool(
            SimpleNamespace(tool_name=ordered_tool_names[1]),
            json.dumps({"query": "Export the projected gene rows as JSON."}),
        )
        file_info = json.loads(formatter_result)
        yield {
            "type": "TOOL_COMPLETE",
            "details": {"toolName": ordered_tool_names[1]},
        }
        yield {"type": "FILE_READY", "details": file_info}

    execute_payload = {
        "flow_id": flow_id,
        "session_id": f"session-{uuid4().hex[:10]}",
        "turn_id": f"turn-{uuid4().hex[:10]}",
        "document_id": str(document_id),
        "user_query": "Run this sample PDF flow and export the gene evidence rows.",
    }
    with _patched_flow_runner(_fake_run_agent_streamed):
        with client.stream("POST", "/api/chat/execute-flow", json=execute_payload) as stream_resp:
            events = _sse_events(stream_resp)
            assert stream_resp.status_code == 200

    event_types = [event.get("type") for event in events]
    assert event_types == [
        "FLOW_STARTED",
        "RUN_STARTED",
        "TOOL_COMPLETE",
        "FLOW_STEP_EVIDENCE",
        "TOOL_COMPLETE",
        "FLOW_STEP_EVIDENCE",
        "FILE_READY",
        "FLOW_FINISHED",
    ]

    file_ready = next(event for event in events if event.get("type") == "FILE_READY")
    file_details = file_ready["details"]
    assert file_details["format"] == "json"
    assert file_details["download_url"].endswith("/download")

    flow_finished = next(event for event in events if event.get("type") == "FLOW_FINISHED")
    assert flow_finished["status"] == "completed"
    assert flow_finished["document_id"] == str(document_id)
    assert flow_finished["total_evidence_records"] == 1

    test_db.expire_all()
    saved_file = test_db.query(FileOutput).filter(
        FileOutput.id == file_details["file_id"]
    ).one()
    assert saved_file.curator_id == "test_valid_user_00u1abc2def4"
    assert saved_file.session_id == execute_payload["session_id"]
    assert saved_file.trace_id == trace_id

    saved_rows = json.loads(Path(saved_file.file_path).read_text(encoding="utf-8"))
    assert saved_rows == [
        {
            "gene": "crumb",
            "primary_external_id": "FlyBase:FBgn0259211",
            "evidence_record_ids": ["sample-pdf-ev-1"],
        }
    ]

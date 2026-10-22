"""ALL-1367 regression using the shipped Alliance RGD agent and group mapping."""
from uuid import uuid4
from unittest.mock import AsyncMock

from tests.integration.test_flow_lifecycle_execute_flow_sse import (
    client,  # noqa: F401 - shared database-backed API fixture
    _flow_definition,
    _mark_fake_streaming_tool_complete,
    _patched_flow_runner,
    _sse_events,
)


def test_group_restricted_saved_flow_keeps_authenticated_context(
    client, test_db, get_auth_mock, monkeypatch,  # noqa: F811 - pytest injects the imported fixture
):
    """Real API/catalog/executor construction; only document I/O and provider run are stubbed."""
    import asyncio
    from src.lib.flows import executor
    from src.lib.document_context import DocumentContext
    from src.models.sql.pdf_document import PDFDocument
    from src.models.sql.user import User

    principal = asyncio.run(get_auth_mock.get_user())
    monkeypatch.setattr(principal, "groups", ["RGDCurator", "FlyBaseCurator"])
    definition = _flow_definition(agent_id="rgd_go_paper_curator", agent_display_name="RGD GO Paper Curator")
    definition["nodes"].append({
        "id": "output", "type": "output", "position": {"x": 520, "y": 0},
        "data": {"agent_id": "chat_output", "agent_display_name": "Chat Output", "output_key": "response"},
    })
    definition["edges"].append({"id": "attachment", "source": "agent_1", "target": "output", "role": "output_attachment"})
    created = client.post("/api/flows", json={"name": f"test_groups_{uuid4().hex}", "flow_definition": definition})
    assert created.status_code == 201, created.text
    flow_id = created.json()["id"]
    saved = client.put(f"/api/flows/{flow_id}", json={"flow_definition": definition})
    assert saved.status_code == 200, saved.text
    assert not saved.json()["has_critical_issues"]

    owner = test_db.query(User).filter_by(auth_sub=principal.uid).one()
    document_id = uuid4()
    test_db.add(PDFDocument(id=document_id, user_id=owner.id, filename="test_groups.pdf",
                            file_path="/tmp/test_groups.pdf", file_hash="a" * 64, file_size=1, page_count=1))
    test_db.commit()
    monkeypatch.setattr(executor.DocumentContext, "fetch", AsyncMock(return_value=DocumentContext(
        document_id=str(document_id), user_id=principal.uid, document_name="test_groups.pdf",
    )))
    built_contexts = []
    original_build = executor.get_agent_by_id
    def record_build(agent_id, **kwargs):
        built = original_build(agent_id, **kwargs)
        built_contexts.append((agent_id, kwargs))
        return built
    monkeypatch.setattr(executor, "get_agent_by_id", record_build)
    calls = []
    async def fake_provider(**kwargs):
        calls.append(kwargs)
        supervisor = kwargs["agent"]
        assert "Document Available" in supervisor.instructions
        assert supervisor._flow_execution_state["ordered_tool_names"] == [
            "ask_rgd_go_paper_curator_specialist", "ask_chat_output_specialist",
        ]
        # Provider behavior is outside this authorization regression.
        for _ in range(2):
            _mark_fake_streaming_tool_complete(supervisor, output="authorization fixture")
        yield {"type": "CHAT_OUTPUT_READY", "details": {
            "output": "authorization fixture", "formatter_node_id": "output", "source_node_ids": ["agent_1"],
        }}

    def request():
        return {"flow_id": flow_id, "document_id": str(document_id),
                "session_id": f"groups-{uuid4().hex}", "user_query": "Review paper"}
    with _patched_flow_runner(fake_provider):
        with client.stream("POST", "/api/chat/execute-flow", json=request()) as response:
            assert response.status_code == 200
            events = _sse_events(response)
        assert not [e for e in events if e.get("type") == "RUN_ERROR"], events
        assert any(e.get("type") == "FLOW_FINISHED" and e.get("status") == "completed" for e in events), events
        assert len(calls) == 1
        context = next(ctx for key, ctx in built_contexts if key == "rgd_go_paper_curator")
        assert set(context["authenticated_groups"]) == {"RGD", "FB"}
        assert context["db_user_id"] == owner.id
        # Revoked/mismatched claims cannot use even a previously saved flow.
        for denied in ([], ["FlyBaseCurator"]):
            monkeypatch.setattr(principal, "groups", denied)
            rejected = client.post("/api/chat/execute-flow", json=request())
            assert rejected.status_code == 403, rejected.text
        assert len(calls) == 1

    # Even an authorized caller cannot put authorization context in node data:
    # the request schema rejects the unsupported field before catalog checks.
    monkeypatch.setattr(principal, "groups", ["RGDCurator"])
    definition["nodes"][1]["data"]["authenticated_groups"] = ["RGD"]
    forged = client.post("/api/flows", json={"name": f"test_forged_{uuid4().hex}", "flow_definition": definition})
    assert forged.status_code == 422, forged.text
    assert any(error["type"] == "extra_forbidden" and error["loc"][-1] == "authenticated_groups"
               for error in forged.json()["detail"])

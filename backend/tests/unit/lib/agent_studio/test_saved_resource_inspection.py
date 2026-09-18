"""Saved-work inspection must preserve caller scope and exact revision identity."""
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.api import agent_studio as api
from src.lib.agent_studio import saved_resource_inspection as inspection
from src.lib.agent_studio.models import ChatContext


def flow():
    return SimpleNamespace(id=uuid4(), name="Stock extraction", description="Saved description",
                           updated_at=datetime.now(timezone.utc), execution_count=2,
                           flow_definition={"nodes": [{"id": "stock", "agent_revision_id": str(uuid4())}]})


def test_saved_flow_reads_only_owned_active_record_and_never_loads_editor():
    db = MagicMock()
    row = flow()
    db.scalars.return_value.one_or_none.return_value = row
    result = inspection.inspect_saved_resource(db, user_id=7, active_group_ids=[],
        request=inspection.SavedResourceInspection(action="flow", flow_id=str(row.id)))
    query = db.scalars.call_args.args[0].compile()
    assert "curation_flows.user_id =" in str(query)
    assert "curation_flows.is_active IS true" in str(query)
    assert set(query.params.values()) == {7, row.id}
    assert result["flow_definition"] == row.flow_definition
    assert result["saved"] is True and result["loaded_in_editor"] is False
    db.commit.assert_not_called()
    db.add.assert_not_called()


def test_unavailable_flow_does_not_disclose_record():
    db = MagicMock()
    db.scalars.return_value.one_or_none.return_value = None
    with pytest.raises(ValueError, match="unavailable to you"):
        inspection.inspect_saved_resource(db, user_id=8, active_group_ids=[],
            request=inspection.SavedResourceInspection(action="flow", flow_id=str(uuid4())))


def test_flow_search_is_owned_bounded_and_continuable(monkeypatch):
    monkeypatch.setattr(inspection, "get_tool_page_default_limit", lambda: 1)
    db = MagicMock()
    db.scalars.return_value.all.return_value = [flow(), flow()]
    result = inspection.inspect_saved_resource(db, user_id=7, active_group_ids=[],
        request=inspection.SavedResourceInspection(action="list_flows", query="%_", offset=3))
    assert len(result["flows"]) == 1
    assert result["next_call"]["arguments"] == {"action": "list_flows", "query": "%_", "offset": 4}
    query = db.scalars.call_args.args[0].compile()
    assert "curation_flows.user_id =" in str(query)
    assert "ESCAPE" in str(query)  # search metacharacters are literal curator text


def test_exact_revision_read_preserves_saved_identity_and_access_arguments(monkeypatch):
    agent, revision = uuid4(), uuid4()
    saved = MagicMock()
    saved.output_contract.generic_profile_ref = None
    saved.model_dump.return_value = {"custom_prompt": "Original prompt"}
    read = MagicMock(return_value=(SimpleNamespace(id=revision, revision=2, fingerprint="exact"), saved))
    monkeypatch.setattr(inspection, "get_execution_revision", read)
    db = MagicMock()
    result = inspection.inspect_saved_resource(db, user_id=7, active_group_ids=["TEAM"],
        request=inspection.SavedResourceInspection(action="agent_revision", agent_id=f"ca_{agent}", revision_id=str(revision)))
    read.assert_called_once_with(db, agent, revision, 7, active_group_ids=["TEAM"])
    assert result["revision_id"] == str(revision)
    assert result["snapshot"] == {"custom_prompt": "Original prompt"}
    assert result["loaded_in_editor"] is False


def test_revision_list_uses_authorized_service_cursor(monkeypatch):
    read = MagicMock(return_value=([], 5))
    monkeypatch.setattr(inspection, "list_execution_revisions", read)
    db, agent = MagicMock(), uuid4()
    result = inspection.inspect_saved_resource(db, user_id=7, active_group_ids=["TEAM"],
        request=inspection.SavedResourceInspection(action="agent_revisions", agent_id=str(agent), before_revision=9))
    read.assert_called_once_with(db, agent, 7, active_group_ids=["TEAM"], before_revision=9)
    assert result["next_call"]["arguments"]["before_revision"] == 5


@pytest.mark.parametrize("tab", ["agents", "flows", "agent_workshop"])
async def test_dispatch_exposes_tool_and_enforces_readonly_transaction(monkeypatch, tab):
    registry = MagicMock()
    registry.get_tool.return_value = None
    registry.get_all_tools.return_value = []
    monkeypatch.setattr(api, "get_diagnostic_tools_registry", lambda: registry)
    monkeypatch.setattr(api, "_ensure_flow_tools_registered", lambda _registry: None)
    context = ChatContext.model_validate({"active_tab": tab})
    tools = {item["name"]: item for item in api._get_all_opus_tools(context)}
    assert "inspect_saved_studio_resource" in tools
    assert api._agent_studio_tool_namespace("inspect_saved_studio_resource")[0] == "studio_saved_work"
    db = MagicMock()
    session = MagicMock()
    session.return_value.__enter__.return_value = db
    monkeypatch.setattr(api, "SessionLocal", session)
    read = MagicMock(return_value={"saved": True, "flows": []})
    monkeypatch.setattr(inspection, "inspect_saved_resource", read)
    result = await api._handle_tool_call("inspect_saved_studio_resource", {"action": "list_flows"},
        context, "curator@example.org", "curator", user_db_id=7, active_group_ids=["TEAM"])
    assert result["success"] is True
    assert str(db.execute.call_args.args[0]) == "SET TRANSACTION READ ONLY"
    assert read.call_args.kwargs["user_id"] == 7
    assert read.call_args.kwargs["active_group_ids"] == ["TEAM"]
    db.commit.assert_not_called()


async def test_dispatch_rejects_missing_identity_before_database_access(monkeypatch):
    session = MagicMock()
    monkeypatch.setattr(api, "SessionLocal", session)
    result = await api._handle_tool_call("inspect_saved_studio_resource", {"action": "list_flows"},
        ChatContext.model_validate({"active_tab": "flows"}), "curator@example.org", "curator")
    assert result["success"] is False
    session.assert_not_called()


def test_raw_sql_and_write_actions_are_not_tool_inputs():
    for data in ({"action": "list_flows", "sql": "SELECT * FROM users"}, {"action": "save_flow"}):
        with pytest.raises(ValidationError):
            inspection.SavedResourceInspection.model_validate(data)


@pytest.mark.parametrize("authorized", [True, False])
def test_saved_revision_includes_only_its_authorized_pinned_structure(monkeypatch, authorized):
    db, agent, revision, profile_id, profile_revision = MagicMock(), uuid4(), uuid4(), uuid4(), uuid4()
    saved = MagicMock()
    saved.output_contract.generic_profile_ref = SimpleNamespace(profile_id=profile_id, revision=3)
    saved.model_dump.return_value = {"output_contract": "exact saved contract"}
    read = MagicMock(return_value=(SimpleNamespace(id=revision, revision=2, fingerprint="agent-pin"), saved))
    profile = SimpleNamespace(id=profile_revision, revision=3, fingerprint="profile-pin",
                              contract={"description": "Only experimental genes", "fields": [{"key": "gene"}]})
    read_profile = MagicMock(return_value=profile)
    monkeypatch.setattr(inspection, "get_execution_revision", read)
    monkeypatch.setattr(inspection, "get_profile_revision", read_profile)
    request = inspection.SavedResourceInspection(action="agent_revision", agent_id=str(agent), revision_id=str(revision))
    if not authorized:
        read.side_effect = ValueError("Unavailable saved revision")
        with pytest.raises(ValueError, match="Unavailable"):
            inspection.inspect_saved_resource(db, user_id=7, active_group_ids=[], request=request)
        read_profile.assert_not_called()
        return
    result = inspection.inspect_saved_resource(db, user_id=7, active_group_ids=[], request=request)
    read_profile.assert_called_once_with(db, profile_id, 3, 7, include_archived=True)
    assert result["output_profile"] == {"profile_id": str(profile_id), "revision_id": str(profile_revision),
                                        "revision": 3, "fingerprint": "profile-pin", "contract": profile.contract}
    assert result["loaded_in_editor"] is False
    db.commit.assert_not_called()


@pytest.mark.parametrize("section", ["all", "instructions", "prompt_manifest", "tools", "group_prompts", "output_profile", "settings"])
def test_saved_revision_sections_fit_provider_and_reconstruct_exactly(monkeypatch, section):
    import json
    record = {
        "saved": True, "loaded_in_editor": False, "agent_id": str(uuid4()),
        "revision_id": str(uuid4()), "revision": 1, "fingerprint": "saved-pin",
        "snapshot": {"instructions": ('quoted "text"\\\nλ ' * 1600),
                     "tool_ids": ["search_document", "read_chunk"], "system_managed_tool_ids": ["record_evidence"],
                     "group_tool_policy": {}, "model_id": "fixture-model",
                     "group_prompt_layers": {"TEAM": "Team guidance"}, "group_prompt_overrides": {},
                     "group_rules_enabled": True,
                     "prompt_layer_manifest": {"layers": [
                         {"kind": "core_static", "content": "Keep supporting evidence"},
                         {"kind": "core_generated", "content": "Use the saved output contract"},
                         {"kind": "base_prompt", "content": "Extract the requested items"}]}},
        "output_profile": {"contract": {"fields": [{"key": "source"}]}},
    }
    read = MagicMock(return_value=record)
    monkeypatch.setattr(inspection, "_read_saved_resource", read)
    monkeypatch.setattr(inspection, "get_agent_studio_provider_tool_result_inline_max_chars", lambda: 1800)
    request = inspection.SavedResourceInspection(action="agent_revision", agent_id=record["agent_id"],
        revision_id=record["revision_id"], section=section)
    chunks = []
    calls = 0
    while True:
        page = inspection.inspect_saved_resource(MagicMock(), user_id=7, active_group_ids=["TEAM"], request=request)
        calls += 1
        assert len(api._serialize_provider_tool_result({"success": True, **page})) <= 1800
        assert page["saved"] and not page["loaded_in_editor"]
        if "content" in page:
            assert page["start"] == sum(map(len, chunks))
            chunks.append(page["content"])
        if page["complete"]:
            assert page["next_call"] is None
            break
        assert page["end"] > page["start"]
        request = inspection.SavedResourceInspection.model_validate(page["next_call"]["arguments"])
    assert read.call_count == calls  # Access is rechecked even on continuation pages.
    value = json.loads("".join(chunks)) if chunks else page.get("detail", record)
    if section == "all":
        assert value == record
    elif section == "instructions":
        assert value == record["snapshot"]["instructions"]
    elif section == "prompt_manifest":
        assert value == record["snapshot"]["prompt_layer_manifest"]
        assert [layer["kind"] for layer in value["layers"]] == ["core_static", "core_generated", "base_prompt"]
    elif section == "output_profile":
        assert value == record["output_profile"]
    elif section == "tools":
        assert value["tool_ids"] == record["snapshot"]["tool_ids"]
        assert value["system_managed_tool_ids"] == ["record_evidence"]
    elif section == "group_prompts":
        assert value["group_prompt_layers"] == {"TEAM": "Team guidance"}
    else:
        assert value["model_id"] == "fixture-model"
        assert "instructions" not in value


def test_saved_record_continuations_reject_changed_content_and_revoked_access(monkeypatch):
    row = {"flow_definition": {"instructions": "long content" * 1000}}
    read = MagicMock(return_value=row)
    monkeypatch.setattr(inspection, "_read_saved_resource", read)
    monkeypatch.setattr(inspection, "get_agent_studio_provider_tool_result_inline_max_chars", lambda: 1400)
    first = inspection.inspect_saved_resource(MagicMock(), user_id=7, active_group_ids=[],
        request=inspection.SavedResourceInspection(action="flow", flow_id=str(uuid4())))
    request = inspection.SavedResourceInspection.model_validate(first["next_call"]["arguments"])
    row["flow_definition"]["instructions"] += "changed"
    with pytest.raises(ValueError, match="changed"):
        inspection.inspect_saved_resource(MagicMock(), user_id=7, active_group_ids=[], request=request)
    read.side_effect = ValueError("Unavailable saved revision")
    with pytest.raises(ValueError, match="Unavailable"):
        inspection.inspect_saved_resource(MagicMock(), user_id=7, active_group_ids=[], request=request)


def test_saved_section_reads_only_requested_group_and_rejects_impossible_page(monkeypatch):
    record = {"snapshot": {"group_prompt_layers": {"TEAM": "selected", "OTHER": "unrelated"},
                           "group_prompt_overrides": {}, "group_rules_enabled": True}}
    request = inspection.SavedResourceInspection(action="agent_revision", agent_id=str(uuid4()),
        revision_id=str(uuid4()), section="group_prompts", group_id="TEAM")
    result = inspection._bounded_saved_record(record, request)
    assert result["detail"]["group_prompt_layers"] == {"TEAM": "selected"}
    with pytest.raises(ValueError, match="no prompt layer"):
        inspection._bounded_saved_record(record, request.model_copy(update={"group_id": "MISSING"}))
    with pytest.raises(ValueError, match="identity is missing"):
        inspection._bounded_saved_record(record, request.model_copy(update={"start": 1}))
    monkeypatch.setattr(inspection, "get_agent_studio_provider_tool_result_inline_max_chars", lambda: 24)
    with pytest.raises(ValueError, match="cannot fit"):
        inspection._bounded_saved_record(record, request)


def test_flow_run_trace_resolution_is_owned_and_bounded(monkeypatch):
    monkeypatch.setattr(inspection, "get_tool_page_default_limit", lambda: 1)
    db = MagicMock()
    db.scalars.return_value.all.return_value = ["trace-a", "trace-b"]
    run_id = str(uuid4())
    result = inspection.inspect_saved_resource(db, user_id=28, active_group_ids=[],
        request=inspection.SavedResourceInspection(action="flow_run_traces", flow_run_id=run_id))
    query = db.scalars.call_args.args[0].compile()
    assert "users.user_id =" in str(query)
    assert "chat_sessions.deleted_at IS NULL" in str(query)
    assert "chat_messages.chat_kind = chat_sessions.chat_kind" in str(query)
    assert 28 in query.params.values() and run_id in query.params.values()
    assert result["trace_ids"] == ["trace-a"]
    assert result["next_call"]["arguments"]["offset"] == 1
    db.commit.assert_not_called()


def test_missing_or_unowned_flow_run_reveals_no_traces():
    db = MagicMock()
    db.scalars.return_value.all.return_value = []
    result = inspection.inspect_saved_resource(db, user_id=8, active_group_ids=[],
        request=inspection.SavedResourceInspection(action="flow_run_traces", flow_run_id=str(uuid4())))
    assert result["trace_ids"] == []
    assert result["complete"] is True and result["next_call"] is None


# ---------------------------------------------------------------------------
# ALL-1244: failed flow runs are readable from durable chat records
# ---------------------------------------------------------------------------

REFUSAL_RUN_ID = "d202ed58-76ca-474d-ab48-d152adfd32c6"
REFUSAL_FLOW_ID = "f05d8320-c96d-4145-8c67-f18408edb3ca"
LEGACY_REFUSAL_MESSAGE = (
    "Flow cannot start because these steps are unavailable: 1 (Mouse Allele Identification), "
    "2 (Allele/Variant Extraction Agent (Custom)2). If a step needs a PDF, open Documents in the "
    "top navigation and load a document into chat."
)


def _summary_row(*, run_id=REFUSAL_RUN_ID, codes="absent", minute=19, document_id=None,
                 status="failed", trace_id=None, failure_reason=LEGACY_REFUSAL_MESSAGE):
    payload = {
        "flow_id": REFUSAL_FLOW_ID, "flow_name": "Identify Mouse Allele IDs",
        "flow_run_id": run_id, "session_id": "ff185910-87a4-4112-80ff-96b9855df133",
        "document_id": document_id, "status": status, "trace_id": trace_id,
        "failure_reason": failure_reason,
        "_replay_terminal_events": [{"type": "FLOW_FINISHED", "reason": "raw private reason"}],
    }
    if codes != "absent":
        payload["unavailable_step_reason_codes"] = codes
    return SimpleNamespace(
        created_at=datetime(2026, 9, 17, 11, minute, 10, tzinfo=timezone.utc),
        payload_json=payload, trace_id=trace_id,
        session_id="ff185910-87a4-4112-80ff-96b9855df133",
    )


def _owned_flow_db(rows, *, owned=True):
    db = MagicMock()
    owned_flow = SimpleNamespace(id=REFUSAL_FLOW_ID, name="Identify Mouse Allele IDs")
    db.scalars.return_value.one_or_none.return_value = owned_flow if owned else None
    db.execute.return_value.all.return_value = rows
    return db


def test_recent_flow_runs_returns_refused_run_without_trace_for_owner():
    rows = [_summary_row(codes=[{"step": 1, "reason_code": "document_required"},
                                {"step": 2, "reason_code": "document_required"}])]
    db = _owned_flow_db(rows)
    result = inspection.inspect_saved_resource(db, user_id=28, active_group_ids=["GROUP_A"],
        request=inspection.SavedResourceInspection(action="recent_flow_runs", flow_id=REFUSAL_FLOW_ID))
    assert result["saved"] is True and result["loaded_in_editor"] is False
    assert result["flow_id"] == REFUSAL_FLOW_ID
    run = result["runs"][0]
    assert run["flow_run_id"] == REFUSAL_RUN_ID
    assert run["status"] == "failed"
    assert run["document_loaded"] is False and run["document_id"] is None
    assert run["trace_id"] is None
    assert run["created_at"] == "2026-09-17T11:19:10+00:00"
    assert run["reason_codes"] == [{"step": 1, "reason_code": "document_required"},
                                   {"step": 2, "reason_code": "document_required"}]
    assert "raw private reason" not in json.dumps(result)
    assert "_replay_terminal_events" not in json.dumps(result)
    assert result["complete"] is True and result["next_call"] is None

    flow_query = db.scalars.call_args.args[0].compile()
    assert "curation_flows.user_id =" in str(flow_query)
    assert "curation_flows.is_active IS true" in str(flow_query)
    assert 28 in flow_query.params.values()
    runs_query = db.execute.call_args.args[0].compile()
    sql = str(runs_query)
    assert "users.user_id =" in sql
    assert "chat_sessions.deleted_at IS NULL" in sql
    assert "chat_messages.chat_kind = chat_sessions.chat_kind" in sql
    assert "chat_messages.message_type =" in sql
    assert "chat_messages.created_at >=" in sql
    assert "ORDER BY chat_messages.created_at DESC" in sql
    assert "flow_summary" in runs_query.params.values()
    assert REFUSAL_FLOW_ID in runs_query.params.values()
    # Active groups never widen the owner-only read.
    assert "GROUP_A" not in runs_query.params.values()
    db.commit.assert_not_called()
    db.add.assert_not_called()


def test_recent_flow_runs_for_another_users_flow_is_unavailable():
    db = _owned_flow_db([_summary_row()], owned=False)
    with pytest.raises(ValueError, match="unavailable to you"):
        inspection.inspect_saved_resource(db, user_id=8, active_group_ids=["GROUP_A"],
            request=inspection.SavedResourceInspection(action="recent_flow_runs", flow_id=REFUSAL_FLOW_ID))
    db.execute.assert_not_called()


def test_recent_flow_runs_requires_a_flow():
    with pytest.raises(ValueError, match="flow"):
        inspection.inspect_saved_resource(MagicMock(), user_id=8, active_group_ids=[],
            request=inspection.SavedResourceInspection(action="recent_flow_runs"))


def test_recent_flow_runs_legacy_record_returns_null_codes_and_stored_reason():
    db = _owned_flow_db([_summary_row(codes="absent")])
    run = inspection.inspect_saved_resource(db, user_id=28, active_group_ids=[],
        request=inspection.SavedResourceInspection(action="recent_flow_runs", flow_id=REFUSAL_FLOW_ID))["runs"][0]
    assert run["reason_codes"] is None
    assert run["failure_reason"] == LEGACY_REFUSAL_MESSAGE
    assert run["document_loaded"] is False


def test_recent_flow_runs_drops_unknown_codes_and_includes_completed_runs():
    rows = [
        _summary_row(run_id="run-new", codes=[{"step": 1, "reason_code": "private exception text"},
                                             {"step": 2, "reason_code": "provider_disabled"}, "bad"]),
        _summary_row(run_id="run-done", codes=[], status="completed", document_id="doc-7",
                     trace_id="trace-7", failure_reason=None, minute=10),
    ]
    db = _owned_flow_db(rows)
    runs = inspection.inspect_saved_resource(db, user_id=28, active_group_ids=[],
        request=inspection.SavedResourceInspection(action="recent_flow_runs", flow_id=REFUSAL_FLOW_ID))["runs"]
    assert runs[0]["reason_codes"] == [{"step": 1, "reason_code": "agent_unavailable"},
                                       {"step": 2, "reason_code": "provider_disabled"}]
    assert "private exception text" not in json.dumps(runs)
    assert runs[1] == {**runs[1], "status": "completed", "document_loaded": True,
                       "document_id": "doc-7", "trace_id": "trace-7", "reason_codes": []}


def test_recent_flow_runs_pages_newest_first_with_next_call(monkeypatch):
    monkeypatch.setattr(inspection, "get_tool_page_default_limit", lambda: 2)
    rows = [_summary_row(run_id=f"run-{minute}", minute=minute) for minute in (30, 20, 10)]
    db = _owned_flow_db(rows)
    result = inspection.inspect_saved_resource(db, user_id=28, active_group_ids=[],
        request=inspection.SavedResourceInspection(action="recent_flow_runs", flow_id=REFUSAL_FLOW_ID, offset=4))
    assert [run["flow_run_id"] for run in result["runs"]] == ["run-30", "run-20"]
    assert result["complete"] is False
    assert result["next_call"] == {"tool": "inspect_saved_studio_resource", "arguments": {
        "action": "recent_flow_runs", "flow_id": REFUSAL_FLOW_ID, "offset": 6}}
    runs_query = db.execute.call_args.args[0].compile()
    assert 4 in runs_query.params.values() and 3 in runs_query.params.values()


def test_recent_flow_runs_page_fits_provider_result_cap(monkeypatch):
    monkeypatch.setattr(inspection, "get_agent_studio_provider_tool_result_inline_max_chars", lambda: 3000)
    rows = [_summary_row(run_id=f"run-{minute}", minute=minute, failure_reason="x" * 900)
            for minute in (30, 20, 10)]
    db = _owned_flow_db(rows)
    result = inspection.inspect_saved_resource(db, user_id=28, active_group_ids=[],
        request=inspection.SavedResourceInspection(action="recent_flow_runs", flow_id=REFUSAL_FLOW_ID))
    assert len(api._serialize_provider_tool_result({"success": True, **result})) <= 3000
    shown = len(result["runs"])
    assert 1 <= shown < 3
    assert result["next_call"]["arguments"]["offset"] == shown


def test_flow_run_lookup_returns_failure_record_without_trace_ids():
    db = MagicMock()
    db.scalars.return_value.all.return_value = []
    db.execute.return_value.all.return_value = [
        _summary_row(codes=[{"step": 1, "reason_code": "document_required"}])
    ]
    result = inspection.inspect_saved_resource(db, user_id=28, active_group_ids=[],
        request=inspection.SavedResourceInspection(action="flow_run_traces", flow_run_id=REFUSAL_RUN_ID))
    assert result["trace_ids"] == []
    assert result["runs"][0]["flow_run_id"] == REFUSAL_RUN_ID
    assert result["runs"][0]["status"] == "failed"
    assert result["runs"][0]["reason_codes"] == [{"step": 1, "reason_code": "document_required"}]
    runs_query = db.execute.call_args.args[0].compile()
    assert "users.user_id =" in str(runs_query)
    assert "chat_sessions.deleted_at IS NULL" in str(runs_query)
    assert 28 in runs_query.params.values() and REFUSAL_RUN_ID in runs_query.params.values()


def test_unowned_flow_run_lookup_returns_no_failure_record():
    db = MagicMock()
    db.scalars.return_value.all.return_value = []
    db.execute.return_value.all.return_value = []
    result = inspection.inspect_saved_resource(db, user_id=8, active_group_ids=[],
        request=inspection.SavedResourceInspection(action="flow_run_traces", flow_run_id=REFUSAL_RUN_ID))
    assert result["trace_ids"] == [] and result["runs"] == []


async def test_recent_flow_runs_defaults_to_open_flow_and_stays_readonly(monkeypatch):
    db = MagicMock()
    session = MagicMock()
    session.return_value.__enter__.return_value = db
    monkeypatch.setattr(api, "SessionLocal", session)
    read = MagicMock(return_value={"saved": True, "runs": []})
    monkeypatch.setattr(inspection, "inspect_saved_resource", read)
    context = ChatContext.model_validate({"active_tab": "flows", "flow_id": REFUSAL_FLOW_ID})
    result = await api._handle_tool_call("inspect_saved_studio_resource", {"action": "recent_flow_runs"},
        context, "curator@example.org", "curator", user_db_id=28, active_group_ids=["GROUP_A"])
    assert result["success"] is True
    assert read.call_args.kwargs["request"].flow_id == REFUSAL_FLOW_ID
    assert read.call_args.kwargs["user_id"] == 28
    assert str(db.execute.call_args.args[0]) == "SET TRANSACTION READ ONLY"
    db.commit.assert_not_called()

    explicit = await api._handle_tool_call("inspect_saved_studio_resource",
        {"action": "recent_flow_runs", "flow_id": "11111111-1111-1111-1111-111111111111"},
        context, "curator@example.org", "curator", user_db_id=28, active_group_ids=[])
    assert explicit["success"] is True
    assert read.call_args.kwargs["request"].flow_id == "11111111-1111-1111-1111-111111111111"


async def test_recent_flow_runs_without_open_flow_asks_for_flow_id(monkeypatch):
    session = MagicMock()
    session.return_value.__enter__.return_value = MagicMock()
    monkeypatch.setattr(api, "SessionLocal", session)
    result = await api._handle_tool_call("inspect_saved_studio_resource", {"action": "recent_flow_runs"},
        ChatContext.model_validate({"active_tab": "agents"}), "curator@example.org", "curator",
        user_db_id=28, active_group_ids=[])
    assert result["success"] is False
    assert "flow_id" in result["error"]


def test_tool_description_and_prompt_direct_assistant_to_recorded_run_reasons():
    from src.api.agent_studio_opus_tools import INSPECT_SAVED_STUDIO_RESOURCE_TOOL
    from src.lib.agent_studio import prompt_builder

    description = INSPECT_SAVED_STUDIO_RESOURCE_TOOL["description"]
    assert "recent_flow_runs" in description
    assert "reason_codes" in description
    schema_actions = INSPECT_SAVED_STUDIO_RESOURCE_TOOL["input_schema"]["properties"]["action"]["enum"]
    assert {"recent_flow_runs", "flow_run_traces"} <= set(schema_actions)
    source = Path(prompt_builder.__file__).read_text(encoding="utf-8")
    assert "recent_flow_runs" in source
    assert "Run ID" in source

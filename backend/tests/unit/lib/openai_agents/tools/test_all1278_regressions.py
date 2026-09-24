"""ALL-1278 regressions for the confirmed production defects (a)-(e) and term-lookup limits.

Behavior-only: these import nothing added by ALL-1278, so they also run
against the pre-fix base (8a0aa9033), where tests (a)-(e) fail.
"""

import json

import pytest

import agr_ai_curation_alliance.tools.weaviate_search as weaviate_search
import src.lib.openai_agents.tools.evidence_workspace as evidence_workspace
from agr_ai_curation_alliance.tools import agr_curation
from src.lib.openai_agents import extraction_builder_workspace as builder

BUDGET = 32768


def _size(value):
    plain = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
    return max(len(json.dumps(plain, default=str, ensure_ascii=False).encode()),
               len(json.dumps(plain, default=str)), len(str(plain).encode()))


@pytest.fixture(autouse=True)
def identity_function_tool(monkeypatch):
    monkeypatch.setattr(evidence_workspace, "function_tool", lambda fn: fn)
    monkeypatch.setattr(weaviate_search, "function_tool", lambda fn: fn)


def _records(count):
    return [{"evidence_record_id": f"ev-{i}", "entity": "g", "verified_quote": "q" * 50,
             "document_id": "doc-1", "agent_note": "n" * 600} for i in range(count)]


@pytest.mark.asyncio
async def test_a_evidence_list_huge_limit_is_bounded():
    tool = evidence_workspace.create_list_recorded_evidence_tool(
        "doc-1", "user-1", workspace_records=_records(300))
    page = await tool(limit=10**9)
    assert page["returned_count"] <= 100
    assert _size(page) <= BUDGET


@pytest.mark.asyncio
async def test_a_evidence_negative_offset_is_rejected():
    tool = evidence_workspace.create_list_recorded_evidence_tool(
        "doc-1", "user-1", workspace_records=_records(3))
    page = await tool(offset=-1)
    assert page["status"] == "invalid_request"


def _workspace(count):
    workspace = builder.ExtractionBuilderWorkspace(run_id="r", document_id="doc-1")
    for i in range(count):
        workspace.upsert_candidate(candidate_id=f"cand-{i:04d}", staged_fields={"x": i},
                                   pending_ref_ids=[f"p-{i}"], evidence_record_ids=[f"e-{i}"])
    return workspace


def test_b_builder_page_limit_is_capped():
    page = agr_curation._builder_candidate_list(_workspace(400), limit=10**6, offset=0)
    assert page["returned_candidate_count"] <= 100


def test_c_builder_ack_does_not_grow_with_workspace():
    small = _size(agr_curation._builder_summary(_workspace(3)))
    large = _size(agr_curation._builder_summary(_workspace(600)))
    assert large - small < 16


def test_c_builder_page_stays_within_budget():
    page = agr_curation._builder_candidate_list(_workspace(600), limit=10**6, offset=0)
    assert _size(page) <= BUDGET


@pytest.mark.asyncio
async def test_e_section_page_is_bounded_by_total_size(monkeypatch):
    chunks = [{"id": f"c-{i}", "text": "t" * 3000, "page_number": 1, "section_title": "R"}
              for i in range(30)]

    async def _section(**_kwargs):
        return chunks

    monkeypatch.setattr(weaviate_search, "get_chunks_by_parent_section", _section)
    tool = weaviate_search.create_read_section_tool("doc-1", "user-1")
    result = await tool("R")
    assert _size(result) <= BUDGET


@pytest.mark.asyncio
async def test_e_section_max_chunks_is_capped(monkeypatch):
    chunks = [{"id": f"c-{i}", "text": "t", "page_number": 1, "section_title": "R"}
              for i in range(300)]

    async def _section(**_kwargs):
        return chunks

    monkeypatch.setattr(weaviate_search, "get_chunks_by_parent_section", _section)
    tool = weaviate_search.create_read_section_tool("doc-1", "user-1")
    result = await tool("R", max_chunks=10**6)
    assert result.section.returned_chunk_count <= 100


def _lookup_limit(result):
    return result.lookup_attempts[0]["attempted_query"]["limit"]


def test_term_search_limit_is_capped(monkeypatch):
    monkeypatch.setenv("TOOL_PAGE_MAX_LIMIT", "50")
    search = agr_curation._unwrap_function_tool_callable(
        agr_curation.search_domain_field_terms, "search_domain_field_terms")
    result = search(domain_pack_id="pack", object_type="Obj", field_path="f", query=" ", limit=10**6)
    assert result.status == "error"
    assert _lookup_limit(result) == 50


def test_term_resolver_limit_is_capped(monkeypatch):
    monkeypatch.setenv("TOOL_PAGE_MAX_LIMIT", "50")
    result = agr_curation._resolve_domain_field_term_impl(
        domain_pack_id="pack", object_type="Obj", field_path="f", source_phrase=" ", limit=10**6)
    assert result.status == "error"
    assert _lookup_limit(result) == 50


def test_b1_recall_chat_history_recent_page_is_bounded(monkeypatch):
    import asyncio
    from datetime import datetime, timezone
    from uuid import uuid4

    from src.lib.chat_history_repository import ASSISTANT_CHAT_KIND, ChatMessageRecord
    from src.lib.openai_agents import supervisor_context_tools as recall_module

    messages = [
        ChatMessageRecord(
            message_id=uuid4(), session_id="s", chat_kind=ASSISTANT_CHAT_KIND,
            turn_id=f"t{i}", role="user", message_type="text",
            content="pasted table row " * 12000, payload_json=None, trace_id=None,
            created_at=datetime(2026, 9, 22, tzinfo=timezone.utc),
        )
        for i in range(3)
    ]
    monkeypatch.setattr(recall_module, "get_current_session_id", lambda: "s")
    monkeypatch.setattr(recall_module, "get_current_user_id", lambda: "u")
    monkeypatch.setattr(recall_module, "_list_session_messages", lambda **_kwargs: messages)

    raw = asyncio.run(recall_module.recall_chat_history(detail="recent"))

    assert len(raw.encode("utf-8")) <= BUDGET

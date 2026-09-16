"""Explicit empty disease finalization, not success inferred from absent output."""

import pytest
from pydantic import ValidationError

from agr_ai_curation_alliance.tools import disease_builder_tools as tools
from src.lib.openai_agents.extraction_builder_workspace import (
    ExtractionBuilderWorkspace,
)


@pytest.fixture
def workspace(monkeypatch):
    state = ExtractionBuilderWorkspace(
        run_id="disease-empty", agent_id="disease_extractor"
    )
    monkeypatch.setattr(tools, "get_active_extraction_builder_workspace", lambda: state)
    monkeypatch.setattr(tools, "get_active_evidence_records_snapshot", lambda: [])
    monkeypatch.setattr(
        tools, "_emit_disease_builder_event", lambda *args, **kwargs: None
    )
    return state


def test_explicit_empty_disease_finalization_is_durable_and_idempotent(workspace):
    result = tools._finalize_disease_extraction_impl(candidate_ids=[])
    assert result.status == "ok"
    final = workspace.finalization
    assert final is not None
    assert final.source_candidate_ids == ()
    assert final.payload["curatable_objects"] == []
    assert final.payload["run_summary"]["kept_count"] == 0
    assert "no retained" in final.payload["summary"]
    assert tools._finalize_disease_extraction_impl(candidate_ids=[]).status == "ok"
    assert workspace.finalization is final
    assert (
        tools._finalize_disease_extraction_impl(candidate_ids=["different"]).status
        == "error"
    )
    assert workspace.finalization is final


@pytest.mark.asyncio
async def test_public_finalizer_tool_accepts_explicit_empty_list(workspace):
    from agents.tool_context import ToolContext

    arguments = '{"candidate_ids": []}'
    await tools.finalize_disease_extraction.on_invoke_tool(
        ToolContext(
            context=None,
            tool_name="finalize_disease_extraction",
            tool_call_id="empty-disease",
            tool_arguments=arguments,
        ),
        arguments,
    )
    assert workspace.finalization is not None
    assert workspace.finalization.payload["curatable_objects"] == []


@pytest.mark.parametrize("bad", [None, [None], [""], ["missing"], ["same", "same"]])
def test_invalid_or_missing_candidates_do_not_become_empty_success(workspace, bad):
    result = tools._finalize_disease_extraction_impl(candidate_ids=bad)
    assert result.lookup_status != "success"
    assert workspace.finalization is None


def test_candidate_list_is_still_required():
    with pytest.raises(ValidationError):
        tools.DiseaseFinalizeInput()


def test_nonempty_candidate_still_requires_evidence(workspace):
    workspace.upsert_candidate(
        candidate_id="unsupported", staged_fields={}, status="valid"
    )
    result = tools._finalize_disease_extraction_impl(candidate_ids=["unsupported"])
    assert result.lookup_status != "success"
    assert workspace.finalization is None

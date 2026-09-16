"""Search syntax failures are repairable; operational failures remain observable."""

import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.api import agent_studio
from src.lib.agent_studio import catalog_service
from src.lib.agent_studio.diagnostic_tools import codebase_tools, reset_registry
from src.lib.agent_studio.models import ChatContext
from src.lib.prompts import cache as prompt_cache


@pytest.fixture(autouse=True)
def _isolated_diagnostic_registry(monkeypatch):
    # The diagnostic registry singleton builds the prompt catalog on first use.
    # Earlier DB-backed tests can leave detached PromptTemplate rows in the
    # prompt cache, so stub the catalog exactly as test_hybrid_tool_registry does
    # and rebuild the registry for this module only.
    monkeypatch.setattr(prompt_cache, "is_initialized", lambda: True)
    monkeypatch.setattr(
        catalog_service,
        "get_prompt_catalog",
        lambda: SimpleNamespace(
            catalog=SimpleNamespace(
                categories=[SimpleNamespace(agents=[SimpleNamespace(agent_id="demo_review")])],
                available_groups=[],
            )
        ),
    )
    reset_registry()
    yield
    reset_registry()


@pytest.fixture
def repository(tmp_path, monkeypatch):
    path = tmp_path / "backend/src/demo.py"
    path.parent.mkdir(parents=True)
    path.write_text("materialize_persisted_envelope_review_rows(\nother_symbol\n")
    monkeypatch.setenv("AGENT_STUDIO_CODEBASE_ROOT", str(tmp_path))
    return tmp_path


def test_real_rg_literal_regex_and_no_matches(repository):
    literal = codebase_tools.search_codebase(
        "materialize_persisted_envelope_review_rows(", match_mode="literal"
    )
    assert literal["result_count"] == 1
    regex = codebase_tools.search_codebase(r"materialize_.*rows\(")
    assert regex["results"] == literal["results"]
    empty = codebase_tools.search_codebase("no_such_symbol")
    assert empty["status"] == "ok" and empty["result_count"] == 0


@pytest.mark.asyncio
async def test_invalid_regex_through_workshop_can_retry_literal(
    repository, monkeypatch
):
    report = Mock()
    monkeypatch.setattr(agent_studio, "_report_agent_studio_exception_once", report)
    context = ChatContext.model_validate({"active_tab": "agent_workshop"})
    query = "materialize_persisted_envelope_review_rows("
    result = await agent_studio._handle_tool_call(
        "search_codebase", {"query": query}, context, "test@example.org", "test-sub"
    )
    assert result["success"] is False
    assert result["error"] == "invalid_regex"
    assert "literal" in result["message"]
    assert query not in str(result)
    report.assert_not_called()
    retry = await agent_studio._handle_tool_call(
        "search_codebase",
        {"query": query, "match_mode": "literal"},
        context,
        "test@example.org",
        "test-sub",
    )
    assert retry["status"] == "ok" and retry["result_count"] == 1
    report.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["io", "timeout", "missing"])
async def test_operational_errors_keep_reporting(repository, monkeypatch, failure):
    report = Mock()
    monkeypatch.setattr(agent_studio, "_report_agent_studio_exception_once", report)
    if failure == "missing":
        monkeypatch.setattr(codebase_tools.shutil, "which", lambda _: None)
    else:

        def run(command, **kwargs):
            if failure == "timeout":
                raise subprocess.TimeoutExpired(command, 1)
            return subprocess.CompletedProcess(command, 2, "", "permission denied")

        monkeypatch.setattr(codebase_tools.subprocess, "run", run)
    result = await agent_studio._handle_tool_call(
        "search_codebase",
        {"query": "symbol"},
        ChatContext.model_validate({"active_tab": "agent_workshop"}),
        "test@example.org",
        "test-sub",
    )
    assert result["success"] is False
    assert result["error"] == "Tool execution failed unexpectedly."
    report.assert_called_once()
    assert report.call_args.kwargs["operation"] == "diagnostic_tool_execution_failed"


def test_literal_continuation_preserves_mode_and_path_controls(repository, monkeypatch):
    (repository / "backend/src/second.py").write_text(
        "materialize_persisted_envelope_review_rows(\n"
    )
    (repository / "backend/src/.env").write_text(
        "materialize_persisted_envelope_review_rows(\n"
    )
    result = codebase_tools.search_codebase(
        "materialize_persisted_envelope_review_rows(",
        match_mode="literal",
        limit=1,
        path_glob="**/*",
    )
    assert result["result_set_count"] == 2
    assert result["next_call"]["arguments"]["match_mode"] == "literal"
    next_page = codebase_tools.search_codebase(**result["next_call"]["arguments"])
    assert next_page["complete"] is True
    assert result["results"][0]["path"] != next_page["results"][0]["path"]


def test_schema_explains_matching_modes():
    tools = {
        tool["name"]: tool
        for tool in agent_studio._get_all_opus_tools(
            ChatContext.model_validate({"active_tab": "agents"})
        )
    }
    schema = tools["search_codebase"]["input_schema"]["properties"]
    assert schema["match_mode"]["enum"] == ["regex", "literal"]
    assert schema["match_mode"]["default"] == "regex"


@pytest.mark.parametrize("prefix", ["regex parse error:\n", "rg: regex parse error:\n"])
def test_recognized_regex_diagnostics_are_bounded(repository, monkeypatch, prefix):
    monkeypatch.setattr(
        codebase_tools.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 2, "", prefix + "private-query" * 10000
        ),
    )
    result = codebase_tools.search_codebase("(")
    assert result["error"] == "invalid_regex"
    assert "private-query" not in str(result)
    assert len(str(result)) < 500


def test_invalid_match_mode_is_repairable(repository):
    result = codebase_tools.search_codebase("symbol", match_mode="unknown")
    assert result["error"] == "invalid_match_mode"
    assert result["success"] is False

"""Tests for agent_studio.runtime_validation diagnostics and startup gating."""

from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def reset_startup_report():
    from src.lib.agent_studio.runtime_validation import (
        reset_startup_agent_validation_report,
    )

    reset_startup_agent_validation_report()
    yield
    reset_startup_agent_validation_report()


def _agent(**kwargs):
    defaults = {
        "agent_key": "agent_key",
        "name": "Agent",
        "visibility": "private",
        "user_id": 1,
        "project_id": None,
        "template_source": None,
        "model_id": "gpt-5.4-mini",
        "model_reasoning": None,
        "tool_ids": [],
        "output_schema_key": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_build_agent_runtime_report_detects_unknown_model_and_tool(monkeypatch):
    import src.lib.agent_studio.runtime_validation as module

    monkeypatch.setattr(module, "_fetch_active_agents", lambda: [
        _agent(agent_key="ca_bad", model_id="unknown-model", tool_ids=["missing_tool"])
    ])
    monkeypatch.setattr(module, "_load_expected_system_agent_keys", lambda: (set(), None))
    monkeypatch.setattr(module, "load_models", lambda: None)
    monkeypatch.setattr(module, "list_models", lambda: [SimpleNamespace(model_id="gpt-5.4-mini")])
    monkeypatch.setattr(
        module,
        "_load_runtime_policy",
        lambda: {
            "tool_bindings": {"agr_curation_query": {"required_context": []}},
            "canonicalize_tool_id": lambda tool_id: tool_id,
            "document_tool_ids": {"search_document"},
            "agr_db_query_tool_ids": {"agr_curation_query"},
        },
    )

    report = module.build_agent_runtime_report(strict_mode=False)
    assert report["status"] == "unhealthy"
    assert any("Unknown model_id 'unknown-model'" in msg for msg in report["errors"])
    assert any("Unknown tool_ids: missing_tool" in msg for msg in report["warnings"])
    assert report["summary"]["disabled_agent_count"] == 1
    assert report["agents"][0]["disabled"] is True


def test_build_agent_runtime_report_unknown_tool_only_warns_and_disables(monkeypatch):
    import src.lib.agent_studio.runtime_validation as module

    monkeypatch.setattr(module, "_fetch_active_agents", lambda: [
        _agent(agent_key="gene", visibility="system", user_id=None, tool_ids=["missing_tool"])
    ])
    monkeypatch.setattr(module, "_load_expected_system_agent_keys", lambda: ({"gene"}, None))
    monkeypatch.setattr(module, "load_models", lambda: None)
    monkeypatch.setattr(module, "list_models", lambda: [SimpleNamespace(model_id="gpt-5.4-mini")])
    monkeypatch.setattr(
        module,
        "_load_runtime_policy",
        lambda: {
            "tool_bindings": {"agr_curation_query": {"required_context": []}},
            "canonicalize_tool_id": lambda tool_id: tool_id,
            "document_tool_ids": {"search_document"},
            "agr_db_query_tool_ids": {"agr_curation_query"},
        },
    )

    report = module.build_agent_runtime_report(strict_mode=False)

    assert report["status"] == "degraded"
    assert report["errors"] == []
    assert any("Unknown tool_ids: missing_tool" in msg for msg in report["warnings"])
    assert any("Disabled: references tools from uninstalled package(s)." in msg for msg in report["warnings"])
    assert report["agents"][0]["disabled"] is True


def test_build_agent_runtime_report_detects_missing_system_agents(monkeypatch):
    import src.lib.agent_studio.runtime_validation as module

    monkeypatch.setattr(module, "_fetch_active_agents", lambda: [
        _agent(agent_key="gene", visibility="system", user_id=None, tool_ids=["agr_curation_query"]),
    ])
    monkeypatch.setattr(module, "_load_expected_system_agent_keys", lambda: ({"gene", "phenotype_extractor"}, None))
    monkeypatch.setattr(module, "load_models", lambda: None)
    monkeypatch.setattr(module, "list_models", lambda: [SimpleNamespace(model_id="gpt-5.4-mini")])
    monkeypatch.setattr(
        module,
        "_load_runtime_policy",
        lambda: {
            "tool_bindings": {"agr_curation_query": {"required_context": []}},
            "canonicalize_tool_id": lambda tool_id: tool_id,
            "document_tool_ids": {"search_document"},
            "agr_db_query_tool_ids": {"agr_curation_query"},
        },
    )

    report = module.build_agent_runtime_report(strict_mode=False)
    assert report["status"] == "unhealthy"
    assert report["summary"]["missing_system_agent_count"] == 1
    assert any(
        "Missing active system agents in unified agents table: phenotype_extractor" in msg
        for msg in report["errors"]
    )


def test_build_agent_runtime_report_warns_when_expected_system_keys_unavailable(monkeypatch):
    import src.lib.agent_studio.runtime_validation as module

    monkeypatch.setattr(module, "_fetch_active_agents", lambda: [
        _agent(agent_key="gene", visibility="system", user_id=None, tool_ids=["agr_curation_query"]),
    ])
    monkeypatch.setattr(
        module,
        "_load_expected_system_agent_keys",
        lambda: (set(), "Failed to load expected system agents from layered sources: boom"),
    )
    monkeypatch.setattr(module, "load_models", lambda: None)
    monkeypatch.setattr(module, "list_models", lambda: [SimpleNamespace(model_id="gpt-5.4-mini")])
    monkeypatch.setattr(
        module,
        "_load_runtime_policy",
        lambda: {
            "tool_bindings": {"agr_curation_query": {"required_context": []}},
            "canonicalize_tool_id": lambda tool_id: tool_id,
            "document_tool_ids": {"search_document"},
            "agr_db_query_tool_ids": {"agr_curation_query"},
        },
    )

    report = module.build_agent_runtime_report(strict_mode=False)
    assert report["status"] == "degraded"
    assert report["errors"] == []
    assert report["summary"]["missing_system_agent_count"] == 0
    assert any("Failed to load expected system agents from layered sources: boom" in msg for msg in report["warnings"])


def test_build_agent_runtime_report_allows_unseeded_core_only_runtime(monkeypatch):
    import src.lib.agent_studio.runtime_validation as module

    monkeypatch.setattr(module, "_fetch_active_agents", lambda: [])
    monkeypatch.setattr(module, "_load_expected_system_agent_keys", lambda: ({"supervisor"}, None))
    monkeypatch.setattr(module, "load_models", lambda: None)
    monkeypatch.setattr(module, "list_models", lambda: [SimpleNamespace(model_id="gpt-5.4-mini")])
    monkeypatch.setattr(
        module,
        "_load_runtime_policy",
        lambda: {
            "tool_bindings": {},
            "canonicalize_tool_id": lambda tool_id: tool_id,
            "document_tool_ids": {"search_document"},
            "agr_db_query_tool_ids": {"agr_curation_query"},
        },
    )

    report = module.build_agent_runtime_report(strict_mode=False)

    assert report["status"] == "degraded"
    assert report["errors"] == []
    assert report["summary"]["missing_system_agent_count"] == 1
    assert any(
        "allowing core-only runtime bootstrap" in msg
        for msg in report["warnings"]
    )


def test_allow_unseeded_core_only_runtime_accepts_chat_output_core_bundle():
    import src.lib.agent_studio.runtime_validation as module

    assert module._allow_unseeded_core_only_runtime(
        expected_system_agent_keys={"supervisor", "chat_output"},
        actual_system_agent_keys=set(),
        agent_count=0,
    ) is True


def test_build_agent_runtime_report_warns_missing_template_tools_non_strict(monkeypatch):
    import src.lib.agent_studio.runtime_validation as module

    monkeypatch.setattr(module, "_fetch_active_agents", lambda: [
        _agent(
            agent_key="gene",
            visibility="system",
            user_id=None,
            tool_ids=["agr_curation_query"],
        ),
        _agent(
            agent_key="ca_custom_gene",
            visibility="private",
            user_id=7,
            template_source="gene",
            tool_ids=[],
        ),
    ])
    monkeypatch.setattr(module, "_load_expected_system_agent_keys", lambda: ({"gene"}, None))
    monkeypatch.setattr(module, "load_models", lambda: None)
    monkeypatch.setattr(module, "list_models", lambda: [SimpleNamespace(model_id="gpt-5.4-mini")])
    monkeypatch.setattr(
        module,
        "_load_runtime_policy",
        lambda: {
            "tool_bindings": {"agr_curation_query": {"required_context": []}},
            "canonicalize_tool_id": lambda tool_id: tool_id,
            "document_tool_ids": {"search_document"},
            "agr_db_query_tool_ids": {"agr_curation_query"},
        },
    )

    report = module.build_agent_runtime_report(strict_mode=False)
    assert report["status"] == "degraded"
    assert report["errors"] == []
    assert report["summary"]["critical_missing_tool_backfill_candidates"] == 1
    assert any("Likely missing critical tools from template 'gene'" in msg for msg in report["warnings"])


def test_build_agent_runtime_report_escalates_template_drift_in_strict_mode(monkeypatch):
    import src.lib.agent_studio.runtime_validation as module

    monkeypatch.setattr(module, "_fetch_active_agents", lambda: [
        _agent(
            agent_key="gene",
            visibility="system",
            user_id=None,
            tool_ids=["agr_curation_query"],
        ),
        _agent(
            agent_key="ca_custom_gene",
            visibility="private",
            user_id=7,
            template_source="gene",
            tool_ids=[],
        ),
    ])
    monkeypatch.setattr(module, "_load_expected_system_agent_keys", lambda: ({"gene"}, None))
    monkeypatch.setattr(module, "load_models", lambda: None)
    monkeypatch.setattr(module, "list_models", lambda: [SimpleNamespace(model_id="gpt-5.4-mini")])
    monkeypatch.setattr(
        module,
        "_load_runtime_policy",
        lambda: {
            "tool_bindings": {"agr_curation_query": {"required_context": []}},
            "canonicalize_tool_id": lambda tool_id: tool_id,
            "document_tool_ids": {"search_document"},
            "agr_db_query_tool_ids": {"agr_curation_query"},
        },
    )

    report = module.build_agent_runtime_report(strict_mode=True)
    assert report["status"] == "unhealthy"
    assert any("Likely missing critical tools from template 'gene'" in msg for msg in report["errors"])


def test_validate_and_cache_agent_runtime_contracts_caches_report(monkeypatch):
    import src.lib.agent_studio.runtime_validation as module

    monkeypatch.setattr(
        module,
        "validate_agent_runtime_contracts",
        lambda strict_mode=None: (
            True,
            {
                "status": "healthy",
                "strict_mode": False,
                "validated_at": "2026-02-25T00:00:00+00:00",
                "errors": [],
                "warnings": [],
                "agents": [],
                "summary": {},
            },
        ),
    )

    report = module.validate_and_cache_agent_runtime_contracts(strict_mode=False)
    cached = module.get_startup_agent_validation_report()
    assert report["status"] == "healthy"
    assert cached is not None
    assert cached["status"] == "healthy"


def test_validate_and_cache_agent_runtime_contracts_raises_on_error(monkeypatch):
    import src.lib.agent_studio.runtime_validation as module

    monkeypatch.setattr(
        module,
        "validate_agent_runtime_contracts",
        lambda strict_mode=None: (
            False,
            {
                "status": "unhealthy",
                "strict_mode": True,
                "validated_at": "2026-02-25T00:00:00+00:00",
                "errors": ["ca_bad: Unknown model_id 'legacy-model'"],
                "warnings": [],
                "agents": [],
                "summary": {},
            },
        ),
    )

    with pytest.raises(RuntimeError, match="Agent runtime validation failed"):
        module.validate_and_cache_agent_runtime_contracts(strict_mode=True)


def test_validate_and_cache_agent_runtime_contracts_disables_missing_tool_agents(monkeypatch):
    import src.lib.agent_studio.runtime_validation as module

    disable_calls = []
    monkeypatch.setattr(
        module,
        "validate_agent_runtime_contracts",
        lambda strict_mode=None: (
            True,
            {
                "status": "degraded",
                "strict_mode": False,
                "validated_at": "2026-02-25T00:00:00+00:00",
                "errors": [],
                "warnings": ["gene: Unknown tool_ids: missing_tool"],
                "agents": [
                    {
                        "agent_key": "gene",
                        "disabled": True,
                        "disable_reason": "references tools from uninstalled package(s).",
                    }
                ],
                "summary": {"disabled_agent_count": 1},
            },
        ),
    )
    monkeypatch.setattr(
        module,
        "_disable_agents_with_missing_tools",
        lambda report: disable_calls.append(report),
    )

    report = module.validate_and_cache_agent_runtime_contracts(strict_mode=False)

    assert report["status"] == "degraded"
    assert len(disable_calls) == 1

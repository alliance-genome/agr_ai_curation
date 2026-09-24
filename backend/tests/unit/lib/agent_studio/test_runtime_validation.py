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
        "category": None,
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
            "tool_bindings": {"demo_search_tool": {"required_context": []}},
            "canonicalize_tool_id": lambda tool_id: tool_id,
            "document_tool_ids": {"search_document"},
            "package_required_tool_ids": {"demo_search_tool"},
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
        _agent(agent_key="demo_agent", visibility="system", user_id=None, tool_ids=["missing_tool"])
    ])
    monkeypatch.setattr(module, "_load_expected_system_agent_keys", lambda: ({"demo_agent"}, None))
    monkeypatch.setattr(module, "load_models", lambda: None)
    monkeypatch.setattr(module, "list_models", lambda: [SimpleNamespace(model_id="gpt-5.4-mini")])
    monkeypatch.setattr(
        module,
        "_load_runtime_policy",
        lambda: {
            "tool_bindings": {"demo_search_tool": {"required_context": []}},
            "canonicalize_tool_id": lambda tool_id: tool_id,
            "document_tool_ids": {"search_document"},
            "package_required_tool_ids": {"demo_search_tool"},
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
        _agent(agent_key="demo_agent", visibility="system", user_id=None, tool_ids=["demo_search_tool"]),
    ])
    monkeypatch.setattr(module, "_load_expected_system_agent_keys", lambda: ({"demo_agent", "demo_enricher"}, None))
    monkeypatch.setattr(module, "load_models", lambda: None)
    monkeypatch.setattr(module, "list_models", lambda: [SimpleNamespace(model_id="gpt-5.4-mini")])
    monkeypatch.setattr(
        module,
        "_load_runtime_policy",
        lambda: {
            "tool_bindings": {"demo_search_tool": {"required_context": []}},
            "canonicalize_tool_id": lambda tool_id: tool_id,
            "document_tool_ids": {"search_document"},
            "package_required_tool_ids": {"demo_search_tool"},
        },
    )

    report = module.build_agent_runtime_report(strict_mode=False)
    assert report["status"] == "unhealthy"
    assert report["summary"]["missing_system_agent_count"] == 1
    assert any(
        "Missing active system agents in unified agents table: demo_enricher" in msg
        for msg in report["errors"]
    )


def test_build_agent_runtime_report_warns_when_expected_system_keys_unavailable(monkeypatch):
    import src.lib.agent_studio.runtime_validation as module

    monkeypatch.setattr(module, "_fetch_active_agents", lambda: [
        _agent(agent_key="demo_agent", visibility="system", user_id=None, tool_ids=["demo_search_tool"]),
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
            "tool_bindings": {"demo_search_tool": {"required_context": []}},
            "canonicalize_tool_id": lambda tool_id: tool_id,
            "document_tool_ids": {"search_document"},
            "package_required_tool_ids": {"demo_search_tool"},
        },
    )

    report = module.build_agent_runtime_report(strict_mode=False)
    assert report["status"] == "degraded"
    assert report["errors"] == []
    assert report["summary"]["missing_system_agent_count"] == 0
    assert any("Failed to load expected system agents from layered sources: boom" in msg for msg in report["warnings"])


def test_build_agent_runtime_report_allows_unseeded_package_runtime(monkeypatch):
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
            "package_required_tool_ids": set(),
        },
    )

    report = module.build_agent_runtime_report(strict_mode=False)

    assert report["status"] == "degraded"
    assert report["errors"] == []
    assert report["summary"]["missing_system_agent_count"] == 1
    assert any(
        "allowing package runtime bootstrap" in msg
        for msg in report["warnings"]
    )


def test_allow_unseeded_package_runtime_requires_empty_database():
    import src.lib.agent_studio.runtime_validation as module

    assert module._allow_unseeded_package_runtime(
        actual_system_agent_keys=set(),
        agent_count=0,
    ) is True
    assert module._allow_unseeded_package_runtime(
        actual_system_agent_keys={"supervisor"},
        agent_count=1,
    ) is False
    assert module._allow_unseeded_package_runtime(
        actual_system_agent_keys=set(),
        agent_count=1,
    ) is False


def test_build_agent_runtime_report_warns_missing_template_tools_non_strict(monkeypatch):
    import src.lib.agent_studio.runtime_validation as module

    monkeypatch.setattr(module, "_fetch_active_agents", lambda: [
        _agent(
            agent_key="demo_agent",
            visibility="system",
            user_id=None,
            tool_ids=["demo_search_tool"],
        ),
        _agent(
            agent_key="ca_custom_demo",
            visibility="private",
            user_id=7,
            template_source="demo_agent",
            tool_ids=[],
        ),
    ])
    monkeypatch.setattr(module, "_load_expected_system_agent_keys", lambda: ({"demo_agent"}, None))
    monkeypatch.setattr(module, "load_models", lambda: None)
    monkeypatch.setattr(module, "list_models", lambda: [SimpleNamespace(model_id="gpt-5.4-mini")])
    monkeypatch.setattr(
        module,
        "_load_runtime_policy",
        lambda: {
            "tool_bindings": {"demo_search_tool": {"required_context": []}},
            "canonicalize_tool_id": lambda tool_id: tool_id,
            "document_tool_ids": {"search_document"},
            "package_required_tool_ids": {"demo_search_tool"},
        },
    )

    report = module.build_agent_runtime_report(strict_mode=False)
    assert report["status"] == "degraded"
    assert report["errors"] == []
    assert report["summary"]["critical_missing_tool_backfill_candidates"] == 1
    assert any("Likely missing critical tools from template 'demo_agent'" in msg for msg in report["warnings"])


def test_build_agent_runtime_report_escalates_template_drift_in_strict_mode(monkeypatch):
    import src.lib.agent_studio.runtime_validation as module

    monkeypatch.setattr(module, "_fetch_active_agents", lambda: [
        _agent(
            agent_key="demo_agent",
            visibility="system",
            user_id=None,
            tool_ids=["demo_search_tool"],
        ),
        _agent(
            agent_key="ca_custom_demo",
            visibility="private",
            user_id=7,
            template_source="demo_agent",
            tool_ids=[],
        ),
    ])
    monkeypatch.setattr(module, "_load_expected_system_agent_keys", lambda: ({"demo_agent"}, None))
    monkeypatch.setattr(module, "load_models", lambda: None)
    monkeypatch.setattr(module, "list_models", lambda: [SimpleNamespace(model_id="gpt-5.4-mini")])
    monkeypatch.setattr(
        module,
        "_load_runtime_policy",
        lambda: {
            "tool_bindings": {"demo_search_tool": {"required_context": []}},
            "canonicalize_tool_id": lambda tool_id: tool_id,
            "document_tool_ids": {"search_document"},
            "package_required_tool_ids": {"demo_search_tool"},
        },
    )

    report = module.build_agent_runtime_report(strict_mode=True)
    assert report["status"] == "unhealthy"
    assert any("Likely missing critical tools from template 'demo_agent'" in msg for msg in report["errors"])


def test_build_agent_runtime_report_rejects_extractor_output_schema_without_finalize(monkeypatch):
    import src.lib.agent_studio.runtime_validation as module

    monkeypatch.setattr(module, "_fetch_active_agents", lambda: [
        _agent(
            agent_key="doug_shape",
            visibility="system",
            user_id=None,
            category="Extraction",
            output_schema_key="GeneExpressionEnvelope",
            tool_ids=["stage_gene_expression_observation"],
        )
    ])
    monkeypatch.setattr(module, "_load_expected_system_agent_keys", lambda: (set(), None))
    monkeypatch.setattr(module, "_resolve_output_schema", lambda schema_key: object())
    monkeypatch.setattr(module, "load_models", lambda: None)
    monkeypatch.setattr(module, "list_models", lambda: [SimpleNamespace(model_id="gpt-5.4-mini")])
    monkeypatch.setattr(
        module,
        "_load_runtime_policy",
        lambda: {
            "tool_bindings": {"stage_gene_expression_observation": {"required_context": []}},
            "canonicalize_tool_id": lambda tool_id: tool_id,
            "document_tool_ids": set(),
            "package_required_tool_ids": set(),
        },
    )

    report = module.build_agent_runtime_report(strict_mode=False)

    assert report["status"] == "unhealthy"
    assert any(
        "doug_shape: builder/extractor agent declares output_schema 'GeneExpressionEnvelope'"
        in msg
        for msg in report["errors"]
    )
    assert any(
        "doug_shape: extractor agent is missing a builder finalize tool" in msg
        for msg in report["errors"]
    )


def test_build_agent_runtime_report_warns_for_custom_extractor_finalize_drift_non_strict(monkeypatch):
    import src.lib.agent_studio.runtime_validation as module

    monkeypatch.setattr(module, "_fetch_active_agents", lambda: [
        _agent(
            agent_key="ca_custom_shape",
            visibility="private",
            user_id=7,
            category="Extraction",
            output_schema_key="GeneExpressionEnvelope",
            tool_ids=["stage_gene_expression_observation"],
        )
    ])
    monkeypatch.setattr(module, "_load_expected_system_agent_keys", lambda: (set(), None))
    monkeypatch.setattr(module, "_resolve_output_schema", lambda schema_key: object())
    monkeypatch.setattr(module, "load_models", lambda: None)
    monkeypatch.setattr(module, "list_models", lambda: [SimpleNamespace(model_id="gpt-5.4-mini")])
    monkeypatch.setattr(
        module,
        "_load_runtime_policy",
        lambda: {
            "tool_bindings": {"stage_gene_expression_observation": {"required_context": []}},
            "canonicalize_tool_id": lambda tool_id: tool_id,
            "document_tool_ids": set(),
            "package_required_tool_ids": set(),
        },
    )

    report = module.build_agent_runtime_report(strict_mode=False)

    assert report["status"] == "degraded"
    assert report["errors"] == []
    assert any(
        "ca_custom_shape: builder/extractor agent declares output_schema "
        "'GeneExpressionEnvelope'" in msg
        for msg in report["warnings"]
    )
    assert any(
        "ca_custom_shape: extractor agent is missing a builder finalize tool" in msg
        for msg in report["warnings"]
    )


def test_build_agent_runtime_report_rejects_custom_extractor_finalize_drift_strict(monkeypatch):
    import src.lib.agent_studio.runtime_validation as module

    monkeypatch.setattr(module, "_fetch_active_agents", lambda: [
        _agent(
            agent_key="ca_custom_shape",
            visibility="private",
            user_id=7,
            category="Extraction",
            output_schema_key="GeneExpressionEnvelope",
            tool_ids=["stage_gene_expression_observation"],
        )
    ])
    monkeypatch.setattr(module, "_load_expected_system_agent_keys", lambda: (set(), None))
    monkeypatch.setattr(module, "_resolve_output_schema", lambda schema_key: object())
    monkeypatch.setattr(module, "load_models", lambda: None)
    monkeypatch.setattr(module, "list_models", lambda: [SimpleNamespace(model_id="gpt-5.4-mini")])
    monkeypatch.setattr(
        module,
        "_load_runtime_policy",
        lambda: {
            "tool_bindings": {"stage_gene_expression_observation": {"required_context": []}},
            "canonicalize_tool_id": lambda tool_id: tool_id,
            "document_tool_ids": set(),
            "package_required_tool_ids": set(),
        },
    )

    report = module.build_agent_runtime_report(strict_mode=True)

    assert report["status"] == "unhealthy"
    assert any(
        "ca_custom_shape: builder/extractor agent declares output_schema "
        "'GeneExpressionEnvelope'" in msg
        for msg in report["errors"]
    )


def test_build_agent_runtime_report_rejects_output_schema_with_finalize_tool(monkeypatch):
    import src.lib.agent_studio.runtime_validation as module

    monkeypatch.setattr(module, "_fetch_active_agents", lambda: [
        _agent(
            agent_key="hybrid_validator",
            visibility="system",
            user_id=None,
            category="Validation",
            output_schema_key="GeneResultEnvelope",
            tool_ids=["search_genes", "finalize_gene_extraction"],
        )
    ])
    monkeypatch.setattr(module, "_load_expected_system_agent_keys", lambda: (set(), None))
    monkeypatch.setattr(module, "_resolve_output_schema", lambda schema_key: object())
    monkeypatch.setattr(module, "load_models", lambda: None)
    monkeypatch.setattr(module, "list_models", lambda: [SimpleNamespace(model_id="gpt-5.4-mini")])
    monkeypatch.setattr(
        module,
        "_load_runtime_policy",
        lambda: {
            "tool_bindings": {
                "search_genes": {"required_context": []},
                "finalize_gene_extraction": {"required_context": []},
            },
            "canonicalize_tool_id": lambda tool_id: tool_id,
            "document_tool_ids": set(),
            "package_required_tool_ids": set(),
        },
    )

    report = module.build_agent_runtime_report(strict_mode=False)

    assert report["status"] == "unhealthy"
    assert any(
        "hybrid_validator: declares both output_schema 'GeneResultEnvelope' "
        "and builder finalize tool(s): finalize_gene_extraction" in msg
        for msg in report["errors"]
    )


def test_validate_and_cache_agent_runtime_contracts_raises_for_finalize_invariant(monkeypatch):
    import src.lib.agent_studio.runtime_validation as module

    monkeypatch.setattr(module, "_fetch_active_agents", lambda: [
        _agent(
            agent_key="extractor_without_finalizer",
            visibility="system",
            user_id=None,
            category="Extraction",
            output_schema_key=None,
            tool_ids=["search_document", "read_section"],
        )
    ])
    monkeypatch.setattr(module, "_load_expected_system_agent_keys", lambda: (set(), None))
    monkeypatch.setattr(module, "load_models", lambda: None)
    monkeypatch.setattr(module, "list_models", lambda: [SimpleNamespace(model_id="gpt-5.4-mini")])
    monkeypatch.setattr(
        module,
        "_load_runtime_policy",
        lambda: {
            "tool_bindings": {
                "search_document": {"required_context": ["document_id"]},
                "read_section": {"required_context": ["document_id"]},
            },
            "canonicalize_tool_id": lambda tool_id: tool_id,
            "document_tool_ids": {"search_document", "read_section"},
            "package_required_tool_ids": set(),
        },
    )

    with pytest.raises(RuntimeError, match="extractor_without_finalizer"):
        module.validate_and_cache_agent_runtime_contracts(strict_mode=False)


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
                "warnings": ["demo_agent: Unknown tool_ids: missing_tool"],
                "agents": [
                    {
                        "agent_key": "demo_agent",
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


def _identity_lookup_report(monkeypatch, row, *, strict):
    import src.lib.agent_studio.runtime_validation as module
    from src.lib.packages import tool_roles

    monkeypatch.setattr(tool_roles, "builder_finalization_tool_names", lambda: frozenset({"finalize_demo"}))
    monkeypatch.setattr(tool_roles, "identity_lookup_tool_names", lambda: frozenset({"lookup_demo", "group_lookup_demo"}))
    monkeypatch.setattr(module, "_fetch_active_agents", lambda: [row])
    monkeypatch.setattr(module, "_load_expected_system_agent_keys", lambda: (set(), None))
    monkeypatch.setattr(module, "_resolve_output_schema", lambda schema_key: object())
    monkeypatch.setattr(module, "load_models", lambda: None)
    monkeypatch.setattr(module, "list_models", lambda: [SimpleNamespace(model_id="gpt-5.4-mini")])
    monkeypatch.setattr(
        module,
        "_load_runtime_policy",
        lambda: {
            "tool_bindings": {
                tool_id: {"required_context": []}
                for tool_id in ("stage_demo", "finalize_demo", "lookup_demo", "group_lookup_demo")
            },
            "canonicalize_tool_id": lambda tool_id: tool_id,
            "document_tool_ids": set(),
            "package_required_tool_ids": set(),
        },
    )
    return module.build_agent_runtime_report(strict_mode=strict)


def test_packaged_extraction_agent_with_identity_lookup_fails_the_runtime_report(monkeypatch):
    report = _identity_lookup_report(monkeypatch, _agent(
        agent_key="demo_extractor",
        visibility="system",
        user_id=None,
        category="Extraction",
        tool_ids=["stage_demo", "finalize_demo", "lookup_demo"],
        group_tool_policy={"rules": [{"tool_id": "group_lookup_demo", "allowed_group_ids": ["TEAM_C"]}]},
    ), strict=False)

    assert report["status"] == "unhealthy"
    [message] = [msg for msg in report["errors"] if "database lookup tools" in msg]
    # Group-scoped lookups count as well as base tools.
    assert "group_lookup_demo, lookup_demo" in message


def test_validation_agent_keeps_its_identity_lookup_tools(monkeypatch):
    report = _identity_lookup_report(monkeypatch, _agent(
        agent_key="demo_validator",
        visibility="system",
        user_id=None,
        category="Validation",
        output_schema_key="DemoValidationResult",
        tool_ids=["lookup_demo"],
    ), strict=True)

    assert not [msg for msg in report["errors"] + report["warnings"] if "database lookup tools" in msg]


def test_custom_extraction_agent_with_identity_lookup_warns_unless_strict(monkeypatch):
    row = _agent(
        agent_key="ca_demo_extractor",
        category="Custom",
        tool_ids=["stage_demo", "finalize_demo", "lookup_demo"],
    )

    relaxed = _identity_lookup_report(monkeypatch, row, strict=False)
    strict = _identity_lookup_report(monkeypatch, row, strict=True)

    assert any("database lookup tools" in msg for msg in relaxed["warnings"])
    assert any("database lookup tools" in msg for msg in strict["errors"])


def test_schema_extraction_agent_without_a_finalizer_is_classified_by_its_head_revision(monkeypatch):
    import src.lib.agent_studio.runtime_validation as module
    from src.lib.packages import tool_roles

    monkeypatch.setattr(tool_roles, "is_validator_output_schema", lambda key: key == "DemoValidationResult")
    extractor = _agent(
        agent_key="ca_schema_extractor", category="Custom", execution_revision_id="rev-extractor",
        output_schema_key="DemoEnvelope", tool_ids=["stage_demo", "lookup_demo"],
    )
    validator = _agent(
        agent_key="ca_schema_validator", category="Custom", execution_revision_id="rev-validator",
        output_schema_key="DemoValidationResult", tool_ids=["lookup_demo"],
    )
    monkeypatch.setattr(module, "_fetch_head_output_contracts", lambda _agents: {
        "rev-extractor": ("structured_extraction", "DemoEnvelope"),
        "rev-validator": ("structured_extraction", "DemoValidationResult"),
    })

    report = _identity_lookup_report(monkeypatch, extractor, strict=True)
    assert any("ca_schema_extractor" in msg and "database lookup tools" in msg for msg in report["errors"])

    report = _identity_lookup_report(monkeypatch, validator, strict=True)
    assert not [msg for msg in report["errors"] + report["warnings"] if "database lookup tools" in msg]


def _report_for_reasoning(monkeypatch, reasoning):
    import src.lib.agent_studio.runtime_validation as module

    monkeypatch.setattr(module, "_fetch_active_agents", lambda: [
        _agent(agent_key="ca_reasoning", model_id="gpt-6-sol", model_reasoning=reasoning)
    ])
    monkeypatch.setattr(module, "_load_expected_system_agent_keys", lambda: (set(), None))
    monkeypatch.setattr(module, "load_models", lambda: None)
    monkeypatch.setattr(module, "list_models", lambda: [SimpleNamespace(
        model_id="gpt-6-sol", supports_reasoning=True,
        reasoning_options=["low", "medium", "high", "xhigh"],
    )])
    monkeypatch.setattr(module, "_load_runtime_policy", lambda: {
        "tool_bindings": {},
        "canonicalize_tool_id": lambda tool_id: tool_id,
        "document_tool_ids": set(),
        "package_required_tool_ids": set(),
    })
    return module.build_agent_runtime_report(strict_mode=False)


def test_startup_rejects_a_reasoning_level_the_catalog_model_does_not_offer(monkeypatch):
    report = _report_for_reasoning(monkeypatch, "minimal")

    assert report["status"] == "unhealthy"
    assert report["errors"] == [
        "ca_reasoning: model_reasoning 'minimal' is not supported by model 'gpt-6-sol'"
    ]


@pytest.mark.parametrize("reasoning", ["medium", "XHIGH", None])
def test_startup_accepts_a_reasoning_level_the_catalog_model_offers(monkeypatch, reasoning):
    report = _report_for_reasoning(monkeypatch, reasoning)

    assert report["errors"] == []


def test_startup_only_warns_for_a_reasoning_value_that_is_never_sent(monkeypatch):
    report = _report_for_reasoning(monkeypatch, "disabled")

    assert report["errors"] == []
    assert any("Invalid model_reasoning 'disabled'" in msg for msg in report["warnings"])

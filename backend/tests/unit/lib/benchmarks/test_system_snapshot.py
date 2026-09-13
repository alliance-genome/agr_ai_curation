from types import SimpleNamespace
from unittest.mock import Mock
from contextlib import nullcontext

import pytest

from src.lib.benchmarks.system_snapshot import (
    FrozenSystemAgent, capture_system_agent, system_runtime_prompt,
)
from src.lib.prompts import assembly


def bundle(agent="extractor", kind="base_prompt", content="Original instructions"):
    return assembly._bundle(agent, [assembly._make_layer(
        layer_id=f"{agent}:base", kind=kind, title="Source", content=content,
        provenance="fixture", editable=False, locked=True, source_ref="fixture",
    )])


def row():
    return SimpleNamespace(
        visibility="system", agent_key="extractor", model_id="model-a",
        model_temperature=0.2, model_reasoning="low", tool_ids=["tool-a"],
        group_tool_policy={"rules": []}, output_schema_key=None, name="Extractor",
        group_rules_enabled=True, credentials="MUST NOT BE CAPTURED",
    )


@pytest.fixture(autouse=True)
def package_metadata(monkeypatch):
    from src.lib.agent_studio import catalog_service
    monkeypatch.setattr("src.lib.config.agent_loader.get_agent_definition", lambda _: SimpleNamespace())
    monkeypatch.setattr(catalog_service, "_launchable_curation_metadata_from_definition", lambda _: None)
    monkeypatch.setattr(catalog_service, "_inherited_curation_definition_for_db_agent", lambda _: None)
    monkeypatch.setattr(catalog_service, "_curation_metadata_from_definition", lambda _: None)


def test_capture_freezes_named_settings_and_renders_without_live_prompt_reads(monkeypatch):
    source = row()
    assemble = Mock(return_value=bundle())
    monkeypatch.setattr(assembly, "build_agent_prompt_layers", assemble)
    snapshot = capture_system_agent(source, active_groups=("TEAM_A",))
    assemble.assert_called_once_with("extractor", group_id=["TEAM_A"])
    source.tool_ids.append("later-tool")
    source.model_temperature = 0.9
    assert snapshot.tool_ids == ("tool-a",)
    assert snapshot.model_temperature == 0.2
    assert "MUST NOT BE CAPTURED" not in snapshot.model_dump_json()
    assert FrozenSystemAgent.model_validate_json(snapshot.model_dump_json()) == snapshot
    with pytest.raises(TypeError):
        snapshot.prompt_layer_manifest["layers"][0]["content"] = "Changed"
    assemble.side_effect = AssertionError("No current prompt reads during frozen rendering")
    rendered = system_runtime_prompt(snapshot, "Paper-specific context")
    assert "Original instructions" in rendered.render()
    assert "Paper-specific context" in rendered.render()
    assert "Paper-specific context" not in snapshot.model_dump_json()


@pytest.mark.parametrize("invalid", ["custom", "wrong_prompt", "runtime_prompt"])
def test_capture_rejects_non_system_or_per_run_source(monkeypatch, invalid):
    source = row()
    selected = bundle()
    if invalid == "custom":
        source.visibility = "private"
    elif invalid == "wrong_prompt":
        selected = bundle(agent="other")
    else:
        selected = bundle(kind="runtime_context")
    assemble = Mock(return_value=selected)
    monkeypatch.setattr(assembly, "build_agent_prompt_layers", assemble)
    with pytest.raises(ValueError):
        capture_system_agent(source, active_groups=())
    if invalid == "custom":
        assemble.assert_not_called()


def test_system_source_is_frozen_in_cell_and_changes_plan_identity(monkeypatch):
    from src.lib.benchmarks.models import BenchmarkSuite, ResolvedBenchmarkPlan
    from src.lib.benchmarks.suites import resolve_suite
    from tests.unit.lib.benchmarks.test_suites import _catalog

    monkeypatch.setattr(assembly, "build_agent_prompt_layers", lambda *a, **kw: bundle())
    first = capture_system_agent(row(), active_groups=())
    changed_row = row()
    changed_row.model_temperature = 0.8
    second = capture_system_agent(changed_row, active_groups=())
    catalog = _catalog()
    target = next(item for item in catalog.targets if item.target.kind == "agent")
    suite = BenchmarkSuite.model_validate({
        "schema_version": 2, "suite_id": "system-source", "cases": [{
            "case_id": "paper", "target": target.target.model_dump(mode="json"),
            "input": {"resolver": "fixture", "reference": "paper", "version": "1",
                      "digest": "sha256:" + "b" * 64},
        }], "configurations": [{"configuration_id": "baseline"}],
    })

    def plan_for(source):
        selected = target.model_copy(update={"system_agent_snapshots": {"extractor": source}})
        current = catalog.model_copy(update={"targets": tuple(
            selected if item is target else item for item in catalog.targets
        )})
        return resolve_suite(suite, current, max_cases=1, max_configurations=1, max_repetitions=1, max_cells=1)

    plan = plan_for(first)
    modified = plan_for(second)
    assert plan.cells[0].system_agent_snapshots["extractor"] == first
    assert plan.cells[0].cell_id != modified.cells[0].cell_id
    assert plan.plan_digest != modified.plan_digest
    assert ResolvedBenchmarkPlan.model_validate_json(plan.model_dump_json()) == plan
    with pytest.raises(TypeError):
        plan.cells[0].system_agent_snapshots["extractor"] = second
    assert "system_agent_snapshots" not in target.model_dump(mode="json")


def test_normal_constructor_uses_saved_system_prompt_settings_and_metadata(monkeypatch):
    from src.lib.agent_studio import catalog_service as catalog
    from src.lib.benchmarks.source_revisions import benchmark_source_revisions
    from src.lib.openai_agents import config

    source = row()
    source.tool_ids = []
    monkeypatch.setattr(assembly, "build_agent_prompt_layers", lambda *a, **kw: bundle())
    saved = capture_system_agent(source, active_groups=()).model_copy(update={
        "curation_metadata": {"adapter_key": "saved-adapter"},
    })
    source.model_id = "later-model"
    source.model_temperature = 0.9
    authorized = Mock(return_value=source)
    monkeypatch.setattr(catalog, "_get_db_agent_row", authorized)
    monkeypatch.setattr(catalog, "require_canonical_agent_identity", lambda value, **kw: value)
    monkeypatch.setattr(catalog, "_build_runtime_context", lambda **kw: "Current paper")
    forbidden = Mock(side_effect=AssertionError("must not load live prompt or curation metadata"))
    monkeypatch.setattr(catalog, "_build_runtime_instructions", forbidden)
    monkeypatch.setattr(catalog, "_attach_live_curation_metadata", forbidden)
    monkeypatch.setattr(catalog, "prompt_templates_for_bundle", forbidden)
    factory = Mock(return_value=SimpleNamespace(name="Extractor"))
    monkeypatch.setattr(catalog, "Agent", factory)
    monkeypatch.setattr(catalog, "set_pending_prompts", Mock())
    monkeypatch.setattr(catalog, "bind_prompt_run", Mock())
    model = Mock(return_value="no-provider-client")
    settings = Mock(return_value={})
    monkeypatch.setattr(config, "get_model_for_agent", model)
    monkeypatch.setattr(config, "build_model_settings", settings)
    monkeypatch.setattr(config, "resolve_model_provider", lambda *a, **kw: "openai")
    monkeypatch.setattr("src.lib.openai_agents.langfuse_client.log_agent_config", Mock())
    with benchmark_source_revisions({}, {"extractor": saved}):
        built = catalog.get_agent_by_id("extractor", db_user_id=7, authenticated_groups=[])
    authorized.assert_called_once()
    assert settings.call_args.kwargs["temperature"] == 0.2
    assert model.call_args.args == ("model-a",)
    assert "Original instructions" in factory.call_args.kwargs["instructions"]
    assert "Current paper" in factory.call_args.kwargs["instructions"]
    assert built.curation_metadata == {"adapter_key": "saved-adapter"}
    forbidden.assert_not_called()
    with benchmark_source_revisions({}, {"extractor": saved}), pytest.raises(ValueError, match="cannot be overridden"):
        catalog.get_agent_by_id("extractor", db_user_id=7, model_temperature_override=0.7)
    drifted = saved.model_copy(update={"output_schema_definition": {"type": "object"}})
    with benchmark_source_revisions({}, {"extractor": drifted}), pytest.raises(ValueError, match="schema changed"):
        catalog.get_agent_by_id("extractor", db_user_id=7)
    with benchmark_source_revisions({}, {}), pytest.raises(ValueError, match="absent"):
        catalog.get_agent_by_id("extractor", db_user_id=7)
    factory.assert_called_once()


@pytest.mark.parametrize("revocation", ["group", "policy"])
def test_frozen_system_tools_do_not_bypass_current_permissions(monkeypatch, revocation):
    from src.lib.benchmarks.system_snapshot import authorized_system_snapshot_row
    monkeypatch.setattr(assembly, "build_agent_prompt_layers", lambda *a, **kw: bundle())
    current = row()
    saved = capture_system_agent(current, active_groups=())
    session = Mock()
    session.execute.return_value.scalars.return_value.all.return_value = [
        SimpleNamespace(tool_key="tool-a", allow_execute=False),
    ]
    monkeypatch.setattr("src.models.sql.database.SessionLocal", lambda: nullcontext(session))
    if revocation == "group":
        current.tool_ids = []
    with pytest.raises(ValueError, match="no longer"):
        authorized_system_snapshot_row(current, saved, active_groups=[])
    if revocation == "group":
        session.execute.assert_not_called()

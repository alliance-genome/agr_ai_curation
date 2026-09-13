from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from src.lib.benchmarks import dependencies as deps
from src.lib.benchmarks import runtime
from tests.unit.lib.benchmarks.test_runtime import _resolved_cell


@pytest.fixture
def configured(monkeypatch):
    from src.lib.benchmarks.system_snapshot import FrozenSystemAgent
    from tests.unit.lib.benchmarks.test_system_snapshot import bundle
    settings = {"benchmark_environment_id": "synthetic", "max_turns": 12}
    monkeypatch.setattr(deps, "execution_settings", lambda: dict(settings))
    registry = Mock(return_value="sha256:" + "a" * 64)
    pack = Mock(return_value="sha256:" + "b" * 64)
    monkeypatch.setattr(deps, "runtime_catalog_digest", registry)
    monkeypatch.setattr(deps, "domain_pack_digest", pack)
    system = FrozenSystemAgent(
        agent_key="extractor", model_id="model-a", model_temperature=0.2, model_reasoning=None,
        tool_ids=(), group_tool_policy={}, output_schema_key=None,
        prompt_layer_manifest=bundle().to_manifest(), curation_metadata={"domain_pack_id": "fixture"},
    )
    target = NS(system_agent_snapshots={"extractor": system},
                source_execution_receipts={})
    snapshot = deps.capture_dependencies(Mock(), NS(db_user_id=7, active_groups=()), target)
    return NS(settings=settings, registry=registry, pack=pack, snapshot=snapshot)


def test_dependency_snapshot_is_detached_and_honest_about_uncontrolled_inputs(configured):
    snapshot = configured.snapshot
    assert deps.BenchmarkDependencies.model_validate_json(snapshot.model_dump_json()) == snapshot
    assert snapshot.planned_cell_order == "case_configuration_repetition"
    assert snapshot.randomized is False
    assert snapshot.external_data == "live_not_snapshotted"
    assert snapshot.provider_revision == "not_pinned"
    assert snapshot.tool_implementation == "deployment_owned_not_snapshotted"
    deps.require_current_dependencies(snapshot)
    configured.settings["max_turns"] = 99
    assert snapshot.execution_settings["max_turns"] == 12
    with pytest.raises(TypeError):
        snapshot.execution_settings["max_turns"] = 99


@pytest.mark.parametrize("change", ["settings", "registry", "domain"])
@pytest.mark.parametrize("kind", ["agent", "flow"])
@pytest.mark.asyncio
async def test_dependency_drift_stops_before_runtime_or_provider_construction(configured, monkeypatch, change, kind):
    if change == "settings":
        configured.settings["max_turns"] = 13
    elif change == "registry":
        configured.registry.return_value = "sha256:" + "c" * 64
    else:
        configured.pack.return_value = "sha256:" + "d" * 64
    construct = Mock(side_effect=AssertionError("No provider construction after dependency drift"))
    flow = Mock(side_effect=AssertionError("No flow after dependency drift"))
    monkeypatch.setattr(runtime, "get_agent_by_id", construct)
    monkeypatch.setattr(runtime, "execute_flow", flow)
    cell = _resolved_cell(kind, "extractor", {}).model_copy(update={"dependencies": configured.snapshot})
    execute = runtime.execute_resolved_agent_cell if kind == "agent" else runtime.execute_resolved_flow_cell
    with pytest.raises(ValueError, match="dependencies changed"):
        await execute(cell, {}, "run")
    construct.assert_not_called()
    flow.assert_not_called()


def test_registry_identity_uses_named_metadata_without_constructing_tools(monkeypatch):
    from src.lib.agent_studio import catalog_service

    metadata = {"version": "1", "exports": ["fixture"]}
    package = NS(package_id="fixture", manifest=NS(model_dump=lambda **kw: dict(metadata)))
    binding = NS(tool_id="lookup", binding_kind=NS(value="function"), import_path="fixture:lookup",
                 import_attribute_kind="callable", required_context=(), metadata={}, provider_adapters={},
                 source=NS(package_id="fixture", package_version="1"))
    registry = NS(package_registry=NS(runtime_version="1", loaded_packages=[package]), bindings=[binding])
    monkeypatch.setattr(catalog_service, "_load_package_tool_registry", lambda: registry)
    construct = Mock(side_effect=AssertionError("Do not instantiate tools to fingerprint metadata"))
    monkeypatch.setattr(catalog_service, "_instantiate_package_tool", construct)
    original = deps.runtime_catalog_digest()
    metadata["version"] = "2"
    assert deps.runtime_catalog_digest() != original
    metadata["version"] = "1"
    binding.import_path = "fixture:replacement"
    assert deps.runtime_catalog_digest() != original
    construct.assert_not_called()


def test_only_explicit_nonsecret_settings_are_recorded(monkeypatch):
    monkeypatch.setenv("BENCHMARK_ENVIRONMENT_ID", "test")
    monkeypatch.setenv("OPENAI_API_KEY", "SECRET_MUST_NOT_APPEAR")
    monkeypatch.setenv("AGENT_MAX_TURNS", "17")
    settings = deps.execution_settings()
    assert "SECRET_MUST_NOT_APPEAR" not in str(settings)
    assert settings["max_turns"] == 17
    assert settings["benchmark_environment_id"] == "test"


def test_historical_plans_do_not_get_invented_dependencies(monkeypatch):
    monkeypatch.setattr(deps, "runtime_catalog_digest", Mock(side_effect=AssertionError("no backfill")))
    deps.require_current_dependencies(None)
    assert "dependencies" not in _resolved_cell("agent", "extractor", {}).model_dump(mode="json")


def test_dependency_changes_change_frozen_cell_and_plan_identity(configured):
    from src.lib.benchmarks.models import ResolvedBenchmarkPlan
    from src.lib.benchmarks.suites import resolve_suite, validate_suite
    from tests.unit.lib.benchmarks.test_suites import _catalog, _payload

    catalog = _catalog()
    suite = validate_suite(_payload())
    def resolve(snapshot):
        targets = tuple(target.model_copy(update={"dependencies": snapshot}) for target in catalog.targets)
        return resolve_suite(suite, catalog.model_copy(update={"targets": targets}),
                             max_cases=100, max_configurations=100, max_repetitions=100, max_cells=10000)
    first = resolve(configured.snapshot)
    changed = configured.snapshot.model_copy(update={"runtime_catalog_digest": "sha256:" + "e" * 64})
    second = resolve(changed)
    assert first.suite_digest == second.suite_digest
    assert first.catalog_digest != second.catalog_digest
    assert first.cells[0].cell_id != second.cells[0].cell_id
    assert first.plan_digest != second.plan_digest
    assert first.cells[0].dependencies == configured.snapshot
    assert ResolvedBenchmarkPlan.model_validate_json(first.model_dump_json()) == first


def test_custom_source_domain_is_resolved_from_pinned_revision(configured, monkeypatch):
    from tests.unit.lib.benchmarks.test_source_revisions import source_receipt

    receipt = source_receipt()
    read = Mock(return_value=(NS(), NS(curation={"domain_pack_id": "custom-domain"}, credentials="not recorded")))
    monkeypatch.setattr("src.lib.agent_studio.execution_revision_service.get_execution_revision", read)
    curator = NS(db_user_id=7, active_groups=("group",))
    target = NS(system_agent_snapshots={}, source_execution_receipts={"agent:" + receipt.agent_key: receipt})
    session = Mock()
    snapshot = deps.capture_dependencies(session, curator, target)
    read.assert_called_once_with(session, receipt.agent_id, receipt.agent_revision_id, 7, active_group_ids=["group"])
    assert set(snapshot.domain_pack_digests) == {"custom-domain"}
    assert "not recorded" not in snapshot.model_dump_json()

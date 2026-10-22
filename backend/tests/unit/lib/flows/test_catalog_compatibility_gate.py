"""Fixed historical selections must survive catalog evolution via explicit repair."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from src.lib.flows.persisted_flow_migrations import (
    migrate_persisted_flow_definition,
    validate_persisted_flow_definition,
    PersistedFlowMigrationError,
)
from src.lib.flows.validation_attachments import validation_attachment_catalog_by_agent
from src.lib.packages.persisted_flow_migration_loader import load_persisted_flow_migration_catalog

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "flows"
IDENTITY_FIELDS = (
    "attachment_id", "domain_pack_id", "validator_id", "validator_binding_id",
    "validator_package_id", "validator_agent_id", "tool_name", "scope",
    "object_type", "object_role", "field_path", "field_type",
)


def assert_catalog_upgrade(previous, current, migrations, tested_removals=frozenset()):
    """Test-only gate: declarations count only if the real repair removes the selection."""
    for agent_id, old_options in previous.items():
        assert agent_id in current, f"Saved-flow agent {agent_id!r} disappeared; add an explicit upgrade and lifecycle fixture"
        current_options = {o["attachment_id"]: o for o in current[agent_id]}
        for old in old_options:
            attachment_id = old["attachment_id"]
            new = current_options.get(attachment_id)
            if new is not None and all(old.get(k) == new.get(k) for k in IDENTITY_FIELDS):
                continue
            definition = {"nodes": [{"id": "extract", "type": "agent", "data": {
                "agent_id": agent_id, "validation_attachments": [deepcopy(old)],
            }}], "edges": []}
            repaired = migrate_persisted_flow_definition(definition, migrations=migrations)
            remaining = repaired.definition["nodes"][0]["data"]["validation_attachments"]
            assert (repaired.changed and not remaining and new is None
                    and (agent_id, attachment_id) in tested_removals), (
                f"Saved-flow catalog changed: {agent_id}/{attachment_id}; "
                "removed, renamed, or rebound without a tested explicit upgrade. "
                "Keep the historical baseline; add the migration and lifecycle fixture."
            )



@pytest.fixture
def tested_removals(monkeypatch):
    """Require a fixed lifecycle case AND a real Alembic upgrade dispatch.

    The adapter isolates PostgreSQL I/O, not migration selection or repair logic.
    This gate checks upgrade wiring; it does not exercise PostgreSQL SQL.
    """
    import importlib.util
    before = json.loads((FIXTURES / "retired-allele-v094.json").read_text())
    expected = json.loads((FIXTURES / "repaired-allele-v094.json").read_text())
    path = Path(__file__).resolve().parents[4] / "alembic" / "versions" / "i6j7k8l9m0n1_reapply_retired_flow_attachment_repair.py"
    spec = importlib.util.spec_from_file_location("catalog_gate_upgrade", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    bind = object()
    results = []

    def isolated_database_runner(connection, migration):
        assert connection is bind
        result = migrate_persisted_flow_definition(before, migrations=(migration,))
        assert result.definition == expected
        assert not migrate_persisted_flow_definition(expected, migrations=(migration,)).changed
        results.append(result)

    monkeypatch.setattr(module.op, "get_bind", lambda: bind)
    monkeypatch.setattr(module, "apply_persisted_flow_migration", isolated_database_runner)
    module.upgrade()
    assert len(results) == 1, "Historical repair is no longer wired into its Alembic upgrade"
    remaining = {a["attachment_id"] for n in expected["nodes"]
                 for a in n["data"].get("validation_attachments", [])}
    return frozenset((n["data"]["agent_id"], a["attachment_id"])
                     for n in before["nodes"]
                     for a in n["data"].get("validation_attachments", [])
                     if a["attachment_id"] not in remaining)


def test_shipped_catalog_preserves_fixed_baseline(tested_removals):
    previous = json.loads((FIXTURES / "catalog-545c47b14.json").read_text())["catalog"]
    assert_catalog_upgrade(previous, validation_attachment_catalog_by_agent(),
                           load_persisted_flow_migration_catalog().migrations, tested_removals)


@pytest.mark.parametrize("change", ["remove", "rename", "rebind", "agent"])
def test_unhandled_catalog_changes_fail_gate(change):
    old = {"example_extractor": [{"attachment_id": "example:check", "validator_binding_id": "check"}]}
    current = deepcopy(old)
    if change == "remove":
        current["example_extractor"] = []
    elif change == "rename":
        current["example_extractor"][0]["attachment_id"] = "example:renamed"
    elif change == "rebind":
        current["example_extractor"][0]["validator_binding_id"] = "different"
    else:
        current = {}
    with pytest.raises(AssertionError, match="Saved-flow"):
        assert_catalog_upgrade(old, current, ())


def test_fixed_historical_selections_require_real_repair(tested_removals):
    definition = json.loads((FIXTURES / "retired-allele-v094.json").read_text())
    original = deepcopy(definition)
    migrations = load_persisted_flow_migration_catalog().migrations
    old = {"allele_extractor": definition["nodes"][1]["data"]["validation_attachments"]}
    current = validation_attachment_catalog_by_agent()
    with pytest.raises(AssertionError, match="without a tested explicit upgrade"):
        assert_catalog_upgrade(old, current, ())
    with pytest.raises(PersistedFlowMigrationError):
        validate_persisted_flow_definition(definition)
    with pytest.raises(AssertionError, match="without a tested explicit upgrade"):
        assert_catalog_upgrade(old, current, migrations)
    assert_catalog_upgrade(old, current, migrations, tested_removals)
    result = migrate_persisted_flow_definition(definition, migrations=migrations)
    expected = json.loads((FIXTURES / "repaired-allele-v094.json").read_text())
    assert result.definition == expected
    assert definition == original
    assert not migrate_persisted_flow_definition(result.definition, migrations=migrations).changed
    validate_persisted_flow_definition(result.definition)


@pytest.mark.asyncio
async def test_repaired_historical_flow_lists_opens_validates_and_reaches_execution(monkeypatch, tested_removals):
    """Use real API/validation code; stub storage/auth and the model construction boundary."""
    import importlib
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock
    from uuid import uuid4
    from src.schemas.flows import FlowDefinition

    api = importlib.import_module("src.api.flows")
    executor = importlib.import_module("src.lib.flows.executor")
    old = json.loads((FIXTURES / "retired-allele-v094.json").read_text())
    repaired = migrate_persisted_flow_definition(old).definition
    now = datetime.now(timezone.utc)
    flow = SimpleNamespace(
        id=uuid4(), user_id=7, visibility="private", project_id=None, shared_at=None,
        name="Historical allele flow", description="Synthetic regression fixture",
        flow_definition=repaired, execution_count=0, last_executed_at=None,
        created_at=now, updated_at=now,
    )
    db = Mock()
    db.scalar.return_value = 1
    db.scalars.return_value.all.return_value = [flow]
    monkeypatch.setattr(api, "provision_user_async", AsyncMock(return_value=SimpleNamespace(id=7)))
    monkeypatch.setattr(api, "get_visible_flow", lambda *_args: flow)
    from src.lib.agent_studio.catalog_service import AGENT_REGISTRY
    monkeypatch.setattr(api, "get_active_visible_agent_metadata",
                        lambda agent_id, **_kwargs: AGENT_REGISTRY[agent_id])
    user = {"sub": "catalog-fixture", "email": "fixture@example.org"}
    listed = await api.list_flows(page=1, page_size=20, user=user, db=db)
    assert [item.id for item in listed.flows] == [flow.id]
    opened = await api.get_flow(flow.id, user=user, db=db)
    assert not opened.has_critical_issues
    validated = api._validated_flow_definition_payload(
        opened.flow_definition, enforce_agent_references=True, enforce_agent_step_policy=True,
    )
    FlowDefinition.model_validate(validated)
    prepared_flow = SimpleNamespace(id=flow.id, name=flow.name, flow_definition=validated)
    monkeypatch.setattr(executor, "_resolve_flow_agent_entry",
                        lambda agent_id, **_kwargs: AGENT_REGISTRY[agent_id])
    monkeypatch.setattr(executor, "get_agent_by_id", lambda *_args, **_kwargs: object())

    async def unused_model_call(*_args, **_kwargs):
        pytest.fail("Catalog gate must not invoke a model")

    monkeypatch.setattr(executor, "_create_streaming_tool", lambda **_kwargs: unused_model_call)
    context = SimpleNamespace(to_agent_kwargs=lambda: {}, section_count=lambda: 1)
    tools, names, unavailable, execution = executor.get_all_agent_tools(
        flow=prepared_flow, document_id="synthetic-document", user_id="catalog-fixture",
        doc_context=context, include_unavailable=True,
    )
    assert unavailable == []
    assert names == {"ask_allele_extractor_specialist"}
    assert len(tools) == 1
    assert execution["ordered_tool_names"] == ["ask_allele_extractor_specialist"]
    from src.lib.flows.validation_attachments import validation_schedule_from_node_data
    schedule = validation_schedule_from_node_data(validated["nodes"][1]["data"])
    scheduled_ids = {item["attachment_id"] for items in schedule.values() for item in items}
    kept_id = repaired["nodes"][1]["data"]["validation_attachments"][0]["attachment_id"]
    assert kept_id in scheduled_ids
    assert not scheduled_ids.intersection(attachment for _, attachment in tested_removals)
    before = deepcopy(flow.flow_definition)
    reached = []

    class ModelConstructionBoundary(Exception):
        pass

    def stop_before_models(**kwargs):
        reached.append(kwargs["flow"].flow_definition)
        raise ModelConstructionBoundary

    monkeypatch.setattr(executor, "clear_pending_configs", lambda: None)
    monkeypatch.setattr(executor, "create_flow_supervisor", stop_before_models)
    stream = executor.execute_flow(flow, user_id="catalog-fixture", session_id="catalog-fixture")
    with pytest.raises(ModelConstructionBoundary):
        await anext(stream)
    await stream.aclose()
    assert reached == [before]
    assert flow.flow_definition == before

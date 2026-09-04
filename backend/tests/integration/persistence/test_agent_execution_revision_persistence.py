"""PostgreSQL identity and output-state constraints for executable revisions."""

import importlib.util
from pathlib import Path
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import DBAPIError, IntegrityError

from src.lib.agent_studio import generic_profile_service
from src.models.sql.agent import Agent
from .test_generic_profile_persistence import profile_db  # noqa: F401


@pytest.fixture
def execution_db(profile_db, request):  # noqa: F811 - pytest injects the imported shared fixture
    db, _ = profile_db
    # Build the pre-migration unified-agent columns, without its new head field.
    metadata = sa.MetaData()
    sa.Table("users", metadata, autoload_with=db.connection())
    sa.Table("projects", metadata, autoload_with=db.connection())
    previous_agents = sa.Table(
        "agents",
        metadata,
        *[
            column._copy()
            for column in Agent.__table__.columns
            if column.name != "execution_revision_id"
        ],
    )
    previous_agents.create(db.connection())
    for values in getattr(request, "param", []):
        db.execute(previous_agents.insert().values(**values))
    migration_path = (
        Path(__file__).resolve().parents[3]
        / "alembic/versions/g4b5c6d7e8f9_add_agent_execution_revisions.py"
    )
    spec = importlib.util.spec_from_file_location("execution_migration", migration_path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with Operations.context(MigrationContext.configure(db.connection())):
        migration.upgrade()
    agent_id, other_id = uuid4(), uuid4()
    db.add_all(
        [
            Agent(
                id=value,
                agent_key="ca_" + value.hex,
                user_id=1,
                name="Test agent",
                instructions="Original instructions",
                model_id="test-model",
                model_temperature=0.0,
                visibility="private",
            )
            for value in (agent_id, other_id)
        ]
    )
    db.flush()
    _, profile_revision = generic_profile_service.create_profile(
        db,
        1,
        {
            "name": "Test",
            "semantic_class": "example",
            "fields": [],
        },
    )
    return db, agent_id, other_id, profile_revision


@pytest.mark.parametrize(
    "execution_db",
    [[
        {
            "id": uuid4(),
            "agent_key": "ca_baseline",
            "user_id": 1,
            "name": "Current scratch head",
            "instructions": "Actual current instructions",
            "model_id": "actual-model",
            "model_temperature": 0.0,
            "visibility": "private",
            "is_active": False,
            "tool_ids": [],
            "group_rules_enabled": False,
            "allowed_group_ids": ["FB"],
            "inherited_allowed_group_ids": ["FB"],
        },
        {
            "id": uuid4(),
            "agent_key": "system_agent",
            "name": "System agent is not a custom revision",
            "instructions": "System instructions",
            "model_id": "system-model",
            "visibility": "system",
        },
    ]],
    indirect=True,
)
def test_migration_baselines_actual_current_head_only(execution_db):
    from src.lib.agent_studio.execution_revision_service import (
        baseline_current_execution_heads,
        get_execution_revision,
    )

    db, _, _, _ = execution_db
    current = db.execute(sa.select(Agent).where(Agent.agent_key == "ca_baseline")).scalar_one()
    assert current.execution_revision_id is not None
    row, saved = get_execution_revision(
        db, current.id, current.execution_revision_id, 1, active_group_ids=["FB"]
    )
    assert row.revision == 1
    assert saved.instructions == "Actual current instructions"
    assert saved.model_id == "actual-model"
    assert saved.model_temperature == 0.0
    assert saved.allowed_group_ids == saved.inherited_allowed_group_ids == ["FB"]
    assert saved.output_contract.output_state == "none"
    assert saved.output_contract.output_mode is None
    system = db.execute(sa.select(Agent).where(Agent.agent_key == "system_agent")).scalar_one()
    assert system.execution_revision_id is None
    original = row.id
    # The fixture adds two new scratch heads after migration. Only those get
    # baselines now; the original immutable bytes/head are never rewritten.
    assert baseline_current_execution_heads(db) == 2
    assert baseline_current_execution_heads(db) == 0
    assert current.execution_revision_id == original


def test_template_baseline_captures_database_group_prompt(execution_db, monkeypatch):
    from src.lib.agent_studio.execution_revision_service import baseline_current_execution_heads, get_execution_revision
    from src.lib.agent_studio.execution_snapshot import saved_runtime_prompt_bundle
    from src.lib.config import agent_loader
    from src.lib.prompts import cache
    from src.models.sql.prompts import PromptTemplate

    db, agent_id, _, _ = execution_db
    PromptTemplate.__table__.create(db.connection())
    for name in ("_active_cache", "_version_cache", "_initialized", "_loaded_at"):
        monkeypatch.setattr(cache, name, getattr(cache, name))  # Restore globals on fixture exit.
    definition = agent_loader.AgentDefinition(folder_name="test_template", agent_id="test_template",
                                              name="Test template", tools=[])
    monkeypatch.setattr(agent_loader, "get_agent_definition", lambda _key: definition)
    head = db.get(Agent, agent_id)
    head.template_source = "test_template"
    head.group_rules_component = "test_template"
    head.group_rules_enabled = True
    prompt = PromptTemplate(agent_name="test_template", prompt_type="group_rules", group_id="FB",
                            content="Database group rules at migration", version=4, is_active=True)
    db.add(prompt)
    db.flush()
    baseline_current_execution_heads(db)
    _, saved = get_execution_revision(db, head.id, head.execution_revision_id, 1, active_group_ids=[])
    assert saved.template_source == saved.group_rules_component == "test_template"
    assert saved.output_contract.output_state == "none"
    assert "Database group rules at migration" in saved_runtime_prompt_bundle(saved, active_groups=["FB"]).render()
    prompt.content = "Changed after migration"
    db.flush()
    cache.initialize(db)
    assert "Changed after migration" not in saved_runtime_prompt_bundle(saved, active_groups=["FB"]).render()


@pytest.mark.parametrize("explicit_pin", [True, False])
def test_catalog_pin_checks_saved_access_and_current_tool_policy(execution_db, monkeypatch, explicit_pin):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from src.lib.agent_studio import catalog_service
    from src.lib.agent_studio.execution_snapshot import capture_execution_snapshot
    from src.lib.agent_studio.execution_revision_service import (
        append_execution_revision, ExecutionRevisionNotFoundError,
    )
    from src.models.sql import database
    from src.models.sql.tool_policy import ToolPolicy
    from src.schemas.agent_execution_revision import AgentOutputContract

    db, agent_id, _, _ = execution_db
    ToolPolicy.__table__.create(db.connection())
    head = db.get(Agent, agent_id)
    head.allowed_group_ids = ["FB"]
    head.inherited_allowed_group_ids = ["FB"]
    saved = capture_execution_snapshot(db, head, AgentOutputContract(output_state="none"))
    # No template-owned tool policy is needed for this private test tool.
    saved.tool_ids = ["saved_tool"]
    revision = append_execution_revision(db, head, saved, user_id=1, expected_revision_id=None)
    policy = ToolPolicy(
        tool_key="saved_tool", display_name="Saved", description="Test",
        category="Test", curator_visible=True, allow_attach=True, allow_execute=True,
    )
    db.add(policy)
    head.allowed_group_ids = ["WB"]
    head.model_id = "Mutable model must not be used"
    db.flush()
    monkeypatch.setattr(database, "SessionLocal", lambda: nullcontext(db))
    monkeypatch.setattr(
        catalog_service, "_create_db_agent",
        lambda _head, *, execution_snapshot, **_kwargs: SimpleNamespace(saved=execution_snapshot),
    )
    args = dict(db_user_id=1, authenticated_groups=["FB"])
    if explicit_pin:
        args["execution_revision_id"] = str(revision.id)
    built = catalog_service.get_agent_by_id(head.agent_key, **args)
    assert built.saved.model_id == "test-model"
    assert built.execution_revision_id == str(revision.id)
    from src.lib.agent_studio.custom_agent_service import get_custom_agent_runtime_info
    head.tool_ids = ["search_document"]
    db.flush()
    info = get_custom_agent_runtime_info(head.agent_key, db=db, user_id=1, active_group_ids=["FB"])
    assert info is not None and not info.requires_document
    assert info.instructions == saved.instructions and info.allowed_group_ids == ["FB"]
    with pytest.raises(ExecutionRevisionNotFoundError):
        catalog_service.get_agent_by_id(head.agent_key, **{**args, "authenticated_groups": ["WB"]})
    with pytest.raises(ExecutionRevisionNotFoundError):
        catalog_service.get_agent_by_id(head.agent_key, **{**args, "db_user_id": 2})
    policy.allow_execute = False
    db.flush()
    with pytest.raises(ValueError, match="no longer available"):
        catalog_service.get_agent_by_id(head.agent_key, **args)
    policy.allow_execute = True
    head.is_active = False
    db.flush()
    if explicit_pin:
        assert catalog_service.get_agent_by_id(head.agent_key, **args).execution_revision_id == str(revision.id)
    else:
        with pytest.raises(ValueError, match="archived"):
            catalog_service.get_agent_by_id(head.agent_key, **args)
        head.is_active = True
        head.execution_revision_id = None
        db.flush()
        with pytest.raises(ValueError, match="no saved executable configuration"):
            catalog_service.get_agent_by_id(head.agent_key, **args)


def test_restore_appends_complete_snapshot_and_checks_expected_head(execution_db):
    from src.lib.agent_studio.execution_snapshot import capture_execution_snapshot
    from src.lib.agent_studio.execution_revision_service import (
        append_execution_revision, restore_execution_revision,
        ExecutionRevisionConflictError,
    )
    from src.schemas.agent_execution_revision import AgentOutputContract

    db, agent_id, _, _ = execution_db
    head = db.get(Agent, agent_id)
    head.allowed_group_ids = ["FB"]
    head.inherited_allowed_group_ids = ["FB"]
    head.group_prompt_overrides = {"FB": "Saved group rules"}
    saved = capture_execution_snapshot(db, head, AgentOutputContract(output_state="none"))
    first = append_execution_revision(db, head, saved, user_id=1, expected_revision_id=None)
    head.model_id = "changed-model"
    head.model_temperature = 0.8
    head.model_reasoning = "high"
    head.instructions = "Changed instructions"
    head.group_prompt_overrides = {"FB": "Changed group rules"}
    changed = capture_execution_snapshot(db, head, AgentOutputContract(output_state="none"))
    second = append_execution_revision(db, head, changed, user_id=1, expected_revision_id=first.id)
    head.template_source = "no-longer-installed-parent"
    head.group_rules_component = "changed-component"
    head.tool_ids = ["changed-tool"]
    head.group_rules_enabled = not saved.group_rules_enabled
    head.allowed_group_ids = ["WB"]
    head.group_tool_policy = {"rules": [{"group_id": "FB", "tool_ids": ["changed-tool"]}]}
    db.flush()
    restored = restore_execution_revision(
        db, agent_id, first.id, user_id=1, expected_revision_id=second.id,
        active_group_ids=["FB"],
    )
    assert restored.id not in (first.id, second.id)
    assert restored.revision == 3
    assert restored.snapshot == first.snapshot == saved.model_dump(mode="json")
    assert restored.fingerprint == first.fingerprint
    assert head.execution_revision_id == restored.id
    assert head.model_id == "test-model" and head.model_temperature == 0.0
    assert head.model_reasoning is None
    assert head.instructions == "Original instructions"
    assert head.group_prompt_overrides == {"FB": "Saved group rules"}
    assert head.template_source == saved.template_source
    assert head.group_rules_component == saved.group_rules_component
    assert head.tool_ids == saved.tool_ids
    assert head.group_tool_policy == saved.group_tool_policy
    assert head.allowed_group_ids == saved.allowed_group_ids
    assert head.group_rules_enabled == saved.group_rules_enabled
    with pytest.raises(ExecutionRevisionConflictError):
        restore_execution_revision(
            db, agent_id, first.id, user_id=1, expected_revision_id=second.id,
            active_group_ids=["FB"],
        )
    assert head.execution_revision_id == restored.id


def test_restore_cannot_broaden_current_inherited_access(execution_db):
    from src.lib.agent_studio.execution_snapshot import capture_execution_snapshot
    from src.lib.agent_studio.execution_revision_service import (
        append_execution_revision, restore_execution_revision,
    )
    from src.schemas.agent_execution_revision import AgentOutputContract

    db, agent_id, _, _ = execution_db
    head = db.get(Agent, agent_id)
    saved = capture_execution_snapshot(db, head, AgentOutputContract(output_state="none"))
    first = append_execution_revision(db, head, saved, user_id=1, expected_revision_id=None)
    head.allowed_group_ids = ["FB"]
    head.inherited_allowed_group_ids = ["FB"]
    db.flush()
    with pytest.raises(ValueError, match="cannot widen"):
        restore_execution_revision(
            db, agent_id, first.id, user_id=1, expected_revision_id=first.id,
            active_group_ids=["FB"],
        )
    assert head.inherited_allowed_group_ids == ["FB"]
    assert head.execution_revision_id == first.id


def test_workshop_create_update_profile_binding_and_atomic_rollback(execution_db):
    from src.lib.agent_studio import custom_agent_service as service
    from src.lib.agent_studio.execution_revision_service import (
        get_execution_revision, ExecutionRevisionConflictError,
    )
    from src.models.sql.custom_agent import CustomAgentVersion
    from src.models.sql.generic_extraction_profile import GenericExtractionProfile
    from src.schemas.agent_execution_revision import AgentOutputContract

    db, _, _, _ = execution_db
    CustomAgentVersion.__table__.create(db.connection())
    head = service.create_custom_agent(
        db, 1, "Workshop snapshot", model_id="gpt-5.6-sol", model_temperature=0.0,
        model_reasoning="high",
        custom_prompt="Extract the details requested by the curator.",
        include_group_rules=False,
        new_generic_profile={"name": "Curator record", "semantic_class": "example", "fields": []},
    )
    first_id = head.execution_revision_id
    first, saved = get_execution_revision(db, head.id, first_id, 1, active_group_ids=[])
    assert saved.model_temperature == 0.0
    assert saved.model_reasoning == "high"
    assert saved.output_contract.output_mode == "profile_bound_generic"
    assert saved.output_contract.generic_profile_ref is not None
    profile_id = saved.output_contract.generic_profile_ref.profile_id
    assert db.get(GenericExtractionProfile, profile_id).owner_id == 1
    service.update_custom_agent(
        db, head, expected_revision_id=first_id, model_temperature=0.6,
        notes="Tune model temperature",
    )
    second_id = head.execution_revision_id
    second, edited = get_execution_revision(db, head.id, second_id, 1, active_group_ids=[])
    assert second.notes == "Tune model temperature"
    assert db.scalar(sa.select(sa.func.count()).select_from(CustomAgentVersion)) == 0
    assert edited.model_temperature == 0.6
    assert edited.output_contract == saved.output_contract
    assert first.snapshot["model_temperature"] == 0.0
    with pytest.raises(ExecutionRevisionConflictError):
        service.update_custom_agent(db, head, expected_revision_id=first_id, model_temperature=0.9)
    assert head.model_temperature == 0.6
    service.update_custom_agent(
        db, head, expected_revision_id=second_id,
        output_contract=AgentOutputContract(output_state="none"),
        model_reasoning=None, model_reasoning_provided=True,
    )
    _, cleared = get_execution_revision(db, head.id, head.execution_revision_id, 1, active_group_ids=[])
    assert cleared.output_contract.output_state == "none"
    assert cleared.output_contract.generic_profile_ref is None
    assert cleared.model_reasoning is None
    before_profiles = db.scalar(sa.select(sa.func.count()).select_from(GenericExtractionProfile))
    with pytest.raises(RuntimeError, match="transaction failure"):
        with db.begin_nested():
            service.create_custom_agent(
                db, 1, "Rolled back", model_id="gpt-5.6-sol",
                custom_prompt="Draft that never commits", include_group_rules=False,
                new_generic_profile={"name": "Rolled back profile", "semantic_class": "example", "fields": []},
            )
            raise RuntimeError("transaction failure")
    assert db.scalar(sa.select(sa.func.count()).select_from(GenericExtractionProfile)) == before_profiles
    assert db.scalar(sa.select(Agent).where(Agent.name == "Rolled back")) is None


def test_clone_api_preserves_profile_pin_and_records_explicit_edits(execution_db, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from src.api import agent_studio_custom as api
    from src.lib.agent_studio import custom_agent_service as service
    from src.lib.agent_studio.execution_revision_service import get_execution_revision

    db, _, _, _ = execution_db
    monkeypatch.setattr(api, "set_global_user_from_cognito", lambda *_args: SimpleNamespace(id=1))
    # Private-schema fixture has no deployment capability catalogs. Keep the
    # canonical draft validation, source authorization and pin authorization real.
    monkeypatch.setattr(service, "authorized_agent_validation_sources", lambda *_args, **kwargs: kwargs["sources"])
    source = service.create_custom_agent(
        db, 1, "Clone API source", model_id="gpt-5.6-sol", custom_prompt="Saved source prompt",
        include_group_rules=False,
        new_generic_profile={"name": "Output record", "semantic_class": "example", "fields": []},
    )
    _, original = get_execution_revision(db, source.id, source.execution_revision_id, 1, active_group_ids=[])
    for label, edits in [("Exact API clone", {}), ("Edited API clone", {"custom_prompt": "New curator prompt"})]:
        response = asyncio.run(api.create_custom_agent_endpoint(
            api.CreateCustomAgentRequest(name=label, clone_source_agent_id=source.agent_key,
                                         clone_source_updated_at=source.updated_at, **edits),
            user={"sub": "test-owner"}, db=db,
        ))
        row, saved = get_execution_revision(db, response.id, response.execution_revision_id, 1, active_group_ids=[])
        assert saved.output_contract == original.output_contract
        assert saved.model_id == original.model_id
        assert saved.instructions == edits.get("custom_prompt", original.instructions)
        assert row.revision == (2 if edits else 1)


def test_revision_api_reads_and_restores_real_saved_head(execution_db, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from fastapi import HTTPException
    from src.api import agent_studio_custom as api
    from src.lib.agent_studio import custom_agent_service as service
    from src.models.sql.custom_agent import CustomAgentVersion

    db, _, _, _ = execution_db
    CustomAgentVersion.__table__.create(db.connection())
    monkeypatch.setattr(api, "set_global_user_from_cognito", lambda _db, user: SimpleNamespace(id=user["db_user_id"]))
    owner, stranger = {"db_user_id": 1}, {"db_user_id": 2}
    head = service.create_custom_agent(
        db, 1, "Revision API", model_id="gpt-5.6-sol",
        custom_prompt="First complete configuration", include_group_rules=False,
    )
    first_id = head.execution_revision_id
    first = asyncio.run(api.get_execution_revision_endpoint(head.id, first_id, user=owner, db=db))
    assert first.snapshot.instructions == "First complete configuration"
    service.update_custom_agent(db, head, expected_revision_id=first_id, model_temperature=0.7)
    second_id = head.execution_revision_id
    history = asyncio.run(api.list_execution_revisions_endpoint(head.id, before_revision=None, user=owner, db=db))
    assert [entry.revision for entry in history.revisions] == [2, 1]
    with pytest.raises(HTTPException) as denied:
        asyncio.run(api.get_execution_revision_endpoint(head.id, first_id, user=stranger, db=db))
    assert denied.value.status_code == 404
    restored = asyncio.run(api.restore_execution_revision_endpoint(
        head.id, first_id, api.RestoreExecutionRevisionRequest(expected_revision_id=second_id),
        user=owner, db=db,
    ))
    assert restored.execution_revision_id not in (first_id, second_id)
    assert restored.model_temperature == first.snapshot.model_temperature
    with pytest.raises(HTTPException) as conflict:
        asyncio.run(api.restore_execution_revision_endpoint(
            head.id, first_id, api.RestoreExecutionRevisionRequest(expected_revision_id=second_id),
            user=owner, db=db,
        ))
    assert conflict.value.status_code == 409


def test_custom_clone_preserves_snapshot_profile_and_inherited_access(execution_db, monkeypatch):
    from src.lib.agent_studio import custom_agent_service as service
    from src.lib.agent_studio.execution_revision_service import get_execution_revision
    from src.models.sql.custom_agent import CustomAgentVersion
    from src.models.sql.generic_extraction_profile import GenericExtractionProfile

    db, _, _, _ = execution_db
    CustomAgentVersion.__table__.create(db.connection())
    source = service.create_custom_agent(
        db, 1, "Clone source", model_id="gpt-5.6-sol", model_temperature=0.0,
        custom_prompt="Exact saved instructions", include_group_rules=False,
        allowed_group_ids=["FB"],
        new_generic_profile={"name": "Shared profile", "semantic_class": "example", "fields": []},
    )
    _, original = get_execution_revision(db, source.id, source.execution_revision_id, 1, active_group_ids=["FB"])
    profile_count = db.scalar(sa.select(sa.func.count()).select_from(GenericExtractionProfile))
    source.model_id = "Do not clone this mutable model"
    source.instructions = "Do not clone these mutable instructions"
    source.template_source = "not-installed"
    source.tool_ids = ["mutable-tool"]
    source.group_tool_policy = {"rules": []}
    db.flush()
    # The public clone entrypoint's visibility lookup is unit-tested separately;
    # get_execution_revision still performs its real DB visibility/access checks.
    monkeypatch.setattr(service, "get_agent_by_key", lambda *_args, **_kwargs: source)
    clone = service.clone_visible_agent_for_user(db, 1, source.agent_key, name="Exact clone", active_group_ids=["FB"])
    _, copied = get_execution_revision(db, clone.id, clone.execution_revision_id, 1, active_group_ids=["FB"])
    assert clone.id != source.id
    assert clone.model_id == original.model_id
    assert clone.instructions == original.instructions
    assert clone.template_source == original.template_source
    assert clone.tool_ids == original.tool_ids
    assert copied.output_contract == original.output_contract
    assert copied.prompt_layer_manifest == original.prompt_layer_manifest
    assert copied.group_prompt_layers == original.group_prompt_layers
    assert copied.group_tool_policy == original.group_tool_policy
    assert copied.inherited_allowed_group_ids == copied.allowed_group_ids == ["FB"]
    assert db.scalar(sa.select(sa.func.count()).select_from(GenericExtractionProfile)) == profile_count
    with pytest.raises(ValueError, match="cannot widen"):
        service.clone_saved_custom_agent(db, 1, source, name="Invalid clone", allowed_group_ids=[], active_group_ids=["FB"])


def insert_revision(
    db,
    agent_id,
    *,
    mode=None,
    state="none",
    schema=None,
    profile=None,
    revision=1,
    snapshot=None,
):
    revision_id = uuid4()
    pin = (
        {
            "profile_revision_id": str(profile.id),
            "fingerprint": profile.fingerprint,
        }
        if profile
        else None
    )
    output = {
        "output_state": state,
        "output_mode": mode,
        "output_schema_key": schema,
        "generic_profile_ref": pin,
    }
    db.execute(
        sa.text("""
        INSERT INTO agent_execution_revisions(
            id, agent_id, revision, creator_id, fingerprint, snapshot,
            output_state, output_mode, output_schema_key, profile_revision_id, profile_fingerprint
        ) VALUES (
            :id, :agent_id, :revision, 1, :fingerprint, :snapshot,
            :state, :mode, :schema, :profile_id, :profile_fingerprint
        )
    """).bindparams(sa.bindparam("snapshot", type_=JSONB)),
        {
            "id": revision_id,
            "agent_id": agent_id,
            "revision": revision,
            "fingerprint": "sha256:" + "b" * 64,
            "snapshot": snapshot
            if snapshot is not None
            else {"output_contract": output},
            "state": state,
            "mode": mode,
            "schema": schema,
            "profile_id": profile.id if profile else None,
            "profile_fingerprint": profile.fingerprint if profile else None,
        },
    )
    return revision_id


@pytest.mark.parametrize(
    "mode", [None, "domain", "profile_bound_generic", "unprofiled_generic"]
)
def test_valid_states_and_profile_fk(execution_db, mode):
    db, agent_id, _, profile = execution_db
    revision_id = insert_revision(
        db,
        agent_id,
        state="structured_extraction" if mode else "none",
        mode=mode,
        schema="Example" if mode == "domain" else None,
        profile=profile if mode == "profile_bound_generic" else None,
    )
    db.execute(
        sa.text(
            "UPDATE agents SET execution_revision_id = :revision_id WHERE id = :id"
        ),
        {"id": agent_id, "revision_id": revision_id},
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"state": "structured_extraction"},
        {"state": "none", "mode": "unprofiled_generic"},
        {"state": "structured_extraction", "mode": "domain"},
        {"state": "structured_extraction", "mode": "profile_bound_generic"},
        {
            "state": "structured_extraction",
            "mode": "unprofiled_generic",
            "schema": "Example",
        },
        {"snapshot": {"output_contract": {"output_state": "structured_extraction"}}},
    ],
)
def test_database_rejects_inconsistent_output_states(execution_db, kwargs):
    db, agent_id, _, _ = execution_db
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            insert_revision(db, agent_id, **kwargs)


def test_canonical_agent_fk_head_ownership_uniqueness_and_immutability(execution_db):
    db, agent_id, other_id, _ = execution_db
    revision_id = insert_revision(db, agent_id)
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            insert_revision(db, agent_id)
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            insert_revision(db, uuid4())
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.execute(sa.text("SET CONSTRAINTS ALL IMMEDIATE"))
            db.execute(
                sa.text(
                    "UPDATE agents SET execution_revision_id = :revision WHERE id = :id"
                ),
                {"revision": revision_id, "id": other_id},
            )
    with pytest.raises(DBAPIError, match="immutable"):
        with db.begin_nested():
            db.execute(
                sa.text(
                    "UPDATE agent_execution_revisions SET fingerprint = :fp WHERE id = :id"
                ),
                {"id": revision_id, "fp": "sha256:" + "a" * 64},
            )
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.execute(sa.text("DELETE FROM agents WHERE id = :id"), {"id": agent_id})


def test_edit_preserves_saved_inherited_tools_and_group_policy(execution_db, monkeypatch):
    from src.lib.agent_studio import custom_agent_service as service
    from src.lib.agent_studio.execution_snapshot import capture_execution_snapshot
    from src.lib.agent_studio.execution_revision_service import append_execution_revision, get_execution_revision
    from src.schemas.agent_execution_revision import AgentOutputContract

    db, agent_id, _, _ = execution_db
    head = db.get(Agent, agent_id)
    head.model_id = "gpt-5.6-sol"
    head.tool_ids = ["record_evidence"]
    head.group_tool_policy = {"rules": []}
    monkeypatch.setattr(service, "_system_managed_tool_ids", lambda *_args: ["record_evidence"])
    saved = capture_execution_snapshot(db, head, AgentOutputContract(output_state="none"))
    revision = append_execution_revision(db, head, saved, user_id=1, expected_revision_id=None)
    # Today's mutable policy must neither replace the saved group rules nor
    # drop an inherited tool when the curator edits the selectable tool list.
    head.group_tool_policy = {}
    db.flush()
    monkeypatch.setattr(service, "_system_managed_tool_ids", lambda *_args: [])
    monkeypatch.setattr(service, "_resolve_system_template_agent",
                        lambda *_args, **_kwargs: pytest.fail("Edit reread today's parent tools"))
    service.update_custom_agent(db, head, expected_revision_id=revision.id, tool_ids=[], model_temperature=0.4)
    _, edited = get_execution_revision(db, head.id, head.execution_revision_id, 1, active_group_ids=[])
    assert edited.tool_ids == edited.system_managed_tool_ids == ["record_evidence"]
    assert edited.group_tool_policy == saved.group_tool_policy == {"rules": []}
    assert edited.model_temperature == 0.4
    assert revision.snapshot == saved.model_dump(mode="json")


def test_normal_save_round_trips_all_output_modes_atomically(execution_db, monkeypatch):
    from pydantic import create_model
    from src.lib.prompts import assembly
    from src.lib.agent_studio import custom_agent_service as service
    from src.lib.agent_studio.execution_snapshot import capture_execution_snapshot
    from src.lib.agent_studio.execution_revision_service import append_execution_revision, get_execution_revision
    from src.schemas.agent_execution_revision import AgentOutputContract, GenericProfilePin

    db, agent_id, _, profile = execution_db
    head = db.get(Agent, agent_id)
    head.model_id = "gpt-5.6-sol"
    head.tool_ids = ["finalize_allele_extraction"]
    # Catalog eligibility is tested separately; this test exercises the real
    # transaction, complete snapshot, output discrimination and relational FKs.
    monkeypatch.setattr(service, "_system_managed_tool_ids", lambda *_args: ["finalize_allele_extraction"])
    monkeypatch.setattr(service, "_require_valid_custom_agent_draft", lambda *_args, **_kwargs: None)
    output_type = create_model("TestPackagedEnvelope", records=(list[str], ...))
    monkeypatch.setattr(assembly, "resolve_output_schema", lambda _key: output_type)
    none = AgentOutputContract(output_state="none")
    domain = AgentOutputContract(output_state="structured_extraction", output_mode="domain",
                                output_schema_key="TestPackagedEnvelope")
    bound = AgentOutputContract(output_state="structured_extraction", output_mode="profile_bound_generic",
                               generic_profile_ref=GenericProfilePin(
                                   profile_id=profile.profile_id, profile_revision_id=profile.id,
                                   revision=profile.revision, fingerprint=profile.fingerprint))
    generic = AgentOutputContract(output_state="structured_extraction", output_mode="unprofiled_generic")
    first = append_execution_revision(db, head, capture_execution_snapshot(db, head, none),
                                      user_id=1, expected_revision_id=None)
    for number, output in enumerate([domain, bound, generic, none, domain], start=2):
        service.update_custom_agent(db, head, expected_revision_id=head.execution_revision_id,
                                    output_contract=output)
        row, saved = get_execution_revision(db, head.id, head.execution_revision_id, 1, active_group_ids=[])
        assert row.revision == number and saved.output_contract == output
        assert row.output_schema_key == head.output_schema_key == output.output_schema_key
        assert row.profile_revision_id == (profile.id if output == bound else None)
        assert row.profile_fingerprint == (profile.fingerprint if output == bound else None)
    assert first.snapshot["output_contract"]["output_state"] == "none"
    before = head.execution_revision_id
    with pytest.raises(ValueError, match="identity mismatch"):
        with db.begin_nested():
            bad_pin = bound.model_copy(deep=True)
            bad_pin.generic_profile_ref.fingerprint = "sha256:" + "f" * 64
            service.update_custom_agent(db, head, expected_revision_id=before, output_contract=bad_pin)
    db.refresh(head)
    assert head.execution_revision_id == before
    assert head.output_schema_key == domain.output_schema_key


def test_profile_identity_mismatch_cannot_be_inserted(execution_db):
    from types import SimpleNamespace

    db, agent_id, _, profile = execution_db
    wrong = SimpleNamespace(id=profile.id, fingerprint="sha256:" + "c" * 64)
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            insert_revision(
                db,
                agent_id,
                state="structured_extraction",
                mode="profile_bound_generic",
                profile=wrong,
            )


def test_service_snapshot_does_not_float_with_mutable_head_and_archival(
    execution_db, monkeypatch
):
    from src.lib.agent_studio import custom_agent_service
    from src.lib.agent_studio.execution_snapshot import capture_execution_snapshot
    from src.lib.agent_studio.execution_revision_service import (
        append_execution_revision,
        get_execution_revision,
        ExecutionRevisionConflictError,
        ExecutionRevisionNotFoundError,
    )
    from src.schemas.agent_execution_revision import AgentOutputContract

    monkeypatch.setattr(custom_agent_service, "_system_managed_tool_ids", lambda *_: [])
    db, agent_id, _, _ = execution_db
    head = db.get(Agent, agent_id)
    head.allowed_group_ids = ["FB"]
    head.inherited_allowed_group_ids = ["FB"]
    saved = capture_execution_snapshot(
        db, head, AgentOutputContract(output_state="none")
    )
    revision = append_execution_revision(
        db, head, saved, user_id=1, expected_revision_id=None
    )
    assert head.execution_revision_id == revision.id
    head.model_id = "changed"
    head.model_temperature = 0.8
    head.instructions = "Changed"
    head.tool_ids = ["changed-tool"]
    head.allowed_group_ids = ["WB"]
    head.inherited_allowed_group_ids = ["WB"]
    head.is_active = False
    db.flush()
    _, restored = get_execution_revision(
        db, agent_id, revision.id, 1, active_group_ids=["FB"]
    )
    assert restored.model_id == "test-model" and restored.model_temperature == 0.0
    assert restored.instructions == "Original instructions"
    assert restored.tool_ids == [] and restored.allowed_group_ids == ["FB"]
    with pytest.raises(ExecutionRevisionNotFoundError):
        get_execution_revision(db, agent_id, revision.id, 1, active_group_ids=["WB"])
    with pytest.raises(ExecutionRevisionNotFoundError):
        get_execution_revision(db, agent_id, revision.id, 2, active_group_ids=["FB"])
    with pytest.raises(ExecutionRevisionConflictError):
        append_execution_revision(db, head, saved, user_id=1, expected_revision_id=None)


def test_service_profile_pin_uses_exact_revision_and_historical_access(
    execution_db, monkeypatch
):
    from src.lib.agent_studio import custom_agent_service
    from src.lib.agent_studio.execution_snapshot import capture_execution_snapshot
    from src.lib.agent_studio.execution_revision_service import (
        append_execution_revision,
        get_execution_revision,
    )
    from src.schemas.agent_execution_revision import AgentOutputContract

    monkeypatch.setattr(custom_agent_service, "_system_managed_tool_ids", lambda *_: [])
    db, agent_id, _, profile = execution_db
    head = db.get(Agent, agent_id)
    output = AgentOutputContract(
        output_state="structured_extraction",
        output_mode="profile_bound_generic",
        generic_profile_ref={
            "profile_id": profile.profile_id,
            "profile_revision_id": profile.id,
            "revision": profile.revision,
            "fingerprint": profile.fingerprint,
        },
    )
    saved = capture_execution_snapshot(db, head, output)
    revision = append_execution_revision(
        db, head, saved, user_id=1, expected_revision_id=None
    )
    generic_profile_service.archive_profile(
        db, profile.profile_id, 1, expected_revision=1
    )
    _, restored = get_execution_revision(
        db, agent_id, revision.id, 1, active_group_ids=[]
    )
    assert (
        restored.output_contract.generic_profile_ref.profile_revision_id == profile.id
    )
    # Losing access to a pinned profile makes the executable unavailable, not
    # an unhandled server error in history or the isolated-test preflight.
    from src.models.sql.generic_extraction_profile import GenericExtractionProfile
    from src.lib.agent_studio.execution_revision_service import ExecutionRevisionNotFoundError

    db.get(GenericExtractionProfile, profile.profile_id).owner_id = 2
    db.flush()
    with pytest.raises(ExecutionRevisionNotFoundError):
        get_execution_revision(db, agent_id, revision.id, 1, active_group_ids=[])

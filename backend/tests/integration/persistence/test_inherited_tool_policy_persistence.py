"""Real template saves and runtime construction with installed tool policies."""

from contextlib import nullcontext
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
import sqlalchemy as sa

from src.lib.agent_studio import custom_agent_service as service
from src.lib.agent_studio.execution_revision_service import get_execution_revision
from src.lib.agent_studio.tool_policy_service import get_tool_policy_cache
from src.models.sql.agent import Agent, ProjectMember
from src.models.sql.custom_agent import CustomAgentVersion
from src.models.sql.tool_policy import ToolPolicy
from .test_agent_execution_revision_persistence import execution_db  # noqa: F401
from .test_generic_profile_persistence import profile_db  # noqa: F401


def migration():
    path = Path(__file__).resolve().parents[3] / "alembic/versions/n1c2d3e4f5a6_backfill_installed_tool_policies.py"
    spec = spec_from_file_location("policy_backfill_persistence", path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def policy_db(execution_db):  # noqa: F811
    db, _, _, _ = execution_db
    ToolPolicy.__table__.create(db.connection())
    ProjectMember.__table__.create(db.connection())
    CustomAgentVersion.__table__.create(db.connection())
    with Operations.context(MigrationContext.configure(db.connection())):
        migration().upgrade()
    get_tool_policy_cache().refresh(db)
    return db


def test_backfill_preserves_explicit_denial_and_existing_metadata(policy_db):
    db = policy_db
    policy = db.get(ToolPolicy, "read_chunk")
    assert policy is not None and policy.allow_attach and policy.allow_execute
    policy.allow_attach = False
    policy.allow_execute = False
    policy.config = {"operator_note": "Explicit local restriction"}
    db.flush()
    original = db.execute(sa.text("SELECT row_to_json(p) FROM tool_policies p WHERE tool_key='read_chunk'")).scalar_one()
    with Operations.context(MigrationContext.configure(db.connection())):
        migration().upgrade()
    after = db.execute(sa.text("SELECT row_to_json(p) FROM tool_policies p WHERE tool_key='read_chunk'")).scalar_one()
    assert after == original


@pytest.mark.parametrize("submit_visible_tools", [False, True])
def test_pre_backfill_snapshot_keeps_newly_designated_helpers(execution_db, submit_visible_tools):  # noqa: F811
    from copy import deepcopy
    from src.lib.agent_studio.domain_output_contract import initial_agent_output_contract
    from src.lib.agent_studio.execution_snapshot import capture_execution_snapshot
    from src.lib.agent_studio.execution_revision_service import append_execution_revision
    from src.lib.config.agent_loader import get_agent_definition
    from src.lib.config import get_valid_group_ids

    db, agent_id, _, _ = execution_db
    ToolPolicy.__table__.create(db.connection())
    ProjectMember.__table__.create(db.connection())
    CustomAgentVersion.__table__.create(db.connection())
    get_tool_policy_cache().refresh(db)
    definition = get_agent_definition("gene_extractor")
    assert definition is not None
    groups = list(get_valid_group_ids())
    head = db.get(Agent, agent_id)
    head.template_source = "gene_extractor"
    head.model_id = "gpt-5.6-sol"
    head.model_reasoning = "medium"
    head.tool_ids = list(definition.tools)
    head.allowed_group_ids = list(definition.access.allowed_group_ids)
    head.group_rules_enabled = False
    snapshot = capture_execution_snapshot(db, head, initial_agent_output_contract(head), active_group_ids=groups)
    assert "agr_species_context_lookup" in snapshot.tool_ids
    assert "agr_species_context_lookup" not in snapshot.system_managed_tool_ids
    old = append_execution_revision(db, head, snapshot, user_id=1, expected_revision_id=None)
    original = deepcopy(old.snapshot), old.fingerprint
    with Operations.context(MigrationContext.configure(db.connection())):
        migration().upgrade()
    get_tool_policy_cache().refresh(db)
    tool_args = {}
    if submit_visible_tools:
        tool_args["tool_ids"] = [p.tool_key for p in db.query(ToolPolicy)
                                 if p.allow_attach and p.tool_key in snapshot.tool_ids]
    service.update_custom_agent(
        db, head, description="Edit existing saved agent", expected_revision_id=old.id,
        active_group_ids=groups, **tool_args,
    )
    _, updated = get_execution_revision(db, head.id, head.execution_revision_id, 1, active_group_ids=groups)
    assert set(updated.tool_ids) == set(snapshot.tool_ids)
    assert "agr_species_context_lookup" in updated.system_managed_tool_ids
    assert "stage_gene_mention_evidence" in updated.system_managed_tool_ids
    db.refresh(old)
    assert (old.snapshot, old.fingerprint) == original


@pytest.mark.parametrize("template_key", ["gene_extractor", "pdf_extraction"])
def test_real_template_create_edit_build_and_revocation(policy_db, monkeypatch, template_key):
    from src.lib.agent_studio import catalog_service
    from src.lib.config.agent_loader import get_agent_definition
    from src.lib.config import get_valid_group_ids
    from src.lib.openai_agents import langfuse_client
    from src.models.sql import database

    db = policy_db
    definition = get_agent_definition(template_key)
    assert definition is not None
    groups = list(get_valid_group_ids())
    template = Agent(
        id=uuid4(), agent_key=template_key, name=definition.name,
        instructions="Extract paper-supported records.", model_id="gpt-5.6-sol",
        model_temperature=0.1, model_reasoning="medium", visibility="system",
        tool_ids=list(definition.tools), allowed_group_ids=list(definition.access.allowed_group_ids),
        group_rules_enabled=False,
    )
    db.add(template)
    db.flush()
    original_tools = list(template.tool_ids)
    head = service.create_custom_agent(
        db, 1, f"Inherited {template_key}", template_source=template_key,
        include_group_rules=False, active_group_ids=groups,
    )
    first_id = head.execution_revision_id
    _, first = get_execution_revision(db, head.id, first_id, 1, active_group_ids=groups)
    assert head.tool_ids == original_tools
    assert "agr_species_context_lookup" in first.system_managed_tool_ids
    assert set(first.tool_ids) <= {p.tool_key for p in db.query(ToolPolicy).filter(ToolPolicy.allow_execute.is_(True))}

    from src.lib.agent_studio.models import AgentWorkshopContext
    from src.lib.agent_studio.workshop_authoring import validate_workshop_context
    output = first.output_contract
    workshop = AgentWorkshopContext(
        getting_started_mode="template", template_source=template_key,
        custom_agent_id=head.agent_key, custom_agent_updated_at=head.updated_at.isoformat(),
        draft_name=head.name, draft_description=head.description or "", draft_icon=head.icon,
        draft_visibility="private", draft_model_id=head.model_id,
        draft_model_reasoning=head.model_reasoning, prompt_draft=head.instructions,
        draft_allowed_group_ids=list(head.allowed_group_ids),
        inherited_allowed_group_ids=list(head.inherited_allowed_group_ids),
        include_group_rules=False, group_prompt_overrides={}, draft_tool_ids=list(head.tool_ids),
        draft_output={"mode": output.output_mode, "schemaKey": "",
                      "domainExtractionRef": output.domain_extraction_ref.model_dump(mode="json")
                      if output.domain_extraction_ref else None},
    )
    result = validate_workshop_context(db, workshop=workshop, user_id=1, active_group_ids=groups)
    assert result.valid, result.findings

    # A different installed helper is not inherited merely because its policy
    # has the same hidden/runtime designation.
    injected = workshop.model_copy(deep=True)
    injected.draft_tool_ids = [*(injected.draft_tool_ids or []), "stage_disease_observation"]
    result = validate_workshop_context(db, workshop=injected, user_id=1, active_group_ids=groups)
    assert any(finding.code == "unavailable_tool" for finding in result.findings)
    with pytest.raises(ValueError, match="not attachable"):
        service.create_custom_agent(
            db, 1, "Unrelated hidden helper", template_source=template_key,
            tool_ids=[*original_tools, "stage_disease_observation"],
            include_group_rules=False, active_group_ids=groups,
        )

    # Saved authoring does not inherit additions from today's mutable template.
    template.tool_ids = [*original_tools, "stage_disease_observation"]
    db.flush()
    result = validate_workshop_context(db, workshop=workshop, user_id=1, active_group_ids=groups)
    assert result.valid, result.findings

    # A metadata-only save must carry source provenance even without tool_ids.
    service.update_custom_agent(
        db, head, description="Metadata-only edit", expected_revision_id=first_id,
        active_group_ids=groups,
    )
    second_id = head.execution_revision_id
    _, second = get_execution_revision(db, head.id, second_id, 1, active_group_ids=groups)
    assert second.tool_ids == first.tool_ids
    assert second.system_managed_tool_ids == first.system_managed_tool_ids

    # The normal UI submits the visible tools; hidden inherited helpers survive.
    visible_tools = [p.tool_key for p in db.query(ToolPolicy)
                     if p.allow_attach and p.tool_key in original_tools]
    service.update_custom_agent(
        db, head, tool_ids=visible_tools, expected_revision_id=second_id,
        active_group_ids=groups,
    )
    assert set(head.tool_ids) == set(original_tools)
    assert template.tool_ids == [*original_tools, "stage_disease_observation"]

    monkeypatch.setattr(database, "SessionLocal", lambda: nullcontext(db))
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-not-a-credential")
    monkeypatch.setattr(langfuse_client, "log_agent_config", lambda **kwargs: None)
    runtime_context = {"db_user_id": 1, "user_id": "test-curator", "document_id": str(uuid4()),
                       "authenticated_groups": groups}
    built = catalog_service.get_agent_by_id(head.agent_key, **runtime_context)
    assert built.execution_revision_id == str(head.execution_revision_id)
    assert "agr_species_context_lookup" in [tool.name for tool in built.tools]

    policy = db.get(ToolPolicy, "agr_species_context_lookup")
    policy.allow_execute = False
    db.flush()
    with pytest.raises(ValueError, match="no longer available for execution"):
        catalog_service.get_agent_by_id(head.agent_key, **runtime_context)
    with pytest.raises(service.AuthoringValidationError):
        service.update_custom_agent(
            db, head, description="Must not bypass revocation",
            expected_revision_id=head.execution_revision_id, active_group_ids=groups,
        )

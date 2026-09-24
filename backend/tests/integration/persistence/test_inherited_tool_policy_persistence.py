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
    head.model_id = "gpt-6-sol"
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
    from src.lib.prompts import cache
    from src.models.sql import database
    from src.models.sql.prompts import PromptTemplate

    db = policy_db
    definition = get_agent_definition(template_key)
    assert definition is not None
    groups = list(get_valid_group_ids())
    template = Agent(
        id=uuid4(), agent_key=template_key, name=definition.name,
        instructions="Extract paper-supported records.", model_id="gpt-6-sol",
        model_temperature=0.1, model_reasoning="medium", visibility="system",
        tool_ids=list(definition.tools), allowed_group_ids=list(definition.access.allowed_group_ids),
        group_rules_enabled=False,
    )
    db.add(template)
    # Saving an inherited prompt uses the same active prompt cache as startup,
    # not the template Agent row alone. Keep it local to this private schema.
    PromptTemplate.__table__.create(db.connection())
    db.add(PromptTemplate(
        agent_name=template_key, prompt_type="system", content=template.instructions,
        version=1, is_active=True,
    ))
    db.flush()
    for name in ("_active_cache", "_version_cache", "_initialized", "_loaded_at"):
        monkeypatch.setattr(cache, name, getattr(cache, name))
    cache.initialize(db)
    original_tools = list(template.tool_ids)
    head = service.create_custom_agent(
        db, 1, f"Inherited {template_key}", template_source=template_key,
        include_group_rules=False, active_group_ids=groups,
    )
    first_id = head.execution_revision_id
    _, first = get_execution_revision(db, head.id, first_id, 1, active_group_ids=groups)
    assert head.instructions == first.instructions == template.instructions
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
        draft_model_reasoning=head.model_reasoning,
        # Workshop receives the serialized editable text, not frozen inherited
        # instructions stored on the agent/revision.
        prompt_draft=service.custom_agent_to_dict(head)["custom_prompt"],
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


RESOLVER_HELPERS = ("search_domain_field_terms", "inspect_ontology_term", "resolve_domain_field_term")


def resolver_inheritance_migration():
    path = (
        Path(__file__).resolve().parents[3]
        / "alembic/versions/s6t7u8v9w0x1_stop_inheriting_extraction_resolver_helpers.py"
    )
    spec = spec_from_file_location("resolver_inheritance_persistence", path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _legacy_expression_extractor(db, groups, *, migrated):
    """A custom extractor saved from the gene expression template while it inherited the
    term resolver helpers, as in production; optionally after s6t7u8v9w0x1 runs."""
    from src.lib.agent_studio.domain_output_contract import initial_agent_output_contract
    from src.lib.agent_studio.execution_revision_service import append_execution_revision
    from src.lib.agent_studio.execution_snapshot import capture_execution_snapshot
    from src.lib.config.agent_loader import get_agent_definition

    # Before s6t7u8v9w0x1 the resolver helpers were designated for inheritance.
    for tool_key in RESOLVER_HELPERS:
        policy = db.get(ToolPolicy, tool_key)
        policy.config = {**policy.config, "system_managed_inheritance": True}
    db.flush()
    get_tool_policy_cache().refresh(db)

    definition = get_agent_definition("gene_expression_extraction")
    assert definition is not None
    head = Agent(
        id=uuid4(), agent_key=f"ca_{uuid4().hex}", user_id=1, name="Saved expression extractor",
        instructions="Extract paper-supported records.", model_id="gpt-6-sol",
        model_temperature=0.1, model_reasoning="medium", visibility="private",
        template_source="gene_expression_extraction",
        tool_ids=list(dict.fromkeys([*definition.tools, *RESOLVER_HELPERS])),
        allowed_group_ids=list(definition.access.allowed_group_ids), group_rules_enabled=False,
    )
    db.add(head)
    db.flush()
    snapshot = capture_execution_snapshot(
        db, head, initial_agent_output_contract(head), active_group_ids=groups,
    )
    # The saved revision carries the helpers as inherited, like the ones in production.
    assert set(RESOLVER_HELPERS) <= set(snapshot.system_managed_tool_ids)
    saved = append_execution_revision(db, head, snapshot, user_id=1, expected_revision_id=None)

    if migrated:
        with Operations.context(MigrationContext.configure(db.connection())):
            resolver_inheritance_migration().upgrade()
        get_tool_policy_cache().refresh(db)
    return head, snapshot, saved


@pytest.mark.parametrize("migrated", [False, True])
@pytest.mark.parametrize("submit", ["visible", "saved_without_lookups", "saved_unchanged"])
def test_resaving_a_gene_expression_extractor_leaves_out_inherited_identity_lookups(
    policy_db, migrated, submit,
):
    """ALL-1276: a custom extractor saved from the gene expression template while it
    inherited the term resolver helpers re-saves without any identity lookup tool,
    including when the Workshop sends its saved tool list back unchanged."""
    from src.lib.config import get_valid_group_ids
    from src.lib.packages.tool_roles import identity_lookup_tool_names

    db = policy_db
    groups = list(get_valid_group_ids())
    head, snapshot, saved = _legacy_expression_extractor(db, groups, migrated=migrated)

    lookups = identity_lookup_tool_names()
    if submit == "visible":
        tool_ids = [p.tool_key for p in db.query(ToolPolicy)
                    if p.allow_attach and p.tool_key in snapshot.tool_ids]
    elif submit == "saved_without_lookups":
        tool_ids = [tool_id for tool_id in snapshot.tool_ids if tool_id not in lookups]
    else:
        tool_ids = list(snapshot.tool_ids)
    service.update_custom_agent(
        db, head, description="Re-save without database lookups", tool_ids=tool_ids,
        expected_revision_id=saved.id, active_group_ids=groups,
    )

    _, updated = get_execution_revision(
        db, head.id, head.execution_revision_id, 1, active_group_ids=groups,
    )
    assert head.execution_revision_id != saved.id
    assert not lookups & set(head.tool_ids)
    assert not lookups & set(updated.tool_ids)
    assert not lookups & set(updated.system_managed_tool_ids)
    assert "finalize_gene_expression_extraction" in updated.tool_ids
    assert "agr_species_context_lookup" in updated.tool_ids


@pytest.mark.parametrize("migrated", [False, True])
def test_workshop_accepts_a_legacy_expression_extractor_but_refuses_an_attached_lookup(
    policy_db, migrated,
):
    """ALL-1276: the Workshop validates the saved tool list unchanged (inherited lookups
    are withheld, as Save withholds them), but a lookup the curator attaches is refused."""
    from src.lib.agent_studio.models import AgentWorkshopContext
    from src.lib.agent_studio.workshop_authoring import validate_workshop_context
    from src.lib.config import get_valid_group_ids

    db = policy_db
    groups = list(get_valid_group_ids())
    head, snapshot, saved = _legacy_expression_extractor(db, groups, migrated=migrated)
    output = snapshot.output_contract
    workshop = AgentWorkshopContext(
        getting_started_mode="template", template_source="gene_expression_extraction",
        custom_agent_id=head.agent_key, custom_agent_updated_at=head.updated_at.isoformat(),
        draft_name=head.name, draft_description=head.description or "", draft_icon=head.icon,
        draft_visibility="private", draft_model_id=head.model_id,
        draft_model_reasoning=head.model_reasoning,
        prompt_draft=service.custom_agent_to_dict(head)["custom_prompt"],
        draft_allowed_group_ids=list(head.allowed_group_ids),
        inherited_allowed_group_ids=list(head.inherited_allowed_group_ids or []),
        include_group_rules=False, group_prompt_overrides={}, draft_tool_ids=list(snapshot.tool_ids),
        draft_output={"mode": output.output_mode, "schemaKey": "",
                      "domainExtractionRef": output.domain_extraction_ref.model_dump(mode="json")
                      if output.domain_extraction_ref else None},
    )
    result = validate_workshop_context(db, workshop=workshop, user_id=1, active_group_ids=groups)
    assert result.valid, result.findings

    attached = workshop.model_copy(deep=True)
    attached.draft_tool_ids = [*snapshot.tool_ids, "agr_curation_query"]
    result = validate_workshop_context(db, workshop=attached, user_id=1, active_group_ids=groups)
    [finding] = [f for f in result.findings if f.code == "identity_lookup_on_extraction_agent"]
    # Only the attached lookup is named; the inherited helpers are withheld, not reported.
    assert finding.fix_hint == "Remove these tools: agr_curation_query."
    with pytest.raises(service.AuthoringValidationError):
        service.update_custom_agent(
            db, head, tool_ids=[*snapshot.tool_ids, "agr_curation_query"],
            expected_revision_id=saved.id, active_group_ids=groups,
        )

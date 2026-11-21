"""Every tool an agent can be saved with has a tool_policies row on an upgraded database.

Policy rows come only from migrations. The table seed reads today's package
defaults, so a fresh database gets rows for every tool, while a database that
ran the seed earlier only gets rows a later migration adds. These tests start
from the rows production had at v0.11.12 and upgrade to head.
"""

from contextlib import nullcontext
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
import sqlalchemy as sa

from src.lib.agent_studio import custom_agent_service as service
from src.lib.agent_studio.tool_policy_service import get_tool_policy_cache
from src.models.sql.database import SessionLocal
from src.models.sql.tool_policy import ToolPolicy
from .test_agent_execution_revision_persistence import execution_db  # noqa: F401
from .test_generic_profile_persistence import profile_db  # noqa: F401
from .test_inherited_tool_policy_persistence import _seed_real_template, policy_db  # noqa: F401


BACKEND_ROOT = Path(__file__).resolve().parents[3]
PRODUCTION_HEAD_V01112 = "f4a8b0c2d3e5"
# tool_policies keys on production at v0.11.12 (read on 20 Nov 2026). Frozen on
# purpose: a tool added after this needs a migration that adds its row.
PRODUCTION_TOOL_KEYS_V01112 = frozenset({
    "agr_curation_query", "agr_literature_reference_lookup", "agr_species_context_lookup",
    "alliance_api_call", "attach_evidence_to_object", "build_default_projection_plan",
    "chebi_api_call", "curation_db_sql", "detach_evidence_from_object",
    "discard_allele_observation", "discard_disease_observation",
    "discard_gene_expression_observation", "discard_gene_mention_evidence",
    "discard_generic_object", "discard_go_recommendation", "discard_phenotype_observation",
    "discard_recorded_evidence", "explain_formatter_capabilities", "finalize_allele_extraction",
    "finalize_and_save", "finalize_chat_output", "finalize_disease_extraction",
    "finalize_gene_expression_extraction", "finalize_gene_extraction",
    "finalize_generic_extraction", "finalize_go_extraction", "finalize_phenotype_extraction",
    "find_staged_allele_observations", "find_staged_disease_observations",
    "find_staged_gene_expression_observations", "find_staged_gene_mention_evidence",
    "find_staged_generic_objects", "find_staged_go_recommendations",
    "find_staged_phenotype_observations", "formatter_cannot_complete", "get_agent_contract",
    "get_recorded_evidence", "go_api_call", "inspect_field_values", "inspect_output_artifacts",
    "inspect_output_rows", "list_generic_object_classes", "list_recorded_evidence",
    "list_staged_allele_observations", "list_staged_disease_observations",
    "list_staged_gene_expression_observations", "list_staged_gene_mention_evidence",
    "list_staged_generic_objects", "list_staged_go_recommendations",
    "list_staged_phenotype_observations", "patch_allele_observation",
    "patch_disease_observation", "patch_gene_expression_observation",
    "patch_gene_mention_evidence", "patch_generic_object", "patch_go_recommendation",
    "patch_phenotype_observation", "preview_output_projection", "quickgo_api_call",
    "read_chunk", "read_output_value", "read_section", "read_subsection", "record_evidence",
    "resolve_gene_product", "save_csv_file", "save_json_file", "save_tsv_file",
    "search_document", "stage_allele_observation", "stage_disease_observation",
    "stage_gene_expression_observation", "stage_gene_mention_evidence", "stage_generic_object",
    "stage_go_recommendation", "stage_phenotype_observation",
    "update_recorded_evidence_metadata", "validate_output_projection",
})
TOPIC_TAG_TOOLS = (
    "stage_topic_entity_tag", "patch_topic_entity_tag", "discard_topic_entity_tag",
    "list_staged_topic_entity_tags", "finalize_topic_entity_tag_extraction",
)


def _alembic_config():
    return Config(str(BACKEND_ROOT / "alembic.ini"))


def _topic_tag_backfill():
    path = BACKEND_ROOT / "alembic/versions/a9b8c7d6e5f4_backfill_topic_entity_tag_tool_policies.py"
    spec = spec_from_file_location("topic_tag_policy_backfill", path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tools_agents_can_be_saved_with():
    """Tools any agent template lists, plus every installed package tool."""
    from src.lib.agent_studio.catalog_service import _load_package_tool_registry
    from src.lib.config.agent_loader import load_agent_definitions

    listed = {
        tool_id: f"agent template {agent_id}"
        for agent_id, definition in load_agent_definitions().items()
        for tool_id in definition.tools
    }
    for binding in _load_package_tool_registry().bindings:
        listed.setdefault(binding.tool_id, "installed package tool")
    return listed


def _policy_rows(session):
    return {
        key: row for key, row in session.execute(
            sa.text("SELECT tool_key, row_to_json(p) FROM tool_policies p")
        ).all()
    }


def test_custom_topic_tag_agent_saves_and_builds_after_the_backfill(policy_db, monkeypatch):  # noqa: F811
    """Without the five rows (as on production at v0.11.12), a curator cannot save a
    copy of the topic-tag agent: inherited helpers need an executable policy row.
    After the backfill the copy saves and builds with all five tools."""
    from src.lib.agent_studio import catalog_service
    from src.lib.agent_studio.authoring_validation import AuthoringValidationError
    from src.lib.openai_agents import langfuse_client
    from src.models.sql import database

    db = policy_db
    db.query(ToolPolicy).filter(ToolPolicy.tool_key.in_(TOPIC_TAG_TOOLS)).delete(
        synchronize_session=False,
    )
    db.flush()
    get_tool_policy_cache().refresh(db)
    template, groups = _seed_real_template(db, monkeypatch, "gene_expression_topic_tag")
    with pytest.raises(AuthoringValidationError) as refused:
        service.create_custom_agent(
            db, 1, "My topic tag copy", template_source="gene_expression_topic_tag",
            include_group_rules=False, active_group_ids=groups,
        )
    unavailable = [finding for finding in refused.value.result.findings if finding.code == "unavailable_tool"]
    assert {finding.message for finding in unavailable} == {
        "This tool is not available for attachment to the current draft.",
    }
    # Exactly the five tools are refused (path is custom_agent.tool_ids.<index>).
    assert {
        template.tool_ids[int(finding.path.rsplit(".", 1)[1])] for finding in unavailable
    } == set(TOPIC_TAG_TOOLS)

    with Operations.context(MigrationContext.configure(db.connection())):
        _topic_tag_backfill().upgrade()
    get_tool_policy_cache().refresh(db)

    head = service.create_custom_agent(
        db, 1, "My topic tag copy", template_source="gene_expression_topic_tag",
        include_group_rules=False, active_group_ids=groups,
    )
    assert set(TOPIC_TAG_TOOLS) <= set(head.tool_ids)
    monkeypatch.setattr(database, "SessionLocal", lambda: nullcontext(db))
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-not-a-credential")
    monkeypatch.setattr(langfuse_client, "log_agent_config", lambda **kwargs: None)
    runtime_context = {"db_user_id": 1, "user_id": "test-curator", "document_id": str(uuid4()),
                       "authenticated_groups": groups}
    built = catalog_service.get_agent_by_id(head.agent_key, **runtime_context)
    assert built.execution_revision_id == str(head.execution_revision_id)
    assert set(TOPIC_TAG_TOOLS) <= {tool.name for tool in built.tools}


def test_upgrade_from_production_head_adds_a_row_for_every_saveable_tool(
    historical_migration_database,
):
    config = _alembic_config()
    command.upgrade(config, PRODUCTION_HEAD_V01112)
    with SessionLocal() as session:
        # The seed read today's defaults; keep only the rows production has.
        session.execute(
            sa.text("DELETE FROM tool_policies WHERE NOT (tool_key = ANY(:keys))"),
            {"keys": sorted(PRODUCTION_TOOL_KEYS_V01112)},
        )
        # An operator decision made before the backfill, on one of the five tools.
        session.execute(sa.text("""
            INSERT INTO tool_policies
                (tool_key, display_name, description, category,
                 curator_visible, allow_attach, allow_execute, config)
            VALUES ('patch_topic_entity_tag', 'Operator name', 'Operator text', 'Operator',
                    true, true, true, '{"operator_note": "kept"}'::jsonb)
        """))
        session.execute(sa.text(
            "UPDATE tool_policies SET allow_attach = false, config = '{\"operator_note\": \"kept\"}'::jsonb "
            "WHERE tool_key = 'read_chunk'"
        ))
        session.commit()
        before = _policy_rows(session)
    assert set(before) - {"patch_topic_entity_tag"} <= PRODUCTION_TOOL_KEYS_V01112

    command.upgrade(config, "head")

    with SessionLocal() as session:
        after = _policy_rows(session)
    assert {key: after[key] for key in before} == before
    missing = {
        tool_id: source for tool_id, source in _tools_agents_can_be_saved_with().items()
        if tool_id not in after
    }
    assert not missing, (
        "These tools have no tool_policies row on a database upgraded from production. "
        "Add a migration that inserts their rows from the package defaults: "
        f"{sorted(missing.items())}"
    )
    for tool_key in set(TOPIC_TAG_TOOLS) - {"patch_topic_entity_tag"}:
        row = after[tool_key]
        assert (row["curator_visible"], row["allow_attach"], row["allow_execute"]) == (False, False, True)
        assert row["category"] == "Runtime Helpers"
        assert row["config"] == {"system_managed_inheritance": True}

    # Running the backfill again changes nothing.
    with SessionLocal() as session:
        with Operations.context(MigrationContext.configure(session.connection())):
            _topic_tag_backfill().upgrade()
        session.commit()
        assert _policy_rows(session) == after

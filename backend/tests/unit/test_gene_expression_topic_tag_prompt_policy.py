"""WormBase scope rules and 0.12.0 disabled-state policy for gene_expression_topic_tag."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy.sql.elements import False_, True_
import yaml

from src.lib.agent_access import is_resource_access_allowed
from src.lib.agent_studio import runtime_validation
from src.lib.agent_studio.flow_agent_policy import flow_palette_show_in_palette
from src.lib.agent_studio.registry_builder import build_agent_registry
from src.lib.agent_studio.studio_guide import (
    prepare_studio_prompt_template,
    read_studio_guide,
)
from src.lib.agent_studio.system_agent_sync import _build_system_agent_values
from src.lib.config import agent_loader, agent_sources
from src.lib.config.groups_loader import load_groups
from src.lib.config.models_loader import load_models
from src.lib.runtime_entrypoint import validate_runtime_packages


REPO_ROOT = Path(__file__).resolve().parents[3]
REPO_PACKAGES_DIR = REPO_ROOT / "packages"
ALLIANCE_STUDIO_PROMPT_PATH = (
    REPO_PACKAGES_DIR / "alliance" / "config" / "agent_studio_system_prompt.md"
)


def _topic_tag_source():
    return next(
        source
        for source in agent_sources.resolve_agent_config_sources(REPO_PACKAGES_DIR)
        if source.folder_name == "gene_expression_topic_tag"
    )


def _content(path: Path) -> str:
    return str(yaml.safe_load(path.read_text(encoding="utf-8"))["content"])


def _load_shipped_agents(monkeypatch):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))
    load_groups(REPO_ROOT / "config" / "groups.yaml", force_reload=True)
    load_models(REPO_ROOT / "config" / "models.yaml", force_reload=True)
    agent_loader.reset_cache()
    return agent_loader.load_agent_definitions(force_reload=True)


def test_topic_tag_agent_enables_group_rules_and_ships_only_wb():
    source = _topic_tag_source()
    agent_data = yaml.safe_load(source.agent_yaml.read_text(encoding="utf-8"))

    assert agent_data["group_rules_enabled"] is True
    group_ids = [
        agent_sources.resolve_group_rule_id(
            path, yaml.safe_load(path.read_text(encoding="utf-8"))
        )
        for path in source.group_rule_files
    ]
    assert group_ids == ["WB"]


def test_wormbase_scope_lives_in_wb_rules_not_the_shared_prompt():
    source = _topic_tag_source()
    base_prompt = _content(source.prompt_yaml)
    (wb_rules_path,) = source.group_rule_files
    wb_rules = _content(wb_rules_path)

    assert "<wormbase_scope>" in wb_rules
    assert "</wormbase_scope>" in wb_rules
    assert "C. elegans" in wb_rules
    for wormbase_only in ("<wormbase_scope>", "C. elegans", "WormBase"):
        assert wormbase_only not in base_prompt


def test_topic_tag_agent_is_disabled_for_0120_across_routing_palette_and_access(
    monkeypatch,
):
    try:
        definitions = _load_shipped_agents(monkeypatch)
        agent = definitions["gene_expression_topic_tag"]

        assert agent.supervisor_routing.enabled is False
        assert agent.frontend.show_in_palette is False
        assert agent.access.allowed_group_ids == ["WB"]
        assert agent.curation.launchable is True

        supervisor_tools = agent_loader.get_supervisor_tools()
        assert "gene_expression_topic_tag" not in {
            tool["agent_id"] for tool in supervisor_tools
        }
        assert "ask_gene_expression_topic_tag_specialist" not in {
            tool["tool_name"] for tool in supervisor_tools
        }

        registry = build_agent_registry()
        entry = registry["gene_expression_topic_tag"]
        assert entry["supervisor"]["enabled"] is False
        assert entry["frontend"]["show_in_palette"] is False
        assert flow_palette_show_in_palette("gene_expression_topic_tag", entry) is False

        assert is_resource_access_allowed(
            visibility_allowed=True,
            allowed_group_ids=agent.access.allowed_group_ids,
            active_group_ids=["WB"],
        )
        for active_groups in ([], ["FB"], ["MGI"], ["RGD"], ["SGD"], ["ZFIN"]):
            assert not is_resource_access_allowed(
                visibility_allowed=True,
                allowed_group_ids=agent.access.allowed_group_ids,
                active_group_ids=active_groups,
            )
    finally:
        agent_loader.reset_cache()


def test_topic_tag_agent_is_excluded_from_agent_studio_templates(monkeypatch):
    import src.api.agent_studio as api_module

    try:
        definitions = _load_shipped_agents(monkeypatch)
        rows = [
            SimpleNamespace(
                **_build_system_agent_values(
                    definitions[agent_id], instructions="System prompt"
                )
            )
            for agent_id in ("gene_expression_topic_tag", "gene_extractor")
        ]
        topic_row = next(r for r in rows if r.agent_key == "gene_expression_topic_tag")
        assert topic_row.supervisor_enabled is False
        assert topic_row.show_in_palette is False
        assert topic_row.allowed_group_ids == ["WB"]
        assert topic_row.is_active is True

        filtered_columns: list[str] = []

        class _FilteringQuery:
            def __init__(self, items):
                self._items = list(items)

            def filter(self, *criteria):
                items = list(self._items)
                for criterion in criteria:
                    col_name = criterion.left.name
                    if isinstance(criterion.right, True_):
                        expected = True
                    elif isinstance(criterion.right, False_):
                        expected = False
                    else:
                        expected = criterion.right.value
                    filtered_columns.append(col_name)
                    items = [item for item in items if getattr(item, col_name) == expected]
                return _FilteringQuery(items)

            def order_by(self, *_args, **_kwargs):
                return self

            def all(self):
                return list(self._items)

        wb_user = {"sub": "wb-curator", "cognito:groups": ["WormBaseCurator"]}
        assert api_module._authenticated_group_ids(wb_user) == ["WB"]
        assert api_module._agent_record_is_group_accessible(topic_row, wb_user) is True

        fake_db = SimpleNamespace(query=lambda *_args, **_kwargs: _FilteringQuery(rows))
        response = asyncio.run(
            api_module.get_agent_templates_endpoint(
                user=wb_user,
                db=fake_db,
            )
        )

        assert "show_in_palette" in filtered_columns
        template_ids = [item.agent_id for item in response.templates]
        assert "gene_expression_topic_tag" not in template_ids
        assert "gene_extractor" in template_ids
    finally:
        agent_loader.reset_cache()


def test_get_prompt_diagnostic_hides_topic_tag_from_non_wb_and_allows_wb(monkeypatch):
    import src.api.agent_studio as api_module
    from src.lib.agent_studio import catalog_service
    from src.lib.agent_studio.diagnostic_tools import tool_definitions
    from src.lib.agent_studio.models import ChatContext
    from src.lib.prompts import cache as prompt_cache
    from src.lib.prompts.assembly import PromptLayer, PromptLayerBundle

    try:
        definitions = _load_shipped_agents(monkeypatch)
        registry = build_agent_registry()
        monkeypatch.setattr(catalog_service, "AGENT_REGISTRY", registry)
        monkeypatch.setattr(prompt_cache, "is_initialized", lambda: True)

        topic_def = definitions["gene_expression_topic_tag"]
        gene_def = definitions["gene_extractor"]
        topic_agent = SimpleNamespace(
            agent_id="gene_expression_topic_tag",
            agent_name=topic_def.name,
            description=topic_def.description,
            source_file="packages/alliance/agents/gene_expression_topic_tag/prompt.yaml",
            has_group_rules=True,
            group_rules={"WB": SimpleNamespace()},
        )
        gene_agent = SimpleNamespace(
            agent_id="gene_extractor",
            agent_name=gene_def.name,
            description=gene_def.description,
            source_file="packages/alliance/agents/gene_extractor/prompt.yaml",
            has_group_rules=True,
            group_rules={"WB": SimpleNamespace(), "FB": SimpleNamespace()},
        )
        agents_by_id = {
            "gene_expression_topic_tag": topic_agent,
            "gene_extractor": gene_agent,
        }
        layer = PromptLayer(
            id="gene_expression_topic_tag:base_prompt",
            kind="base_prompt",
            title="Base",
            content="Topic tag base prompt",
            provenance="prompt_template:system",
            editable=True,
            locked=False,
            source_ref="prompt_templates:topic:version:1",
            hash="hash-topic",
        )
        bundle = PromptLayerBundle(
            agent_id="gene_expression_topic_tag",
            layers=(layer,),
            hash="hash-topic",
        )
        fake_catalog = SimpleNamespace(
            get_agent=lambda agent_id: agents_by_id.get(agent_id),
            get_effective_prompt_bundle=lambda agent_id, group_id=None: (
                bundle if agent_id == "gene_expression_topic_tag" else None
            ),
            catalog=SimpleNamespace(
                categories=[SimpleNamespace(agents=list(agents_by_id.values()))],
                available_groups=["FB", "WB"],
            ),
        )
        monkeypatch.setattr(catalog_service, "get_prompt_catalog", lambda: fake_catalog)

        # Tool contract description sent to Studio chat hides topic tag for non-WB callers.
        non_wb_desc, _ = tool_definitions._get_prompt_diagnostic_contract(
            active_group_ids=["FB"]
        )
        assert "gene_expression_topic_tag" not in non_wb_desc
        assert "gene_extractor" in non_wb_desc

        wb_desc, _ = tool_definitions._get_prompt_diagnostic_contract(
            active_group_ids=["WB"]
        )
        assert "gene_expression_topic_tag" in wb_desc

        with tool_definitions.scoped_diagnostic_active_groups(["FB"]):
            fb_tools = {
                t["name"]: t
                for t in api_module._get_all_opus_tools(ChatContext(active_tab="agents"))
            }
        assert "gene_expression_topic_tag" not in fb_tools["get_prompt"]["description"]

        with tool_definitions.scoped_diagnostic_active_groups(["WB"]):
            wb_tools = {
                t["name"]: t
                for t in api_module._get_all_opus_tools(ChatContext(active_tab="agents"))
            }
        assert "gene_expression_topic_tag" in wb_tools["get_prompt"]["description"]

        # Runtime execution via _execute_tool_call enforces group access.
        fb_result = asyncio.run(
            api_module._execute_tool_call(
                tool_name="get_prompt",
                tool_input={"agent_id": "gene_expression_topic_tag"},
                context=ChatContext(active_tab="agents"),
                user_email="fb@example.org",
                user_auth_sub="fb-sub",
                active_group_ids=["FB"],
            )
        )
        assert fb_result["status"] == "error"
        assert "gene_expression_topic_tag" not in fb_result["available_agents"]
        assert "gene_extractor" in fb_result["available_agents"]

        wb_result = asyncio.run(
            api_module._execute_tool_call(
                tool_name="get_prompt",
                tool_input={"agent_id": "gene_expression_topic_tag"},
                context=ChatContext(active_tab="agents"),
                user_email="wb@example.org",
                user_auth_sub="wb-sub",
                active_group_ids=["WB"],
            )
        )
        assert wb_result["status"] == "ok"
        assert wb_result["agent_id"] == "gene_expression_topic_tag"
    finally:
        agent_loader.reset_cache()


def test_registry_metadata_omits_topic_tag_domain_format_and_marks_hidden(monkeypatch):
    import src.api.agent_studio as api_module
    from src.lib.agent_studio import catalog_service

    try:
        _load_shipped_agents(monkeypatch)
        monkeypatch.setattr(catalog_service, "AGENT_REGISTRY", build_agent_registry())
        monkeypatch.setattr(
            api_module,
            "provision_user_async",
            lambda _principal: asyncio.sleep(0, result=SimpleNamespace(id=1)),
        )
        monkeypatch.setattr(
            api_module, "list_custom_agents_visible_to_user", lambda _db, _uid: []
        )

        wb_user = {"sub": "wb-curator", "cognito:groups": ["WormBaseCurator"]}
        assert api_module._authenticated_group_ids(wb_user) == ["WB"]
        wb_metadata = asyncio.run(
            api_module.get_registry_metadata(user=wb_user, db=SimpleNamespace())
        )
        topic_meta = wb_metadata.agents["gene_expression_topic_tag"]
        assert topic_meta.show_in_palette is False
        assert topic_meta.domain_extraction_ref is None
        assert wb_metadata.agents["gene_extractor"].show_in_palette is True
        assert wb_metadata.agents["gene_extractor"].domain_extraction_ref is not None

        fb_user = {"sub": "fb-curator", "cognito:groups": ["FlyBaseCurator"]}
        assert api_module._authenticated_group_ids(fb_user) == ["FB"]
        fb_metadata = asyncio.run(
            api_module.get_registry_metadata(user=fb_user, db=SimpleNamespace())
        )
        assert "gene_expression_topic_tag" not in fb_metadata.agents
    finally:
        agent_loader.reset_cache()


def test_topic_tag_disabled_config_passes_runtime_startup_validation(monkeypatch):
    try:
        definitions = _load_shipped_agents(monkeypatch)
        monkeypatch.setenv(
            "AGR_RUNTIME_OVERRIDES_PATH",
            str(REPO_PACKAGES_DIR / "alliance" / "config" / "runtime_overrides.yaml"),
        )
        registry = validate_runtime_packages()
        assert registry.loaded_packages

        system_rows = [
            SimpleNamespace(
                execution_revision_id=None,
                **_build_system_agent_values(agent, instructions="System prompt"),
            )
            for agent in definitions.values()
        ]
        monkeypatch.setattr(
            runtime_validation, "_fetch_active_agents", lambda: system_rows
        )
        monkeypatch.setattr(
            runtime_validation, "_fetch_head_output_contracts", lambda _agents: {}
        )

        report = runtime_validation.build_agent_runtime_report(strict_mode=True)

        assert report["errors"] == []
        assert report["status"] == "healthy"
        assert report["summary"]["missing_system_agent_count"] == 0
        topic_entry = next(
            item
            for item in report["agents"]
            if item["agent_key"] == "gene_expression_topic_tag"
        )
        assert topic_entry["errors"] == []
        assert topic_entry["disabled"] is False
    finally:
        agent_loader.reset_cache()


def test_agent_studio_prompt_and_docs_hide_topic_tag_for_0120():
    raw_prompt = ALLIANCE_STUDIO_PROMPT_PATH.read_text(encoding="utf-8")
    always_sent, _ = prepare_studio_prompt_template(raw_prompt)
    architecture = read_studio_guide(
        template=raw_prompt,
        render_diagnostic_tools=str,
        topic="system_architecture",
    )

    assert architecture["success"] is True
    assert "gene_expression_topic_tag" not in raw_prompt
    assert "gene_expression_topic_tag" not in always_sent
    assert "gene_expression_topic_tag" not in architecture["content"]

    docs_path = (
        REPO_PACKAGES_DIR / "alliance" / "agents" / "gene_expression_topic_tag" / "docs.yaml"
    )
    docs = yaml.safe_load(docs_path.read_text(encoding="utf-8"))
    assert docs["use_when"] == ["Not offered in release 0.12.0."]
    assert (
        "This agent is not offered to curators in release 0.12.0 while its rules and "
        "ABC workflow are being finished."
    ) in docs["limitations"]
    assert "cannot be submitted to ABC until then" not in "\n".join(docs["limitations"])

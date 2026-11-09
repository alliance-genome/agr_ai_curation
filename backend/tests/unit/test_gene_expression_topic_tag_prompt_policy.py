"""WormBase scope rules for the gene-expression topic-tag agent (ALL-1462).

The shared topic-tag prompt stays organism-neutral. WormBase's scope for
ATP:0000041 (wild-type C. elegans only, with WB's exclusions) is a WB group
rule, so it reaches WB curators and no other group.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from src.lib.config import agent_sources


REPO_PACKAGES_DIR = Path(__file__).resolve().parents[3] / "packages"


def _topic_tag_source():
    return next(
        source
        for source in agent_sources.resolve_agent_config_sources(REPO_PACKAGES_DIR)
        if source.folder_name == "gene_expression_topic_tag"
    )


def _content(path: Path) -> str:
    return str(yaml.safe_load(path.read_text(encoding="utf-8"))["content"])


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

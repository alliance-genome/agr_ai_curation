import sys
from pathlib import Path
from types import SimpleNamespace

import yaml

from src.lib.config.agent_sources import resolve_agent_config_sources
from src.lib.agent_studio import catalog_service


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _load_allele_extractor_mgi_group_rule() -> str:
    source = next(
        source
        for source in resolve_agent_config_sources(_repo_root() / "packages")
        if source.folder_name == "allele_extractor"
    )
    mgi_path = next(path for path in source.group_rule_files if path.stem == "mgi")
    data = yaml.safe_load(mgi_path.read_text(encoding="utf-8"))
    return str(data.get("content") or "")


def test_allele_extractor_mgi_overlay_includes_lab_code_disambiguation_workflow():
    content = _load_allele_extractor_mgi_group_rule()

    assert "tm1.1Hko" in content
    assert "em1Cya" in content
    assert "em1Gpt" in content
    assert "fullname_attribution" in content
    assert "`search_alleles` substring matching" in content
    assert "cross-check that the candidate's gene matches the experimental gene(s) discussed in the current paper" in content
    assert "ambiguities[]" in content


def test_allele_extractor_mgi_overlay_injects_for_active_mgi_group(monkeypatch):
    content = _load_allele_extractor_mgi_group_rule()
    fake_cache_module = SimpleNamespace(
        get_prompt_optional=lambda component, prompt_type, group_id=None: (
            SimpleNamespace(content=content)
            if component == "allele_extractor"
            and prompt_type == "group_rules"
            and group_id == "MGI"
            else None
        )
    )
    monkeypatch.setitem(sys.modules, "src.lib.prompts.cache", fake_cache_module)

    injected = catalog_service._inject_group_rules_with_overrides(
        base_prompt="BASE",
        group_ids=["MGI"],
        component_name="allele_extractor",
        group_overrides={},
    )

    assert injected.startswith("BASE")
    assert "fullname_attribution" in injected
    assert "`search_alleles` substring matching" in injected
